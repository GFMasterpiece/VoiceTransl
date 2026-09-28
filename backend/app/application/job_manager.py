import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from ..domain import Job, JobError, JobStatus
from ..pipeline import PipelineContext, PipelineRunner, PreprocessStage, ASRStage, AlignmentStage, TranslationStage, ExportStage
from ..providers import MockASRProvider, MockAlignerProvider, MockTranslatorProvider

class JobManager:
    def __init__(self,store,event_bus,delay_ms=500,max_concurrent=1):
        self.store=store
        self.event_bus=event_bus
        self.delay_ms=delay_ms
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
                            return
                        stages=[PreprocessStage(),ASRStage(MockASRProvider(self.delay_ms)),AlignmentStage(MockAlignerProvider(self.delay_ms)),TranslationStage(MockTranslatorProvider(self.delay_ms)),ExportStage()]
                        c=PipelineContext(project=project,project_dir=self.store.project_dir(project_id),job=job,preset={"name":"mock"},store=self.store,cancel_event=ev)
                        await PipelineRunner(stages,self.event_bus).run(c,resume=resume)
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
                    job.error=JobError(type=type(exc).__name__,message=str(exc),retryable=True)
                    job.updated_at=datetime.now(timezone.utc)
                    self.store.save_job(job)
                    await self.event_bus.publish({
                        "schema_version":"1.0",
                        "event_id":f"evt_{uuid4().hex}",
                        "type":"job.failed",
                        "project_id":project_id,
                        "job_id":job.id,
                        "timestamp":datetime.now(timezone.utc).isoformat(),
                        "data":{"message":str(exc)},
                    })
                finally:
                    # A completed task must never remove a newer task that
                    # has already been registered for the same project.
                    if self.tasks.get(project_id) is current_task:
                        self.tasks.pop(project_id,None)
                    if self.cancel_events.get(project_id) is ev:
                        self.cancel_events.pop(project_id,None)
            self.tasks[project_id]=asyncio.create_task(work())
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
