"""Free, deterministic HTTP fixtures. Never evidence of a successful DeepSeek connection."""

import json
from pathlib import Path
import socket

import httpx
import pytest

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig, load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.check_connection import check_connection, main


FAKE_KEY = "sk-NOT-A-REAL-KEY-test-only"


def response_fixture() -> dict:
    return {"id": "fixture-response", "model": "fixture-model", "choices": [
        {"message": {"role": "assistant", "content": "fixture OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}


def test_request_and_response(caplog: pytest.LogCaptureFixture) -> None:
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + FAKE_KEY
        payload = json.loads(request.content)
        assert payload["messages"] == [{"role": "user", "content": "连接测试"}]
        assert payload["model"] == "deepseek-flash" and payload["stream"] is False
        assert payload["thinking"] == {"type": "disabled"} and payload["max_tokens"] == 64
        assert "tools" not in payload and "response_format" not in payload
        return httpx.Response(200, json=response_fixture())
    config = DeepSeekConfig(FAKE_KEY, base_url="https://api.deepseek.com/v1/")
    result = DeepSeekClient(config, transport=httpx.MockTransport(handler)).chat("连接测试")
    assert len(calls) == 1 and result.model == "fixture-model"
    assert result.content == "fixture OK" and result.usage["total_tokens"] == 7
    assert result.latency_seconds >= 0
    assert FAKE_KEY not in repr(config) + caplog.text + json.dumps(result.to_dict())


@pytest.mark.parametrize("status,code", [(400,"LLM_BAD_REQUEST"),(401,"LLM_AUTH_ERROR"),
    (402,"LLM_INSUFFICIENT_BALANCE"),(403,"LLM_FORBIDDEN"),(404,"LLM_NOT_FOUND"),
    (422,"LLM_BAD_REQUEST"),(429,"LLM_RATE_LIMIT"),(500,"LLM_SERVICE_ERROR"),
    (503,"LLM_SERVICE_ERROR"),(307,"LLM_HTTP_ERROR")])
def test_http_errors_no_retry_or_secret_leak(status: int, code: str, caplog: pytest.LogCaptureFixture) -> None:
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text="sensitive: " + FAKE_KEY, headers={"Location": "https://untrusted.invalid"})
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(handler)).chat("test")
    assert caught.value.code == code and len(calls) == 1
    assert FAKE_KEY not in json.dumps(caught.value.to_dict()) + caplog.text
    assert caught.value.__suppress_context__ is False  # HTTP bodies were never made exception text.


@pytest.mark.parametrize("kind,code", [(httpx.ReadTimeout,"LLM_TIMEOUT"),(httpx.ConnectError,"LLM_NETWORK_ERROR")])
def test_network_errors(kind: type, code: str) -> None:
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise kind("secret " + FAKE_KEY, request=request)
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(handler)).chat("test")
    assert caught.value.code == code and len(calls) == 1
    assert caught.value.__suppress_context__
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("kind", ["json", "choices", "message", "content", "model", "usage", "tokens", "tools", "secret", "nan"])
def test_invalid_responses(kind: str) -> None:
    data = response_fixture()
    if kind == "choices": data["choices"] = []
    elif kind == "message": data["choices"][0]["message"] = []
    elif kind == "content": data["choices"][0]["message"]["content"] = None
    elif kind == "model": data["model"] = ""
    elif kind == "usage": data["usage"] = []
    elif kind == "tokens": data["usage"]["total_tokens"] = True
    elif kind == "tools": data["choices"][0]["message"]["tool_calls"] = [{"id": "not-supported"}]
    elif kind == "secret": data["choices"][0]["message"]["content"] = FAKE_KEY
    content = b"not JSON" if kind == "json" else b'{"usage":NaN}' if kind == "nan" else json.dumps(data).encode()
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))).chat("test")
    assert caught.value.code == "LLM_RESPONSE_INVALID"


def test_missing_usage_is_not_fabricated() -> None:
    data = response_fixture()
    del data["usage"]
    result = DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))).chat("test")
    assert result.usage is None


def test_dotenv_and_environment_precedence(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(f'DEEPSEEK_API_KEY="{FAKE_KEY}"\nDEEPSEEK_MODEL=file-model\n', encoding="utf-8-sig")
    config = load_deepseek_config(path, environ={"DEEPSEEK_MODEL": "environment-model"})
    assert config.model == "environment-model" and config.api_key == FAKE_KEY
    with pytest.raises(DeepSeekError) as caught:
        load_deepseek_config(path, environ={"DEEPSEEK_API_KEY": ""})
    assert caught.value.code == "LLM_KEY_MISSING"


@pytest.mark.parametrize("changes", [{"api_key":""},{"api_key":"has space"},{"api_key":"bad\nheader"},
    {"base_url":"http://api.deepseek.com"},{"base_url":"https://other.invalid"},
    {"base_url":"https://api.deepseek.com@other.invalid"},{"base_url":"https://api.deepseek.com?key=secret"},
    {"base_url":"https://api.deepseek.com/beta"},{"model":""},{"model":FAKE_KEY},
    {"max_tokens":0},{"max_tokens":True},{"max_tokens":1025},{"timeout_seconds":float("nan")},
    {"timeout_seconds":0},{"timeout_seconds":121}])
def test_invalid_configuration(changes: dict) -> None:
    with pytest.raises(DeepSeekError):
        DeepSeekConfig(**{"api_key": FAKE_KEY, **changes})


@pytest.mark.parametrize("prompt", ["", None, "a" * 4001, FAKE_KEY])
def test_invalid_prompt_does_not_send(prompt: object) -> None:
    def must_not_run(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid input must not make an HTTP request")
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(FAKE_KEY), transport=httpx.MockTransport(must_not_run)).chat(prompt)
    assert caught.value.code == "LLM_INPUT_ERROR"


def test_default_network_guard() -> None:
    with pytest.raises(AssertionError, match="Network blocked"):
        socket.create_connection(("api.deepseek.com", 443))


def test_missing_key_check_is_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    report = check_connection(tmp_path / "absent.env")
    assert report["status"] == "failed" and report["error_code"] == "LLM_KEY_MISSING"
    assert report["model"] is None and report["usage"] is None
    assert report["api_response_received"] is False
    output = tmp_path / "reports"
    assert main(["--env-file", str(tmp_path / "absent.env"), "--output-dir", str(output)]) == 1
    saved = json.loads(next(output.glob("*/connection.json")).read_text(encoding="utf-8"))
    assert saved["error_code"] == "LLM_KEY_MISSING"


def test_length_response_not_reported_as_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bulk_rnaseq.llm.client import ChatResponse
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setattr(DeepSeekClient, "chat", lambda self, prompt: ChatResponse("fixture", "fixture-model", "partial", "length", None, .1))
    report = check_connection(tmp_path / "missing.env")
    assert report["status"] == "failed" and report["error_code"] == "LLM_INCOMPLETE_RESPONSE"
    assert report["api_response_received"] is True
