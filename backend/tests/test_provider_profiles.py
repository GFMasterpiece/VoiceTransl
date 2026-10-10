import asyncio
import json

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


def profile_payload(profile_id="openai-main"):
    return {
        "id": profile_id,
        "provider_type": "openai_transcribe",
        "display_name": "OpenAI transcription",
        "base_url": "https://api.example.test/v1/",
        "api_key_env": "OPENAI_API_KEY",
        "region": "global",
        "default_timeout": 180,
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_provider_profile_crud_is_secret_free(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/provider-profiles", json=profile_payload())
        assert created.status_code == 201
        assert created.json()["base_url"] == "https://api.example.test/v1"
        assert '"api_key":"' not in created.text

        listed = await client.get("/api/v1/provider-profiles")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == ["openai-main"]

        duplicate = await client.post("/api/v1/provider-profiles", json=profile_payload())
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "PROVIDER_PROFILE_EXISTS"

        invalid = await client.post(
            "/api/v1/provider-profiles",
            json={**profile_payload("bad-key"), "api_key_env": "OPENAI-KEY"},
        )
        assert invalid.status_code == 422
        # Values are never treated as secrets by the server; only the
        # environment-variable name is stored, and the persisted document has
        # no key material field.
        document = json.loads((tmp_path / "provider_profiles.json").read_text())
        assert document["profiles"][0]["api_key_env"] == "OPENAI_API_KEY"
        assert all("api_key" not in item for item in document["profiles"])


@pytest.mark.asyncio
async def test_provider_profile_reference_is_snapshotted_and_protected_from_delete(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.post("/api/v1/provider-profiles", json=profile_payload())
        ).status_code == 201
        created = await client.post(
            "/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")}
        )
        project_id = created.json()["project"]["id"]
        config = (await client.get(f"/api/v1/projects/{project_id}/config")).json()
        config["asr"]["provider_profile_id"] = "openai-main"
        saved = await client.put(f"/api/v1/projects/{project_id}/config", json=config)
        assert saved.status_code == 200

        blocked = await client.delete("/api/v1/provider-profiles/openai-main")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "PROVIDER_PROFILE_IN_USE"

        started = await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})
        assert started.status_code == 202
        for _ in range(100):
            job = (await client.get(f"/api/v1/projects/{project_id}/job")).json()
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.05)
        assert job["status"] == "completed"
        snapshot = job["provider_profiles_snapshot"]["openai-main"]
        assert snapshot["provider_type"] == "openai_transcribe"
        assert "api_key" not in snapshot

        updated = await client.put(
            "/api/v1/provider-profiles/openai-main",
            json={**profile_payload(), "display_name": "Updated OpenAI"},
        )
        assert updated.status_code == 200
        unchanged_job = (await client.get(f"/api/v1/projects/{project_id}/job")).json()
        assert (
            unchanged_job["provider_profiles_snapshot"]["openai-main"]["display_name"]
            == "OpenAI transcription"
        )

        # The profile may be disabled or deleted only after the editable
        # project reference is removed; the completed job keeps its snapshot.
        config["asr"]["provider_profile_id"] = None
        assert (
            await client.put(f"/api/v1/projects/{project_id}/config", json=config)
        ).status_code == 200
        assert (await client.delete("/api/v1/provider-profiles/openai-main")).status_code == 204
        final_job = (await client.get(f"/api/v1/projects/{project_id}/job")).json()
        assert (
            final_job["provider_profiles_snapshot"]["openai-main"]["display_name"]
            == "OpenAI transcription"
        )
