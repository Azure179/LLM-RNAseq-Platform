"""Static descriptions must agree with Python contracts; no dispatcher is introduced."""

from importlib import import_module
from importlib.resources import files
import inspect
import json
from pathlib import Path
import tomllib

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
import pytest

from bulk_rnaseq import SkillResult
from bulk_rnaseq.exceptions import SkillError
from test_execution import fake_process
from test_pca import fake_pca_process
from test_volcano import deg_file, fake_renderer
from test_heatmap import heatmap_inputs, renderer
from test_go import go_files, fake_go
from test_kegg import kegg_files, fake_kegg


ROOT = Path(str(files("bulk_rnaseq.manifests")))
NAMES = ("deg", "pca", "volcano", "heatmap", "go", "kegg", "gsea")
TYPES = json.loads((ROOT / "schemas" / "input-types.schema.json").read_text(encoding="utf-8"))["$defs"]
SCHEMAS = [(path.as_uri(), Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))))
           for path in sorted((ROOT / "schemas").glob("*.json"))]
REFERENCES = Registry().with_resources(SCHEMAS)  # Local JSON Schema references, NOT a Skill Registry.


def manifest(name: str) -> dict:
    return json.loads((ROOT / f"rnaseq.{name}.json").read_text(encoding="utf-8"))


def validator(name: str, direction: str) -> Draft202012Validator:
    schema = manifest(name)[direction + "_schema"]
    if isinstance(schema, dict):
        schema = {**schema, "$id": (ROOT / f"rnaseq.{name}.json").as_uri()}
    return Draft202012Validator(schema, registry=REFERENCES)


def function(name: str):
    module, attribute = manifest(name)["entry_point"].split(":")
    return getattr(import_module(module), attribute)


def valid_inputs(name: str) -> dict:
    """Path placeholders only; schema checks do not read files or run analysis."""
    properties = manifest(name)["input_schema"]["properties"]
    result = {key: "fixture" for key in manifest(name)["input_schema"]["required"]}
    if name == "deg":
        result.update(treatment="treatment", control="control")
    if "universe" in properties:
        result["universe"] = "background.txt"
    if name == "heatmap":
        result["normalized_expression"] = "vst.csv"
    return result


