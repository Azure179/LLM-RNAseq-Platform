"""Phase 8 public contract guard; test expectations, not runtime Skill definitions."""

from collections import Counter
from pathlib import Path
import tomllib

import pytest

from bulk_rnaseq import __version__
from bulk_rnaseq.api.validation import FORBIDDEN_PARAMETERS
from bulk_rnaseq.api.routes import router as skill_router
from bulk_rnaseq.api.workflows import router as workflow_router
from bulk_rnaseq.api.assistant import router as assistant_router
from bulk_rnaseq.api.results import router as result_router
from bulk_rnaseq.api.customizations import router as customization_router
from test_api import client, no_execution


# Changing these requires an explicit compatibility decision and documentation.
ENDPOINTS = [
    ("get", "/ready", None, "ReadinessResponse"),  # Phase 9.4 additive, /health unchanged.
    ("get", "/health", None, "HealthResponse"),
    ("get", "/skills", None, "SkillsResponse"),
    ("get", "/skills/{skill_name}", None, "SkillDetail"),
    ("post", "/skills/{skill_name}/run", "SkillRunRequest", "SkillResultResponse"),
    ("post", "/workflow/plan", "WorkflowPlanRequest", "WorkflowPlanResponse"),
    ("post", "/workflow/run", "WorkflowRunRequest", "WorkflowResult"),
    ("post", "/assistant/analyze", "AssistantRequest", "AssistantResponse"),
    ("get", "/projects/{project_id}/results", None, "ProjectResults"),
    ("get", "/projects/{project_id}/results/{run_id}", None, "ResultDetail"),
    ("post", "/customizations/plan", "CustomizationPlanRequest", "CustomizationPlanResponse"),
    ("post", "/customizations/run", "CustomizationRunRequest", "CustomizationRunResponse"),
]
REQUEST_FIELDS = {
    "SkillRunRequest": {"project_id", "parameters", "source_run_id"},
    "WorkflowPlanRequest": {"project_id", "request"},
    "WorkflowRunRequest": {"project_id", "plan"},
    "AssistantRequest": {"project_id", "request", "request_id", "source_run_id"},
    "CustomizationPlanRequest": {"project_id", "request", "source_run_id"},
    "CustomizationRunRequest": {"project_id", "plan"},
}


def test_no_duplicate_endpoints_or_operation_ids(client):
    actual = [(method.lower(), route.path) for router in (skill_router, workflow_router, assistant_router,
        result_router, customization_router) for route in router.routes for method in route.methods]
    assert Counter(actual) == Counter((method, path) for method, path, _, _ in ENDPOINTS)
    document = client.get("/openapi.json").json()
    assert set(document["paths"]) == {path for _, path, _, _ in ENDPOINTS}
    operations = [document["paths"][path][method]["operationId"] for method, path, _, _ in ENDPOINTS]
    assert len(operations) == len(set(operations)) == 12


@pytest.mark.parametrize("method,path,request_model,response_model", ENDPOINTS)
def test_public_operation_contract(client, method, path, request_model, response_model):
    document = client.get("/openapi.json").json()
    operation = document["paths"][path][method]
    assert operation.get("description")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/" + response_model}
    for status in ("404", "409", "422", "500"):
        if status in operation["responses"]:
            assert operation["responses"][status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ApiErrorResponse"}
    if request_model:
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/" + request_model}
        schema = document["components"]["schemas"][request_model]
        assert set(schema["properties"]) == REQUEST_FIELDS[request_model]
        assert schema["additionalProperties"] is False
        assert not set(schema["properties"]) & FORBIDDEN_PARAMETERS


def test_status_and_error_envelopes_remain_distinct(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["WorkflowResult"]["properties"]["status"]["enum"] == ["succeeded", "failed", "partial"]
    assert schemas["AssistantResponse"]["properties"]["status"]["enum"] == [
        "success", "partial_success", "failed", "needs_input", "unsupported"]
    assert set(schemas["ApiErrorResponse"]["properties"]) == {"error"}
    assert set(schemas["ApiError"]["properties"]) == {"code", "message", "details", "request_id"}
    assert set(schemas["ApiError"]["required"]) == {"code", "message", "request_id"}


def test_business_schemas_are_generated_not_forked(client):
    operation = client.get("/openapi.json").json()["paths"]["/skills/{skill_name}/run"]["post"]
    definitions = operation["x-skill-parameters"]
    service = client.app.state.execution_service
    assert set(definitions) == set(service.registry.list_skills())
    for name, schema in definitions.items():
        assert schema == service.parameter_schema(name)
        assert not set(schema["properties"]) & FORBIDDEN_PARAMETERS
    state = client.app.state
    assert state.assistant_service.single is service
    assert state.assistant_service.workflow is state.workflow_service
    assert state.workflow_service.registry is state.customization_service.registry is service.registry
    assert state.workflow_service.manager is state.customization_service.manager is service.manager


def test_declared_and_runtime_versions_agree(client):
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project.open("rb") as handle:
        declared = tomllib.load(handle)["project"]["version"]
    assert declared == __version__ == client.get("/health").json()["version"]
    assert client.get("/openapi.json").json()["info"]["version"] == declared
