"""Single non-streaming DeepSeek requests. No dispatch, retries or biological inference."""

from dataclasses import dataclass
import json
import logging
from time import perf_counter
from typing import Any

import httpx

from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.tool_adapter import validate_tool_definitions


LOGGER = logging.getLogger(__name__)


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON is unsupported.")


HTTP_ERRORS = {
    400: ("LLM_BAD_REQUEST", "DeepSeek rejected the request parameters."),
    401: ("LLM_AUTH_ERROR", "DeepSeek authentication failed; check your local API key."),
    402: ("LLM_INSUFFICIENT_BALANCE", "DeepSeek account has insufficient balance."),
    403: ("LLM_FORBIDDEN", "DeepSeek denied access to this request."),
    404: ("LLM_NOT_FOUND", "DeepSeek endpoint or requested resource was not found."),
    422: ("LLM_BAD_REQUEST", "DeepSeek rejected the request format."),
    429: ("LLM_RATE_LIMIT", "DeepSeek rate limit reached; no automatic retry was attempted."),
}


@dataclass(frozen=True, slots=True)
class ChatResponse:
    response_id: str
    model: str
    content: str
    finish_reason: str
    usage: dict[str, Any] | None
    latency_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {"response_id": self.response_id, "model": self.model, "content": self.content,
                "finish_reason": self.finish_reason, "usage": self.usage, "latency_seconds": self.latency_seconds}


