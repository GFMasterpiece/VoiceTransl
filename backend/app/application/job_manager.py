import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from ..domain import JobStatus
from ..domain.project import PRIMARY_AUDIO_ID
from ..domain.workflow import (
    WorkflowNodeError,
    WorkflowNodeRecord,
    WorkflowNodeStatus,
    WorkflowReusedFrom,
    WorkflowRun,
    WorkflowRunError,
    WorkflowRunStatus,
    validate_workflow_audio_selection,
    validate_workflow_graph,
    validate_workflow_resources,
    workflow_parameter_sources,
    workflow_snapshot,
)
from .adjustable_limiter import AdjustableLimiter
from .workflow_connections import (
    freeze_workflow_connections,
    validate_frozen_workflow_connections,
)
from .workflow_reuse import digest, prepare_reuse, reject
from .workflow_runner import execute_workflow_run

logger = logging.getLogger("voicetransl.jobs")


class AudioTaskMap(dict):
    """Tuple keys internally, with legacy primary lookup via project ID."""

    def __getitem__(self, key):
        return super().__getitem__((key, PRIMARY_AUDIO_ID) if isinstance(key, str) else key)


class JobManager:
    def __init__(
        self,
        store,
        event_bus,
        delay_ms=500,
        max_concurrent=1,
        delay_explicit=False,
    ):
        self.store = store
        self.event_bus = event_bus
        self.delay_ms = delay_ms
        self._initial_delay_ms = delay_ms
        self.delay_explicit = delay_explicit
        self.tasks: dict[tuple[str, str], asyncio.Task] = AudioTaskMap()
        self.preparations: set[asyncio.Task] = set()
        self.stopping = False
        self.cancel_events: dict[tuple[str, str], asyncio.Event] = {}
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.workflow_cancel_events: dict[tuple[str, str], asyncio.Event] = {}
        self.workflow_runs: dict[tuple[str, str], WorkflowRun] = {}
        self.reservations: dict[tuple[str, str], str] = {}
        self.max_concurrent = max_concurrent
        self.semaphore = AdjustableLimiter(max_concurrent)

    def set_max_concurrent(self, limit):
        self.semaphore.resize(limit)
        self.max_concurrent = limit

    def job(self, project_id, audio_id=PRIMARY_AUDIO_ID):
        return self.store.scope(project_id, audio_id).load_job(project_id)

    def running(self, project_id, audio_id=PRIMARY_AUDIO_ID):
        t = self.tasks.get((project_id, audio_id))
        return t is not None and not t.done()

    def running_workflow(self, project_id, audio_id=PRIMARY_AUDIO_ID):
        key = (project_id, audio_id)
        run = self.workflow_runs.get(key)
        return run is not None and run.status in {
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.CANCELLING,
        }

    def workflow_run(self, project_id, audio_id=PRIMARY_AUDIO_ID):
        return self.workflow_runs.get((project_id, audio_id))

    async def start_workflow_from_snapshot(
        self, project_id, audio_id, snapshot, owner, sequence=None
    ):
        return await self.start_workflow(
            project_id,
            audio_id,
            snapshot.workflow_revision,
            _snapshot=snapshot,
            _owner=owner,
            _sequence=sequence,
        )

    def audio_busy(self, project_id, audio_id, owner=None):
        reservation = self.reservations.get((project_id, audio_id))
        if reservation is not None and reservation != owner:
            return True
        if self.running(project_id, audio_id):
            return True
        previous = self.job(project_id, audio_id)
        return previous.status in {JobStatus.RUNNING, JobStatus.CANCELLING} or (
            previous.status == JobStatus.PENDING and previous.config_snapshot is not None
        )

    async def start_workflow(self, project_id, audio_id, workflow_revision, **kwargs):
        # Track the whole submission, including lock waits and reuse preflight.
        # There is no run record yet when shutdown cancels those early stages.
        preparation_task = asyncio.current_task()
        self.preparations.add(preparation_task)
        try:
            return await self._start_workflow(project_id, audio_id, workflow_revision, **kwargs)
        finally:
            self.preparations.discard(preparation_task)

    async def _start_workflow(
        self,
        project_id,
        audio_id,
        workflow_revision,
        *,
        _snapshot=None,
        _owner=None,
        _sequence=None,
        rerun=None,
        request_id=None,
    ):
        async with self.store.mutation_lock(project_id):
            async with self._lock(project_id, audio_id):
                key = (project_id, audio_id)
                request_digest = (
                    digest(
                        {
                            "workflow_revision": workflow_revision,
                            "rerun": rerun.model_dump() if rerun else None,
                        }
                    )
                    if request_id
                    else None
                )
                if request_id:
                    for existing in self.store.list_workflow_runs(
                        project_id, audio_id, limit=2**63 - 1, offset=0
                    )[0]:
                        if existing.request_id == request_id:
                            if existing.request_digest != request_digest:
                                raise RuntimeError("WORKFLOW_REQUEST_CONFLICT")
                            return existing
                if self.stopping:
                    raise RuntimeError("WORKFLOW_STOPPING")
                if self.audio_busy(project_id, audio_id, _owner):
                    raise RuntimeError("AUDIO_BUSY")
                store = self.store
                document = _snapshot or self.store.load_workflow(project_id)
                if document.workflow_revision != workflow_revision:
                    raise RuntimeError("WORKFLOW_REVISION_CONFLICT")
                issues = validate_workflow_graph(document.nodes, document.edges)
                issues.extend(
                    validate_workflow_resources(self.store.load_project(project_id), document.nodes)
                )
                if issues:
                    raise ValueError("invalid workflow")
                validate_workflow_audio_selection(
                    document.nodes,
                    audio_id=audio_id,
                    allow_collection_subset=rerun is not None
                    or (_snapshot is not None and _owner is not None),
                )
                snapshot = (
                    _snapshot.model_copy(deep=True)
                    if _snapshot is not None
                    else workflow_snapshot(document)
                )
                reuse = (
                    await prepare_reuse(self.store, project_id, audio_id, snapshot, rerun)
                    if rerun
                    else None
                )
                if self.stopping:
                    raise RuntimeError("WORKFLOW_STOPPING")
                if _snapshot is None:
                    snapshot = freeze_workflow_connections(
                        self.store,
                        snapshot,
                        project_id,
                        node_ids=set(reuse.plan.execute_node_ids) if reuse else None,
                    )
                else:
                    validate_frozen_workflow_connections(snapshot, self.store, project_id)
                from .configuration_evaluator import validate_configuration_snapshot

                await validate_configuration_snapshot(
                    self.store,
                    snapshot,
                    project_id,
                    audio_id,
                    node_ids=set(reuse.plan.execute_node_ids) if reuse else None,
                )
                queued_mock_delay_ms = self.delay_ms
                run = WorkflowRun(
                    id=f"wfr_{uuid4().hex}",
                    project_id=project_id,
                    audio_id=audio_id,
                    batch_id=_owner if _snapshot is not None else None,
                    batch_item_sequence=reuse.source.batch_item_sequence
                    if reuse
                    else _sequence
                    if _snapshot is not None
                    else None,
                    rerun=reuse.metadata(rerun) if reuse else None,
                    source_identity=reuse.source_identity if reuse else None,
                    request_id=request_id,
                    request_digest=request_digest,
                    workflow_revision=document.workflow_revision,
                    workflow=snapshot,
                    nodes=[
                        WorkflowNodeRecord(
                            node_id=node.id,
                            node_type=node.type,
                            config=node.config,
                            execution_kind="executed"
                            if reuse and node.id in reuse.plan.execute_node_ids
                            else None,
                            parameter_sources=workflow_parameter_sources(node, snapshot.edges),
                        )
                        for node in snapshot.nodes
                    ],
                )
                try:
                    self.store.create_workflow_run(run)
                except OSError:
                    # run.json is the commit point. A later project timestamp
                    # failure must not leave a published pending run unscheduled.
                    published = self.store.load_workflow_run(project_id, audio_id, run.id)
                    if published != run:
                        raise
                event = asyncio.Event()
                if reuse:
                    from ..domain.checkpoint import CheckpointDescriptor
                    from .workflow_checkpoints import copy_checkpoint

                    source_records = {record.node_id: record for record in reuse.source.nodes}
                    nodes = {node.id: node for node in snapshot.nodes}
                    for record in run.nodes:
                        if record.node_id in reuse.plan.skipped_node_ids:
                            record.status = WorkflowNodeStatus.SKIPPED
                    try:
                        for record in run.nodes:
                            if record.node_id in reuse.plan.reuse_node_ids:
                                original = source_records[record.node_id]
                                descriptor, result = await copy_checkpoint(
                                    store,
                                    reuse.source,
                                    run,
                                    nodes[record.node_id],
                                    CheckpointDescriptor.model_validate(original.checkpoint),
                                    event,
                                    available_outputs=reuse.available_outputs,
                                )
                                record.checkpoint = descriptor.model_dump(mode="json")
                                record.status = WorkflowNodeStatus.COMPLETED
                                record.execution_kind = "reused"
                                record.reused_from = WorkflowReusedFrom(
                                    run_id=reuse.source.id, node_id=record.node_id
                                )
                                record.effective_config = original.effective_config
                                record.artifact = result.artifact
                                record.preview = result.preview
                        store.save_workflow_run(run)
                    except asyncio.CancelledError:
                        # No job task exists until all reuse files have been
                        # copied. The HTTP task owns preparation and must
                        # publish a terminal record after copy cleanup joins.
                        run.status = WorkflowRunStatus.INTERRUPTED
                        run.finished_at = datetime.now(timezone.utc)
                        for record in run.nodes:
                            if record.status == WorkflowNodeStatus.PENDING:
                                record.status = WorkflowNodeStatus.INTERRUPTED
                                record.finished_at = run.finished_at
                        store.save_workflow_run(run)
                        raise
                    except Exception:
                        run.status = WorkflowRunStatus.FAILED
                        run.error = WorkflowRunError(
                            type="RERUN_CHECKPOINT_INVALID", message="复用结果复制失败"
                        )
                        run.finished_at = datetime.now(timezone.utc)
                        for record in run.nodes:
                            if record.status == WorkflowNodeStatus.PENDING:
                                record.status = WorkflowNodeStatus.BLOCKED
                                record.finished_at = run.finished_at
                        store.save_workflow_run(run)
                        reject(reuse.plan, "RERUN_CHECKPOINT_INVALID", "复用结果复制失败")
                if self.stopping:
                    run.status = WorkflowRunStatus.INTERRUPTED
                    run.finished_at = datetime.now(timezone.utc)
                    for record in run.nodes:
                        if record.status == WorkflowNodeStatus.PENDING:
                            record.status = WorkflowNodeStatus.INTERRUPTED
                            record.finished_at = run.finished_at
                    store.save_workflow_run(run)
                    raise RuntimeError("WORKFLOW_STOPPING")
                self.workflow_cancel_events[key] = event
                self.workflow_runs[key] = run

                async def work():
                    current_task = asyncio.current_task()
                    try:
                        async with self.semaphore:
                            if event.is_set():
                                run.cancel_requested = True
                                run.status = WorkflowRunStatus.CANCELLED
                                run.finished_at = datetime.now(timezone.utc)
                                for node in run.nodes:
                                    if node.status in {
                                        WorkflowNodeStatus.PENDING,
                                        WorkflowNodeStatus.RUNNING,
                                    }:
                                        node.status = WorkflowNodeStatus.CANCELLED
                                        node.finished_at = run.finished_at
                                store.save_workflow_run(run)
                                return
                            await execute_workflow_run(
                                store,
                                run,
                                event,
                                mock_delay_ms=queued_mock_delay_ms,
                            )
                    except asyncio.CancelledError:
                        if run.status in {
                            WorkflowRunStatus.PENDING,
                            WorkflowRunStatus.RUNNING,
                            WorkflowRunStatus.CANCELLING,
                        }:
                            run.status = (
                                WorkflowRunStatus.CANCELLED
                                if event.is_set()
                                else WorkflowRunStatus.INTERRUPTED
                            )
                            run.finished_at = datetime.now(timezone.utc)
                            for node in run.nodes:
                                if node.status in {
                                    WorkflowNodeStatus.PENDING,
                                    WorkflowNodeStatus.RUNNING,
                                }:
                                    node.status = (
                                        WorkflowNodeStatus.CANCELLED
                                        if event.is_set()
                                        else WorkflowNodeStatus.INTERRUPTED
                                    )
                                    node.finished_at = run.finished_at
                            store.save_workflow_run(run)
                        raise
                    except Exception:
                        if run.status not in {
                            WorkflowRunStatus.COMPLETED,
                            WorkflowRunStatus.PARTIAL,
                            WorkflowRunStatus.FAILED,
                            WorkflowRunStatus.CANCELLED,
                            WorkflowRunStatus.INTERRUPTED,
                        }:
                            run.status = WorkflowRunStatus.FAILED
                            run.error = WorkflowRunError(
                                type="WORKFLOW_RUN_FAILED", message="工作流运行失败"
                            )
                            run.finished_at = datetime.now(timezone.utc)
                            for node in run.nodes:
                                if node.status in {
                                    WorkflowNodeStatus.PENDING,
                                    WorkflowNodeStatus.RUNNING,
                                }:
                                    node.status = WorkflowNodeStatus.BLOCKED
                                    node.finished_at = run.finished_at
                                    node.error = WorkflowNodeError(
                                        type="DEPENDENCY_BLOCKED",
                                        message="工作流运行失败，节点未执行",
                                    )
                            store.save_workflow_run(run)
                    finally:
                        if self.tasks.get(key) is current_task:
                            self.tasks.pop(key, None)
                        if self.workflow_cancel_events.get(key) is event:
                            self.workflow_cancel_events.pop(key, None)
                        if self.workflow_runs.get(key) is run:
                            self.workflow_runs.pop(key, None)

                task = asyncio.create_task(work())
                self.tasks[key] = task

                def done(completed):
                    # A task cancelled before its first instruction never enters
                    # work()'s finally block. Persist and release it here too.
                    if completed.cancelled() and run.status in {
                        WorkflowRunStatus.PENDING,
                        WorkflowRunStatus.RUNNING,
                        WorkflowRunStatus.CANCELLING,
                    }:
                        run.status = (
                            WorkflowRunStatus.CANCELLED
                            if event.is_set()
                            else WorkflowRunStatus.INTERRUPTED
                        )
                        run.finished_at = datetime.now(timezone.utc)
                        for node in run.nodes:
                            if node.status in {
                                WorkflowNodeStatus.PENDING,
                                WorkflowNodeStatus.RUNNING,
                            }:
                                node.status = (
                                    WorkflowNodeStatus.CANCELLED
                                    if event.is_set()
                                    else WorkflowNodeStatus.INTERRUPTED
                                )
                                node.finished_at = run.finished_at
                        store.save_workflow_run(run)
                    if self.tasks.get(key) is completed:
                        self.tasks.pop(key, None)
                    if self.workflow_cancel_events.get(key) is event:
                        self.workflow_cancel_events.pop(key, None)
                    if self.workflow_runs.get(key) is run:
                        self.workflow_runs.pop(key, None)

                task.add_done_callback(done)
                return run

    def cancel_workflow(self, project_id, audio_id, run_id, *, force=False):
        key = (project_id, audio_id)
        run = self.workflow_runs.get(key)
        if run is None or run.id != run_id:
            raise RuntimeError("WORKFLOW_RUN_NOT_ACTIVE")
        if run.status == WorkflowRunStatus.PENDING and not self.running(project_id, audio_id):
            return False
        if run.status not in {
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.CANCELLING,
        }:
            return False
        event = self.workflow_cancel_events.get(key)
        if event is None:
            return False
        # Re-read state under the audio lock so a cancel racing with normal
        # completion cannot regress a terminal run to cancelling.
        store = self.store
        persisted = store.load_workflow_run(project_id, audio_id, run_id)
        if persisted.status not in {
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.CANCELLING,
        }:
            return False
        event.set()
        persisted.cancel_requested = True
        persisted.status = WorkflowRunStatus.CANCELLING
        store.save_workflow_run(persisted)
        run.cancel_requested = True
        run.status = WorkflowRunStatus.CANCELLING
        if force:
            task = self.tasks.get(key)
            if task is not None:
                task.cancel()
        return True

    def _lock(self, project_id: str, audio_id=PRIMARY_AUDIO_ID) -> asyncio.Lock:
        return self.locks.setdefault((project_id, audio_id), asyncio.Lock())

    async def delete_project(self, project_id: str) -> None:
        """Delete a project while serializing against start requests."""
        async with self.store.mutation_lock(project_id):
            for audio in self.store.load_project(project_id).audios:
                if (project_id, audio.id) in self.reservations:
                    raise RuntimeError("PROJECT_BUSY")
                if self.running_workflow(project_id, audio.id):
                    raise RuntimeError("PROJECT_BUSY")
                job = self.job(project_id, audio.id)
                if (
                    self.running(project_id, audio.id)
                    or job.status in {JobStatus.RUNNING, JobStatus.CANCELLING}
                    or (job.status == JobStatus.PENDING and job.config_snapshot is not None)
                ):
                    raise RuntimeError("PROJECT_BUSY")
            self.store.delete_project(project_id)

    async def shutdown(self):
        """Stop in-process jobs during application shutdown.

        Cancellation is cooperative at stage boundaries, but the task itself
        is also cancelled so shutdown cannot leave a dangling task waiting on
        the global semaphore.
        """
        self.stopping = True
        tasks = list(dict.fromkeys([*self.tasks.values(), *self.preparations]))
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
