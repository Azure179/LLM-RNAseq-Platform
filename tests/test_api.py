"""HTTP transport tests: real Registry metadata, no network/R/Skill execution."""

import json
import socket
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import pytest

from bulk_rnaseq import __version__
from bulk_rnaseq.api.dependencies import get_skill_registry
from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.registry import SkillRegistry


@pytest.fixture(autouse=True)
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Metadata API must not execute analysis, load secrets or launch a process")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    monkeypatch.setattr(SkillRegistry, "get_executor", forbidden)
    monkeypatch.setattr("bulk_rnaseq.llm.client.DeepSeekClient._request", forbidden)
    monkeypatch.setattr("bulk_rnaseq.r_runner.run_rscript", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("dotenv.load_dotenv", forbidden)
    monkeypatch.setattr("dotenv.dotenv_values", forbidden)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(workspace_root=tmp_path / "workspace")) as connection:
        yield connection


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "llm-rnaseq-platform", "version": __version__}
    assert UUID(response.headers["x-request-id"])


def test_list_comes_from_registry(client: TestClient) -> None:
    response = client.get("/skills")
    assert response.status_code == 200
    registry = client.app.state.skill_registry
    assert [s["name"] for s in response.json()["skills"]] == registry.list_skills(available_only=False)
    for item in response.json()["skills"]:
        original = registry.get_manifest(item["name"])
        assert item == {key: original[key] for key in item}


def test_detail_and_referenced_schemas(client: TestClient) -> None:
    registry = client.app.state.skill_registry
    for name in registry.list_skills(available_only=False):
        response = client.get("/skills/" + name)
        assert response.status_code == 200
        body, original = response.json(), registry.get_manifest(name)
        for key in ("input_schema", "output_schema", "dependencies", "limitations"):
            assert body[key] == original[key]
        assert "entry_point" not in body and "requirements" not in body
        # A consumer can resolve the unmodified schemas using the supplied documents.
        base = "https://metadata.example/"
        references = Registry().with_resources((base + uri, Resource.from_contents(document))
            for uri, document in body["schema_documents"].items())
        for direction in ("input_schema", "output_schema"):
            schema = body[direction]
            Draft202012Validator.check_schema(schema)
            if isinstance(schema, dict):
                validator = Draft202012Validator({**schema, "$id": base + name}, registry=references)
                list(validator.iter_errors({}))  # Resolves all required bundled refs, never HTTP.


def test_unimplemented_gsea_is_explicit(client: TestClient) -> None:
    body = client.get("/skills/rnaseq.gsea").json()
    assert body["availability"] == {"status": "not_implemented", "callable": False}
    assert body["input_schema"] is False and body["output_schema"] is False


@pytest.mark.parametrize("name", ["not-exist", "rnaseq.unknown"])
def test_unknown_skill(client: TestClient, name: str) -> None:
    response = client.get("/skills/" + name)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "SKILL_NOT_FOUND", "message": "The requested Skill was not found.",
        "details": None, "request_id": response.headers["x-request-id"]}}


def test_registry_created_once_and_dependency_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = create_default_registry()
    factory = Mock(return_value=registry)
    monkeypatch.setattr("bulk_rnaseq.api.main.create_default_registry", factory)
    with TestClient(create_app(workspace_root=tmp_path / "workspace")) as client:
        client.get("/health")
        client.get("/skills")
        client.get("/skills/rnaseq.deg")
        factory.assert_called_once_with()
        empty = SkillRegistry()
        client.app.dependency_overrides[get_skill_registry] = lambda: empty
        assert client.get("/skills").json() == {"skills": []}
        assert client.get("/skills/rnaseq.deg").status_code == 404


def test_no_secrets_or_machine_paths(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "fake-secret-api-test-only"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setenv("PROJECT_ROOT", str(Path.cwd()))
    paths = ["/health", "/skills", "/openapi.json"] + ["/skills/" + name
        for name in client.app.state.skill_registry.list_skills(available_only=False)]
    for path in paths:
        text = json.dumps(client.get(path).json())
        for forbidden in (secret, "DEEPSEEK_API_KEY", "project_root", "C:\\\\Users", "file://", "Traceback"):
            assert forbidden not in text
        assert str(Path.cwd()).replace("\\", "\\\\") not in text


def test_openapi_and_docs(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
    assert set(schema["paths"]) == {"/health", "/ready", "/skills", "/skills/{skill_name}", "/skills/{skill_name}/run", "/workflow/plan", "/workflow/run", "/assistant/analyze",
        "/projects/{project_id}/results", "/projects/{project_id}/results/{run_id}", "/customizations/plan", "/customizations/run"}
    assert set(schema["paths"]["/skills/{skill_name}/run"]) == {"post"}
    assert schema["paths"]["/skills/{skill_name}"]["get"]["responses"]["404"]
    docs = client.get("/docs")
    assert docs.status_code == 200 and "SwaggerUIBundle" in docs.text and "/openapi.json" in docs.text


def test_no_cors_or_unvalidated_execution(client: TestClient) -> None:
    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "https://untrusted.example"}).headers
    assert client.post("/skills/rnaseq.deg/run").status_code == 422
    assert client.post("/workflow/run").status_code == 422
    response = client.post("/skills")
    assert response.status_code == 405 and response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize("error", [RuntimeError("private-token C:/private/path"),
                                 SkillError("PRIVATE_ERROR", "private-token C:/private/path")])
def test_unexpected_backend_error_is_safe(client: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def broken(name: str) -> dict:
        raise error
    monkeypatch.setattr(client.app.state.skill_registry, "get_manifest", broken)
    response = client.get("/skills/rnaseq.deg")
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "private-token" not in response.text and "private/path" not in response.text
    assert response.headers["x-request-id"]


def test_log_does_not_echo_request_secrets(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    logger = client.app.state.logger
    logger.addHandler(caplog.handler)
    try:
        response = client.get("/skills/not-secret-token?api_key=query-secret", headers={"Authorization": "Bearer header-secret"})
    finally:
        logger.removeHandler(caplog.handler)
    log = caplog.text
    assert "method=GET endpoint=/skills/{skill_name} status=404" in log
    assert response.headers["x-request-id"] in log
    assert all(secret not in log for secret in ("not-secret-token", "query-secret", "header-secret"))


def test_health_does_not_read_manifest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.app.state.skill_registry, "get_manifest", Mock(side_effect=AssertionError("Not needed")))
    assert client.get("/health").status_code == 200


def test_network_guard_allows_only_internal_socketpair() -> None:
    left, right = socket.socketpair()
    try:
        left.send(b"x")
        assert right.recv(1) == b"x"
    finally:
        left.close()
        right.close()
    for destination in ("127.0.0.1", "198.51.100.1"):
        with socket.socket() as connection:
            with pytest.raises(AssertionError, match="Network blocked"):
                connection.connect((destination, 443))
            with pytest.raises(AssertionError, match="Network blocked"):
                connection.connect_ex((destination, 443))
