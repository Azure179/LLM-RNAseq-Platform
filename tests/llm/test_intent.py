"""Free contract fixtures only. These are not claimed as DeepSeek semantic test results."""

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import ChatResponse, DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent import IntentParser, parse_intent
from bulk_rnaseq.llm.intent_catalog import build_catalog, defaults_for
from bulk_rnaseq.llm.intent_prompt import build_system_prompt
from bulk_rnaseq.registry import SkillRegistry


def fixture_response(content: str, finish: str = "stop") -> ChatResponse:
    return ChatResponse("fixture", "fixture-model", content, finish, None, .1)


@pytest.fixture
def catalog() -> dict:
    return build_catalog(create_default_registry())


def pca_fixture() -> dict:
    return {"status": "needs_input", "skill_name": "rnaseq.pca", "parameters": {},
            "missing_parameters": ["count_matrix", "metadata", "group_column", "output_dir"],
            "message": "Supply data and group column.", "gene_subset": None, "evidence": {}}


def test_projection_uses_registered_defaults(catalog: dict) -> None:
    assert len(catalog) == 6 and "rnaseq.gsea" not in catalog
    deg = defaults_for(catalog["rnaseq.deg"]["input_schema"])
    assert deg == {"alpha": .05, "lfc_threshold": 1}
    assert defaults_for(catalog["rnaseq.heatmap"]["input_schema"])["top_n"] == 50
    assert "group_column" not in defaults_for(catalog["rnaseq.pca"]["input_schema"])
    assert "rscript_executable" not in json.dumps(catalog)
    assert len(build_system_prompt(catalog, {})) < 40000


def test_json_mode_no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid = Mock(side_effect=AssertionError("Intent must not execute a Skill"))
    monkeypatch.setattr(SkillRegistry, "execute", forbid)
    monkeypatch.setattr(SkillRegistry, "get_executor", forbid)
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["temperature"] == 0
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1] == {"role": "user", "content": "画PCA"}
        assert payload["max_tokens"] == 768 and "tools" not in payload
        return httpx.Response(200, json={"id": "fixture", "model": "fixture", "choices": [{"message": {
            "role": "assistant", "content": json.dumps(pca_fixture())}, "finish_reason": "stop"}]})
    client = DeepSeekClient(DeepSeekConfig("sk-test-only-secret"), transport=httpx.MockTransport(handler))
    result = IntentParser(client, create_default_registry()).parse("画PCA")
    assert result.intent.status == "needs_input" and len(result.prompt_sha256) == 64
    forbid.assert_not_called()


@pytest.mark.parametrize("raw", ["not json", "```json\n{}\n```", '{"status":NaN}', '{"status":"ready","status":"unsupported"}'])
def test_invalid_json(raw: str, catalog: dict) -> None:
    with pytest.raises(DeepSeekError) as caught:
        parse_intent(fixture_response(raw), catalog, "test", {})
    assert caught.value.code == "LLM_INTENT_JSON_ERROR"


@pytest.mark.parametrize("case", ["extra", "status", "ready_missing", "unsupported_skill", "missing_message", "parameters_type"])
def test_pydantic_rejects(case: str, catalog: dict) -> None:
    data = pca_fixture()
    if case == "extra": data["shell"] = "anything"
    elif case == "status": data["status"] = "success"
    elif case == "ready_missing": data["status"] = "ready"
    elif case == "unsupported_skill": data["status"] = "unsupported"
    elif case == "missing_message": data["message"] = ""
    else: data["parameters"] = []
    with pytest.raises(DeepSeekError) as caught:
        parse_intent(fixture_response(json.dumps(data)), catalog, "test", {})
    assert caught.value.code == "LLM_INTENT_SCHEMA_ERROR"


