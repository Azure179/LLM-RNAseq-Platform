"""One optional paid tools-array acceptance probe. No argument dispatch or Skill execution."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.tool_adapter import ToolAdapter
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import write_json


@pytest.mark.llm
def test_real_deepseek_tool_schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Phase 6.2 must not retrieve or execute a Skill")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    monkeypatch.setattr(SkillRegistry, "get_executor", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    destination = Path(request.config.getoption("--llm-report-dir")) / datetime.now(timezone.utc).strftime("tools_%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    user_input = "Compare OT and T differential expression. OT is treatment and T is control."
    adapter = ToolAdapter(create_default_registry())
    tools = adapter.build_all_tool_definitions()
    write_json(destination / "tool_definitions.json", {"tools": tools})
    report = {"test_case": "phase_6_2_schema_acceptance", "user_input": user_input,
              "n_tools": len(tools), "tool_names": [tool["function"]["name"] for tool in tools],
              "tools_sha256": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest(),
              "status": "failed", "schema_accepted": False, "skill_execution_status": "not_executed"}
    try:
        client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
        response = client.probe_tool_schema(user_input, tools)
        report.update(response.to_dict(), schema_accepted=True)
        assert response.finish_reason in {"stop", "tool_calls"}, "Incomplete provider response"
        # This phase checks acceptance only. Arguments are neither parsed nor executed.
        report["status"] = "succeeded"
    except DeepSeekError as error:
        report.update(error.to_dict())
        pytest.fail(f"{error.code}: {error}", pytrace=False)
    finally:
        write_json(destination / "tool_schema_acceptance.json", report)
