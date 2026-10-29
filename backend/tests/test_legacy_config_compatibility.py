import asyncio
import json

import pytest
from app.domain import LegacyPipelineConfig, default_legacy_pipeline_config
from app.main import create_app
from httpx import ASGITransport, AsyncClient


async def create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("sample.wav", b"RIFF", "audio/wav")},
        data={"source_language": "ja", "target_language": "zh-CN"},
    )
    assert response.status_code == 201
    return response.json()["project"]["id"]


@pytest.mark.asyncio
async def test_legacy_config_get_is_readable_and_put_is_read_only_without_rewrite(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        path = tmp_path / "projects" / project_id / "config.json"
        before = path.read_bytes()
        current = await client.get(f"/api/v1/projects/{project_id}/config")
        assert current.status_code == 200
        assert current.json()["preprocess"]["provider"] == "mock"
        assert current.json()["asr"]["provider"] == "mock"

        response = await client.put(
            f"/api/v1/projects/{project_id}/config",
            json=default_legacy_pipeline_config().model_dump(mode="json"),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LEGACY_CONFIG_READ_ONLY"
        assert path.read_bytes() == before

        audio_id = (await client.get(f"/api/v1/projects/{project_id}")).json()["audios"][0]["id"]
        audio_response = await client.put(
            f"/api/v1/projects/{project_id}/audios/{audio_id}/config",
            json=default_legacy_pipeline_config().model_dump(mode="json"),
        )
        assert audio_response.status_code == 409
        assert audio_response.json()["error"]["code"] == "LEGACY_CONFIG_READ_ONLY"
        assert not (
            tmp_path / "projects" / project_id / "audios" / audio_id / "config.json"
        ).exists()


@pytest.mark.asyncio
async def test_historical_legacy_provider_fields_are_readable_but_never_executable(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        path = tmp_path / "projects" / project_id / "config.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["preprocess"]["provider"] = "ffmpeg"
        payload["asr"].update(
            {
                "provider": "crispasr",
                "provider_profile_id": "historical",
                "backend": "qwen3",
                "model": "old-model",
                "vad_enabled": False,
                "chunk_seconds": 45,
            }
        )
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = path.read_bytes()

        response = await client.get(f"/api/v1/projects/{project_id}/config")
        assert response.status_code == 200
        assert response.json()["preprocess"]["provider"] == "ffmpeg"
        assert response.json()["asr"]["provider"] == "crispasr"
        assert response.json()["asr"]["backend"] == "qwen3"
        assert path.read_bytes() == before

        project = (await client.get(f"/api/v1/projects/{project_id}")).json()
        audio_id = project["audios"][0]["id"]
        default = await client.get(f"/api/v1/projects/{project_id}/workflow")
        run = await client.post(
            f"/api/v1/projects/{project_id}/audios/{audio_id}/workflow-runs",
            json={"workflow_revision": default.json()["workflow_revision"]},
        )
        assert run.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        detail = await client.get(
            f"/api/v1/projects/{project_id}/audios/{audio_id}/workflow-runs/{run.json()['id']}"
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "completed"
        assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_legacy_snapshot_with_historical_provider_remains_readable(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        project_file = tmp_path / "projects" / project_id / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        audio_id = project["audios"][0]["id"]
        job_path = tmp_path / "projects" / project_id / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        config = default_legacy_pipeline_config().model_dump(mode="json")
        config["preprocess"]["provider"] = "ffmpeg"
        config["asr"].update({"provider": "crispasr", "backend": "qwen3", "model": "old"})
        job["config_snapshot"] = config
        job_path.write_text(json.dumps(job), encoding="utf-8")
        before = job_path.read_bytes()

        response = await client.get(f"/api/v1/projects/{project_id}/job")
        assert response.status_code == 200
        assert response.json()["config_snapshot"]["asr"]["provider"] == "crispasr"
        assert job_path.read_bytes() == before
        assert audio_id


@pytest.mark.parametrize(
    "section,provider",
    [
        ("preprocess", "ffmpeg"),
        ("asr", "crispasr"),
    ],
)
def test_legacy_models_accept_historical_provider_but_workflow_models_do_not(section, provider):
    payload = default_legacy_pipeline_config().model_dump(mode="json")
    payload[section]["provider"] = provider
    assert (
        LegacyPipelineConfig.model_validate(payload).model_dump(mode="json")[section]["provider"]
        == provider
    )

    # Node configs reject legacy sections and fields at the domain boundary.
    from app.domain import validate_workflow_graph
    from app.domain.workflow import WorkflowEdge, WorkflowNode

    nodes = [
        WorkflowNode(id="input", type="audio_input"),
        WorkflowNode(id="asr", type="asr", config={"provider": provider}),
        WorkflowNode(id="text", type="text_output"),
    ]
    edges = [
        WorkflowEdge(
            id="e1", source="input", source_port="audio", target="asr", target_port="audio"
        ),
        WorkflowEdge(
            id="e2", source="asr", source_port="transcript", target="text", target_port="transcript"
        ),
    ]
    errors = validate_workflow_graph(nodes, edges)
    assert any(error.code == "INVALID_NODE_CONFIG" for error in errors)
