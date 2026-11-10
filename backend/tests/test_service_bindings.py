"""Service families retain frozen effective values, including processing ancestors."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app.domain.context_values import JSONValue
from app.domain.node_execution import NodeResult
from app.domain.node_registry import NODE_DEFINITIONS, TYPE_DEFINITIONS
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_batches import setup
from test_inline_credentials import cloud as cloud  # noqa: F401
from test_inline_credentials import graph, run, save, update_body
from test_workflow_reuse import plan, rerun_body, submit


@pytest.mark.asyncio
async def test_processing_strings_and_json_feed_effective_model_without_fallback(
    tmp_path, monkeypatch, cloud
):
    async def produce(context):
        with pytest.raises(TypeError):
            context.available_outputs["untrusted"] = {}
        result = NodeResult()
        result.add("string", "future-runtime-model")
        result.add("json", JSONValue.from_object({"temperature": 0.2}))
        return result

    template = NODE_DEFINITIONS["preprocess"]
    monkeypatch.setitem(
        NODE_DEFINITIONS,
        "runtime_model_values",
        replace(
            template,
            type="runtime_model_values",
            output_ports=("string", "json"),
            execute=produce,
            config_kind="none",
            fields=(),
            settings_default_fields=(),
            normalize_config=lambda value: {},
            validate_config=lambda value: {},
        ),
    )

    async def forward(context):
        result = NodeResult()
        result.add("asr_model", context.input("asr_model").value)
        return result

    monkeypatch.setitem(
        NODE_DEFINITIONS,
        "forward_model",
        replace(
            NODE_DEFINITIONS["asr_model"],
            type="forward_model",
            input_ports=("asr_model",),
            required_input_ports=("asr_model",),
            evaluation_kind="processing",
            evaluate_config=None,
            execute=forward,
            fields=(),
            settings_default_fields=(),
            normalize_config=lambda value: {},
            validate_config=lambda value: {},
        ),
    )
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        body = graph(document)
        body["nodes"].append({"id": "forward", "type": "forward_model", "config": {}})
        next(edge for edge in body["edges"] if edge["id"] == "model_cloud")["source"] = "forward"
        body["edges"].append(
            {
                "id": "model_forward",
                "source": "cloud_model",
                "source_port": "asr_model",
                "target": "forward",
                "target_port": "asr_model",
            }
        )
        body["nodes"].append({"id": "runtime_values", "type": "runtime_model_values", "config": {}})
        body["edges"].extend(
            [
                {
                    "id": "input_values",
                    "source": "input",
                    "source_port": "audio",
                    "target": "runtime_values",
                    "target_port": "audio",
                },
                {
                    "id": "model_name",
                    "source": "runtime_values",
                    "source_port": "string",
                    "target": "cloud_model",
                    "target_port": "model_name",
                },
                {
                    "id": "model_parameters",
                    "source": "runtime_values",
                    "source_port": "json",
                    "target": "cloud_model",
                    "target_port": "additional_parameters",
                },
            ]
        )
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        completed = await run(client, app, pid, aids[0], document)
        assert completed.status.value == "completed", completed.error
        assert cloud["calls"][0]["model"] == "future-runtime-model"
        assert cloud["calls"][0]["additional_parameters"] == {"temperature": 0.2}
        record = next(record for record in completed.nodes if record.node_id == "cloud_model")
        assert record.config["model_name"] == "qwen-audio-3.1-asr"
        assert record.effective_config["model_name"] == "future-runtime-model"
        preview = await plan(client, pid, aids[0], rerun_body(document, completed))
        assert preview["valid"], preview
        rerun = await submit(client, app, pid, aids[0], rerun_body(document, completed))
        assert rerun.status.value == "completed", rerun.error
        assert len(cloud["calls"]) == 1
        assert (
            next(record for record in rerun.nodes if record.node_id == "forward").execution_kind
            == "reused"
        )


@pytest.mark.parametrize(
    "change", [{"model_name": "forged-model"}, {"parameters_json": '{"temperature":0.9}'}]
)
@pytest.mark.asyncio
async def test_same_family_forgery_is_rejected_before_adapter_and_by_codec(
    tmp_path, monkeypatch, cloud, change
):
    async def forge(context):
        result = NodeResult()
        result.add("asr_model", context.input("asr_model").value.model_copy(update=change))
        return result

    template = NODE_DEFINITIONS["asr_model"]
    monkeypatch.setitem(
        NODE_DEFINITIONS,
        "forge_model",
        replace(
            template,
            type="forge_model",
            input_ports=("asr_model",),
            required_input_ports=("asr_model",),
            evaluation_kind="processing",
            evaluate_config=None,
            execute=forge,
            fields=(),
            settings_default_fields=(),
            normalize_config=lambda value: {},
            validate_config=lambda value: {},
        ),
    )
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        body = graph(document)
        body["nodes"].append({"id": "forged", "type": "forge_model", "config": {}})
        edge = next(edge for edge in body["edges"] if edge["id"] == "model_cloud")
        edge["source"] = "forged"
        body["edges"].append(
            {
                "id": "model_forge",
                "source": "cloud_model",
                "source_port": "asr_model",
                "target": "forged",
                "target_port": "asr_model",
            }
        )
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        completed = await run(client, app, pid, aids[0], document)
        record = next(record for record in completed.nodes if record.node_id == "cloud")
        assert record.error.type == "MODEL_BINDING_CHANGED"
        assert cloud["calls"] == []
        from app.application.configuration_evaluator import evaluate_configuration_graph

        values = await evaluate_configuration_graph(
            app.state.store,
            completed,
            app.state.store.load_project(pid),
            asyncio.Event(),
            required_ids={"cloud_model"},
        )
        forged = values["cloud_model"].outputs["asr_model"].value.model_copy(update=change)
        codec = TYPE_DEFINITIONS["asr_model"].checkpoint_codec
        with pytest.raises(Exception, match="frozen effective configuration"):
            await codec.decode(
                forged.model_dump(mode="json"),
                SimpleNamespace(store=app.state.store, run=completed, cancel_event=asyncio.Event()),
            )
