"""Offline HTTP security regressions; no credentials, live provider, R or real data."""

import json
import logging
from uuid import UUID

import pytest
from starlette.exceptions import HTTPException

from bulk_rnaseq.api.schemas import ApiErrorResponse
from bulk_rnaseq.api.security import safe_value, request_security
from bulk_rnaseq.api.validation import FORBIDDEN_PARAMETERS
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.workspace.result_manager import ResultManager
from test_api_execution import api, run
from test_api_workflow import plan, deg, volcano
from test_api_workflow import planner_fixture
from test_api_results_customizations import custom, proposal


def assert_error(response, code=None):
    assert response.status_code >= 400, response.text
    value = ApiErrorResponse.model_validate(response.json())
    assert set(response.json()) == {"error"}
    assert value.error.request_id == response.headers["x-request-id"]
    assert UUID(value.error.request_id)
    if code:
        assert value.error.code == code, response.text
    return value.error


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/skills/unknown", None), ("get", "/projects/absent/results", None),
    ("get", "/projects/demo/results/missing", None),
    ("post", "/skills/rnaseq.deg/run", {}), ("post", "/workflow/plan", {}),
    ("post", "/workflow/run", {}), ("post", "/assistant/analyze", {}),
    ("post", "/customizations/plan", {}), ("post", "/customizations/run", {}),
    ("post", "/health", None), ("get", "/absent", None),
])
def test_error_contract_all_endpoints(api, method, path, payload):
    response = api[0].request(method, path, **({"json": payload} if payload is not None else {}))
    assert_error(response)
    assert not api[2]


@pytest.mark.parametrize("key", sorted(FORBIDDEN_PARAMETERS))
def test_host_parameters_rejected_before_execution(api, key):
    response = run(api, params={"treatment": "treatment", "control": "control", key: "not-a-path"})
    assert_error(response, "FORBIDDEN_PARAMETER")
    assert response.status_code == 422 and not api[2]


@pytest.mark.parametrize("path", ["/workflow/plan", "/assistant/analyze", "/customizations/plan"])
def test_unknown_envelope_and_forbidden_nested(api, path):
    response = api[0].post(path, json={"project_id": "demo", "request": "PCA", "unexpected": True})
    assert_error(response, "UNKNOWN_PARAMETER")
    response = api[0].post(path, json={"project_id": "demo", "request": "PCA", "unexpected": {"output_dir": "x"}})
    assert_error(response, "FORBIDDEN_PARAMETER")
    assert not api[2]


@pytest.mark.parametrize("value", ["../secret", "..\\..\\secret", "%2e%2e/secret", "%252e%252e%252fsecret",
    "C:\\Users\\private\\x", "/etc/passwd", "file:///etc/passwd", ".env", "runtime/private.json"])
def test_paths_rejected_in_user_text_and_source(api, value):
    for path in ("/workflow/plan", "/customizations/plan", "/assistant/analyze"):
        response = api[0].post(path, json={"project_id": "demo", "request": "read " + value})
        assert_error(response)
        assert value not in response.text
    response = api[0].post("/customizations/plan", json={"project_id": "demo", "request": "red", "source_run_id": value})
    assert_error(response)
    assert not api[2]


@pytest.mark.parametrize("query,code", [({"unknown": "x"}, "UNKNOWN_PARAMETER"),
    ({"output_dir": "x"}, "FORBIDDEN_PARAMETER"), ({"status": "anything"}, "INVALID_REQUEST"),
    ({"limit": "1.5"}, "INVALID_REQUEST"), ({"limit": "1.0"}, "INVALID_REQUEST"), ({"skill_name": " "}, "INVALID_REQUEST")])
def test_query_validation(api, query, code):
    assert_error(api[0].get("/projects/demo/results", params=query), code)


@pytest.mark.parametrize("params", [{"alpha": True}, {"alpha": "0.05"}, {"alpha": -1},
    {"lfc_threshold": -1}, {"treatment": " "}, {"treatment": "control"}, {"unknown": 5}])
def test_skill_parameter_constraints(api, params):
    args = {"treatment": "treatment", "control": "control", **params}
    assert_error(run(api, params=args))
    assert not api[2]


@pytest.mark.parametrize("top", [0, -1, 2.0, "2", True])
def test_top_n_strict_before_dependency_resolution(api, top):
    response = run(api, "heatmap", {"top_n": top, "group_column": "group"})
    assert_error(response, "INVALID_PARAMETER")
    assert not api[2]


def test_invalid_workflow_no_execution(api):
    for kind, code in (("cycle", "WORKFLOW_CYCLE_DETECTED"), ("binding", "INVALID_OUTPUT_BINDING"),
                       ("host", "FORBIDDEN_PARAMETER"), ("unknown", "UNKNOWN_PARAMETER")):
        payload = plan(deg(), volcano())
        if kind == "cycle":
            payload["steps"][0]["depends_on"] = ["volcano"]
        elif kind == "binding":
            payload["steps"][1]["input_bindings"]["deg_result_file"]["output"] = "nonexistent"
        else:
            payload["steps"][0]["parameters"]["command" if kind == "host" else "typo"] = "x"
        assert_error(api[0].post("/workflow/run", json={"project_id": "demo", "plan": payload}), code)
    assert not api[2]


