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
from .workflow_profiles import freeze_workflow_profiles, validate_frozen_workflow_profiles

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

    def get(self, pid, bid):
        batch = self.active.get(bid)
        if batch is not None and batch.project_id == pid:
            return batch
        return self.store.load(pid, bid)

    async def create(self, pid, revision, audio_ids, request_id, *, retry=None, cancelled=False):
        payload = (
            {"retry_of": retry.id, "include_cancelled": cancelled}
            if retry is not None
            else {"workflow_revision": revision, "audio_ids": audio_ids}
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
            if retry is None:
                document = self.projects.load_workflow(pid)
                if document.workflow_revision != revision:
                    raise RuntimeError("WORKFLOW_REVISION_CONFLICT")
                issues = validate_workflow_graph(document.nodes, document.edges)
                issues.extend(validate_workflow_resources(project, document.nodes))
                if issues:
                    raise ValueError("INVALID_WORKFLOW")
                snapshot = workflow_snapshot(document).model_copy(deep=True)
                snapshot = freeze_workflow_profiles(self.projects, snapshot, pid)
            else:
                if retry.status in ACTIVE_BATCH_STATUSES:
                    raise RuntimeError("BATCH_BUSY")
                snapshot = retry.workflow.model_copy(deep=True)
                validate_frozen_workflow_profiles(snapshot, self.projects, pid)
                states = {"failed", "interrupted"} | ({"cancelled"} if cancelled else set())
                audio_ids = [item.audio_id for item in retry.items if item.status in states]
                if not audio_ids:
                    raise ValueError("BATCH_NOT_RETRYABLE")
            validate_workflow_audio_selection(
                snapshot.nodes, audio_ids=audio_ids, allow_collection_subset=retry is not None
            )
            audios = {audio.id: audio for audio in project.audios}
            if any(aid not in audios for aid in audio_ids):
                raise ValueError("AUDIO_NOT_FOUND")
            if any(self.jobs.audio_busy(pid, aid) for aid in audio_ids):
                raise RuntimeError("AUDIO_BUSY")
            old_items = {item.audio_id: item for item in retry.items} if retry else {}
            items = []
            for index, aid in enumerate(audio_ids):
                audio = audios[aid]
                origin = getattr(audio.source, "origin", None)
                if origin is not None and hasattr(origin, "model_dump"):
                    origin = origin.model_dump(mode="json")
                old = old_items.get(aid)
                items.append(
                    BatchItem(
                        index=index,
                        sequence=(old.sequence if old.sequence is not None else old.index + 1)
                        if old
                        else index + 1,
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
            )
            self.store.save(batch)
            self.active[batch.id] = batch
            for item in items:
                self.jobs.reservations[pid, item.audio_id] = batch.id
                self.pending.append((batch.id, item.index))
        self._dispatch()
        return batch

    def _dispatch(self):
        if self.stopping:
            return
        for _ in range(self.jobs.max_concurrent - len(self.workers)):
            if not self.pending:
                break
            task = asyncio.create_task(self._worker())
            self.workers.add(task)
            task.add_done_callback(self._worker_done)

    def _worker_done(self, task):
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

    def _release(self, batch, item):
        key = (batch.project_id, item.audio_id)
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

    async def _worker(self):
        while self.pending and not self.stopping:
            bid, index = self.pending.popleft()
            batch = self.active.get(bid)
            if batch is None:
                continue
            item = batch.items[index]
            if item.status != "queued":
                continue
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
                    sequence=item.sequence if item.sequence is not None else item.index + 1,
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
                        self.jobs.cancel_workflow(
                            batch.project_id, item.audio_id, run.id, force=True
                        )
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