@pytest.mark.parametrize("case", ["unregistered", "invented_path", "invented_column", "wrong_missing", "host_param", "evidence_not_quote"])
def test_manifest_and_grounding_rejections(case: str, catalog: dict) -> None:
    data = pca_fixture()
    if case == "unregistered": data["skill_name"] = "rnaseq.gsea"
    elif case in {"invented_path", "invented_column", "evidence_not_quote"}:
        key = "group_column" if case == "invented_column" else "count_matrix"
        data["parameters"][key] = "invented"
        data["missing_parameters"].remove(key)
        data["evidence"][key] = "not user text" if case == "evidence_not_quote" else "test"
    elif case == "wrong_missing": data["missing_parameters"] = ["metadata"]
    else: data["parameters"]["rscript_executable"] = "shell"
    with pytest.raises(DeepSeekError) as caught:
        parse_intent(fixture_response(json.dumps(data)), catalog, "test", {})
    assert caught.value.code == "LLM_INTENT_VALIDATION_ERROR"


def test_ready_is_only_request_completeness(catalog: dict) -> None:
    data = pca_fixture()
    data.update(status="ready", missing_parameters=[], message="")
    supplied = {"count_matrix": "declared/counts.csv", "metadata": "declared/meta.csv", "group_column": "group", "output_dir": "declared/out"}
    data["parameters"] = supplied
    result = parse_intent(fixture_response(json.dumps(data)), catalog, "PCA", {"rnaseq.pca": supplied})
    assert result.status == "ready"  # No file is read, created or declared scientifically validated.


def test_truncation_is_error(catalog: dict) -> None:
    with pytest.raises(DeepSeekError) as caught:
        parse_intent(fixture_response("{}", "length"), catalog, "test", {})
    assert caught.value.code == "LLM_INTENT_INCOMPLETE"


def test_context_rejected_before_request() -> None:
    client = Mock()
    parser = IntentParser(client, create_default_registry())
    with pytest.raises(DeepSeekError):
        parser.parse("test", context={"rnaseq.pca": {"rscript_executable": "bad"}})
    client.chat.assert_not_called()


@pytest.mark.parametrize("context", [[], "", 0, {"rnaseq.deg": []}])
def test_context_type_is_not_silently_replaced(context: object) -> None:
    client = Mock()
    with pytest.raises(DeepSeekError) as caught:
        IntentParser(client, create_default_registry()).parse("test", context=context)
    assert caught.value.code == "LLM_INPUT_ERROR"
    client.chat.assert_not_called()


def test_missing_defaults_rejected_not_locally_filled(catalog: dict) -> None:
    data = pca_fixture()
    data.update(skill_name="rnaseq.volcano", missing_parameters=["deg_result_file", "output_dir"])
    with pytest.raises(DeepSeekError) as caught:
        parse_intent(fixture_response(json.dumps(data)), catalog, "volcano", {})
    assert caught.value.code == "LLM_INTENT_VALIDATION_ERROR"
    assert data["parameters"] == {}


@pytest.mark.parametrize("name,value", [("alpha", 0), ("alpha", 1), ("alpha", True),
                                       ("alpha", "0.01"), ("lfc_threshold", -1)])
def test_existing_manifest_threshold_constraints(name: str, value: object, catalog: dict) -> None:
    data = pca_fixture()
    data.update(skill_name="rnaseq.deg", parameters={"alpha": .05, "lfc_threshold": 1},
                missing_parameters=["count_matrix_path", "metadata_path", "treatment", "control", "output_dir"])
    data["parameters"][name] = value
    with pytest.raises(DeepSeekError) as caught:
        parse_intent(fixture_response(json.dumps(data)), catalog, "test", {})
    assert caught.value.code == "LLM_INTENT_VALIDATION_ERROR"


def test_alternative_dependency_is_one_missing_item(catalog: dict) -> None:
    data = pca_fixture()
    data.update(skill_name="rnaseq.heatmap", parameters=defaults_for(catalog["rnaseq.heatmap"]["input_schema"]),
                missing_parameters=["deg_result_file", "metadata", "group_column", "output_dir", "normalized_expression|raw_counts"])
    intent = parse_intent(fixture_response(json.dumps(data)), catalog, "heatmap", {})
    assert "normalized_expression|raw_counts" in intent.missing_parameters
