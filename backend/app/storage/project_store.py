import asyncio
import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from ..domain import (
    Job,
    JobStatus,
    LegacyPipelineConfig,
    Project,
    ProviderProfile,
    ProviderProfilesDocument,
    Transcript,
    WorkflowDocument,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowRun,
    WorkflowRunStatus,
    default_legacy_pipeline_config,
    default_workflow,
    normalize_workflow_graph,
    validate_workflow_graph,
    workflow_semantic_digest,
)
from ..domain.project import PRIMARY_AUDIO_ID, AudioAsset
from ..domain.schema import validate_schema_payload
from .atomic import atomic_write_bytes, atomic_write_json, atomic_write_text


class ProjectNotFound(FileNotFoundError):
    pass


class TranscriptProtectedError(ValueError):
    pass


class SourceUploadTooLarge(ValueError):
    pass


class WorkflowRevisionConflict(RuntimeError):
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

    def _workflow_path(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "workflow.json"
        if path.is_symlink():
            raise ValueError("workflow must not be a symlink")
        return path

    def _workflow_history_dir(self, project_id: str) -> Path:
        return self._project_subdir(project_id, "workflow-revisions")

    def load_workflow(self, project_id: str) -> WorkflowDocument:
        project = self.load_project(project_id)
        path = self._workflow_path(project_id)
        if not path.exists():
            return default_workflow(project_id, project.updated_at)
        document = WorkflowDocument.model_validate(self._load_json(path, "workflow"))
        if document.project_id != project_id:
            raise ValueError("invalid workflow ownership")
        self._repair_workflow_history(document)
        return document

    def _repair_workflow_history(self, document: WorkflowDocument) -> None:
        """Restore a history slot that was not published after a stopped save."""

        history_path = self._workflow_history_dir(document.project_id) / (
            f"{document.revision:08d}.json"
        )
        if history_path.is_symlink():
            raise ValueError("workflow revision history must not be a symlink")
        if history_path.exists():
            return
        atomic_write_json(history_path, document.model_dump(mode="json"))

    def save_workflow(
        self,
        project_id: str,
        current: WorkflowDocument,
        *,
        expected_revision: int,
        nodes: list[WorkflowNode],
        edges: list[WorkflowEdge],
    ) -> WorkflowDocument:
        revision_path = self._workflow_path(project_id)
        if current.project_id != project_id:
            raise ValueError("invalid workflow ownership")
        if current.revision != expected_revision:
            raise WorkflowRevisionConflict

        nodes, edges = normalize_workflow_graph(nodes, edges)
        issues = validate_workflow_graph(nodes, edges)
        if issues:
            raise ValueError("invalid workflow")
        updated = WorkflowDocument(
            project_id=project_id,
            revision=current.revision,
            workflow_revision=current.workflow_revision,
            updated_at=current.updated_at,
            nodes=nodes,
            edges=edges,
        )
        if revision_path.exists() and updated.model_dump(
            exclude={"updated_at"}
        ) == current.model_dump(exclude={"updated_at"}):
            return current

        semantic_changed = workflow_semantic_digest(
            current.nodes, current.edges
        ) != workflow_semantic_digest(nodes, edges)
        updated.revision = current.revision + 1
        updated.workflow_revision = current.workflow_revision + int(
            semantic_changed or current.revision == 0
        )
        updated.updated_at = datetime.now(timezone.utc)
        history_path = self._workflow_history_dir(project_id) / (f"{updated.revision:08d}.json")
        payload = updated.model_dump(mode="json")
        if history_path.is_symlink():
            raise ValueError("workflow revision history must not be a symlink")
        if history_path.exists():
            existing = WorkflowDocument.model_validate(self._load_json(history_path, "workflow"))
            if existing.model_dump(exclude={"updated_at"}) != (
                updated.model_dump(exclude={"updated_at"})
            ):
                raise ValueError("workflow revision history is immutable")
        # Current is authoritative. If the process stops before the history
        # write, load_workflow repairs that revision from the committed current
        # document on the next access.
        atomic_write_json(revision_path, payload)
        self._touch_project(project_id)
        if not history_path.exists():
            atomic_write_json(history_path, payload)
        return updated

    def workflow_run_root(self, project_id: str, audio_id: str) -> Path:
        self.audio(project_id, audio_id)
        if audio_id == PRIMARY_AUDIO_ID:
            root = self.project_dir(project_id) / "workflow-runs"
        else:
            root = self.scope(project_id, audio_id).audio_dir() / "workflow-runs"
        if root.is_symlink():
            raise ValueError("workflow runs directory must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve()
        boundary = (
            self.project_dir(project_id)
            if audio_id == PRIMARY_AUDIO_ID
            else self.scope(project_id, audio_id).audio_dir().resolve()
        )
        if resolved.parent != boundary and boundary not in resolved.parents:
            raise ValueError("workflow runs directory escapes audio boundary")
        return root

    @staticmethod
    def _validate_workflow_run_id(run_id: str) -> None:
        if (
            not run_id.startswith("wfr_")
            or len(run_id) != 36
            or any(char not in "0123456789abcdef" for char in run_id[4:])
        ):
            raise ValueError("invalid workflow run id")

    def workflow_run_dir(self, project_id: str, audio_id: str, run_id: str) -> Path:
        self._validate_workflow_run_id(run_id)
        root = self.workflow_run_root(project_id, audio_id)
        path = root / run_id
        if path.is_symlink():
            raise ValueError("workflow run directory must not be a symlink")
        if path.resolve().parent != root.resolve():
            raise ValueError("workflow run directory escapes runs root")
        return path

    def create_workflow_run(self, run: WorkflowRun) -> Path:
        directory = self.workflow_run_dir(run.project_id, run.audio_id, run.id)
        directory.mkdir(parents=False, exist_ok=False)
        atomic_write_json(directory / "run.json", run.model_dump(mode="json"))
        self._touch_project(run.project_id)
        return directory

    def save_workflow_run(self, run: WorkflowRun) -> Path:
        directory = self.workflow_run_dir(run.project_id, run.audio_id, run.id)
        if not directory.is_dir():
            raise ProjectNotFound(run.id)
        path = directory / "run.json"
        if path.is_symlink():
            raise ValueError("workflow run state must not be a symlink")
        atomic_write_json(path, run.model_dump(mode="json"))
        self._touch_project(run.project_id)
        return directory

    def load_workflow_run(self, project_id: str, audio_id: str, run_id: str) -> WorkflowRun:
        directory = self.workflow_run_dir(project_id, audio_id, run_id)
        path = directory / "run.json"
        if path.is_symlink() or not path.is_file():
            raise ProjectNotFound(run_id)
        run = WorkflowRun.model_validate(self._load_json(path, "workflow run"))
        if run.project_id != project_id or run.audio_id != audio_id or run.id != run_id:
            raise ValueError("invalid workflow run ownership")
        return run

    def list_workflow_runs(
        self, project_id: str, audio_id: str, *, limit: int, offset: int
    ) -> tuple[list[WorkflowRun], int]:
        self.audio(project_id, audio_id)
        if audio_id == PRIMARY_AUDIO_ID:
            root = self.project_dir(project_id) / "workflow-runs"
        else:
            root = self.scope(project_id, audio_id).audio_dir() / "workflow-runs"
        if not root.is_dir() or root.is_symlink():
            return [], 0
        runs = []
        for directory in root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                run = self.load_workflow_run(project_id, audio_id, directory.name)
            except (OSError, ValueError):
                continue
            runs.append(run)
        runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        return runs[offset : offset + limit], len(runs)

    def workflow_artifact_path(
        self,
        project_id: str,
        audio_id: str,
        run_id: str,
        node_id: str,
        filename: str,
    ) -> Path:
        if not node_id or node_id in {".", ".."} or "/" in node_id or "\\" in node_id:
            raise ValueError("invalid workflow node id")
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValueError("invalid workflow artifact filename")
        directory = self.workflow_run_dir(project_id, audio_id, run_id)
        node_dir = directory / "nodes" / node_id
        for candidate in (directory / "nodes", node_dir):
            if candidate.is_symlink():
                raise ValueError("workflow artifact directory must not be a symlink")
        path = node_dir / filename
        if path.is_symlink():
            raise ValueError("workflow artifact must not be a symlink")
        if node_dir.resolve().parent != (directory / "nodes").resolve():
            raise ValueError("workflow artifact path escapes node directory")
        return path

    def mark_workflow_runs_interrupted(self) -> None:
        for _, project in self._published_projects():
            for audio in project.audios:
                try:
                    if audio.id == PRIMARY_AUDIO_ID:
                        root = self.project_dir(project.id) / "workflow-runs"
                    else:
                        root = self.scope(project.id, audio.id).audio_dir() / "workflow-runs"
                    if not root.is_dir() or root.is_symlink():
                        continue
                    for directory in root.iterdir():
                        if directory.is_symlink() or not directory.is_dir():
                            continue
                        try:
                            run = self.load_workflow_run(project.id, audio.id, directory.name)
                        except (OSError, ValueError):
                            continue
                        if run.status not in {
                            WorkflowRunStatus.PENDING,
                            WorkflowRunStatus.RUNNING,
                            WorkflowRunStatus.CANCELLING,
                        }:
                            continue
                        run.status = WorkflowRunStatus.INTERRUPTED
                        run.finished_at = datetime.now(timezone.utc)
                        for node in run.nodes:
                            if node.status in {
                                WorkflowNodeStatus.PENDING,
                                WorkflowNodeStatus.RUNNING,
                            }:
                                node.status = WorkflowNodeStatus.INTERRUPTED
                                node.finished_at = run.finished_at
                        directory_path = self.workflow_run_dir(project.id, audio.id, run.id)
                        atomic_write_json(directory_path / "run.json", run.model_dump(mode="json"))
                except Exception:
                    continue

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
        if candidate.is_symlink():
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
        # Resolving first is necessary to detect escapes, but resolving also
        # hides symlinks.  Walk the un-resolved path explicitly so a source
        # link cannot silently point at another authorized-looking file.
        project_root = self.project_dir(project_id).resolve()
        raw_parts = Path(relative_path).parts
        current = project_root
        for part in raw_parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("source path must not contain symlinks")
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
        if child.is_symlink():
            raise ValueError(f"{name} directory must not be a symlink")
        child.mkdir(parents=True, exist_ok=True)
        resolved = child.resolve()
        if project_dir not in resolved.parents or child.is_symlink():
            raise ValueError(f"{name} directory escapes project directory")
        return child

    async def create_project_from_upload(
        self,
        project: Project,
        job: Job,
        upload,
        max_bytes: int,
        *,
        config: LegacyPipelineConfig | None = None,
    ) -> tuple[Project, Job]:
        """Publish project metadata and optional initial config atomically."""
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
            if config is not None:
                atomic_write_json(temp_dir / "config.json", config.model_dump(mode="json"))
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
        """Return the legacy full listing with its existing 200-item cap."""
        return self.list_projects_page(limit=200, offset=0)[0]

    def list_projects_page(self, *, limit: int, offset: int) -> tuple[list[Project], int]:
        """Return a stable newest-first project page and the unpaged total."""
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")
        items = [project for _, project in self._published_projects()]
        items.sort(key=lambda project: (project.updated_at, project.id), reverse=True)
        return items[offset : offset + limit], len(items)

    def _published_projects(self):
        """Yield only complete, named project directories after a restart."""
        for p in self.projects_dir.iterdir():
            if p.name.startswith(".") or p.is_symlink() or not p.is_dir():
                continue
            metadata = p / "project.json"
            if metadata.is_symlink() or not metadata.is_file():
                continue
            try:
                project = Project.model_validate(self._load_json(metadata, "project"))
                if project.id == p.name:
                    yield p, project
            except Exception:
                continue

    @staticmethod
    def _load_json(path: Path, document: str) -> object:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_schema_payload(payload, document)
        return payload

    def load_project(self, project_id: str) -> Project:
        path = self.project_dir(project_id) / "project.json"
        if path.is_symlink() or not path.is_file():
            raise ProjectNotFound(project_id)
        return Project.model_validate(self._load_json(path, "project"))

    def save_project(self, project: Project) -> None:
        atomic_write_json(
            self.project_dir(project.id) / "project.json", project.model_dump(mode="json")
        )

    def load_job(self, project_id: str) -> Job:
        path = self.project_dir(project_id) / "job.json"
        if path.is_symlink() or not path.is_file():
            raise ProjectNotFound(project_id)
        job = Job.model_validate(self._load_json(path, "job"))
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

    def load_pipeline_config(self, project_id: str) -> LegacyPipelineConfig:
        """Load a project's editable config, providing a legacy-safe default."""

        path = self.project_dir(project_id) / "config.json"
        if path.is_symlink():
            raise ValueError("config must not be a symlink")
        if not path.exists():
            project = self.load_project(project_id)
            return default_legacy_pipeline_config(project.source.language)
        return LegacyPipelineConfig.model_validate(self._load_json(path, "pipeline config"))

    def save_pipeline_config(self, project_id: str, value: LegacyPipelineConfig) -> None:
        self.save_initial_pipeline_config(project_id, value)

    def save_initial_pipeline_config(self, project_id: str, value: LegacyPipelineConfig) -> None:
        """Persist the initial config without requiring a second metadata write."""
        atomic_write_json(
            self.project_dir(project_id) / "config.json", value.model_dump(mode="json")
        )
        self._touch_project(project_id)

    def _provider_profiles_path(self) -> Path:
        path = self.data_dir / "provider_profiles.json"
        if path.is_symlink():
            raise ValueError("provider profiles must not be a symlink")
        return path

    def load_provider_profiles(self) -> list[ProviderProfile]:
        """Load global profiles, returning an empty list for a fresh install."""

        path = self._provider_profiles_path()
        if not path.exists():
            return []
        document = ProviderProfilesDocument.model_validate(
            self._load_json(path, "provider profiles")
        )
        return [profile.model_copy(deep=True) for profile in document.profiles]

    def save_provider_profiles(self, profiles: list[ProviderProfile]) -> None:
        document = ProviderProfilesDocument(profiles=profiles)
        atomic_write_json(self._provider_profiles_path(), document.model_dump(mode="json"))

    def provider_profile(self, profile_id: str) -> ProviderProfile | None:
        return next(
            (profile for profile in self.load_provider_profiles() if profile.id == profile_id),
            None,
        )

    def provider_profile_in_use(self, profile_id: str) -> bool:
        """Return whether an editable project config references a profile."""

        for _, project in self._published_projects():
            for audio in project.audios:
                try:
                    config = self.scope(project.id, audio.id).load_pipeline_config(project.id)
                except (FileNotFoundError, ProjectNotFound, ValueError):
                    continue
                if config.asr.provider_profile_id == profile_id:
                    return True
        return False

    def load_transcript(self, project_id: str, stage: str) -> Transcript:
        if stage not in {"raw", "aligned", "translated"}:
            raise ValueError("invalid transcript stage")
        path = self._project_subdir(project_id, "transcript") / f"{stage}.json"
        return Transcript.model_validate(self._load_json(path, "transcript"))

    def save_transcript(
        self, project_id: str, name: str, value: Transcript, *, overwrite: bool = False
    ) -> Path:
        if name not in {"raw", "aligned", "translated"}:
            raise ValueError("invalid transcript stage")
        path = self._project_subdir(project_id, "transcript") / f"{name}.json"
        if path.exists() and not overwrite:
            try:
                existing = Transcript.model_validate(self._load_json(path, "transcript"))
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

    def _direct_output_path(self, project_id: str, filename: str) -> Path:
        """Return output/name without following its final component."""
        from ..exporters import validate_export_filename

        validate_export_filename(filename)
        output = self._project_subdir(project_id, "output")
        candidate = output / filename
        if candidate.is_symlink():
            raise ValueError("export output must not be a symlink")
        return candidate

    def list_outputs(self, project_id: str) -> list[Path]:
        output = self._project_subdir(project_id, "output")
        manifest_present = self.has_export_manifest(project_id)
        if manifest_present:
            manifest = self.load_export_manifest(project_id)
            result = []
            for item in manifest:
                try:
                    path = self._direct_output_path(project_id, str(item["name"]))
                except (FileNotFoundError, ValueError):
                    continue
                if path.is_file() and not path.is_symlink():
                    result.append(path)
            return sorted(result, key=lambda path: path.name)
        legacy = {"zh.srt", "ja-zh.srt", "zh.vtt", "ja-zh.vtt", "zh.lrc"}
        return sorted(
            path
            for path in output.iterdir()
            if path.name in legacy and path.is_file() and not path.is_symlink()
        )

    def export_manifest_path(self, project_id: str) -> Path:
        return self._project_subdir(project_id, "output") / "manifest.json"

    def has_export_manifest(self, project_id: str) -> bool:
        # A present but invalid manifest (including a symlink or directory) is
        # still authoritative: do not fall back to legacy discovery.
        try:
            self.export_manifest_path(project_id).lstat()
        except FileNotFoundError:
            return False
        return True

    def load_export_manifest(self, project_id: str) -> list[dict[str, object]]:
        from ..domain.schema import require_schema_version
        from ..exporters import EXPORT_FORMATS, validate_export_filename

        path = self.export_manifest_path(project_id)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return []
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("invalid export manifest")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("invalid export manifest") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "items"}:
            raise ValueError("invalid export manifest")
        require_schema_version(payload.get("schema_version"), "export manifest")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid export manifest")
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {
                "name",
                "format",
                "kind",
                "language",
                "bilingual",
                "size_bytes",
                "sha256",
            }:
                raise ValueError("invalid export manifest")
            name = item.get("name")
            format_name = item.get("format")
            kind = item.get("kind")
            language = item.get("language")
            bilingual = item.get("bilingual")
            size = item.get("size_bytes")
            digest = item.get("sha256")
            if (
                not isinstance(name, str)
                or format_name not in EXPORT_FORMATS
                or kind not in {"translated", "bilingual"}
                or not isinstance(language, str)
                or not language
                or not isinstance(bilingual, bool)
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("invalid export manifest")
            validate_export_filename(name)
            if not name.endswith(f".{format_name}"):
                raise ValueError("invalid export manifest")
            if name.casefold() in seen:
                raise ValueError("duplicate export manifest filename")
            seen.add(name.casefold())
            result.append(dict(item))
        return result

    def save_export_outputs(self, project_id: str, payloads: dict[str, str], files) -> None:
        """Stage new files, publish their manifest, then remove old managed files."""
        from ..exporters import ExportFile

        if not all(isinstance(item, ExportFile) for item in files):
            raise ValueError("invalid export metadata")
        names = {item.name for item in files}
        if set(payloads) != names:
            raise ValueError("export payload names do not match metadata")
        output = self._project_subdir(project_id, "output")
        for name in names:
            self._direct_output_path(project_id, name)
        managed = (
            self.load_export_manifest(project_id) if self.has_export_manifest(project_id) else []
        )
        previous = {str(item["name"]) for item in managed}
        # Only the historical built-in outputs may be removed without a manifest.
        legacy = {"zh.srt", "ja-zh.srt", "zh.vtt", "ja-zh.vtt", "zh.lrc"}
        if not self.has_export_manifest(project_id):
            previous |= legacy
        existing = {path.name.casefold(): path.name for path in output.iterdir()}
        previous_folded = {name.casefold() for name in previous}
        unmanaged = {
            name
            for name in names
            if name.casefold() in existing and name.casefold() not in previous_folded
        }
        if unmanaged:
            raise ValueError("export filename would overwrite an unmanaged file")
        # A managed name alone is not authorization to replace or delete a file:
        # the prior manifest's size/hash must still describe the on-disk bytes.
        for item in managed:
            prior_name = str(item["name"])
            try:
                prior_path = self._direct_output_path(project_id, prior_name)
            except (FileNotFoundError, ValueError):
                continue
            if not prior_path.is_file() or prior_path.is_symlink():
                continue
            data = prior_path.read_bytes()
            if (
                len(data) != item["size_bytes"]
                or hashlib.sha256(data).hexdigest() != item["sha256"]
            ):
                raise ValueError("managed export file was modified outside the application")
        for name in names:
            candidate = output / name
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError("export output must be a regular file")

        staged: dict[str, Path] = {}
        try:
            for item in sorted(files, key=lambda value: value.name):
                temporary = output / f".{item.name}.{uuid4().hex}.tmp"
                staged[item.name] = temporary
                atomic_write_text(temporary, payloads[item.name])
            entries = []
            for item in sorted(files, key=lambda value: value.name):
                data = staged[item.name].read_bytes()
                entries.append(
                    {
                        "name": item.name,
                        "format": item.format,
                        "kind": item.kind,
                        "language": item.language,
                        "bilingual": item.bilingual,
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            for item in sorted(files, key=lambda value: value.name):
                os.replace(staged[item.name], output / item.name)
            atomic_write_json(output / "manifest.json", {"schema_version": "1.0", "items": entries})
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
        for name in previous - names - {"manifest.json"}:
            try:
                path = self._direct_output_path(project_id, name)
            except (FileNotFoundError, ValueError):
                continue
            if path.is_file() and not path.is_symlink():
                path.unlink()
        self._touch_project(project_id)

    def prune_generated_outputs(self, project_id: str, keep: set[str]) -> None:
        output = self._project_subdir(project_id, "output")
        generated = {"zh.srt", "ja-zh.srt", "zh.vtt", "ja-zh.vtt", "zh.lrc"}
        for name in generated - keep:
            path = output / name
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


def verify_export_manifest(store, project_id: str, expected_names: set[str]) -> None:
    """Reject a completed export whose manifest does not match actual bytes."""
    if not store.has_export_manifest(project_id):
        # Legacy outputs have no recorded hashes; required names were already
        # checked by the resume path and are never treated as user-editable.
        return
    manifest = store.load_export_manifest(project_id)
    if {str(item["name"]) for item in manifest} != expected_names:
        raise ValueError("export manifest does not match configured outputs")
    for item in manifest:
        path = store._direct_output_path(project_id, str(item["name"]))
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("export manifest output is missing")
        data = path.read_bytes()
        if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("export manifest output was modified")


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
            return LegacyPipelineConfig.model_validate(
                self.parent._load_json(path, "pipeline config")
            )
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
        job = Job.model_validate(self.parent._load_json(path, "job"))
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
            return LegacyPipelineConfig.model_validate(
                self.parent._load_json(path, "pipeline config")
            )
        payload = self.parent.load_pipeline_config(project_id).model_dump(mode="json")
        language = self.parent.audio(project_id, self.audio_id).source.language
        payload["source_language"] = language
        payload["asr"]["language"] = language
        return LegacyPipelineConfig.model_validate(payload)

    def save_pipeline_config(self, project_id, value):
        self.project_dir(project_id)
        atomic_write_json(self.audio_dir() / "config.json", value.model_dump(mode="json"))
        self._touch_project(project_id)

    def load_provider_profiles(self):
        return self.parent.load_provider_profiles()

    def save_provider_profiles(self, profiles):
        return self.parent.save_provider_profiles(profiles)

    def provider_profile(self, profile_id):
        return self.parent.provider_profile(profile_id)

    def provider_profile_in_use(self, profile_id):
        return self.parent.provider_profile_in_use(profile_id)
