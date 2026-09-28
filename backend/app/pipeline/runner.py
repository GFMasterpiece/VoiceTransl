from datetime import datetime, timezone
from uuid import uuid4
from ..domain import JobError, JobStatus, StageStatus
from .context import PipelineCancelled

def now(): return datetime.now(timezone.utc)

class PipelineRunner:
    def __init__(self, stages, event_bus): self.stages=stages; self.event_bus=event_bus
    async def _save(self,c): c.job.updated_at=now(); c.store.save_job(c.job)
    async def _event(self,c,kind,data=None): await self.event_bus.publish({"schema_version":"1.0","event_id":f"evt_{uuid4().hex}","type":kind,"project_id":c.project.id,"job_id":c.job.id,"timestamp":now().isoformat(),"data":data or {}})
    async def run(self,c,resume=True):
        job=c.job; job.status=JobStatus.RUNNING; job.error=None; await self._save(c); await self._event(c,"job.started")
        try:
            for stage in self.stages:
                c.check_cancelled(); state=job.stages[stage.name]
                if resume and state.status==StageStatus.COMPLETED:
                    # A state file can be durable while its artifact was
                    # deleted manually or by a partial restore.  Re-run that
                    # stage instead of allowing recovery to fail outside the
                    # normal stage error path.
                    try:
                        n={"asr":"raw","alignment":"aligned","translation":"translated"}.get(stage.name.value)
                        if n:
                            c.transcript=c.store.load_transcript(c.project.id,n)
                        elif state.artifact:
                            artifact=(c.project_dir / state.artifact).resolve()
                            if c.project_dir.resolve() not in artifact.parents:
                                raise ValueError("stage artifact escapes project directory")
                            if not artifact.exists(): raise FileNotFoundError(state.artifact)
                    except (FileNotFoundError, ValueError):
                        state.status=StageStatus.PENDING
                        state.progress=0
                        state.artifact=None
                    else:
                        job.progress=sum(s.progress for s in job.stages.values())/len(job.stages)
                        await self._save(c)
                        continue
                state.status=StageStatus.RUNNING; state.started_at=now(); state.finished_at=None; state.error=None; state.progress=0; job.current_stage=stage.name; await self._save(c); await self._event(c,"stage.started",{"stage":stage.name.value}); await self._event(c,"stage.progress",{"stage":stage.name.value,"progress":0,"message":f"{stage.name.value} processing"})
                try: await stage.run(c); c.check_cancelled()
                except PipelineCancelled: raise
                except Exception as e:
                    state.status=StageStatus.FAILED; state.error=str(e); state.finished_at=now(); job.status=JobStatus.FAILED; job.current_stage=stage.name; job.error=JobError(type=type(e).__name__,message=str(e),stage=stage.name,retryable=True); await self._save(c); await self._event(c,"job.failed",{"stage":stage.name.value,"message":str(e)}); return
                state.status=StageStatus.COMPLETED; state.progress=1; state.finished_at=now(); artifact=c.artifacts.get(stage.name.value); state.artifact=artifact.relative_to(c.project_dir).as_posix() if artifact else None; job.progress=sum(s.progress for s in job.stages.values())/len(job.stages); await self._save(c); await self._event(c,"stage.progress",{"stage":stage.name.value,"progress":1,"message":f"{stage.name.value} complete"}); await self._event(c,"stage.completed",{"stage":stage.name.value})
            job.status=JobStatus.COMPLETED; job.current_stage=None; job.progress=1; await self._save(c); await self._event(c,"job.completed")
        except PipelineCancelled:
            if job.current_stage is not None:
                state = job.stages[job.current_stage]
                if state.status == StageStatus.RUNNING:
                    state.status = StageStatus.PENDING
                    state.started_at = None
                    state.progress = 0
            job.status=JobStatus.CANCELLED; job.cancel_requested=True; job.current_stage=None; await self._save(c); await self._event(c,"job.cancelled")
