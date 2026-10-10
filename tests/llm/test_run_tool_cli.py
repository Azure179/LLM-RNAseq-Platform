"""Free CLI tests; no real client requests or Skill execution."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import DeepSeekConfig
from bulk_rnaseq.llm.run_tool import main
from bulk_rnaseq.llm.tool_runner import ToolExecutionResult


@pytest.mark.parametrize("status", ["succeeded", "validated", "needs_input", "unsupported", "failed"])
def test_cli_result_status(status: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "context.json"
    config.write_text(json.dumps({"project_root": str(tmp_path), "output_root": "runs"}), encoding="utf-8")
    monkeypatch.setattr("bulk_rnaseq.llm.run_tool.load_deepseek_config", Mock(return_value=DeepSeekConfig("sk-test-only")))
    run = Mock(return_value=ToolExecutionResult(request_id="fixture", status=status))
    monkeypatch.setattr("bulk_rnaseq.llm.run_tool.ToolRunner.run", run)
    assert main(["request", "--context", str(config), "--no-execute"]) == (1 if status == "failed" else 0)
    assert run.call_args.kwargs["execute"] is False


def test_cli_invalid_context_has_no_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    load = Mock()
    monkeypatch.setattr("bulk_rnaseq.llm.run_tool.load_deepseek_config", load)
    assert main(["request", "--context", str(tmp_path / "absent.json")]) == 1
    load.assert_not_called()
    assert "INVALID_EXECUTION_CONTEXT" in caplog.text and "Traceback" not in caplog.text


def test_cli_rejected_output_path_no_second_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "context.json"
    config.write_text(json.dumps({"project_root": str(tmp_path), "output_root": "../outside"}), encoding="utf-8")
    monkeypatch.setattr("bulk_rnaseq.llm.run_tool.load_deepseek_config", Mock(return_value=DeepSeekConfig("sk-test-only")))
    request = Mock()
    monkeypatch.setattr(DeepSeekClient, "request_tool_call", request)
    assert main(["request", "--context", str(config)]) == 1
    request.assert_not_called()


def test_nested_credential_redaction_handles_json_escaping() -> None:
    key = 'sk-fixture-"-\\-secret'
    client = DeepSeekClient(DeepSeekConfig(key))
    assert client.sanitize_for_audit({"input": ["prefix " + key]}) == {"input": ["prefix [REDACTED]"]}
