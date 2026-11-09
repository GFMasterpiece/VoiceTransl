import json
from pathlib import Path

import pytest
from app.domain import (
    Job,
    JobStatus,
    LegacyPipelineConfig,
    Project,
    SourceAsset,
    default_stages,
    default_workflow,
    workflow_snapshot,
)
from app.domain.project import PRIMARY_AUDIO_ID
from app.domain.schema import SchemaVersionError
from app.domain.workflow import WorkflowRun
from app.main import create_app
from app.storage.project_store import FileSystemProjectStore
from httpx import ASGITransport, AsyncClient


def make_store(tmp_path: Path) -> tuple[FileSystemProjectStore, str]:
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(
            path="source/sample.wav",
            original_name="sample.wav",
            media_type="audio/wav",
            size_bytes=4,
        ),
    )
    store.create_project(project)
    return store, project.id


def make_job(project_id: str, config: LegacyPipelineConfig | None = None) -> Job:
    return Job(
        id="job_test",
        project_id=project_id,
        stages=default_stages(),
        config_snapshot=config,
    )


def remove_nested_version(path: Path, key: str) -> bytes:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key].pop("schema_version")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.read_bytes()


def test_job_rejects_missing_nested_config_version_and_preserves_bytes(tmp_path):
    store, project_id = make_store(tmp_path)
    job = make_job(project_id, LegacyPipelineConfig())
    store.save_job(job)
    path = tmp_path / "projects" / project_id / "job.json"
    before = remove_nested_version(path, "config_snapshot")

    with pytest.raises(SchemaVersionError):
        store.load_job(project_id)
    assert path.read_bytes() == before


def test_mark_interrupted_does_not_rewrite_invalid_nested_version(tmp_path):
    store, project_id = make_store(tmp_path)
    job = make_job(project_id, LegacyPipelineConfig())
    job.status = JobStatus.RUNNING
    store.save_job(job)
    path = tmp_path / "projects" / project_id / "job.json"
    before = remove_nested_version(path, "config_snapshot")

    store.mark_interrupted()
    assert path.read_bytes() == before


def test_workflow_run_rejects_missing_nested_snapshot_version(tmp_path):
    store, project_id = make_store(tmp_path)
    document = default_workflow(project_id)
    run = WorkflowRun(
        id="wfr_" + "a" * 32,
        project_id=project_id,
        audio_id="aud_primary",
        workflow_revision=document.workflow_revision,
        workflow=workflow_snapshot(document),
    )
    directory = store.create_workflow_run(run)
    path = directory / "run.json"
    before = remove_nested_version(path, "workflow")

    with pytest.raises(SchemaVersionError):
        store.load_workflow_run(project_id, "aud_primary", run.id)
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [None, 1, 1.0, "1.1", "2.0", ""])
def test_persisted_top_level_version_missing_or_wrong_is_rejected(tmp_path, version):
    store, project_id = make_store(tmp_path)
    path = tmp_path / "projects" / project_id / "project.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if version is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = version
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(SchemaVersionError):
        store.load_project(project_id)
    assert path.read_bytes() == before


async def create_api_project(app) -> str:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/projects",
            files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            data={"source_language": "ja", "target_language": "zh-CN"},
        )
        assert response.status_code == 201, response.text
        return response.json()["project"]["id"]


def assert_schema_rejection(response, private_root: Path, expected="1.0") -> None:
    assert response.status_code == 422, response.text
    assert response.json() == {
        "error": {
            "code": "UNSUPPORTED_SCHEMA_VERSION",
            "message": f"不支持该数据版本，当前仅支持 schema_version {expected!r}",
            "details": None,
        }
    }
    assert str(private_root) not in response.text


@pytest.mark.asyncio
async def test_api_maps_persisted_project_schema_error_and_preserves_bytes(tmp_path):
    app = create_app(tmp_path)
    project_id = await create_api_project(app)
    path = tmp_path / "projects" / project_id / "project.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "2.0"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for route in (
            f"/api/v1/projects/{project_id}",
            f"/api/v1/projects/{project_id}/job",
            f"/api/v1/projects/{project_id}/config",
            f"/api/v1/projects/{project_id}/workflow",
            f"/api/v1/projects/{project_id}/audios",
            f"/api/v1/projects/{project_id}/audios/{PRIMARY_AUDIO_ID}/job",
            f"/api/v1/projects/{project_id}/audios/{PRIMARY_AUDIO_ID}/workflow-runs",
        ):
            response = await client.get(route)
            assert_schema_rejection(response, tmp_path)

    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("document", ["job", "config", "workflow", "workflow-run"])
async def test_api_maps_nested_schema_errors_instead_of_404(tmp_path, document):
    app = create_app(tmp_path)
    project_id = await create_api_project(app)
    project_dir = tmp_path / "projects" / project_id
    if document == "job":
        app.state.store.save_job(make_job(project_id, LegacyPipelineConfig()))
        path = project_dir / "job.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["config_snapshot"]["schema_version"] = "2.0"
        path.write_text(json.dumps(payload), encoding="utf-8")
        route = f"/api/v1/projects/{project_id}/job"
    elif document == "config":
        path = project_dir / "config.json"
        payload = LegacyPipelineConfig().model_dump(mode="json")
        payload["schema_version"] = "2.0"
        path.write_text(json.dumps(payload), encoding="utf-8")
        route = f"/api/v1/projects/{project_id}/config"
    elif document == "workflow":
        payload = default_workflow(project_id).model_dump(mode="json")
        payload["schema_version"] = "1.0"
        path = project_dir / "workflow-v2.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        route = f"/api/v1/projects/{project_id}/workflow"
    else:
        default = default_workflow(project_id)
        run = WorkflowRun(
            id="wfr_" + "b" * 32,
            project_id=project_id,
            audio_id=PRIMARY_AUDIO_ID,
            workflow_revision=default.workflow_revision,
            workflow=workflow_snapshot(default),
        )
        directory = app.state.store.create_workflow_run(run)
        path = directory / "run.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["workflow"]["schema_version"] = "1.0"
        path.write_text(json.dumps(payload), encoding="utf-8")
        route = f"/api/v1/projects/{project_id}/audios/{PRIMARY_AUDIO_ID}/workflow-runs/{run.id}"
    before = path.read_bytes()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(route)
        assert_schema_rejection(
            response, tmp_path, "2.0" if document in {"workflow", "workflow-run"} else "1.0"
        )
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_explicit_request_schema_versions_are_clearly_rejected(tmp_path):
    app = create_app(tmp_path)
    project_id = await create_api_project(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for response in (
            await client.post(
                f"/api/v1/projects/{project_id}/workflow/validate",
                json={"schema_version": "1.0", "nodes": [], "edges": []},
            ),
            await client.put(
                f"/api/v1/projects/{project_id}/workflow",
                json={
                    "schema_version": "1.0",
                    "expected_revision": 0,
                    "nodes": [],
                    "edges": [],
                },
            ),
        ):
            assert_schema_rejection(response, tmp_path, "2.0")

        retired = await client.put(
            f"/api/v1/projects/{project_id}/config",
            json={**LegacyPipelineConfig().model_dump(mode="json"), "schema_version": "1.0"},
        )
        assert retired.status_code == 409
        assert retired.json()["error"]["code"] == "LEGACY_CONFIG_READ_ONLY"
