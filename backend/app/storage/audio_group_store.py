"""One atomic file per group; callers own the project's mutation lock."""

import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from ..domain.audio_group import AudioGroup, AudioGroupSummary, AudioGroupValues
from ..domain.schema import require_schema_version
from .atomic import atomic_write_json

GROUP_ID_PATTERN = r"grp_[0-9a-f]{32}"
MAX_GROUP_FILE_BYTES = 4 * 1024 * 1024


class AudioGroupNotFound(FileNotFoundError):
    pass


class AudioGroupRevisionConflict(ValueError):
    pass


class AudioGroupAudioNotFound(ValueError):
    def __init__(self, audio_ids):
        self.audio_ids = audio_ids
        super().__init__("组内音频不属于当前项目")


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _check_file_stat(metadata):
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("音频组必须是没有其他链接的普通文件")


@contextmanager
def _checked_reader(path):
    before = path.stat(follow_symlinks=False)
    _check_file_stat(before)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        _check_file_stat(opened)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("音频组文件在打开时已改变")
        yield stream
        after = os.fstat(stream.fileno())
        _check_file_stat(after)
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("音频组文件在读取时已改变")


class AudioGroupStore:
    def __init__(self, projects):
        self.projects = projects

    def root(self, project_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", project_id):
            raise ValueError("项目标识无效")
        project = self.projects.project_dir(project_id)
        if _linked(self.projects.projects_dir / project_id):
            raise ValueError("组目录不能使用链接")
        root = project / "audio-groups"
        if _linked(root) or root.resolve().parent != project.resolve():
            raise ValueError("组目录不能越过项目边界")
        return root

    def path(self, project_id, group_id):
        if not re.fullmatch(GROUP_ID_PATTERN, group_id):
            raise ValueError("音频组标识无效")
        root = self.root(project_id)
        path = root / f"{group_id}.json"
        if _linked(path) or path.resolve().parent != root.resolve():
            raise ValueError("音频组文件不能使用链接或越界")
        return path

    def _load(self, project_id, group_id, audio_ids):
        path = self.path(project_id, group_id)
        try:
            with _checked_reader(path) as stream:
                payload = stream.read(MAX_GROUP_FILE_BYTES + 1)
        except FileNotFoundError:
            raise AudioGroupNotFound(group_id) from None
        if len(payload) > MAX_GROUP_FILE_BYTES:
            raise ValueError("音频组文件过大")
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("音频组文件无效")
        require_schema_version(raw.get("schema_version"), "audio group")
        group = AudioGroup.model_validate(raw)
        if group.id != group_id:
            raise ValueError("音频组标识与文件不一致")
        self._check_members(group.audio_ids, audio_ids)
        return group

    def load(self, project_id, group_id):
        audio_ids = {audio.id for audio in self.projects.load_project(project_id).audios}
        return self._load(project_id, group_id, audio_ids)

    def list(self, project_id, *, limit=20, offset=0):
        root = self.root(project_id)
        if not root.exists():
            return [], 0
        audio_ids = {audio.id for audio in self.projects.load_project(project_id).audios}
        groups = [self._load(project_id, path.stem, audio_ids) for path in root.glob("*.json")]
        groups.sort(key=lambda group: (group.updated_at, group.id), reverse=True)
        return [
            AudioGroupSummary.from_group(group) for group in groups[offset : offset + limit]
        ], len(groups)

    def _values(self, project_id, values):
        values = AudioGroupValues.model_validate(values)
        audios = {audio.id for audio in self.projects.load_project(project_id).audios}
        self._check_members(values.audio_ids, audios)
        return values.model_dump(mode="json")

    @staticmethod
    def _check_members(values, audios):
        missing = [audio_id for audio_id in values if audio_id not in audios]
        if missing:
            raise AudioGroupAudioNotFound(missing)

    @staticmethod
    def _write(path, group):
        try:
            with _checked_reader(path):
                pass
        except FileNotFoundError:
            pass
        payload = group.model_dump(mode="json")
        if (
            len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            > MAX_GROUP_FILE_BYTES
        ):
            raise ValueError("音频组文件过大")
        atomic_write_json(path, payload)

    def create(self, project_id, **values):
        values = self._values(project_id, values)
        now = datetime.now(timezone.utc)
        group = AudioGroup(
            id=f"grp_{uuid4().hex}", revision=1, created_at=now, updated_at=now, **values
        )
        path = self.path(project_id, group.id)
        path.parent.mkdir(exist_ok=True)
        if path.exists():
            raise FileExistsError("音频组标识冲突")
        self._write(path, group)
        return group

    def update(self, project_id, group_id, expected_revision, **values):
        current = self.load(project_id, group_id)
        if current.revision != expected_revision:
            raise AudioGroupRevisionConflict
        values = self._values(project_id, values)
        updated = AudioGroup(
            id=current.id,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=datetime.now(timezone.utc),
            **values,
        )
        self._write(self.path(project_id, group_id), updated)
        return updated

    def delete(self, project_id, group_id, expected_revision):
        current = self.load(project_id, group_id)
        if current.revision != expected_revision:
            raise AudioGroupRevisionConflict
        path = self.path(project_id, group_id)
        with _checked_reader(path):
            pass
        path.unlink()
