"""Registry contracts. Small executors below are explicit test doubles, not RNA-seq results."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulk_rnaseq import SkillResult
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.skills.deg import run_deg
from test_execution import fake_process
from test_pca import fake_pca_process
from test_volcano import deg_file, fake_renderer
from test_heatmap import heatmap_inputs, renderer
from test_go import go_files, fake_go
from test_kegg import kegg_files, fake_kegg


@pytest.fixture
def registry() -> SkillRegistry:
    return create_default_registry()


def toy_manifest() -> dict:
    return {
        "manifest_schema_version": "1.0", "name": "rnaseq.fixture", "version": "1.0.0",
        "description": "Explicit test double", "category": "test", "requirements": {},
        "dependencies": {}, "limitations": ["Tests only"], "entry_point": "DO_NOT_IMPORT:anything",
        "availability": {"status": "implemented", "callable": True},
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"},
                         "repetitions": {"type": "integer", "minimum": 1, "default": 1}},
                         "required": ["path"], "additionalProperties": False},
        "output_schema": {"$ref": "schemas/skill-result.schema.json"},
    }


def toy_executor(path: Path, repetitions: int = 1) -> SkillResult:
    assert isinstance(path, Path)
    return SkillResult("succeeded", "rnaseq.fixture", "test-double", inputs={"path": path},
                       summary={"repetitions": repetitions})


def test_register_lookup_and_snapshot() -> None:
    registry = SkillRegistry()
    manifest = toy_manifest()
    registry.register(manifest, toy_executor)
    manifest["input_schema"]["required"].clear()
    entry = registry.get("rnaseq.fixture")
    assert entry.name == "rnaseq.fixture" and entry.available
    assert entry.executor is toy_executor
    assert entry.input_schema["required"] == ["path"]
    copy = registry.get_manifest(entry.name)
    copy["input_schema"]["required"].clear()
    assert registry.get_input_schema(entry.name)["required"] == ["path"]
    assert registry.get_executor(entry.name) is toy_executor


def test_duplicate_does_not_replace() -> None:
    registry = SkillRegistry()
    registry.register(toy_manifest(), toy_executor)
    with pytest.raises(SkillError, match="already registered") as caught:
        registry.register(toy_manifest(), None)
    assert caught.value.code == "DUPLICATE_SKILL"
    assert registry.get_executor("rnaseq.fixture") is toy_executor


def test_list_and_disabled_skill(registry: SkillRegistry) -> None:
    assert registry.list_skills() == sorted(f"rnaseq.{name}" for name in ("deg", "pca", "volcano", "heatmap", "go", "kegg"))
    assert len(registry.list_skills(available_only=False)) == 7
    registry.list_skills().clear()
    assert len(registry.list_skills()) == 6
    assert registry.get_manifest("rnaseq.gsea")["availability"]["callable"] is False
    assert registry.get_input_schema("rnaseq.gsea") is False
    assert not registry.get("rnaseq.gsea").available
    with pytest.raises(SkillError) as caught:
        registry.get_executor("rnaseq.gsea")
    assert caught.value.code == "SKILL_UNAVAILABLE"
    failure = registry.execute("rnaseq.gsea", {})
    assert failure.status == "failed" and failure.error_code == "SKILL_UNAVAILABLE"


@pytest.mark.parametrize("name", ["rnaseq.unknown", "", "../bad", None, []])
def test_unknown(registry: SkillRegistry, name: object) -> None:
    with pytest.raises(SkillError) as caught:
        registry.get(name)
    assert caught.value.code == "UNKNOWN_SKILL"
    failure = registry.execute(name, {})
    assert failure.status == "failed" and failure.error_code == "UNKNOWN_SKILL"
    assert failure.run_id is None and not failure.outputs


def test_execute_path_conversion_and_defaults(tmp_path: Path) -> None:
    registry = SkillRegistry()
    registry.register(toy_manifest(), toy_executor)
    arguments = {"path": tmp_path / "never-read.txt"}
    result = registry.execute("rnaseq.fixture", arguments)
    assert isinstance(result, SkillResult) and result.status == "succeeded"
    assert result.summary == {"repetitions": 1}
    assert isinstance(arguments["path"], Path)  # Caller input unchanged.
    assert registry.execute("rnaseq.fixture", {"path": str(tmp_path), "repetitions": 2}).summary == {"repetitions": 2}


@pytest.mark.parametrize("payload", [{}, {"path": "a", "unknown": 1}, {"path": "a", "repetitions": 0},
                                     {"path": "a", "repetitions": True}, {"path": 1}, [], None,
                                     {"path": "a", "repetitions": float("nan")},
                                     {"path": "a", "repetitions": float("inf")}])
def test_invalid_input_does_not_execute(payload: object) -> None:
    called = Mock()
    def executor(path: Path, repetitions: int = 1) -> SkillResult:
        called()
        return toy_executor(path, repetitions)
    registry = SkillRegistry()
    registry.register(toy_manifest(), executor)
    result = registry.execute("rnaseq.fixture", payload)
    assert result.error_code == "INVALID_INPUT" and result.run_id is None
    called.assert_not_called()


@pytest.mark.parametrize("case", ["schema", "parameters", "required", "external_ref", "broken_ref", "availability", "nan"])
def test_invalid_registration_is_atomic(case: str) -> None:
    manifest = toy_manifest()
    if case == "schema": manifest["input_schema"]["type"] = "invalid"
    elif case == "parameters": manifest["input_schema"]["properties"]["unknown"] = {"type": "string"}
    elif case == "required": manifest["input_schema"]["required"] = []
    elif case == "external_ref": manifest["output_schema"] = {"$ref": "https://untrusted.invalid/schema"}
    elif case == "broken_ref": manifest["output_schema"] = {"$ref": "#/missing"}
    elif case == "availability": manifest["availability"]["callable"] = False
    else: manifest["extra"] = float("nan")
    registry = SkillRegistry()
    with pytest.raises(SkillError) as caught:
        registry.register(manifest, toy_executor)
    assert caught.value.code == "INVALID_MANIFEST"
    assert registry.list_skills(available_only=False) == []


@pytest.mark.parametrize("case,code", [("expected", "TEST_FAILURE"), ("unexpected", "INTERNAL_ERROR"),
                                      ("wrong_type", "INVALID_SKILL_RESULT"), ("wrong_name", "INVALID_SKILL_RESULT"),
                                      ("bad_details", "INTERNAL_ERROR")])
def test_executor_failures_are_safe(case: str, code: str, caplog: pytest.LogCaptureFixture) -> None:
    def executor(path: Path, repetitions: int = 1) -> SkillResult:
        if case == "expected": raise SkillError("TEST_FAILURE", "Expected fixture failure")
        if case == "unexpected": raise RuntimeError("private-secret-must-not-leak")
        if case == "bad_details": raise SkillError("TEST_FAILURE", "Bad fixture", details={"n": float("nan")})
        if case == "wrong_type": return {"status": "succeeded"}
        return SkillResult("succeeded", "rnaseq.wrong", "test-double")
    registry = SkillRegistry()
    registry.register(toy_manifest(), executor)
    result = registry.execute("rnaseq.fixture", {"path": "a"})
    assert result.error_code == code and result.skill_name == "rnaseq.fixture"
    assert result.outputs == {}
    assert "private-secret" not in json.dumps(result.to_dict()) + caplog.text
    assert "Traceback" not in caplog.text


def test_builtin_binding_uses_existing_function(registry: SkillRegistry) -> None:
    assert registry.get_executor("rnaseq.deg") is run_deg


@pytest.mark.parametrize("name", ["deg", "pca", "volcano", "heatmap", "go", "kegg"])
def test_all_existing_skills_execute(name: str, registry: SkillRegistry, request: pytest.FixtureRequest,
                                    tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "registry-output"
    arguments = {"output_dir": str(output)}
    if name in ("deg", "pca"):
        counts, metadata = request.getfixturevalue("input_files")
        if name == "deg":
            arguments.update(count_matrix_path=str(counts), metadata_path=str(metadata), treatment="treatment", control="control")
            process = fake_process
        else:
            arguments.update(count_matrix=str(counts), metadata=str(metadata), group_column="condition")
            process = fake_pca_process
    elif name == "volcano":
        arguments["deg_result_file"] = str(request.getfixturevalue("deg_file"))
        process = fake_renderer
    elif name == "heatmap":
        expression, deg, metadata = request.getfixturevalue("heatmap_inputs")
        arguments.update(deg_result_file=str(deg), metadata=str(metadata), group_column="cohort", normalized_expression=str(expression))
        process = renderer
    else:
        query, mapping, universe = request.getfixturevalue(name + "_files")
        arguments.update(gene_list=str(query), universe=str(universe))
        arguments["gene_to_go_mapping" if name == "go" else "gene_to_pathway_mapping"] = str(mapping)
        process = fake_go if name == "go" else fake_kegg
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    result = registry.execute("rnaseq." + name, arguments)
    assert isinstance(result, SkillResult) and result.status == "succeeded"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert result.to_dict() == SkillResult.from_dict(summary).to_dict()
    assert all(Path(path).is_file() for path in result.outputs.values())


def test_skill_failure_preserves_run_id_and_code(registry: SkillRegistry, tmp_path: Path) -> None:
    output = tmp_path / "failure"
    result = registry.execute("rnaseq.deg", {"count_matrix_path": str(tmp_path / "missing.csv"),
                             "metadata_path": "missing.csv", "treatment": "A", "control": "B", "output_dir": output})
    assert result.error_code == "FILE_NOT_FOUND" and result.run_id
    original = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    assert result.to_dict() == SkillResult.from_dict(original).to_dict()


@pytest.mark.integration
def test_registry_real_deg(demo_files: tuple[Path, Path], tmp_path: Path, request: pytest.FixtureRequest) -> None:
    result = create_default_registry().execute("rnaseq.deg", {
        "count_matrix_path": demo_files[0], "metadata_path": demo_files[1], "treatment": "treatment",
        "control": "control", "output_dir": tmp_path / "real-r", "rscript_executable": request.config.getoption("--rscript"),
    })
    assert result.status == "succeeded", result.to_dict()
    assert result.summary["genes_analyzed"] == 30
    assert result.metadata["provenance"]["deseq2_version"]
    assert Path(result.outputs["deg_csv"]).is_file()
