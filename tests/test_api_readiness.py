"""Readiness must not execute a Skill, R or a provider request."""

import pytest

from test_api import client, no_execution


def test_ready_and_health_contract(client, monkeypatch):
    monkeypatch.setattr("bulk_rnaseq.api.readiness.shutil.which", lambda _: "/runtime/Rscript")
    before = set(client.app.state.workspace_root.iterdir())
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {
        "initialized": True, "analysis_available": True, "workspace_writable": True}}
    assert set(client.app.state.workspace_root.iterdir()) == before
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("reason", ["startup", "rscript", "storage"])
def test_unready_is_safe_and_liveness_is_independent(client, monkeypatch, reason):
    monkeypatch.setattr("bulk_rnaseq.api.readiness.shutil.which", lambda _: "/runtime/Rscript")
    if reason == "startup":
        client.app.state.ready = False
    elif reason == "rscript":
        monkeypatch.setattr("bulk_rnaseq.api.readiness.shutil.which", lambda _: None)
    else:
        def denied(**kwargs):
            raise PermissionError("/private/secret-sentinel")
        monkeypatch.setattr("bulk_rnaseq.api.readiness.tempfile.TemporaryFile", denied)
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["error"]["code"] == "SERVICE_NOT_READY"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert not all(response.json()["error"]["details"]["checks"].values())
    assert "private" not in response.text and "secret-sentinel" not in response.text
    assert client.get("/health").status_code == 200


def test_shutdown_clears_readiness(tmp_path):
    from fastapi.testclient import TestClient
    from bulk_rnaseq.api.main import create_app
    app = create_app(workspace_root=tmp_path / "workspace")
    assert app.state.ready is False
    with TestClient(app):
        assert app.state.ready is True
    assert app.state.ready is False
