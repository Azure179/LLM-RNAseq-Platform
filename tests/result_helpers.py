"""Shared contract assertions, called with real Skill return objects, not invented results."""

import json
from pathlib import Path

from bulk_rnaseq import SkillResult
from bulk_rnaseq.models import ResultEnvelope


def assert_result_contract(result: ResultEnvelope, skill_name: str) -> None:
    canonical = result.to_skill_result()
    assert isinstance(canonical, SkillResult)
    assert canonical.skill_name == skill_name
    assert canonical.status == "succeeded" and canonical.run_id
    assert canonical.error_code is None and canonical.message is None and canonical.details == {}
    assert result.to_dict() == json.loads(Path(canonical.outputs["summary_json"]).read_text(encoding="utf-8"))
    assert canonical.summary == result.to_dict()["metrics"] == result.summary
    assert canonical.inputs["parameters"] == result.to_dict()["parameters"]
    assert canonical.metadata["processes"] and all(p["return_code"] == 0 for p in canonical.metadata["processes"])
    assert canonical.metadata["provenance"]["r_scripts_sha256"]
    assert all(Path(path).is_absolute() and Path(path).is_file() for path in canonical.outputs.values())
    assert canonical.outputs == result.outputs
    assert all(set(warning) == {"code", "message", "details"} for warning in canonical.warnings)
    encoded = json.dumps(canonical.to_dict(), allow_nan=False)
    assert SkillResult.from_dict(json.loads(encoded)).to_dict() == canonical.to_dict()
    # A consumer changing its own copy must not corrupt the returned run snapshot.
    canonical.summary.clear()
    assert result.summary == result.to_dict()["metrics"] and result.summary
