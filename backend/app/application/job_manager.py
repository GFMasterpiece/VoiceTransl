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
    WorkflowRun,
    WorkflowRunError,
    WorkflowRunStatus,
    validate_workflow_graph,
    validate_workflow_resources,
    workflow_snapshot,
)
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
        self.cancel_events: dict[tuple[str, str], asyncio.Event] = {}
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.workflow_cancel_events: dict[tuple[str, str], asyncio.Event] = {}
        self.workflow_runs: dict[tuple[str, str], WorkflowRun] = {}
        self.semaphore = asyncio.Semaphore(max_concurrent)

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

    async def start_workflow(self, project_id, audio_id, workflow_revision):
        async with self.store.mutation_lock(project_id):
            async with self._lock(project_id, audio_id):
                key = (project_id, audio_id)
                if self.running(project_id, audio_id):
                    raise RuntimeError("AUDIO_BUSY")
                store = self.store
                previous = store.scope(project_id, audio_id).load_job(project_id)
                if previous.status in {JobStatus.RUNNING, JobStatus.CANCELLING} or (
                    previous.status == JobStatus.PENDING and previous.config_snapshot is not None
                ):
                    raise RuntimeError("AUDIO_BUSY")
                document = self.store.load_workflow(project_id)
                if document.workflow_revision != workflow_revision:
                    raise RuntimeError("WORKFLOW_REVISION_CONFLICT")
                issues = validate_workflow_graph(document.nodes, document.edges)
                issues.extend(
                    validate_workflow_resources(self.store.load_project(project_id), document.nodes)
                )
                if issues:
                    raise ValueError("invalid workflow")
                from ..domain.node_registry import NODE_DEFINITIONS

                for node in document.nodes:
                    definition = NODE_DEFINITIONS.get(node.type)
                    binding = (
                        (definition.presentation or {}).get("resource_input")
                        if definition
                        else None
                    )
                    if binding and binding["config_key"] in node.config:
                        selected = node.config[binding["config_key"]]
                        if not isinstance(selected, str):
                            raise ValueError("invalid audio binding")
                        if selected != audio_id:
                            raise ValueError("INPUT_AUDIO_MISMATCH")
                snapshot = workflow_snapshot(document)
                queued_mock_delay_ms = self.delay_ms
                run = WorkflowRun(
                    id=f"wfr_{uuid4().hex}",
                    project_id=project_id,
                    audio_id=audio_id,
                    workflow_revision=document.workflow_revision,
                    workflow=snapshot,
                    nodes=[
                        WorkflowNodeRecord(
                            node_id=node.id,
                            node_type=node.type,
                            config=node.config,
                        )
                        for node in document.nodes
                    ],
                )
                self.store.create_workflow_run(run)
                event = asyncio.Event()
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
                            run.status = WorkflowRunStatus.INTERRUPTED
                            run.finished_at = datetime.now(timezone.utc)
                            for node in run.nodes:
                                if node.status in {
                                    WorkflowNodeStatus.PENDING,
                                    WorkflowNodeStatus.RUNNING,
                                }:
                                    node.status = WorkflowNodeStatus.INTERRUPTED
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

                self.tasks[key] = asyncio.create_task(work())
                return run

    def cancel_workflow(self, project_id, audio_id, run_id):
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
        return True

    def _lock(self, project_id: str, audio_id=PRIMARY_AUDIO_ID) -> asyncio.Lock:
        return self.locks.setdefault((project_id, audio_id), asyncio.Lock())

    async def delete_project(self, project_id: str) -> None:
        """Delete a project while serializing against start requests."""
        async with self.store.mutation_lock(project_id):
            for audio in self.store.load_project(project_id).audios:
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
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
