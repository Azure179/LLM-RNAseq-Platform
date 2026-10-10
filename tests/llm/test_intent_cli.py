"""Free manual entry-point checks: no live client is used."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent_schema import StructuredIntent
from bulk_rnaseq.llm.request_intent import main


def test_cli_valid_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.llm.request_intent.load_deepseek_config", Mock())
    intent = StructuredIntent(status="unsupported", skill_name=None, parameters={},
                              missing_parameters=[], message="Unsupported fixture")
    result = Mock(intent=intent, prompt_sha256="fixture")
    result.response.model = "fixture"
    result.response.usage = None
    result.response.latency_seconds = .1
    parse = Mock(return_value=result)
    monkeypatch.setattr("bulk_rnaseq.llm.request_intent.IntentParser.parse", parse)
    assert main(["mock request", "--output-dir", str(tmp_path)]) == 0
    report = json.loads(next(tmp_path.glob("*/intent.json")).read_text(encoding="utf-8"))
    assert report["intent"]["status"] == "unsupported"
    assert report["skill_execution_status"] == "not_executed"
    parse.assert_called_once_with("mock request", context=None)


def test_cli_api_error_is_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("bulk_rnaseq.llm.request_intent.load_deepseek_config",
                        Mock(side_effect=DeepSeekError("LLM_KEY_MISSING", "Configure your key.")))
    assert main(["mock request", "--output-dir", str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.glob("*/intent.json")).read_text(encoding="utf-8"))
    assert report["error_code"] == "LLM_KEY_MISSING"
    assert "Traceback" not in caplog.text


def test_cli_bad_context_before_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    load = Mock()
    monkeypatch.setattr("bulk_rnaseq.llm.request_intent.load_deepseek_config", load)
    assert main(["mock request", "--context-file", str(tmp_path / "absent.json"), "--output-dir", str(tmp_path)]) == 1
    load.assert_not_called()


def test_cli_unwritable_destination_before_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "file"
    target.touch()
    load = Mock()
    monkeypatch.setattr("bulk_rnaseq.llm.request_intent.load_deepseek_config", load)
    assert main(["mock request", "--output-dir", str(target)]) == 1
    load.assert_not_called()
