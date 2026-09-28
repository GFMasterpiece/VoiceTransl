import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from ..domain import Job, JobError, JobStatus
from ..pipeline import PipelineContext, PipelineRunner, PreprocessStage, ASRStage, AlignmentStage, TranslationStage, ExportStage
from ..providers import MockASRProvider, MockAlignerProvider, MockTranslatorProvider
from ..config import load_preset
from ..logging_utils import stable_job_error
import logging

logger = logging.getLogger("voicetransl.jobs")

class JobManager:
    def __init__(self,store,event_bus,delay_ms=500,max_concurrent=1,preset_dir=None,delay_explicit=False):
        self.store=store
        self.event_bus=event_bus
        self.delay_ms=delay_ms
        self._initial_delay_ms = delay_ms
        self.delay_explicit = delay_explicit
        self.preset_dir = preset_dir
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.semaphore=asyncio.Semaphore(max_concurrent)
    def job(self,project_id): return self.store.load_job(project_id)
    def running(self,project_id):
        t=self.tasks.get(project_id); return t is not None and not t.done()
    def _lock(self, project_id: str) -> asyncio.Lock:
        return self.locks.setdefault(project_id, asyncio.Lock())

    async def start(self,project_id,resume=True):
        # The lock covers the running check and task registration.  Without it,
        # two simultaneous HTTP requests could both pass the check.
        async with self._lock(project_id):
            if self.running(project_id):
                raise RuntimeError("JOB_ALREADY_RUNNING")
            project=self.store.load_project(project_id)
            previous=self.store.load_job(project_id)
            # The in-memory task map is intentionally not the source of truth:
            # a task can disappear after a process restart or an unexpected
            # scheduler failure while job.json still says it is running.
            if previous.status in {JobStatus.RUNNING, JobStatus.CANCELLING}:
                raise RuntimeError("JOB_ALREADY_RUNNING")
            if previous.status == JobStatus.COMPLETED and resume:
                raise RuntimeError("JOB_ALREADY_RUNNING")

            if resume and previous.status in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
                job = previous
            elif resume and previous.status == JobStatus.PENDING:
                job = previous
            else:
                # Explicit restart gets a new job identity while keeping the
                # project's current job pointer in job.json.
                job = Job(id=f"job_{uuid4().hex}", project_id=project_id, stages={})
                from ..domain import default_stages
                job.stages = default_stages()
            job.status=JobStatus.PENDING
            job.cancel_requested=False
            job.error=None
            job.current_stage=None
            self.store.save_job(job)
            ev=asyncio.Event()
            self.cancel_events[project_id]=ev

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
                            self.store.save_job(job)
                            await self.event_bus.publish({
                                "schema_version": "1.0",
                                "event_id": f"evt_{uuid4().hex}",
                                "type": "job.cancelled",
                                "project_id": project_id,
                                "job_id": job.id,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "data": {},
                            })
                            logger.info("job cancelled before start project=%s job=%s", project_id, job.id)
                            return
                        preset = load_preset(self.preset_dir, project.preset) if self.preset_dir else None
                        # Defaults -> preset -> explicit app.yaml -> env.  A
                        # direct test/runtime assignment to delay_ms is also
                        # treated as explicit for backwards compatibility.
                        delay_ms = preset.mock.get("delay_ms", self._initial_delay_ms) if preset else self._initial_delay_ms
                        if self.delay_explicit or self.delay_ms != self._initial_delay_ms:
                            delay_ms = self.delay_ms
                        stages=[PreprocessStage(),ASRStage(MockASRProvider(delay_ms)),AlignmentStage(MockAlignerProvider(delay_ms)),TranslationStage(MockTranslatorProvider(delay_ms)),ExportStage()]
                        c=PipelineContext(project=project,project_dir=self.store.project_dir(project_id),job=job,preset=preset.model_dump(mode="json") if preset else {"name":"mock"},store=self.store,cancel_event=ev,allow_transcript_overwrite=not resume)
                        logger.info("job started project=%s job=%s", project_id, job.id)
                        await PipelineRunner(stages,self.event_bus).run(c,resume=resume)
                        logger.info("job finished project=%s job=%s status=%s", project_id, job.id, job.status.value)
                except asyncio.CancelledError:
                    # The public cancel API uses cooperative cancellation and
                    # never calls Task.cancel().  If shutdown does cancel the
                    # task, still leave a recoverable state on disk.
                    if job.status in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLING}:
                        job.status=JobStatus.INTERRUPTED
                        job.current_stage=None
                        job.updated_at=datetime.now(timezone.utc)
                        self.store.save_job(job)
                    raise
                except Exception as exc:
                    # Runner handles stage failures.  This guard covers
                    # storage/configuration failures outside a stage so a job
                    # never remains permanently stuck in running state.
                    job.status=JobStatus.FAILED
                    job.current_stage=None
                    error_type, error_message = stable_job_error(exc)
                    job.error=JobError(type=error_type,message=error_message,retryable=True)
                    job.updated_at=datetime.now(timezone.utc)
                    self.store.save_job(job)
                    append_log = getattr(self.store, "append_log", None)
                    if callable(append_log):
                        try:
                            append_log(project_id, f"job failed job={job.id} code={error_type}")
                        except Exception:
                            logger.warning("unable to append project pipeline log", exc_info=True)
                    await self.event_bus.publish({
                        "schema_version":"1.0",
                        "event_id":f"evt_{uuid4().hex}",
                        "type":"job.failed",
                        "project_id":project_id,
                        "job_id":job.id,
                        "timestamp":datetime.now(timezone.utc).isoformat(),
                        "data":{"message":error_message,"code":error_type},
                    })
                    logger.exception("job failed project=%s job=%s", project_id, job.id)
                finally:
                    # A completed task must never remove a newer task that
                    # has already been registered for the same project.
                    if self.tasks.get(project_id) is current_task:
                        self.tasks.pop(project_id,None)
                    if self.cancel_events.get(project_id) is ev:
                        self.cancel_events.pop(project_id,None)
            self.tasks[project_id]=asyncio.create_task(work())
            logger.info("job queued project=%s job=%s resume=%s", project_id, job.id, resume)
            return job
    def cancel(self,project_id):
        if not self.running(project_id): return False
        event = self.cancel_events.get(project_id)
        if event is None:
            return False
        event.set()
        job=self.store.load_job(project_id)
        if job.status not in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CANCELLING}:
            return False
        job.cancel_requested=True
        job.status=JobStatus.CANCELLING
        self.store.save_job(job)
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
