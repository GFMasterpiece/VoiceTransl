import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from ..config import load_preset
from ..domain import Job, JobError, JobStatus, PipelineConfig
from ..domain.project import PRIMARY_AUDIO_ID
from ..domain.workflow import (
    WorkflowNodeError,
    WorkflowNodeRecord,
    WorkflowNodeStatus,
    WorkflowRun,
    WorkflowRunError,
    WorkflowRunStatus,
    validate_workflow_graph,
    workflow_snapshot,
)
from ..logging_utils import stable_job_error
from ..pipeline import (
    AlignmentStage,
    ASRStage,
    ExportStage,
    PipelineContext,
    PipelineRunner,
    PreprocessStage,
    TranslationStage,
)
from ..providers import (
    CrispASRProvider,
    FFmpegPreprocessProvider,
    HTTPTranslationProvider,
    MockAlignerProvider,
    MockASRProvider,
    MockPreprocessProvider,
    MockTranslatorProvider,
)
from .workflow_asr import ASRSettings
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
        preset_dir=None,
        delay_explicit=False,
        ffmpeg_path="ffmpeg",
        preprocess_runner=None,
        crispasr_executable=("crispasr/linux-v0.8.38/crispasr-linux-x86_64-cuda13/crispasr"),
        crispasr_model_dir="crispasr/models",
        crispasr_timeout_seconds=300,
        crispasr_process_factory=None,
    ):
        self.store = store
        self.event_bus = event_bus
        self.delay_ms = delay_ms
        self._initial_delay_ms = delay_ms
        self.delay_explicit = delay_explicit
        self.preset_dir = preset_dir
        self.ffmpeg_path = ffmpeg_path
        self.preprocess_runner = preprocess_runner
        self.crispasr_executable = crispasr_executable
        self.crispasr_model_dir = crispasr_model_dir
        self.crispasr_timeout_seconds = crispasr_timeout_seconds
        self.crispasr_process_factory = crispasr_process_factory
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
                if issues:
                    raise ValueError("invalid workflow")
                snapshot = workflow_snapshot(document)
                # Queue-time capture: later runtime setting edits cannot alter
                # an already accepted run.
                asr_settings = ASRSettings(
                    executable=self.crispasr_executable,
                    model_dir=self.crispasr_model_dir,
                    timeout_seconds=self.crispasr_timeout_seconds,
                    process_factory=self.crispasr_process_factory,
                    mock_delay_ms=self.delay_ms,
                )
                queued_ffmpeg_path = self.ffmpeg_path
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
                                ffmpeg_path=queued_ffmpeg_path,
                                asr_settings=asr_settings,
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

    async def start(self, project_id, resume=True, audio_id=PRIMARY_AUDIO_ID):
        async with self.store.mutation_lock(project_id):
            return await self._start(project_id, resume, audio_id)

    async def _start(self, project_id, resume, audio_id):
        # The lock covers the running check and task registration.  Without it,
        # two simultaneous HTTP requests could both pass the check.
        async with self._lock(project_id, audio_id):
            store = self.store.scope(project_id, audio_id)
            key = (project_id, audio_id)
            if self.running(project_id, audio_id):
                raise RuntimeError(
                    "AUDIO_BUSY"
                    if self.running_workflow(project_id, audio_id)
                    else "JOB_ALREADY_RUNNING"
                )
            if self.running_workflow(project_id, audio_id):
                raise RuntimeError("AUDIO_BUSY")
            project = store.load_project(project_id)
            previous = store.load_job(project_id)
            # The in-memory task map is intentionally not the source of truth:
            # a task can disappear after a process restart or an unexpected
            # scheduler failure while job.json still says it is running.
            if previous.status in {JobStatus.RUNNING, JobStatus.CANCELLING} or (
                previous.status == JobStatus.PENDING and previous.config_snapshot is not None
            ):
                raise RuntimeError("JOB_ALREADY_RUNNING")
            pipeline_resume = resume
            if previous.status == JobStatus.COMPLETED:
                # A completed job always starts a fresh pipeline run with a
                # new identity and the current editable configuration.
                pipeline_resume = False

            if resume and previous.status in {
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.INTERRUPTED,
            }:
                job = previous
            elif resume and previous.status == JobStatus.PENDING:
                job = previous
            else:
                # Explicit restart gets a new job identity while keeping the
                # project's current job pointer in job.json.
                job = Job(
                    id=f"job_{uuid4().hex}", project_id=project_id, audio_id=audio_id, stages={}
                )
                from ..domain import default_stages

                job.stages = default_stages()
            # Legacy projects have no config.json or snapshot.  Resolve their
            # safe mock defaults at run time and persist the exact copy used
            # by this job before the asynchronous pipeline starts.
            snapshot: PipelineConfig = (
                previous.config_snapshot.model_copy(deep=True)
                if pipeline_resume and previous.config_snapshot is not None
                else store.load_pipeline_config(project_id).model_copy(deep=True)
            )
            provider_profiles = (
                {
                    profile_id: profile.model_copy(deep=True)
                    for profile_id, profile in previous.provider_profiles_snapshot.items()
                }
                if pipeline_resume
                else {}
            )
            # Resolve a profile exactly once for a new run. Resumes use the
            # profile copy persisted with the original run.
            profile_id = snapshot.asr.provider_profile_id
            if profile_id and not provider_profiles:
                profile = store.provider_profile(profile_id)
                if profile is None:
                    raise ValueError(f"provider profile not found: {profile_id}")
                if not profile.enabled:
                    raise ValueError(f"provider profile disabled: {profile_id}")
                provider_profiles = {profile.id: profile.model_copy(deep=True)}
            job.status = JobStatus.PENDING
            job.cancel_requested = False
            job.error = None
            job.current_stage = None
            job.config_snapshot = snapshot
            job.provider_profiles_snapshot = provider_profiles
            store.save_job(job)
            ev = asyncio.Event()
            self.cancel_events[key] = ev

            async def work():
                current_task = asyncio.current_task()
                try:
                    async with self.semaphore:
                        if ev.is_set():
                            # A queued task can be cancelled before it gets a
                            # semaphore slot; it never enters a pipeline
                            # stage and is reported directly as cancelled.
                            job.status = JobStatus.CANCELLED
                            job.cancel_requested = True
                            job.updated_at = datetime.now(timezone.utc)
                            store.save_job(job)
                            await self.event_bus.publish(
                                {
                                    "schema_version": "1.0",
                                    "event_id": f"evt_{uuid4().hex}",
                                    "type": "job.cancelled",
                                    "project_id": project_id,
                                    "audio_id": audio_id,
                                    "job_id": job.id,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "data": {},
                                }
                            )
                            logger.info(
                                "job cancelled before start project=%s job=%s", project_id, job.id
                            )
                            return
                        preset = (
                            load_preset(self.preset_dir, project.preset)
                            if self.preset_dir
                            else None
                        )
                        # Defaults -> preset -> explicit app.yaml -> env.  A
                        # direct test/runtime assignment to delay_ms is also
                        # treated as explicit for backwards compatibility.
                        delay_ms = (
                            preset.mock.get("delay_ms", self._initial_delay_ms)
                            if preset
                            else self._initial_delay_ms
                        )
                        if self.delay_explicit or self.delay_ms != self._initial_delay_ms:
                            delay_ms = self.delay_ms
                        preprocess_config = snapshot.preprocess
                        if preprocess_config.provider == "ffmpeg":
                            preprocess_provider = FFmpegPreprocessProvider(
                                self.ffmpeg_path, runner=self.preprocess_runner
                            )
                        elif preprocess_config.provider == "mock":
                            preprocess_provider = MockPreprocessProvider()
                        else:
                            raise ValueError(
                                f"unsupported preprocess provider: {preprocess_config.provider}"
                            )
                        if snapshot.asr.provider == "mock":
                            asr_provider = MockASRProvider(delay_ms)
                        elif snapshot.asr.provider == "crispasr":
                            asr_provider = CrispASRProvider(
                                self.crispasr_executable,
                                self.crispasr_model_dir,
                                snapshot.asr,
                                timeout_seconds=self.crispasr_timeout_seconds,
                                cancel_event=ev,
                                process_factory=self.crispasr_process_factory,
                            )
                        else:
                            raise ValueError(f"unsupported asr provider: {snapshot.asr.provider}")
                        if snapshot.translation.provider == "mock":
                            translation_provider = MockTranslatorProvider(delay_ms)
                        elif snapshot.translation.provider in {
                            "openai_chat",
                            "openai_responses",
                            "anthropic_messages",
                        }:
                            translation_provider = HTTPTranslationProvider(
                                snapshot.translation, cancel_event=ev
                            )
                        else:
                            raise ValueError(
                                f"unsupported translation provider: {snapshot.translation.provider}"
                            )
                        stages = [
                            PreprocessStage(preprocess_provider),
                            ASRStage(asr_provider),
                            AlignmentStage(MockAlignerProvider(delay_ms)),
                            TranslationStage(translation_provider),
                            ExportStage(),
                        ]
                        c = PipelineContext(
                            project=project,
                            project_dir=store.project_dir(project_id),
                            job=job,
                            preset=preset.model_dump(mode="json") if preset else {"name": "mock"},
                            store=store,
                            cancel_event=ev,
                            allow_transcript_overwrite=not pipeline_resume,
                            config=snapshot,
                        )
                        logger.info("job started project=%s job=%s", project_id, job.id)
                        await PipelineRunner(stages, self.event_bus).run(c, resume=pipeline_resume)
                        logger.info(
                            "job finished project=%s job=%s status=%s",
                            project_id,
                            job.id,
                            job.status.value,
                        )
                except asyncio.CancelledError:
                    # The public cancel API uses cooperative cancellation and
                    # never calls Task.cancel().  If shutdown does cancel the
                    # task, still leave a recoverable state on disk.
                    if job.status in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLING}:
                        job.status = JobStatus.INTERRUPTED
                        job.current_stage = None
                        job.updated_at = datetime.now(timezone.utc)
                        store.save_job(job)
                    raise
                except Exception as exc:
                    # Runner handles stage failures.  This guard covers
                    # storage/configuration failures outside a stage so a job
                    # never remains permanently stuck in running state.
                    job.status = JobStatus.FAILED
                    job.current_stage = None
                    error_type, error_message = stable_job_error(exc)
                    job.error = JobError(type=error_type, message=error_message, retryable=True)
                    job.updated_at = datetime.now(timezone.utc)
                    store.save_job(job)
                    append_log = getattr(store, "append_log", None)
                    if callable(append_log):
                        try:
                            append_log(project_id, f"job failed job={job.id} code={error_type}")
                        except Exception:
                            logger.warning("unable to append project pipeline log", exc_info=True)
                    await self.event_bus.publish(
                        {
                            "schema_version": "1.0",
                            "event_id": f"evt_{uuid4().hex}",
                            "type": "job.failed",
                            "project_id": project_id,
                            "audio_id": audio_id,
                            "job_id": job.id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "data": {"message": error_message, "code": error_type},
                        }
                    )
                    logger.exception("job failed project=%s job=%s", project_id, job.id)
                finally:
                    # A completed task must never remove a newer task that
                    # has already been registered for the same project.
                    if self.tasks.get(key) is current_task:
                        self.tasks.pop(key, None)
                    if self.cancel_events.get(key) is ev:
                        self.cancel_events.pop(key, None)

            self.tasks[key] = asyncio.create_task(work())
            logger.info("job queued project=%s job=%s resume=%s", project_id, job.id, resume)
            return job

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

    def cancel(self, project_id, audio_id=PRIMARY_AUDIO_ID):
        if not self.running(project_id, audio_id):
            return False
        event = self.cancel_events.get((project_id, audio_id))
        if event is None:
            return False
        event.set()
        store = self.store.scope(project_id, audio_id)
        job = store.load_job(project_id)
        if job.status not in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLING}:
            return False
        job.cancel_requested = True
        job.status = JobStatus.CANCELLING
        store.save_job(job)
        logger.info("job cancellation requested project=%s job=%s", project_id, job.id)
        return True

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
