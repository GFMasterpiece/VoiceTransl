import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
from ..domain import Project, Job, Transcript, JobStatus
from .atomic import atomic_write_json

class ProjectNotFound(FileNotFoundError): pass

class FileSystemProjectStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve(); self.projects_dir = self.data_dir / "projects"; self.projects_dir.mkdir(parents=True, exist_ok=True)
    def project_dir(self, project_id: str) -> Path:
        if not project_id or project_id in (".", "..") or "/" in project_id or "\\" in project_id: raise ValueError("invalid project id")
        path = (self.projects_dir / project_id).resolve()
        if self.projects_dir not in path.parents: raise ValueError("invalid project path")
        return path
    def create_project(self, project: Project) -> Path:
        d = self.project_dir(project.id); d.mkdir(parents=True, exist_ok=False)
        for name in ("source", "cache", "transcript", "output", "logs"): (d / name).mkdir()
        atomic_write_json(d / "project.json", project.model_dump(mode="json")); return d

    def remove_project(self, project_id: str) -> None:
        """Remove an incomplete project directory during failed creation.

        This is only used before a project has been returned to an API client.
        Runtime projects are never silently removed by the service.
        """
        directory = self.project_dir(project_id)
        if directory.exists():
            shutil.rmtree(directory)
    def list_projects(self) -> list[Project]:
        items=[]
        for p in self.projects_dir.iterdir():
            if p.is_dir() and (p / "project.json").exists():
                try: items.append(Project.model_validate_json((p / "project.json").read_text(encoding="utf-8")))
                except Exception: continue
        return sorted(items, key=lambda x: x.updated_at, reverse=True)[:200]
    def load_project(self, project_id: str) -> Project:
        path=self.project_dir(project_id)/"project.json"
        if not path.exists(): raise ProjectNotFound(project_id)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))
    def save_project(self, project: Project) -> None: atomic_write_json(self.project_dir(project.id)/"project.json", project.model_dump(mode="json"))
    def load_job(self, project_id: str) -> Job:
        path=self.project_dir(project_id)/"job.json"
        if not path.exists(): raise ProjectNotFound(project_id)
        return Job.model_validate_json(path.read_text(encoding="utf-8"))
    def save_job(self, job: Job) -> None: atomic_write_json(self.project_dir(job.project_id)/"job.json", job.model_dump(mode="json"))
    def load_transcript(self, project_id: str, stage: str) -> Transcript:
        if stage not in {"raw","aligned","translated"}: raise ValueError("invalid transcript stage")
        path=self.project_dir(project_id)/"transcript"/f"{stage}.json"
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    def save_transcript(self, project_id: str, name: str, value: Transcript) -> Path:
        if name not in {"raw","aligned","translated"}: raise ValueError("invalid transcript stage")
        path=self.project_dir(project_id)/"transcript"/f"{name}.json"; atomic_write_json(path, value.model_dump(mode="json")); return path
    def list_outputs(self, project_id: str) -> list[Path]:
        d=self.project_dir(project_id)/"output"; return sorted([p for p in d.iterdir() if p.is_file()]) if d.exists() else []
    def resolve_output(self, project_id: str, filename: str) -> Path:
        if not filename or "/" in filename or "\\" in filename or ".." in filename: raise ValueError("invalid output filename")
        d=(self.project_dir(project_id)/"output").resolve(); path=(d/filename).resolve()
        if path.parent != d: raise ValueError("invalid output filename")
        return path
    def mark_interrupted(self) -> None:
        for p in self.projects_dir.iterdir() if self.projects_dir.exists() else []:
            path=p/"job.json"
            if not path.exists(): continue
            try:
                job=Job.model_validate_json(path.read_text(encoding="utf-8"))
                if job.status.value in ("running","cancelling"):
                    job.status=JobStatus.INTERRUPTED; job.updated_at=datetime.now(timezone.utc); self.save_job(job)
            except Exception: continue
