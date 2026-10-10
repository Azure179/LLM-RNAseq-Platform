"""Free Tool projection and schema-drift checks; never retrieves an executor."""

import json
from unittest.mock import Mock

from jsonschema import Draft202012Validator
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent_catalog import build_catalog
from bulk_rnaseq.llm.tool_adapter import ToolAdapter, validate_tool_definitions
from bulk_rnaseq.registry import SkillRegistry


@pytest.fixture(autouse=True)
def forbid_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Tool Adapter must not execute or retrieve an executor")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    monkeypatch.setattr(SkillRegistry, "get_executor", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)


@pytest.fixture
def adapter() -> ToolAdapter:
    return ToolAdapter(create_default_registry())


@pytest.mark.parametrize("name", ["deg", "pca", "volcano", "heatmap", "go", "kegg"])
def test_each_tool_and_drift(name: str, adapter: ToolAdapter) -> None:
    skill = "rnaseq." + name
    tool = adapter.build_tool_definition(skill)
    validate_tool_definitions([tool])
    function = tool["function"]
    assert function["name"] == "rnaseq_" + name
    assert adapter.tool_name_to_skill_name(function["name"]) == skill
    assert adapter.skill_name_to_tool_name(skill) == function["name"]
    source = build_catalog(create_default_registry())[skill]["input_schema"]
    projected = function["parameters"]
    managed = set(adapter.system_managed_parameters(skill))
    assert set(projected["required"]) == set(source["required"]) - managed
    for key, field in projected["properties"].items():
        assert field == source["properties"][key]  # Every bound/type/default unchanged.
    raw = create_default_registry().get_input_schema(skill)
    assert set(projected["properties"]) | managed == set(raw["properties"])
    assert not set(projected["properties"]) & managed
    manifest = create_default_registry().get_manifest(skill)
    for text in [manifest["description"], *manifest["use_when"], *manifest["do_not_use_when"]]:
        assert text in function["description"]
    assert "$ref" not in json.dumps(tool) and "x-path" not in json.dumps(tool)


def test_inventory_unique_and_snapshot(adapter: ToolAdapter) -> None:
    tools = adapter.build_all_tool_definitions()
    names = [tool["function"]["name"] for tool in tools]
    assert len(names) == len(set(names)) == 6
    assert {adapter.tool_name_to_skill_name(name) for name in names} == set(create_default_registry().list_skills())
    assert json.loads(json.dumps(tools, allow_nan=False)) == tools
    tools[0]["function"]["parameters"]["properties"].clear()
    assert adapter.build_all_tool_definitions()[0]["function"]["parameters"]["properties"]


@pytest.mark.parametrize("name,required,defaults", [
    ("deg", ["treatment", "control"], {"alpha": .05, "lfc_threshold": 1}),
    ("pca", ["group_column"], {}),
    ("volcano", [], {"padj_threshold": .05, "log2fc_threshold": 1, "top_n_labels": 10,
                     "label_genes": None, "color_up": "#D54E4E", "color_down": "#3974B5", "color_ns": "#A8A8A8",
                     "title": "Volcano plot", "figure_width": 1200, "figure_height": 900}),
    ("heatmap", ["group_column"], {"top_n": 50, "padj_threshold": .05, "log2fc_threshold": 1,
                     "gene_list": None, "ranking_method": "padj", "show_gene_names": True,
                     "title": "Top DEG expression heatmap", "figure_width": 1500, "figure_height": None}),
    ("go", [], {"pvalue_threshold": .05, "padj_threshold": .05, "ontology": "ALL", "mapping_sheet": None}),
    ("kegg", [], {"pvalue_threshold": 1, "padj_threshold": .05, "mapping_sheet": None}),
])
def test_required_and_defaults(name: str, required: list, defaults: dict, adapter: ToolAdapter) -> None:
    schema = adapter.build_tool_definition("rnaseq." + name)["function"]["parameters"]
    assert schema["required"] == required
    assert {key: field["default"] for key, field in schema["properties"].items() if "default" in field} == defaults
    Draft202012Validator(schema).validate({key: "supplied" for key in required})


@pytest.mark.parametrize("name", ["rnaseq.gsea", "rnaseq.unknown", "rnaseq_deg", "", None])
def test_unknown_or_unavailable_skill(name: str, adapter: ToolAdapter) -> None:
    with pytest.raises(DeepSeekError) as caught:
        adapter.build_tool_definition(name)
    assert caught.value.code == "LLM_TOOL_UNKNOWN_SKILL"


def test_unknown_tool(adapter: ToolAdapter) -> None:
    with pytest.raises(DeepSeekError) as caught:
        adapter.tool_name_to_skill_name("rnaseq.deg")
    assert caught.value.code == "LLM_TOOL_UNKNOWN_NAME"


@pytest.mark.parametrize("key", ["output_dir", "count_matrix_path", "metadata_path", "rscript_executable",
                                 "api_key", "command", "run_id", "temp_path", "env"])
def test_host_or_unknown_parameters_rejected(key: str, adapter: ToolAdapter) -> None:
    schema = adapter.build_tool_definition("rnaseq.deg")["function"]["parameters"]
    assert not Draft202012Validator(schema).is_valid({"treatment": "OT", "control": "T", key: "untrusted"})


def test_future_unreviewed_parameter_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = build_catalog(create_default_registry())
    catalog["rnaseq.deg"]["input_schema"]["properties"]["api_key"] = {"type": "string"}
    monkeypatch.setattr("bulk_rnaseq.llm.tool_adapter.build_catalog", Mock(return_value=catalog))
    with pytest.raises(DeepSeekError) as caught:
        ToolAdapter(create_default_registry())
    assert caught.value.code == "LLM_TOOL_POLICY_ERROR"


def test_name_collision_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = create_default_registry()
    entry = build_catalog(registry)["rnaseq.pca"]
    monkeypatch.setattr("bulk_rnaseq.llm.tool_adapter.build_catalog", Mock(return_value={"rnaseq.a": entry, "rnaseq_a": entry}))
    monkeypatch.setattr(registry, "get_manifest", Mock(return_value=registry.get_manifest("rnaseq.pca")))
    with pytest.raises(DeepSeekError):
        ToolAdapter(registry)


@pytest.mark.parametrize("fault", ["duplicate", "name", "description", "required", "default", "schema", "extra"])
def test_invalid_definition_rejected(fault: str, adapter: ToolAdapter) -> None:
    tool = adapter.build_tool_definition("rnaseq.deg")
    function = tool["function"]
    tools = [tool]
    if fault == "duplicate": tools.append(tool)
    elif fault == "name": function["name"] = "a.b"
    elif fault == "description": function["description"] = ""
    elif fault == "required": function["parameters"]["required"].append("absent")
    elif fault == "default": function["parameters"]["properties"]["alpha"]["default"] = -1
    elif fault == "schema": function["parameters"]["type"] = "invalid"
    else: function["strict"] = True
    with pytest.raises(DeepSeekError) as caught:
        validate_tool_definitions(tools)
    assert caught.value.code == "LLM_TOOL_SCHEMA_ERROR"
