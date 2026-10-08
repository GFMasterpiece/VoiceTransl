import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from ..domain import Job, JobStatus, PipelineConfig, Project, Transcript, default_pipeline_config
from ..domain.project import PRIMARY_AUDIO_ID, AudioAsset
from .atomic import atomic_write_bytes, atomic_write_json, atomic_write_text


class ProjectNotFound(FileNotFoundError):
    pass


class TranscriptProtectedError(ValueError):
    pass


class SourceUploadTooLarge(ValueError):
    pass


class FileSystemProjectStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.projects_dir = self.data_dir / "projects"
        if self.projects_dir.exists() and self.projects_dir.is_symlink():
            raise ValueError("projects directory must not be a symlink")
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_lock = RLock()
        self._mutation_locks: dict[str, asyncio.Lock] = {}

    def mutation_lock(self, project_id: str) -> asyncio.Lock:
        return self._mutation_locks.setdefault(project_id, asyncio.Lock())

    def audio(self, project_id: str, audio_id: str) -> AudioAsset:
        for audio in self.load_project(project_id).audios:
            if audio.id == audio_id:
                return audio
        raise ProjectNotFound(audio_id)

    def scope(self, project_id: str, audio_id: str = PRIMARY_AUDIO_ID):
        self.audio(project_id, audio_id)
        if audio_id == PRIMARY_AUDIO_ID:
            return PrimaryAudioProjectStore(self, project_id)
        return AudioProjectStore(self, project_id, audio_id)

    async def append_audio(self, project_id, audio, job, upload, max_bytes):
        # Stage a complete resource before publishing its metadata membership.
        async with self.mutation_lock(project_id):
            self.load_project(project_id)
            root = self._project_subdir(project_id, "audios")
            target = root / audio.id
            temporary = root / f".{audio.id}.tmp_{uuid4().hex}"
            total = 0
            try:
                temporary.mkdir()
                for name in ("source", "cache", "transcript", "output", "logs"):
                    (temporary / name).mkdir()
                source = temporary / "source" / Path(audio.source.path).name
                with source.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise SourceUploadTooLarge
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                audio.source.size_bytes = total
                audio.source.path = f"audios/{audio.id}/source/{source.name}"
                atomic_write_json(temporary / "job.json", job.model_dump(mode="json"))
                if target.exists():
                    raise FileExistsError(audio.id)
                os.replace(temporary, target)
                with self._metadata_lock:
                    project = self.load_project(project_id)
                    project.audios.append(audio)
                    project.updated_at = datetime.now(timezone.utc)
                    self.save_project(project)
                return audio, job
            except (Exception, asyncio.CancelledError):
                shutil.rmtree(temporary, ignore_errors=True)
                # Only unpublished upload artifacts can be removed here.
                if target.exists() and all(
                    existing.id != audio.id for existing in self.load_project(project_id).audios
                ):
                    shutil.rmtree(target)
                raise

    def project_dir(self, project_id: str) -> Path:
        if not project_id or project_id in (".", "..") or "/" in project_id or "\\" in project_id:
            raise ValueError("invalid project id")
        root = self.projects_dir.resolve()
        path = (root / project_id).resolve()
        if root not in path.parents:
            raise ValueError("invalid project path")
        candidate = root / project_id
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("project path must not be a symlink")
        return path

    def create_project(self, project: Project) -> Path:
        d = self.project_dir(project.id)
        d.mkdir(parents=True, exist_ok=False)
        for name in ("source", "cache", "transcript", "output", "logs"):
            (d / name).mkdir()
        atomic_write_json(d / "project.json", project.model_dump(mode="json"))
        return d

    def resolve_project_path(self, project_id: str, relative_path: str) -> Path:
        if not relative_path or "\\" in relative_path:
            raise ValueError("project path must be relative POSIX")
        root = self.project_dir(project_id).resolve()
        candidate = (root / relative_path).resolve()
        if root not in candidate.parents:
            raise ValueError("project path escapes project directory")
        return candidate

    def resolve_artifact_path(self, project_id: str, relative_path: str) -> Path:
        resolved = self.resolve_project_path(project_id, relative_path)
        canonical = resolved.relative_to(self.project_dir(project_id))
        if canonical.parts[0] == "audios":
            raise ValueError("artifact belongs to another audio")
        return resolved

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

    async def create_project_from_upload(
        self, project: Project, job: Job, upload, max_bytes: int
    ) -> tuple[Project, Job]:
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
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_source, source_path)
            finally:
                temp_source.unlink(missing_ok=True)
            project.source.path = source_path.relative_to(temp_dir).as_posix()
            project.source.size_bytes = total
            atomic_write_json(temp_dir / "project.json", project.model_dump(mode="json"))
            atomic_write_json(temp_dir / "job.json", job.model_dump(mode="json"))
            if final_dir.exists():
                raise FileExistsError(project.id)
            os.replace(temp_dir, final_dir)
            return project, job
        except (Exception, asyncio.CancelledError):
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

    def delete_project(self, project_id: str) -> None:
        """Delete a published project after validating its managed directory."""
        directory = self.project_dir(project_id)
        if not directory.is_dir() or not (directory / "project.json").is_file():
            raise ProjectNotFound(project_id)
        if (directory / "project.json").is_symlink():
            raise ValueError("project metadata must not be a symlink")
        shutil.rmtree(directory)

    def list_projects(self) -> list[Project]:
        items = []
        for _, project in self._published_projects():
            items.append(project)
        return sorted(items, key=lambda x: x.updated_at, reverse=True)[:200]

    def _published_projects(self):
        """Yield only complete, named project directories after a restart."""
        for p in self.projects_dir.iterdir():
            if p.name.startswith(".") or p.is_symlink() or not p.is_dir():
                continue
            metadata = p / "project.json"
            if metadata.is_symlink() or not metadata.is_file():
                continue
            try:
                project = Project.model_validate_json(metadata.read_text(encoding="utf-8"))
                if project.id == p.name:
                    yield p, project
            except Exception:
                continue

    def load_project(self, project_id: str) -> Project:
        path = self.project_dir(project_id) / "project.json"
        if path.is_symlink() or not path.is_file():
            raise ProjectNotFound(project_id)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save_project(self, project: Project) -> None:
        atomic_write_json(
            self.project_dir(project.id) / "project.json", project.model_dump(mode="json")
        )

    def load_job(self, project_id: str) -> Job:
        path = self.project_dir(project_id) / "job.json"
        if path.is_symlink() or not path.is_file():
            raise ProjectNotFound(project_id)
        job = Job.model_validate_json(path.read_text(encoding="utf-8"))
        if job.project_id != project_id or job.audio_id != PRIMARY_AUDIO_ID:
            raise ValueError("invalid primary job ownership")
        return job

    def _touch_project(self, project_id: str) -> None:
        with self._metadata_lock:
            project = self.load_project(project_id)
            project.updated_at = datetime.now(timezone.utc)
            self.save_project(project)

    def save_job(self, job: Job) -> None:
        if job.audio_id != PRIMARY_AUDIO_ID:
            raise ValueError("additional audio jobs require a scoped store")
        atomic_write_json(
            self.project_dir(job.project_id) / "job.json", job.model_dump(mode="json")
        )
        self._touch_project(job.project_id)

    def load_pipeline_config(self, project_id: str) -> PipelineConfig:
        """Load a project's editable config, providing a legacy-safe default."""

        path = self.project_dir(project_id) / "config.json"
        if path.is_symlink():
            raise ValueError("config must not be a symlink")
        if not path.exists():
            project = self.load_project(project_id)
            return default_pipeline_config(project.source.language)
        return PipelineConfig.model_validate_json(path.read_text(encoding="utf-8"))

    def save_pipeline_config(self, project_id: str, value: PipelineConfig) -> None:
        atomic_write_json(
            self.project_dir(project_id) / "config.json", value.model_dump(mode="json")
        )
        self._touch_project(project_id)

    def load_transcript(self, project_id: str, stage: str) -> Transcript:
        if stage not in {"raw", "aligned", "translated"}:
            raise ValueError("invalid transcript stage")
        path = self._project_subdir(project_id, "transcript") / f"{stage}.json"
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))

    def save_transcript(
        self, project_id: str, name: str, value: Transcript, *, overwrite: bool = False
    ) -> Path:
        if name not in {"raw", "aligned", "translated"}:
            raise ValueError("invalid transcript stage")
        path = self._project_subdir(project_id, "transcript") / f"{name}.json"
        if path.exists() and not overwrite:
            try:
                existing = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                raise TranscriptProtectedError(f"transcript/{name}.json is protected")
            if existing.model_dump(exclude={"created_at"}) != value.model_dump(
                exclude={"created_at"}
            ):
                raise TranscriptProtectedError(
                    f"transcript/{name}.json was edited and is protected"
                )
            return path
        atomic_write_json(path, value.model_dump(mode="json"))
        self._touch_project(project_id)
        return path

    def save_source(self, project_id: str, extension: str, payload: bytes) -> Path:
        extension = extension.lower().lstrip(".")
        if not extension or "/" in extension or "\\" in extension or ".." in extension:
            raise ValueError("invalid source extension")
        path = self._source_dir(project_id) / f"original.{extension}"
        atomic_write_bytes(path, payload)
        self._touch_project(project_id)
        return path

    async def save_source_upload(
        self, project_id: str, extension: str, upload, max_bytes: int
    ) -> tuple[Path, int]:
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
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        except (Exception, asyncio.CancelledError):
            temp.unlink(missing_ok=True)
            raise
        self._touch_project(project_id)
        return target, total

    def save_cache_json(self, project_id: str, name: str, payload: dict) -> Path:
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("invalid cache name")
        path = self._project_subdir(project_id, "cache") / name
        atomic_write_json(path, payload)
        self._touch_project(project_id)
        return path

    def save_output_text(self, project_id: str, name: str, payload: str) -> Path:
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("invalid output name")
        path = self._project_subdir(project_id, "output") / name
        atomic_write_text(path, payload)
        self._touch_project(project_id)
        return path

    def append_log(self, project_id: str, message: str) -> Path:
        path = self._project_subdir(project_id, "logs") / "pipeline.log"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(message.rstrip("\n") + "\n")
        self._touch_project(project_id)
        return path

    def list_outputs(self, project_id: str) -> list[Path]:
        d = self._project_subdir(project_id, "output")
        return (
            sorted([p for p in d.iterdir() if p.is_file() and not p.is_symlink()])
            if d.exists()
            else []
        )

    def prune_generated_outputs(self, project_id: str, keep: set[str]) -> None:
        generated = {"zh.srt", "ja-zh.srt", "zh.vtt", "ja-zh.vtt", "zh.lrc"}
        for name in generated - keep:
            path = self._project_subdir(project_id, "output") / name
            if path.is_file() and not path.is_symlink():
                path.unlink()

    def resolve_output(self, project_id: str, filename: str) -> Path:
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError("invalid output filename")
        d = self._project_subdir(project_id, "output").resolve()
        path = (d / filename).resolve()
        if path.parent != d:
            raise ValueError("invalid output filename")
        return path

    def mark_interrupted(self) -> None:
        for p, project in self._published_projects():
            for audio in project.audios:
                try:
                    scope = self.scope(project.id, audio.id)
                    job = scope.load_job(project.id)
                    if job.project_id != project.id or job.audio_id != audio.id:
                        continue
                    if job.status in {JobStatus.RUNNING, JobStatus.CANCELLING} or (
                        job.status == JobStatus.PENDING and job.config_snapshot is not None
                    ):
                        job.status = JobStatus.INTERRUPTED
                        job.updated_at = datetime.now(timezone.utc)
                        scope.save_job(job)
                except Exception:
                    continue


