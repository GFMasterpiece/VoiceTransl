import asyncio
import hashlib
import json
import logging
from collections import deque
from uuid import uuid4

from ..domain.batch import (
    ACTIVE_BATCH_STATUSES,
    ACTIVE_ITEM_STATUSES,
    BatchItem,
    BatchRecord,
    item_counts,
    now,
)
from ..domain.workflow import (
    WorkflowRunError,
    validate_workflow_audio_selection,
    validate_workflow_graph,
    validate_workflow_resources,
    workflow_snapshot,
)
from ..storage.batch_store import BatchStore
from .workflow_connections import (
    freeze_workflow_connections,
    validate_frozen_workflow_connections,
)

logger = logging.getLogger("voicetransl.batches")


class BatchManager:
    """Persistent batches with a bounded dispatcher, sharing the job busy boundary."""

    def __init__(self, projects, jobs):
        self.projects = projects
        self.jobs = jobs
        self.store = BatchStore(projects)
        self.active: dict[str, BatchRecord] = {}
        self.pending = deque()
        self.workers: set[asyncio.Task] = set()
        self.stopping = False
        self.claimed_audio = set()
        self.claimed_sources = set()
        self.unstarted_claims = {}

    def get(self, pid, bid):
        batch = self.active.get(bid)
        if batch is not None and batch.project_id == pid:
            return batch
        return self.store.load(pid, bid)

    async def create(
        self,
        pid,
        revision,
        audio_ids,
        request_id,
        *,
        retry=None,
        cancelled=False,
        partial=False,
        target=None,
        context_audio_id=None,
        expected_group_revisions=None,
    ):
        payload = (
            {
                "retry_of": retry.id,
                "include_cancelled": cancelled,
                **({"include_partial": True} if partial else {}),
            }
            if retry is not None
            else {
                "workflow_revision": revision,
                "audio_ids": audio_ids,
                **(
                    {
                        "target": target,
                        "context_audio_id": context_audio_id,
                        "expected_group_revisions": expected_group_revisions,
                    }
                    if target
                    else {}
                ),
            }
        )
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        async with self.projects.mutation_lock(pid):
            previous = self.store.list(pid)
            # A prior final save may have failed while the filesystem was
            # unavailable. Reconcile our terminal state before accepting more
            # work once storage is writable again.
            for index, persisted in enumerate(previous):
                live = self.active.get(persisted.id)
                if live is not None and live.status not in ACTIVE_BATCH_STATUSES:
                    self.store.save(live)
                    self.active.pop(live.id, None)
                    previous[index] = live
            for existing in previous:
                if existing.request_id == request_id:
                    if existing.request_digest != digest:
                        raise RuntimeError("BATCH_REQUEST_CONFLICT")
                    return self.get(pid, existing.id)
            if self.stopping:
                raise RuntimeError("BATCH_STOPPING")
            if any(batch.status in ACTIVE_BATCH_STATUSES for batch in previous) or any(
                batch.project_id == pid and batch.status in ACTIVE_BATCH_STATUSES
                for batch in self.active.values()
            ):
                raise RuntimeError("BATCH_BUSY")
            project = self.projects.load_project(pid)
            from ..domain.workflow_scope import plan_source_scope, source_nodes
            from .configuration_evaluator import validate_configuration_snapshot
            from .workflow_groups import (
                freeze_group_snapshots,
                selected_audio_ids,
                validate_source_audio,
            )

            if retry is None:
                document = self.projects.load_workflow(pid)
                if document.workflow_revision != revision:
                    raise RuntimeError("WORKFLOW_REVISION_CONFLICT")
                issues = validate_workflow_graph(document.nodes, document.edges)
                issues.extend(validate_workflow_resources(project, document.nodes))
                if issues:
                    raise ValueError("INVALID_WORKFLOW")
                snapshot = workflow_snapshot(document).model_copy(deep=True)
                sources = (
                    [target["node_id"]]
                    if target and target["kind"] == "source"
                    else [node.id for node in source_nodes(snapshot.nodes)]
                    if target
                    else [plan_source_scope(snapshot).source_node_id]
                )
                scopes = {node_id: plan_source_scope(snapshot, node_id) for node_id in sources}
                needed = set().union(*(scope.node_ids for scope in scopes.values()))
                snapshot = freeze_group_snapshots(
                    self.projects, snapshot, pid, needed, expected_group_revisions
                )
                snapshot = freeze_workflow_connections(
                    self.projects, snapshot, pid, node_ids=needed
                )
                selections = []
                for node_id in sources:
                    selected = (
                        selected_audio_ids(snapshot, node_id, context_audio_id)
                        if target
                        else audio_ids
                    )
                    if not target:
                        node = next(node for node in snapshot.nodes if node.id == node_id)
                        validate_workflow_audio_selection([node], audio_ids=selected)
                    for source_index, aid in enumerate(selected):
                        selections.append(
                            (
                                node_id if target else None,
                                source_index + 1 if target else None,
                                aid,
                                None,
                            )
                        )
                completed_predecessors = {}
            else:
                if retry.status in ACTIVE_BATCH_STATUSES:
                    raise RuntimeError("BATCH_BUSY")
                snapshot = retry.workflow.model_copy(deep=True)
                states = (
                    {"failed", "interrupted", "blocked"}
                    | ({"cancelled"} if cancelled else set())
                    | ({"partial"} if partial else set())
                )
                selections = [
                    (item.source_node_id, item.source_sequence, item.audio_id, item)
                    for item in retry.items
                    if item.status in states
                ]
                if not selections:
                    raise ValueError("BATCH_NOT_RETRYABLE")
                needed = set().union(
                    *(
                        plan_source_scope(snapshot, node_id).node_ids
                        for node_id, _, _, _ in selections
                    )
                )
                validate_frozen_workflow_connections(snapshot, self.projects, pid, node_ids=needed)
                completed_predecessors = {
                    key: list(value) for key, value in retry.completed_predecessors.items()
                }
                for item in retry.items:
                    if item.status == "completed" and item.source_node_id and item.source_sequence:
                        completed_predecessors.setdefault(item.source_node_id, []).append(
                            item.source_sequence
                        )
                selected_keys = {(node_id, source_seq) for node_id, source_seq, _, _ in selections}
                for node_id, source_seq, _, old in selections:
                    if not self._continuous(retry, old):
                        continue
                    done = set(completed_predecessors.get(node_id, []))
                    for predecessor in retry.items:
                        if (
                            predecessor.source_node_id == node_id
                            and predecessor.source_sequence < source_seq
                            and predecessor.status != "completed"
                            and (node_id, predecessor.source_sequence) not in selected_keys
                            and predecessor.source_sequence not in done
                        ):
                            error = ValueError("BATCH_DEPENDENCY_NOT_COMPLETED")
                            error.details = {
                                "source_node_id": node_id,
                                "predecessor_sequence": predecessor.source_sequence,
                                "audio_id": predecessor.audio_id,
                                "status": predecessor.status,
                            }
                            raise error
            from ..domain.limits import MAX_BATCH_AUDIO_ITEMS

            if not selections or len(selections) > MAX_BATCH_AUDIO_ITEMS:
                raise ValueError("BATCH_ITEM_LIMIT")
            audios = {audio.id: audio for audio in project.audios}
            if any(aid not in audios for _, _, aid, _ in selections):
                raise ValueError("AUDIO_NOT_FOUND")
            for node_id, _, aid, _ in selections:
                scope = plan_source_scope(snapshot, node_id)
                validate_source_audio(snapshot, scope.source_node_id, aid)
                await validate_configuration_snapshot(
                    self.projects,
                    snapshot,
                    pid,
                    aid,
                    node_ids=set(scope.node_ids),
                    source_node_id=node_id,
                )
            if any(self.jobs.audio_busy(pid, aid) for _, _, aid, _ in selections):
                raise RuntimeError("AUDIO_BUSY")
            items = []
            for index, (node_id, source_sequence, aid, old) in enumerate(selections):
                audio = audios[aid]
                origin = getattr(audio.source, "origin", None)
                items.append(
                    BatchItem(
                        index=index,
                        sequence=old.sequence if old else index + 1,
                        source_node_id=node_id,
                        source_sequence=source_sequence,
                        audio_id=aid,
                        audio_name=old.audio_name if old else audio.source.original_name,
                        source_language=old.source_language if old else audio.source.language,
                        origin=old.origin if old else origin,
                    )
                )
            batch = BatchRecord(
                id=f"bat_{uuid4().hex}",
                project_id=pid,
                workflow_revision=snapshot.workflow_revision,
                workflow=snapshot,
                request_id=request_id,
                request_digest=digest,
                retry_of=retry.id if retry else None,
                total=len(items),
                counts=item_counts(items),
                items=items,
                completed_predecessors=completed_predecessors,
            )
            self.store.save(batch)
            self.active[batch.id] = batch
            for item in items:
                self.jobs.reservations[pid, item.audio_id] = batch.id
                self.pending.append((batch.id, item.index))
        self._dispatch()
        return batch

    def resize(self):
        """Start additional workers after the shared job limit changes."""
        self._dispatch()

    def _continuous(self, batch, item):
        if not item.source_node_id:
            return False
        from .workflow_groups import source_group_id

        group = batch.workflow.group_snapshots.get(
            source_group_id(batch.workflow, item.source_node_id), {}
        )
        return group.get("mode") == "continuous"

    def _ready(self, batch, item, predecessors, active_audio):
        if (batch.project_id, item.audio_id) in self.claimed_audio:
            return False
        if self._continuous(batch, item):
            if (batch.id, item.source_node_id) in self.claimed_sources:
                return False
            previous = predecessors.get((item.source_node_id, item.source_sequence - 1))
            if previous and previous.status in ACTIVE_ITEM_STATUSES:
                return False
            if item.source_sequence > 1 and not (
                previous
                and previous.status == "completed"
                or previous is None
                and item.source_sequence - 1
                in batch.completed_predecessors.get(item.source_node_id, [])
            ):
                item.status = "blocked"
                item.finished_at = now()
                item.error = WorkflowRunError(
                    type="GROUP_PREDECESSOR_FAILED", message="连续组前项未完成，后续项未执行"
                )
                active_audio[item.audio_id] -= 1
                self._release(batch, item, active_audio)
                return False
        return True

    def _dispatch(self):
        if self.stopping:
            return
        remaining = deque()
        changed = {}
        predecessors = {}
        active_audio = {}
        while self.pending:
            bid, index = self.pending.popleft()
            batch = self.active.get(bid)
            if batch is None or batch.items[index].status != "queued":
                continue
            item = batch.items[index]
            if bid not in predecessors:
                predecessors[bid] = {
                    (other.source_node_id, other.source_sequence): other for other in batch.items
                }
                from collections import Counter

                active_audio[bid] = Counter(
                    other.audio_id for other in batch.items if other.status in ACTIVE_ITEM_STATUSES
                )
            if len(self.workers) >= self.jobs.max_concurrent or not self._ready(
                batch, item, predecessors[bid], active_audio[bid]
            ):
                if item.status == "queued":
                    remaining.append((bid, index))
                else:
                    changed[bid] = batch
                continue
            self.claimed_audio.add((batch.project_id, item.audio_id))
            if self._continuous(batch, item):
                self.claimed_sources.add((batch.id, item.source_node_id))
            task = asyncio.create_task(self._worker(bid, index))
            self.unstarted_claims[task] = (
                batch.project_id,
                item.audio_id,
                batch.id,
                item.source_node_id,
            )
            self.workers.add(task)
            task.add_done_callback(self._worker_done)
        self.pending = remaining
        for batch in changed.values():
            try:
                self._finish(batch)
            except OSError:
                logger.exception("Unable to persist blocked batch %s", batch.id)

    def _worker_done(self, task):
        claim = self.unstarted_claims.pop(task, None)
        if claim:
            self.claimed_audio.discard(claim[:2])
            self.claimed_sources.discard(claim[2:])
        self.workers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Batch worker stopped", exc_info=task.exception())
            for batch in list(self.active.values()):
                if not any(item.status in ACTIVE_ITEM_STATUSES for item in batch.items):
                    try:
                        self._finish(batch)
                    except OSError:
                        logger.exception("Unable to persist terminal batch %s", batch.id)
        self._dispatch()

    def _release(self, batch, item, active_audio=None):
        key = (batch.project_id, item.audio_id)
        if (
            active_audio is not None
            and active_audio[item.audio_id]
            or active_audio is None
            and any(
                other.audio_id == item.audio_id and other.status in ACTIVE_ITEM_STATUSES
                for other in batch.items
            )
        ):
            return
        if self.jobs.reservations.get(key) == batch.id:
            self.jobs.reservations.pop(key, None)

    def _finish(self, batch):
        batch.refresh_counts()
        if any(item.status in ACTIVE_ITEM_STATUSES for item in batch.items):
            self.store.save(batch)
            return
        counts = batch.counts
        batch.status = (
            "interrupted"
            if counts["interrupted"]
            else "cancelled"
            if batch.cancel_requested
            else "completed"
            if counts["completed"] == batch.total
            else "failed"
            if counts["failed"] == batch.total
            else "partial"
        )
        batch.finished_at = now()
        self.store.save(batch)
        self.active.pop(batch.id, None)

    async def _worker(self, bid, index):
        claim = self.unstarted_claims.pop(asyncio.current_task(), None)
        batch = self.active.get(bid)
        if batch is None:
            if claim:
                self.claimed_audio.discard(claim[:2])
                self.claimed_sources.discard(claim[2:])
            return
        item = batch.items[index]
        if item.status != "queued":
            self.claimed_audio.discard((batch.project_id, item.audio_id))
            self.claimed_sources.discard((batch.id, item.source_node_id))
            return
        run = None
        task = None
        try:
            item.status = "running"
            item.started_at = now()
            batch.status = "running"
            batch.started_at = batch.started_at or now()
            self.store.save(batch)
            run = await self.jobs.start_workflow_from_snapshot(
                batch.project_id,
                item.audio_id,
                batch.workflow,
                batch.id,
                sequence=item.sequence,
                source_node_id=item.source_node_id,
            )
            task = self.jobs.tasks.get((batch.project_id, item.audio_id))
            item.run_id = run.id
            self.store.save(batch)
            if batch.cancel_requested:
                self.jobs.cancel_workflow(batch.project_id, item.audio_id, run.id, force=True)
            if task is not None:
                await task
            item.status = run.status.value
            item.error = run.error
        except asyncio.CancelledError:
            if batch.cancel_requested and not asyncio.current_task().cancelling():
                item.status = "cancelled"
            else:
                item.status = "interrupted"
                raise
        except Exception:
            logger.exception("Batch item failed: %s/%s", bid, item.audio_id)
            if run is not None and task is not None and not task.done():
                try:
                    self.jobs.cancel_workflow(batch.project_id, item.audio_id, run.id, force=True)
                except Exception:
                    logger.exception("Unable to persist cancellation for %s", run.id)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            item.status = "failed"
            item.error = WorkflowRunError(type="BATCH_ITEM_FAILED", message="该音频运行失败")
        finally:
            item.finished_at = now()
            self.claimed_audio.discard((batch.project_id, item.audio_id))
            self.claimed_sources.discard((batch.id, item.source_node_id))
            self._release(batch, item)
            self._finish(batch)

    async def cancel(self, pid, bid):
        async with self.projects.mutation_lock(pid):
            batch = self.get(pid, bid)
            if batch.status not in ACTIVE_BATCH_STATUSES:
                return batch, False
            batch.cancel_requested = True
            batch.status = "cancelling"
            for item in batch.items:
                if item.status == "queued":
                    item.status = "cancelled"
                    item.finished_at = now()
                    self._release(batch, item)
                elif item.status == "running" and item.run_id:
                    try:
                        self.jobs.cancel_workflow(pid, item.audio_id, item.run_id, force=True)
                    except RuntimeError:
                        pass
            self._finish(batch)
            return batch, True

    async def shutdown(self):
        self.stopping = True
        tasks = list(self.workers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for batch in list(self.active.values()):
            for item in batch.items:
                if item.status in ACTIVE_ITEM_STATUSES:
                    item.status = "interrupted"
                    item.finished_at = now()
                    self._release(batch, item)
            self._finish(batch)
        self.pending.clear()
