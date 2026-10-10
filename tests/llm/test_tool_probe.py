"""Mock HTTP acceptance checks, never evidence of a real DeepSeek response."""

import json

import httpx
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.tool_adapter import ToolAdapter


FAKE_KEY = "sk-fixture-not-real-tool-probe"


def tool_fixture() -> dict:
    return {"id": "fixture", "model": "fixture-model", "choices": [{"finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "fixture-call", "type": "function",
            "function": {"name": "rnaseq_deg", "arguments": '{"treatment":"OT","control":"T"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def test_tool_probe_passes_array_without_execution() -> None:
    tools = ToolAdapter(create_default_registry()).build_all_tool_definitions()
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["tools"] == tools and payload["tool_choice"] == "auto"
        assert payload["max_tokens"] == 256 and "response_format" not in payload
        assert len(payload["messages"]) == 1 and payload["messages"][0]["role"] == "user"
        return httpx.Response(200, json=tool_fixture())
    response = DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(handler)).probe_tool_schema("fixture request", tools)
    assert len(calls) == 1 and response.finish_reason == "tool_calls"
    assert response.to_dict()["message"] == tool_fixture()["choices"][0]["message"]


@pytest.mark.parametrize("fault", ["secret", "invalid_type", "invalid_args", "missing_name"])
def test_probe_rejects_unsafe_response(fault: str) -> None:
    data = tool_fixture()
    call = data["choices"][0]["message"]["tool_calls"][0]
    if fault == "secret": call["function"]["arguments"] = FAKE_KEY
    elif fault == "invalid_type": call["type"] = "shell"
    elif fault == "invalid_args": call["function"]["arguments"] = {}
    else: del call["function"]["name"]
    client = DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)))
    with pytest.raises(DeepSeekError) as caught:
        client.probe_tool_schema("fixture request", ToolAdapter(create_default_registry()).build_all_tool_definitions())
    assert caught.value.code == "LLM_RESPONSE_INVALID"
    assert FAKE_KEY not in json.dumps(caught.value.to_dict())


def test_probe_accepts_text_and_leaves_arguments_unparsed() -> None:
    data = tool_fixture()
    data["choices"][0].update(finish_reason="stop", message={"role": "assistant", "content": "Need host inputs."})
    client = DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)))
    tools = ToolAdapter(create_default_registry()).build_all_tool_definitions()
    assert client.probe_tool_schema("fixture", tools).message["content"] == "Need host inputs."
    data = tool_fixture()
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not valid JSON"
    assert client.probe_tool_schema("fixture", tools).message["tool_calls"][0]["function"]["arguments"] == "not valid JSON"


@pytest.mark.parametrize("fault", ["invalid_schema", "secret"])
def test_probe_rejects_before_network(fault: str) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid schema/credential must never be sent")
    tools = ToolAdapter(create_default_registry()).build_all_tool_definitions()
    if fault == "secret": tools[0]["function"]["description"] = FAKE_KEY
    else: tools[0]["function"]["name"] = "bad.name"
    client = DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(forbidden))
    with pytest.raises(DeepSeekError):
        client.probe_tool_schema("fixture", tools)
