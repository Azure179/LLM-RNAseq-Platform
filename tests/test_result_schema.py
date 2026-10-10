"""Schema-only examples and existing mock fixtures; no fabricated analysis evidence."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulk_rnaseq import SkillResult
from bulk_rnaseq.cli import main
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.deg import run_deg
from bulk_rnaseq.skills.go_enrichment import run_go_enrichment
from bulk_rnaseq.skills.heatmap import run_heatmap
from bulk_rnaseq.skills.kegg_enrichment import run_kegg_enrichment
from bulk_rnaseq.skills.pca import run_pca
from bulk_rnaseq.skills.volcano import run_volcano
from test_execution import fake_process


def test_json_roundtrip_and_snapshot(tmp_path: Path) -> None:
    inputs = {"source": tmp_path / "input", "parameters": {"threshold": .05}}
    metrics = {"custom_metric": {"value": 42}}
    result = SkillResult("succeeded", "rnaseq.deg", "schema-test", inputs=inputs, summary=metrics)
    inputs["parameters"]["threshold"] = 1
    metrics["custom_metric"]["value"] = 0
    payload = result.to_dict()
    assert payload["inputs"]["source"] == str(tmp_path / "input")
    assert payload["inputs"]["parameters"]["threshold"] == .05
    assert payload["summary"]["custom_metric"]["value"] == 42
    assert SkillResult.from_dict(json.loads(json.dumps(payload))).to_dict() == payload
    payload["summary"].clear()
    assert result.summary


@pytest.mark.parametrize("changes", [
    {"status": "success"}, {"status": "pending"}, {"skill_name": ""}, {"skill_name": "rnaseq."},
    {"run_id": ""}, {"run_id": None}, {"inputs": []}, {"outputs": {"plot": 12}},
    {"summary": {"value": float("nan")}}, {"metadata": {"value": float("inf")}},
    {"error_code": "FAILED"}, {"message": "not a success"}, {"details": {"reason": "failure"}},
    {"warnings": {}}, {"warnings": ["plain text"]}, {"warnings": [{"code": "X"}]},
    {"warnings": [{"code": "X", "message": "test", "details": []}]},
])
def test_invalid_success_contract(changes: dict) -> None:
    with pytest.raises(ValueError):
        SkillResult(**{"status": "succeeded", "skill_name": "rnaseq.pca", "run_id": "test", **changes})


@pytest.mark.parametrize("changes", [
    {"error_code": None}, {"message": ""}, {"summary": {"n": 1}}, {"outputs": {"plot": "fake.png"}},
])
def test_invalid_failure_contract(changes: dict) -> None:
    with pytest.raises(ValueError):
        SkillResult(**{"status": "failed", "skill_name": "rnaseq.pca", "run_id": None,
                       "error_code": "INVALID_INPUT", "message": "Bad input", **changes})


def test_failure_before_run_and_r_diagnostics() -> None:
    error = SkillError("RSCRIPT_FAILED", "R failed", return_code=7, stdout="partial", stderr="diagnostic",
                       operation="kegg_enrichment", details={"stage": "enrichment"})
    result = error.to_skill_result()
    assert result.status == "failed" and result.run_id is None
    assert result.skill_name == "rnaseq.kegg" and result.error_code == "RSCRIPT_FAILED"
    assert result.outputs == {} and result.summary == {}
    assert result.metadata["processes"] == [{"return_code": 7, "stdout": "partial", "stderr": "diagnostic"}]
    assert error.to_dict()["error"] == {"code": result.error_code, "message": result.message, "details": result.details}


def test_legacy_migration_preserves_special_fields(tmp_path: Path) -> None:
    legacy = {"schema_version": "1.0", "operation": "pca", "status": "succeeded", "run_id": "legacy",
              "output_dir": str(tmp_path), "inputs": {"count_matrix": "counts.csv"},
              "parameters": {"blind": True}, "metrics": {"pc1_variance": 40, "special": {"n": 7}},
              "artifacts": {"plot": "pca_plot.png"}, "error": None,
              "warnings": [{"code": "EXAMPLE", "message": "Schema fixture", "count": 3}],
              "provenance": {"r_script_sha256": "a", "vst_module_sha256": "b"},
              "process": {"return_code": 0, "stdout": "", "stderr": ""},
              "custom_extension": {"retain": True}}
    old_file = tmp_path / "old-summary.json"
    old_file.write_text(json.dumps(legacy), encoding="utf-8")
    original = old_file.read_bytes()
    result = SkillResult.from_legacy_summary(json.loads(old_file.read_text(encoding="utf-8")))
    assert old_file.read_bytes() == original
    assert result.summary == legacy["metrics"]
    assert result.metadata["custom_extension"] == {"retain": True}
    assert result.metadata["provenance"]["r_scripts_sha256"] == {"transform_pca.R": "a", "vst_common.R": "b"}
    assert result.outputs == {"plot": str(tmp_path / "pca_plot.png")}
    assert result.warnings == [{"code": "EXAMPLE", "message": "Schema fixture", "details": {"count": 3}}]
    compatible = result.to_dict(include_legacy=True)
    assert compatible["metrics"] == legacy["metrics"]
    assert compatible["provenance"] == legacy["provenance"]
    assert compatible["warnings"][0]["count"] == 3
    assert compatible["artifacts"] == legacy["artifacts"]
    assert compatible["schema_version"] == "2.0"


def test_unsupported_version_and_missing_fields() -> None:
    with pytest.raises(ValueError, match="Unsupported schema"):
        SkillResult.from_dict({"schema_version": "1.0"})
    with pytest.raises(ValueError, match="Missing result fields"):
        SkillResult.from_dict({"schema_version": "2.0"})


@pytest.mark.parametrize("skill", ["deg", "pca", "volcano", "heatmap", "go", "kegg"])
def test_all_skill_failure_contracts(skill: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing, output = tmp_path / "missing.csv", tmp_path / "out"
    process = Mock()
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    with pytest.raises(SkillError) as caught:
        if skill == "deg":
            run_deg(missing, missing, "treatment", "control", output)
        elif skill == "pca":
            run_pca(missing, missing, "group", output)
        elif skill == "volcano":
            run_volcano(missing, output)
        elif skill == "heatmap":
            run_heatmap(missing, missing, "group", output, normalized_expression=missing)
        elif skill == "go":
            run_go_enrichment(missing, missing, output, universe=missing)
        else:
            run_kegg_enrichment(missing, missing, output, universe=missing)
    process.assert_not_called()
    error = caught.value
    persisted = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    assert persisted == error.to_dict()
    result = SkillResult.from_dict(persisted)
    assert result.skill_name == "rnaseq." + skill and result.run_id
    assert result.error_code == "FILE_NOT_FOUND" and result.status == "failed"
    assert result.outputs == {} and result.summary == {}
    assert str(missing) in result.inputs.values()
    assert "Traceback" not in json.dumps(persisted)
    assert not (output / "summary.json").exists()


@pytest.mark.parametrize("skill", ["deg", "pca", "volcano", "heatmap", "go", "kegg"])
def test_cli_argument_failure_contract(skill: str, capsys: pytest.CaptureFixture) -> None:
    assert main([skill]) == 2
    captured = capsys.readouterr()
    result = SkillResult.from_dict(json.loads(captured.out))
    assert result.skill_name == "rnaseq." + skill
    assert result.error_code == "INVALID_ARGUMENT" and result.run_id is None
    assert "Traceback" not in captured.out + captured.err


def test_cli_success_matches_published_summary(input_files: tuple[Path, Path], tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_process)
    output = tmp_path / "cli"
    assert main(["deg", "--counts", str(input_files[0]), "--metadata", str(input_files[1]),
                 "--treatment", "treatment", "--control", "control", "--output-dir", str(output)]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted == json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert emitted["summary"]["significant_genes"] == 1


def test_return_does_not_reread_disk(input_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_process)
    result = run_deg(*input_files, "treatment", "control", tmp_path / "out")
    expected = result.to_dict()
    result.summary_json.unlink()  # Own temporary test output, never user data.
    assert result.to_dict() == expected
