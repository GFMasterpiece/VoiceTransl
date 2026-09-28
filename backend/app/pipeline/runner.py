from datetime import datetime, timezone
from uuid import uuid4
from ..domain import JobError, JobStatus, StageStatus
from .context import PipelineCancelled
from ..logging_utils import redact_secrets, stable_stage_error
import logging

logger = logging.getLogger("voicetransl.pipeline")

def now(): return datetime.now(timezone.utc)

class PipelineRunner:
    def __init__(self, stages, event_bus): self.stages=stages; self.event_bus=event_bus
    async def _save(self,c): c.job.updated_at=now(); c.store.save_job(c.job)
    async def _event(self,c,kind,data=None): await self.event_bus.publish({"schema_version":"1.0","event_id":f"evt_{uuid4().hex}","type":kind,"project_id":c.project.id,"job_id":c.job.id,"timestamp":now().isoformat(),"data":data or {}})
    @staticmethod
    def _audit(c, message: str) -> None:
        append_log = getattr(c.store, "append_log", None)
        if callable(append_log):
            try:
                append_log(c.project.id, redact_secrets(message))
            except Exception:
                # A diagnostic log must never alter pipeline state.
                logger.warning("unable to append project pipeline log", exc_info=True)
    async def run(self,c,resume=True):
        job=c.job; job.status=JobStatus.RUNNING; job.error=None; await self._save(c); await self._event(c,"job.started")
        logger.info("job started project=%s job=%s resume=%s", c.project.id, job.id, resume)
        self._audit(c, f"job started job={job.id} resume={resume}")
        try:
            reset_from = None
            if resume:
                # A completed stage can outlive its artifact (manual cleanup,
                # partial restore, or a crash between writes).  Its dependants
                # are no longer trustworthy and must be rerun as well.
                for index, stage in enumerate(self.stages):
                    state = job.stages[stage.name]
                    if state.status != StageStatus.COMPLETED:
                        continue
                    try:
                        self._restore_stage_artifact(stage.name, c, state)
                    except (FileNotFoundError, ValueError):
                        reset_from = index
                        break
            if reset_from is not None:
                for stage in self.stages[reset_from:]:
                    state = job.stages[stage.name]
                    state.status = StageStatus.PENDING
                    state.progress = 0
                    state.started_at = None
                    state.finished_at = None
                    state.artifact = None
                    state.error = None
                job.progress = sum(s.progress for s in job.stages.values()) / len(job.stages)
                await self._save(c)

            for index, stage in enumerate(self.stages):
                c.check_cancelled(); state=job.stages[stage.name]
                if resume and state.status==StageStatus.COMPLETED:
                    logger.info("stage restored project=%s job=%s stage=%s", c.project.id, job.id, stage.name.value)
                    self._audit(c, f"stage restored job={job.id} stage={stage.name.value}")
                    self._restore_stage_artifact(stage.name, c, state)
                    job.progress=sum(s.progress for s in job.stages.values())/len(job.stages)
                    await self._save(c)
                    continue
                state.status=StageStatus.RUNNING; state.started_at=now(); state.finished_at=None; state.error=None; state.progress=0; job.current_stage=stage.name; await self._save(c); await self._event(c,"stage.started",{"stage":stage.name.value}); await self._event(c,"stage.progress",{"stage":stage.name.value,"progress":0,"message":f"{stage.name.value} processing"}); logger.info("stage started project=%s job=%s stage=%s", c.project.id, job.id, stage.name.value)
                self._audit(c, f"stage started job={job.id} stage={stage.name.value}")
                try: await stage.run(c); c.check_cancelled()
                except PipelineCancelled: raise
                except Exception as e:
                    error_type, error_message = stable_stage_error(e)
                    state.status=StageStatus.FAILED; state.error=error_type; state.finished_at=now(); job.status=JobStatus.FAILED; job.current_stage=stage.name; job.error=JobError(type=error_type,message=error_message,stage=stage.name,retryable=True); await self._save(c); await self._event(c,"job.failed",{"stage":stage.name.value,"message":error_message,"code":error_type}); logger.exception("stage failed project=%s job=%s stage=%s", c.project.id, job.id, stage.name.value); self._audit(c, f"stage failed job={job.id} stage={stage.name.value} code={error_type}"); return
                state.status=StageStatus.COMPLETED; state.progress=1; state.finished_at=now(); artifact=c.artifacts.get(stage.name.value); state.artifact=artifact.relative_to(c.project_dir).as_posix() if artifact else None; job.progress=sum(s.progress for s in job.stages.values())/len(job.stages); await self._save(c); await self._event(c,"stage.progress",{"stage":stage.name.value,"progress":1,"message":f"{stage.name.value} complete"}); await self._event(c,"stage.completed",{"stage":stage.name.value}); logger.info("stage completed project=%s job=%s stage=%s", c.project.id, job.id, stage.name.value)
                self._audit(c, f"stage completed job={job.id} stage={stage.name.value}")
            job.status=JobStatus.COMPLETED; job.current_stage=None; job.progress=1; await self._save(c); await self._event(c,"job.completed"); logger.info("job completed project=%s job=%s", c.project.id, job.id); self._audit(c, f"job completed job={job.id}")
        except PipelineCancelled:
            if job.current_stage is not None:
                state = job.stages[job.current_stage]
                if state.status == StageStatus.RUNNING:
                    state.status = StageStatus.PENDING
                    state.started_at = None
                    state.progress = 0
            job.status=JobStatus.CANCELLED; job.cancel_requested=True; job.current_stage=None; await self._save(c); await self._event(c,"job.cancelled"); logger.info("job cancelled project=%s job=%s", c.project.id, job.id); self._audit(c, f"job cancelled job={job.id}")

    @staticmethod
    def _restore_stage_artifact(name, context, state):
        stage_name = name.value
        transcript_name = {"asr": "raw", "alignment": "aligned", "translation": "translated"}.get(stage_name)
        if transcript_name:
            context.transcript = context.store.load_transcript(context.project.id, transcript_name)
            return
        if not state.artifact:
            raise FileNotFoundError(f"missing artifact for {stage_name}")
        artifact = (context.project_dir / state.artifact).resolve()
        project_dir = context.project_dir.resolve()
        if project_dir not in artifact.parents:
            raise ValueError("stage artifact escapes project directory")
        if not artifact.exists():
            raise FileNotFoundError(state.artifact)
        if stage_name == "export":
            required = {"zh.srt", "ja-zh.srt", "zh.lrc"}
            if not artifact.is_dir() or not all((artifact / filename).is_file() for filename in required):
                raise FileNotFoundError("export output is incomplete")