class PrimaryAudioProjectStore:
    """Keep legacy root artifacts while isolating the primary config override."""

    def __init__(self, parent, project_id):
        self.parent = parent
        self.project_id = project_id

    def __getattr__(self, name):
        return getattr(self.parent, name)

    def _config_path(self, project_id):
        if project_id != self.project_id:
            raise ValueError("wrong audio project")
        return self.parent.project_dir(project_id) / "primary-config.json"

    def load_pipeline_config(self, project_id):
        path = self._config_path(project_id)
        if path.is_symlink():
            raise ValueError("config must not be a symlink")
        if path.exists():
            return PipelineConfig.model_validate_json(path.read_text(encoding="utf-8"))
        return self.parent.load_pipeline_config(project_id)

    def save_pipeline_config(self, project_id, value):
        atomic_write_json(self._config_path(project_id), value.model_dump(mode="json"))
        self.parent._touch_project(project_id)


class AudioProjectStore(FileSystemProjectStore):
    """A pipeline store view retaining parent IDs and project-relative artifacts."""

    def __init__(self, parent, project_id, audio_id):
        self.parent = parent
        self.project_id = project_id
        self.audio_id = audio_id

    def project_dir(self, project_id):
        if project_id != self.project_id:
            raise ValueError("wrong audio project")
        return self.parent.project_dir(project_id)

    def audio_dir(self):
        self.parent.audio(self.project_id, self.audio_id)
        root = self.parent._project_subdir(self.project_id, "audios")
        candidate = root / self.audio_id
        resolved = candidate.resolve()
        if candidate.is_symlink() or root.resolve() not in resolved.parents:
            raise ValueError("invalid audio directory")
        if not candidate.is_dir():
            raise ProjectNotFound(self.audio_id)
        return candidate

    def _project_subdir(self, project_id, name):
        self.project_dir(project_id)
        root = self.audio_dir()
        child = root / name
        if child.is_symlink() or root.resolve() not in child.resolve().parents:
            raise ValueError("invalid audio subdirectory")
        child.mkdir(exist_ok=True)
        return child

    def resolve_project_path(self, project_id, relative_path):
        prefix = f"audios/{self.audio_id}/"
        relative = relative_path if relative_path.startswith(prefix) else prefix + relative_path
        path = self.parent.resolve_project_path(project_id, relative)
        if self.audio_dir().resolve() not in path.parents:
            raise ValueError("path escapes audio directory")
        return path

    def resolve_artifact_path(self, project_id, relative_path):
        if not relative_path.startswith(f"audios/{self.audio_id}/"):
            raise ValueError("artifact belongs to another audio")
        return self.resolve_project_path(project_id, relative_path)

    def load_project(self, project_id):
        project = self.parent.load_project(project_id).model_copy(deep=True)
        project.source = self.parent.audio(project_id, self.audio_id).source
        return project

    def _touch_project(self, project_id):
        self.parent._touch_project(project_id)

    def load_job(self, project_id):
        self.project_dir(project_id)
        path = self.audio_dir() / "job.json"
        if path.is_symlink() or not path.is_file():
            raise ProjectNotFound(self.audio_id)
        job = Job.model_validate_json(path.read_text(encoding="utf-8"))
        if job.project_id != project_id or job.audio_id != self.audio_id:
            raise ValueError("invalid audio job ownership")
        return job

    def save_job(self, job):
        if job.project_id != self.project_id or job.audio_id != self.audio_id:
            raise ValueError("invalid audio job ownership")
        atomic_write_json(self.audio_dir() / "job.json", job.model_dump(mode="json"))
        self._touch_project(job.project_id)

    def load_pipeline_config(self, project_id):
        self.project_dir(project_id)
        path = self.audio_dir() / "config.json"
        if path.is_symlink():
            raise ValueError("config must not be a symlink")
        if path.exists():
            return PipelineConfig.model_validate_json(path.read_text(encoding="utf-8"))
        payload = self.parent.load_pipeline_config(project_id).model_dump(mode="json")
        language = self.parent.audio(project_id, self.audio_id).source.language
        payload["source_language"] = language
        payload["asr"]["language"] = language
        return PipelineConfig.model_validate(payload)

    def save_pipeline_config(self, project_id, value):
        self.project_dir(project_id)
        atomic_write_json(self.audio_dir() / "config.json", value.model_dump(mode="json"))
        self._touch_project(project_id)
