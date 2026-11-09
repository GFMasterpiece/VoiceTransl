import re

from ..domain.batch import (
    ACTIVE_BATCH_STATUSES,
    ACTIVE_ITEM_STATUSES,
    BatchRecord,
    now,
)
from ..domain.schema import SchemaVersionError, require_schema_version
from .atomic import atomic_write_json
from .project_store import ProjectNotFound


class BatchStore:
    def __init__(self, projects):
        self.projects = projects

    def root(self, project_id):
        # Project IDs are filesystem components on every supported platform.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", project_id):
            raise ValueError("invalid project id")
        root = self.projects.project_dir(project_id) / "batches-v2"
        if root.is_symlink():
            raise ValueError("batch directory must not be a symlink")
        return root

    def path(self, project_id, batch_id):
        if not re.fullmatch(r"bat_[0-9a-f]{32}", batch_id):
            raise ValueError("invalid batch id")
        path = self.root(project_id) / f"{batch_id}.json"
        if path.is_symlink():
            raise ValueError("batch file must not be a symlink")
        return path

    def save(self, batch):
        batch.refresh_counts()
        path = self.path(batch.project_id, batch.id)
        path.parent.mkdir(exist_ok=True)
        atomic_write_json(path, batch.model_dump(mode="json"))

    def load(self, project_id, batch_id):
        path = self.path(project_id, batch_id)
        if not path.is_file():
            raise ProjectNotFound(batch_id)
        payload = self.projects._load_json(path, "batch")
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise SchemaVersionError("batch.workflow", None)
        require_schema_version(workflow.get("schema_version"), "batch.workflow")
        batch = BatchRecord.model_validate(payload)
        if batch.id != batch_id or batch.project_id != project_id:
            raise ValueError("invalid batch ownership")
        return batch

    def list(self, project_id, *, recovery=False):
        root = self.root(project_id)
        result = []
        if not root.is_dir():
            return result
        for path in root.glob("bat_*.json"):
            try:
                result.append(self.load(project_id, path.stem))
            except SchemaVersionError:
                if not recovery:
                    raise
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda batch: (batch.created_at, batch.id), reverse=True)

    def mark_interrupted(self):
        for _, project in self.projects._published_projects():
            try:
                batches = self.list(project.id, recovery=True)
            except (OSError, ValueError):
                continue
            for batch in batches:
                if batch.status not in ACTIVE_BATCH_STATUSES:
                    continue
                for item in batch.items:
                    if item.status in ACTIVE_ITEM_STATUSES:
                        item.status = "interrupted"
                        item.finished_at = now()
                batch.status = "interrupted"
                batch.finished_at = now()
                self.save(batch)
