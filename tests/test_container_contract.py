"""Static packaging checks only: these never imply Docker build/run success."""

from pathlib import Path

import pytest

from container.context_audit import inventory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["transform_pca.R", "plot_volcano.R", "plot_heatmap.R",
                                  "enrichment_common.R", "replot_enrichment.R"])
def test_png_renderers_select_headless_device_on_unix(script: str) -> None:
    """Regression: compiled R may default to Xlib even when Cairo is available.

    Real rendering is additionally exercised by opt-in R/container integration.
    Keep Windows' existing device selection unchanged.
    """
    source = (ROOT / "src/bulk_rnaseq/r_scripts" / script).read_text(encoding="utf-8")
    assert 'type = if (.Platform$OS.type == "unix" && capabilities("cairo")) "cairo" else getOption("bitmapType")' in source


def test_context_excludes_private_data_and_runtime() -> None:
    result = inventory(ROOT)
    assert result["total_bytes"] < 5 * 1024 * 1024
    paths = [item["path"] for item in result["files"]]
    assert "Dockerfile" in paths and "src/bulk_rnaseq/api/main.py" in paths
    assert not any(path.startswith(("workspace/", "outputs/", "Real_DATA", ".git/", ".venv/", "tests/")) for path in paths)
    assert not any(".env" in Path(path).parts or "__pycache__" in Path(path).parts for path in paths)


def test_dockerfile_pins_runtime_and_separates_data() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.13.2-bookworm" in text
    assert "R-4.6.1.tar.gz" in text and "sha256sum --check" in text
    assert "libuv1-dev" in text  # fs configure requires uv.h on the audited CRAN release.
    assert "USER 10001:10001" in text
    assert 'VOLUME ["/workspace"]' in text
    assert "RNASEQ_WORKSPACE_ROOT=/workspace" in text
    assert "bulk_rnaseq.api.main:app" in text
    for line in text.splitlines():
        if line.startswith(("COPY ", "ADD ", "ARG ", "ENV ")):
            assert "DEEPSEEK_API_KEY" not in line and ".env" not in line
            assert not line.startswith("COPY . ")


def test_smoke_does_not_contact_an_llm() -> None:
    source = (ROOT / "container/smoke_runtime.py").read_text(encoding="utf-8")
    assert "DeepSeekClient" not in source
    assert "source_run_id" in source and "verify-reuse" in source
    assert '"/health", "/skills"' in source