@pytest.mark.parametrize("field", ["output_dir", "command", "python_executable"])
def test_model_generated_workflow_not_trusted(api, monkeypatch, field):
    step = deg()
    step["parameters"][field] = "untrusted"
    planner_fixture(api, monkeypatch, {"status": "ready", "steps": [step]})
    response = api[0].post("/workflow/plan", json={"project_id": "demo", "request": "以control为对照比较treatment"})
    assert_error(response)
    assert response.status_code == 502 and not api[2]


def test_missing_fields_are_actionable_without_raw_input(api):
    response = api[0].post("/assistant/analyze", json={"project_id": 12})
    error = assert_error(response)
    assert {item["field"] for item in error.details} == {"project_id", "request"}
    assert all(set(item) == {"field", "message"} for item in error.details)


def test_approved_customization_cannot_add_code(custom):
    prepared = proposal(custom).json()["plan"]
    prepared["decision"]["mode"] = "python_execute"
    assert_error(custom[0][0].post("/customizations/run", json={"project_id": "demo", "plan": prepared}), "INVALID_CUSTOMIZATION_PLAN")
    prepared["decision"]["mode"] = "parameter_override"
    prepared["decision"]["parameters"]["command"] = "install something"
    assert_error(custom[0][0].post("/customizations/run", json={"project_id": "demo", "plan": prepared}), "FORBIDDEN_PARAMETER")
    assert len(custom[0][2]) == 1


@pytest.mark.parametrize("mode", ["exception", "domain", "http"])
def test_secrets_tracebacks_and_logs(api, monkeypatch, caplog, mode):
    secret = "opaque-test-secret-875938"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    def broken(*args, **kwargs):
        logging.getLogger("bulk_rnaseq.security_fixture").error("diagnostic %s C:/Users/private/.env", secret)
        if mode == "domain":
            raise SkillError("INVALID_PARAMETER", secret, details={"errors": [{"message": secret,
                "nested": [{"Authorization": "Bearer " + secret}, {"stdout": "Traceback"}], "path": "/home/private/input"}]})
        if mode == "http":
            raise HTTPException(500, detail=secret, headers={"secret": secret})
        raise RuntimeError("Traceback: " + secret)
    monkeypatch.setattr(api[0].app.state.execution_service, "execute", broken)
    logger = api[0].app.state.logger
    logger.addHandler(caplog.handler)
    try:
        response = run(api)
    finally:
        logger.removeHandler(caplog.handler)
    assert_error(response, "INVALID_PARAMETER" if mode == "domain" else "INTERNAL_SERVER_ERROR")
    assert all(value not in response.text + caplog.text for value in (secret, "Traceback", "C:/Users", "/home/private"))
    assert "duration_seconds=" in caplog.text and response.headers["x-request-id"] in caplog.text
    assert request_security.get() is None


def test_secret_in_success_warning_and_no_global_logging_change(api, monkeypatch):
    secret = "opaque-success-secret-954923"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    execute = api[0].app.state.skill_registry.execute
    def with_warning(*args, **kwargs):
        result = execute(*args, **kwargs)
        result.warnings.append({"code": "FIXTURE", "message": secret, "details": {"Authorization": "Bearer " + secret}})
        return result
    monkeypatch.setattr(api[0].app.state.skill_registry, "execute", with_warning)
    response = run(api)
    assert response.status_code == 200 and secret not in response.text
    record = logging.getLogRecordFactory()("outside", logging.INFO, "", 1, "C:/outside/path", (), None)
    assert record.getMessage() == "C:/outside/path"  # Streamlit/CLI outside API unaffected.


def test_framework_exception_header_is_not_an_escape(api, monkeypatch):
    def failure(*args, **kwargs):
        raise HTTPException(405, headers={"Allow": "Bearer hidden-header", "Authorization": "Bearer hidden-header"})
    monkeypatch.setattr(api[0].app.state.execution_service, "execute", failure)
    response = run(api)
    assert_error(response, "METHOD_NOT_ALLOWED")
    assert "hidden-header" not in str(response.headers) + response.text


def test_cross_project_reads_and_header_identity(api):
    result = run(api).json()
    foreign = api[0].get("/projects/empty/results/" + result["run_id"], headers={"X-Request-ID": "untrusted-client-id"})
    error = assert_error(foreign, "RESULT_NOT_FOUND")
    assert error.request_id != "untrusted-client-id"
    assert api[0].get("/projects/empty/results").json()["results"] == []
    assert ResultManager(api[1], "demo").get_run(result["run_id"]).run_id == result["run_id"]


def test_all_openapi_errors_reference_single_model(api):
    document = api[0].get("/openapi.json").json()
    for path, operations in document["paths"].items():
        for operation in operations.values():
            for status, response in operation["responses"].items():
                if int(status) >= 400:
                    assert response["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ApiErrorResponse"}, (path, status)
    assert set(document["components"]["schemas"]["ApiError"]["properties"]) == {"code", "message", "details", "request_id"}


@pytest.mark.parametrize("value", ["/home/user/private", "C:\\Users\\private", "../secret", "%2e%2e/secret",
    "runtime/secret", ".env", "open '.env'", "token=private", "Authorization: Bearer hidden", "Traceback File hidden"])
def test_recursive_sanitizer(value):
    assert safe_value({"message": [{"nested": value}], "password": "invisible"}) == {"message": [{"nested": "[redacted]"}]}
    assert safe_value("results/run123/volcano_plot.png") == "results/run123/volcano_plot.png"