def test_complete_inventory_and_packaging() -> None:
    assert {p.name for p in ROOT.glob("rnaseq.*.json")} == {f"rnaseq.{name}.json" for name in NAMES}
    project = tomllib.loads((ROOT.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = project["tool"]["setuptools"]["package-data"]["bulk_rnaseq.manifests"]
    packaged = {p for pattern in patterns for p in ROOT.glob(pattern)}
    assert set(ROOT.rglob("*.json")) <= packaged


@pytest.mark.parametrize("name", NAMES)
def test_required_fields_and_schema_validity(name: str) -> None:
    data = manifest(name)
    assert {"name", "version", "description", "category", "input_schema", "output_schema", "requirements",
            "dependencies", "limitations", "use_when", "do_not_use_when", "raw_count_requirement"} <= data.keys()
    assert data["name"] == "rnaseq." + name
    assert data["manifest_schema_version"] == "1.0"
    assert data["description"] and data["limitations"] and data["use_when"] and data["do_not_use_when"]
    for direction in ("input", "output"):
        Draft202012Validator.check_schema(data[direction + "_schema"])
    for _, resource in SCHEMAS:
        Draft202012Validator.check_schema(resource.contents)


@pytest.mark.parametrize("name", NAMES[:-1])
def test_signature_defaults_and_required_inputs(name: str) -> None:
    data = manifest(name)
    assert data["availability"] == {"status": "implemented", "callable": True}
    params = inspect.signature(function(name)).parameters
    schema = data["input_schema"]
    assert set(schema["properties"]) == set(params)
    assert set(schema["required"]) == {key for key, p in params.items() if p.default is inspect.Parameter.empty}
    for key, parameter in params.items():
        declaration = schema["properties"][key]
        if "$ref" in declaration:
            declaration = {**TYPES[declaration["$ref"].split("/")[-1]], **declaration}
        if parameter.default is not inspect.Parameter.empty:
            assert declaration["default"] == parameter.default
    v = validator(name, "input")
    v.validate(valid_inputs(name))
    with pytest.raises(ValidationError):
        v.validate({**valid_inputs(name), "unknown_argument": "do not silently ignore"})
    for key in schema["required"]:
        invalid = valid_inputs(name)
        del invalid[key]
        assert not v.is_valid(invalid)


@pytest.mark.parametrize("name", ("heatmap", "go", "kegg"))
def test_exactly_one_non_null_input(name: str) -> None:
    keys = ("normalized_expression", "raw_counts") if name == "heatmap" else ("universe", "analysis_genes")
    data = valid_inputs(name)
    for key in keys:
        data.pop(key, None)
    v = validator(name, "input")
    assert not v.is_valid(data)
    assert not v.is_valid({**data, keys[0]: None, keys[1]: None})
    assert not v.is_valid({**data, keys[0]: "a", keys[1]: "b"})
    for first, second in (keys, keys[::-1]):
        v.validate({**data, first: "a", second: None})


@pytest.mark.parametrize("name,key,valid,invalid", [
    ("deg", "alpha", [.05], [0, 1, True, "0.05"]),
    ("volcano", "padj_threshold", [.05], [0, 1, False]),
    ("heatmap", "padj_threshold", [.05], [0, 1]),
    ("go", "padj_threshold", [0, 1], [-.1, 1.1, True]),
    ("kegg", "pvalue_threshold", [0, 1], [-.1, 1.1]),
    ("volcano", "top_n_labels", [0, 10], [-1, 1.5, True]),
    ("heatmap", "top_n", [1, 50], [0, -1, 1.5, True]),
    ("go", "ontology", ["ALL", "BP", "MF", "CC"], ["all", "unknown", None]),
    ("deg", "lfc_threshold", [0, 1], [-1, True]),
    ("pca", "timeout", [1, 600], [0, -1, False]),
])
def test_parameter_constraints(name: str, key: str, valid: list, invalid: list) -> None:
    v = validator(name, "input")
    for value in valid:
        v.validate({**valid_inputs(name), key: value})
    for value in invalid:
        assert not v.is_valid({**valid_inputs(name), key: value})


def test_gsea_is_not_a_callable_promise() -> None:
    data = manifest("gsea")
    assert data["availability"] == {"status": "not_implemented", "callable": False}
    assert data["entry_point"] is None
    assert data["input_schema"] is False and data["output_schema"] is False
    for direction in ("input", "output"):
        assert not validator("gsea", direction).is_valid({})
    assert not (ROOT.parent / "skills" / "gsea.py").exists()


@pytest.mark.parametrize("name", NAMES[:-1])
def test_dependencies_point_to_existing_contracts(name: str) -> None:
    data = manifest(name)
    for direction in ("upstream", "downstream"):
        for edge in data["dependencies"][direction]:
            other = manifest(edge["skill"].removeprefix("rnaseq."))
            assert other["availability"]["callable"]
            source, target = (other, data) if direction == "upstream" else (data, other)
            assert edge["output"] in source["output_schema"]["then"]["properties"]["outputs"]["properties"]
            assert edge["input"] in target["input_schema"]["properties"]
    assert manifest("pca")["dependencies"]["downstream"] == []  # No exported VST artifact.


@pytest.mark.parametrize("name", NAMES[:-1])
def test_real_skill_return_matches_output_schema(name: str, request: pytest.FixtureRequest,
                                               tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "manifest-output"
    kwargs = {}
    if name in ("deg", "pca"):
        inputs = request.getfixturevalue("input_files")
        args = (*inputs, "treatment", "control", output) if name == "deg" else (*inputs, "condition", output)
        process = fake_process if name == "deg" else fake_pca_process
    elif name == "volcano":
        args, process = (request.getfixturevalue("deg_file"), output), fake_renderer
    elif name == "heatmap":
        expression, deg, metadata = request.getfixturevalue("heatmap_inputs")
        args, process = (deg, metadata, "cohort", output), renderer
        kwargs = {"normalized_expression": expression}
    else:
        query, mapping, universe = request.getfixturevalue(name + "_files")
        args, process = (query, mapping, output), fake_go if name == "go" else fake_kegg
        kwargs = {"universe": universe}
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    result = function(name)(*args, **kwargs)  # Existing fake R fixtures; real Python validation/publication.
    v = validator(name, "output")
    v.validate(result.to_skill_result().to_dict())
    v.validate(result.to_dict())
    v.validate(json.loads(result.summary_json.read_text(encoding="utf-8")))
    broken = result.to_skill_result().to_dict()
    broken["outputs"].pop(next(iter(broken["outputs"])))
    assert not v.is_valid(broken)
    broken = result.to_skill_result().to_dict()
    broken["skill_name"] = "rnaseq.wrong"
    assert not v.is_valid(broken)


@pytest.mark.parametrize("name", NAMES[:-1])
def test_failure_envelope_matches_schema(name: str) -> None:
    operation = {"deg": "differential_expression", "go": "go_enrichment", "kegg": "kegg_enrichment"}.get(name, name)
    error = SkillError("FILE_NOT_FOUND", "Input missing", operation=operation)
    v = validator(name, "output")
    v.validate(error.to_dict())
    v.validate(error.to_skill_result().to_dict())
    broken = error.to_dict()
    broken["outputs"] = {"invented": "not-an-output"}
    assert not v.is_valid(broken)
    broken = error.to_dict()
    broken["error_code"] = None
    assert not v.is_valid(broken)


def test_common_schema_version_matches_model() -> None:
    result = SkillResult("succeeded", "rnaseq.deg", "schema-fixture").to_dict()
    schema = dict(SCHEMAS)[(ROOT / "schemas" / "skill-result.schema.json").as_uri()].contents
    Draft202012Validator(schema).validate(result)
    assert schema["properties"]["schema_version"]["const"] == result["schema_version"]
