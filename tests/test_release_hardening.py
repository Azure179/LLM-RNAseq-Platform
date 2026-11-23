"""Release defaults must work with a clean cwd and keep error details private."""
from pathlib import Path

from bulk_rnaseq.cli import main


def test_installed_cli_default_without_project_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_invalid_config_without_echoing_user_data(tmp_path, capsys):
    config = tmp_path / "private-setting-value"
    assert main(["--config", str(config)]) == 2
    response = capsys.readouterr().out
    assert "CONFIG_ERROR" in response
    assert config.name not in response


def test_compose_hides_streamlit_error_details():
    compose = Path(__file__).resolve().parents[1] / "compose.yaml"
    assert 'STREAMLIT_CLIENT_SHOW_ERROR_DETAILS: "none"' in compose.read_text()


def test_uvicorn_access_log_cannot_disclose_raw_urls():
    import logging
    from bulk_rnaseq.api.security import install_request_log_redaction

    install_request_log_redaction()
    assert not logging.getLogger("uvicorn.access").isEnabledFor(logging.INFO)


def test_provider_trust_excludes_only_affected_root(tmp_path, monkeypatch):
    import hashlib
    import ssl
    import certifi
    from bulk_rnaseq.llm.tls import deepseek_tls_context

    def fingerprints(context):
        return {hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)}

    original = fingerprints(ssl.create_default_context(cafile=certifi.where()))
    keylog = tmp_path / "tls-session.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    actual = deepseek_tls_context()
    assert actual.keylog_filename is None and not keylog.exists()
    assert actual.check_hostname and actual.verify_mode == ssl.CERT_REQUIRED
    assert original and fingerprints(actual) == original - {
        "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"}


def test_preview_rejects_other_valid_image_formats():
    from io import BytesIO
    import pytest
    from PIL import Image
    from bulk_rnaseq.workspace.errors import ProjectError
    from bulk_rnaseq.workspace.result_preview import validate_image

    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="TIFF")
    with pytest.raises(ProjectError) as error:
        validate_image(buffer.getvalue())
    assert error.value.code == "INVALID_RESULT_IMAGE"


def test_ui_unexpected_error_hides_exception_message(monkeypatch, caplog):
    from streamlit.testing.v1 import AppTest

    def fail():
        raise RuntimeError("private-diagnostic-marker")

    monkeypatch.setattr("bulk_rnaseq.config.load_runtime_config", fail)
    app = Path(__file__).resolve().parents[1] / "app/main.py"
    view = AppTest.from_file(str(app)).run()
    assert view.error and not view.exception
    assert "private-diagnostic-marker" not in caplog.text
    assert all("private-diagnostic-marker" not in error.value for error in view.error)