@dataclass(frozen=True, slots=True)
class ToolSchemaResponse:
    """Unexecuted assistant message, including raw tool-call argument strings."""

    response_id: str
    model: str
    message: dict[str, Any]
    finish_reason: str
    usage: dict[str, Any] | None
    latency_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {"response_id": self.response_id, "model": self.model, "message": self.message,
                "finish_reason": self.finish_reason, "usage": self.usage, "latency_seconds": self.latency_seconds}


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._transport = transport  # Dependency injection for free MockTransport tests only.

    def chat(self, user_message: str, *, system_prompt: str | None = None,
             json_mode: bool = False, max_tokens: int | None = None) -> ChatResponse:
        data, latency = self._request(user_message, system_prompt=system_prompt, json_mode=json_mode, max_tokens=max_tokens)
        choice = data["choices"][0]
        return ChatResponse(data["id"], data["model"], choice["message"]["content"], choice["finish_reason"], data.get("usage"), latency)

    def probe_tool_schema(self, user_message: str, tools: list[dict[str, Any]], *, max_tokens: int = 256) -> ToolSchemaResponse:
        """Explicit one-request API acceptance probe; no parsing/binding/executing tool arguments."""
        validate_tool_definitions(tools)
        data, latency = self._request(user_message, tools=tools, max_tokens=max_tokens)
        choice = data["choices"][0]
        return ToolSchemaResponse(data["id"], data["model"], choice["message"], choice["finish_reason"], data.get("usage"), latency)

    def request_tool_call(self, user_message: str, tools: list[dict[str, Any]], *, system_prompt: str,
                          max_tokens: int = 512) -> ToolSchemaResponse:
        """One auto-selection request. Execution belongs to the separately validated runner."""
        validate_tool_definitions(tools)
        data, latency = self._request(user_message, tools=tools, system_prompt=system_prompt, max_tokens=max_tokens)
        choice = data["choices"][0]
        return ToolSchemaResponse(data["id"], data["model"], choice["message"], choice["finish_reason"], data.get("usage"), latency)

    def sanitize_for_audit(self, value: Any) -> Any:
        """Redact the configured credential from JSON-safe local audit information."""
        def redact(item: Any) -> Any:
            if isinstance(item, str):
                return item.replace(self._config.api_key, "[REDACTED]")
            if isinstance(item, dict):
                return {redact(key): redact(child) for key, child in item.items()}
            if isinstance(item, list):
                return [redact(child) for child in item]
            return item
        return json.loads(json.dumps(redact(value), ensure_ascii=False, allow_nan=False))

    def _request(self, user_message: str, *, system_prompt: str | None = None,
                 json_mode: bool = False, max_tokens: int | None = None,
                 tools: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], float]:
        if not isinstance(user_message, str) or not user_message.strip() or len(user_message) > 4000:
            raise DeepSeekError("LLM_INPUT_ERROR", "Provide a non-empty message of at most 4000 characters.")
        if self._config.api_key in user_message:
            raise DeepSeekError("LLM_INPUT_ERROR", "Do not include API credentials in messages.")
        if system_prompt is not None and (not isinstance(system_prompt, str) or not system_prompt.strip()
                                          or len(system_prompt) > 40000 or self._config.api_key in system_prompt):
            raise DeepSeekError("LLM_INPUT_ERROR", "Invalid system prompt.")
        limit = self._config.max_tokens if max_tokens is None else max_tokens
        if type(limit) is not int or not 1 <= limit <= 1024 or type(json_mode) is not bool:
            raise DeepSeekError("LLM_INPUT_ERROR", "Invalid JSON mode or output token limit (1..1024).")
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt is not None else [])
        messages.append({"role": "user", "content": user_message})
        payload = {"model": self._config.model, "messages": messages,
                   "stream": False, "max_tokens": limit, "thinking": {"type": "disabled"}}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
            payload["temperature"] = 0
        if tools is not None:
            # Snapshot and check for accidental credential inclusion before sending.
            serialized = json.dumps(tools, ensure_ascii=False, allow_nan=False)
            if self.sanitize_for_audit(tools) != tools:
                raise DeepSeekError("LLM_INPUT_ERROR", "Do not include API credentials in tool definitions.")
            payload.update(tools=json.loads(serialized), tool_choice="auto", temperature=0)
        started = perf_counter()
        LOGGER.info("DeepSeek request started; max_tokens=%d; automatic retries=0", limit)
        try:
            with httpx.Client(transport=self._transport, timeout=self._config.timeout_seconds,
                              follow_redirects=False, trust_env=False) as client:
                response = client.post(self._config.base_url + "/chat/completions", json=payload,
                                       headers={"Authorization": "Bearer " + self._config.api_key,
                                                "Content-Type": "application/json"})
        except httpx.TimeoutException:
            raise DeepSeekError("LLM_TIMEOUT", "DeepSeek request timed out; no retry was attempted.") from None
        except httpx.RequestError:
            raise DeepSeekError("LLM_NETWORK_ERROR", "Unable to reach DeepSeek; check network access.") from None
        latency = round(perf_counter() - started, 6)
        LOGGER.info("DeepSeek response received: http_status=%d latency_seconds=%.3f", response.status_code, latency)
        if not 200 <= response.status_code < 300:
            fallback = ("LLM_SERVICE_ERROR", "DeepSeek service error; no retry was attempted.") if response.status_code >= 500 else ("LLM_HTTP_ERROR", "Unexpected HTTP response; redirects are not followed.")
            code, message = HTTP_ERRORS.get(response.status_code, fallback)
            # Never echo response bodies, headers, request objects, URLs or provider error messages.
            raise DeepSeekError(code, message, details={"http_status": response.status_code, "latency_seconds": latency})
        try:
            data = json.loads(response.content, parse_constant=_reject_constant)
            if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1:
                raise ValueError("Expected one choice")
            choice = data["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("Invalid choice")
            message = choice["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Expected assistant response")
            for value in (data["id"], data["model"], choice["finish_reason"]):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Missing response field")
            calls = message.get("tool_calls")
            if calls:
                if tools is None or not isinstance(calls, list) or choice["finish_reason"] != "tool_calls":
                    raise ValueError("Unexpected tool calls")
                if message.get("content") is not None and not isinstance(message["content"], str):
                    raise ValueError("Invalid content")
                for call in calls:
                    if call["type"] != "function" or not isinstance(call["id"], str) or not call["id"]:
                        raise ValueError("Invalid tool call envelope")
                    function = call["function"]
                    if not isinstance(function["name"], str) or not function["name"] or not isinstance(function["arguments"], str):
                        raise ValueError("Invalid function fields")
            elif not isinstance(message.get("content"), str) or not message["content"].strip() or choice["finish_reason"] == "tool_calls":
                raise ValueError("Missing text or tool calls")
            usage = data.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError("Invalid usage")
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if key in usage and (type(usage[key]) is not int or usage[key] < 0):
                        raise ValueError("Invalid token count")
            exposed = {"id": data["id"], "model": data["model"], "message": message,
                       "finish_reason": choice["finish_reason"], "usage": usage}
            if self.sanitize_for_audit(exposed) != exposed:
                raise ValueError("Credential reflected in response")
        except (ValueError, KeyError, TypeError, IndexError, UnicodeError):
            raise DeepSeekError("LLM_RESPONSE_INVALID", "DeepSeek returned an invalid or unsafe chat response.",
                                details={"http_status": response.status_code, "latency_seconds": latency}) from None
        return data, latency
