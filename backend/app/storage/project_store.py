import shutil
import os
from uuid import uuid4
from pathlib import Path
from datetime import datetime, timezone
from ..domain import Project, Job, Transcript, JobStatus
from .atomic import atomic_write_bytes, atomic_write_json, atomic_write_text

class ProjectNotFound(FileNotFoundError): pass
class TranscriptProtectedError(ValueError): pass
class SourceUploadTooLarge(ValueError): pass

class FileSystemProjectStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.projects_dir = self.data_dir / "projects"
        if self.projects_dir.exists() and self.projects_dir.is_symlink():
            raise ValueError("projects directory must not be a symlink")
        self.projects_dir.mkdir(parents=True, exist_ok=True)
    def project_dir(self, project_id: str) -> Path:
        if not project_id or project_id in (".", "..") or "/" in project_id or "\\" in project_id: raise ValueError("invalid project id")
        root = self.projects_dir.resolve()
        path = (root / project_id).resolve()
        if root not in path.parents: raise ValueError("invalid project path")
        candidate = root / project_id
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("project path must not be a symlink")
        return path
    def create_project(self, project: Project) -> Path:
        d = self.project_dir(project.id); d.mkdir(parents=True, exist_ok=False)
        for name in ("source", "cache", "transcript", "output", "logs"): (d / name).mkdir()
        atomic_write_json(d / "project.json", project.model_dump(mode="json")); return d

    def resolve_project_path(self, project_id: str, relative_path: str) -> Path:
        if not relative_path or "\\" in relative_path:
            raise ValueError("project path must be relative POSIX")
        root = self.project_dir(project_id).resolve()
        candidate = (root / relative_path).resolve()
        if root not in candidate.parents:
            raise ValueError("project path escapes project directory")
        return candidate

    def source_path(self, project_id: str, relative_path: str) -> Path:
        path = self.resolve_project_path(project_id, relative_path)
        source_root = self._source_dir(project_id).resolve()
        if source_root not in path.parents or not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    def _source_dir(self, project_id: str) -> Path:
        return self._project_subdir(project_id, "source")

    def _project_subdir(self, project_id: str, name: str) -> Path:
        project_dir = self.project_dir(project_id).resolve()
        child = project_dir / name
        if child.exists() and child.is_symlink():
            raise ValueError(f"{name} directory must not be a symlink")
        child.mkdir(parents=True, exist_ok=True)
        resolved = child.resolve()
        if project_dir not in resolved.parents or child.is_symlink():
            raise ValueError(f"{name} directory escapes project directory")
        return child

    async def create_project_from_upload(self, project: Project, job: Job, upload, max_bytes: int) -> tuple[Project, Job]:
        """Publish a complete project with one atomic rename."""
        final_dir = self.project_dir(project.id)
        if final_dir.exists():
            raise FileExistsError(project.id)
        temp_id = f".{project.id}.tmp_{uuid4().hex}"
        temp_dir = self.project_dir(temp_id)
        total = 0
        try:
            temp_dir.mkdir(parents=True, exist_ok=False)
            for name in ("source", "cache", "transcript", "output", "logs"):
                (temp_dir / name).mkdir()
            source_path = temp_dir / "source" / Path(project.source.path).name
            temp_source = source_path.with_name(f".{source_path.name}.{uuid4().hex}.tmp")
            try:
                with temp_source.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise SourceUploadTooLarge
                        stream.write(chunk)
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temp_source, source_path)
            finally:
                temp_source.unlink(missing_ok=True)
            project.source.path = source_path.relative_to(temp_dir).as_posix()
            project.source.size_bytes = total
            atomic_write_json(temp_dir / "project.json", project.model_dump(mode="json"))
            atomic_write_json(temp_dir / "job.json", job.model_dump(mode="json"))
            os.replace(temp_dir, final_dir)
            return project, job
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

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
            if p.is_symlink():
                continue
            if p.is_dir() and (p / "project.json").is_file():
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
    def _touch_project(self, project_id: str) -> None:
        project = self.load_project(project_id)
        project.updated_at = datetime.now(timezone.utc)
        atomic_write_json(self.project_dir(project_id) / "project.json", project.model_dump(mode="json"))

    def save_job(self, job: Job) -> None:
        atomic_write_json(self.project_dir(job.project_id)/"job.json", job.model_dump(mode="json"))
        self._touch_project(job.project_id)
    def load_transcript(self, project_id: str, stage: str) -> Transcript:
        if stage not in {"raw","aligned","translated"}: raise ValueError("invalid transcript stage")
        path=self._project_subdir(project_id, "transcript")/f"{stage}.json"
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    def save_transcript(self, project_id: str, name: str, value: Transcript, *, overwrite: bool = False) -> Path:
        if name not in {"raw","aligned","translated"}: raise ValueError("invalid transcript stage")
        path=self._project_subdir(project_id, "transcript")/f"{name}.json"
        if path.exists() and not overwrite:
            try:
                existing = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                raise TranscriptProtectedError(f"transcript/{name}.json is protected")
            if existing.model_dump(exclude={"created_at"}) != value.model_dump(exclude={"created_at"}):
                raise TranscriptProtectedError(f"transcript/{name}.json was edited and is protected")
            return path
        atomic_write_json(path, value.model_dump(mode="json")); self._touch_project(project_id); return path

    def save_source(self, project_id: str, extension: str, payload: bytes) -> Path:
        extension = extension.lower().lstrip(".")
        if not extension or "/" in extension or "\\" in extension or ".." in extension:
            raise ValueError("invalid source extension")
        path = self._source_dir(project_id) / f"original.{extension}"
        atomic_write_bytes(path, payload); self._touch_project(project_id); return path

    async def save_source_upload(self, project_id: str, extension: str, upload, max_bytes: int) -> tuple[Path, int]:
        extension = extension.lower().lstrip(".")
        if not extension or "/" in extension or "\\" in extension or ".." in extension:
            raise ValueError("invalid source extension")
        source_dir = self._source_dir(project_id)
        target = source_dir / f"original.{extension}"
        temp = source_dir / f".{target.name}.{uuid4().hex}.tmp"
        total = 0
        try:
            with temp.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise SourceUploadTooLarge
                    stream.write(chunk)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, target)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        self._touch_project(project_id)
        return target, total

    def save_cache_json(self, project_id: str, name: str, payload: dict) -> Path:
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("invalid cache name")
        path = self._project_subdir(project_id, "cache") / name
        atomic_write_json(path, payload); self._touch_project(project_id); return path

    def save_output_text(self, project_id: str, name: str, payload: str) -> Path:
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("invalid output name")
        path = self._project_subdir(project_id, "output") / name
        atomic_write_text(path, payload); self._touch_project(project_id); return path

    def append_log(self, project_id: str, message: str) -> Path:
        path = self._project_subdir(project_id, "logs") / "pipeline.log"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(message.rstrip("\n") + "\n")
        self._touch_project(project_id); return path
    def list_outputs(self, project_id: str) -> list[Path]:
        d=self._project_subdir(project_id, "output"); return sorted([p for p in d.iterdir() if p.is_file() and not p.is_symlink()]) if d.exists() else []
    def resolve_output(self, project_id: str, filename: str) -> Path:
        if not filename or "/" in filename or "\\" in filename or ".." in filename: raise ValueError("invalid output filename")
        d=self._project_subdir(project_id, "output").resolve(); path=(d/filename).resolve()
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
