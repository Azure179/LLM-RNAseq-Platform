"""Shared pytest data and explicit opt-in for real R integration tests."""

from pathlib import Path
import shutil
import socket

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-r", action="store_true", help="Run real R tests (DEG/PCA/raw-count Heatmap require DESeq2).")
    parser.addoption("--rscript", default="Rscript", help="Rscript executable for integration tests.")
    parser.addoption("--run-llm", action="store_true", help="Explicitly allow paid DeepSeek integration tests.")
    parser.addoption("--llm-env-file", default=".env", help="Local DeepSeek configuration; never pass the API key on the command line.")
    parser.addoption("--llm-report-dir", default="outputs/deepseek_tests", help="Directory for credential-free live API reports.")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-llm"):
        for item in items:
            if item.get_closest_marker("llm"):
                item.add_marker(pytest.mark.skip(reason="Use --run-llm to explicitly allow paid API tests."))
    if not config.getoption("--run-r"):
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(pytest.mark.skip(reason="Use --run-r to enable real R tests."))


@pytest.fixture(autouse=True)
def prevent_unapproved_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with --run-llm, only llm-marked tests may connect; MockTransport stays usable."""
    if request.config.getoption("--run-llm") and request.node.get_closest_marker("llm"):
        return
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network blocked: use an llm-marked test with explicit --run-llm.")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def input_files(tmp_path: Path) -> tuple[Path, Path]:
    counts, metadata = tmp_path / "counts.csv", tmp_path / "metadata.csv"
    counts.write_text("gene_id,c1,c2,t1,t2\ng1,10,12,80,85\ng2,20,24,23,21\ng0,0,0,0,0\n", encoding="utf-8")
    metadata.write_text("sample_id,condition\nc1,control\nc2,control\nt1,treatment\nt2,treatment\n", encoding="utf-8")
    return counts, metadata


@pytest.fixture
def demo_files(tmp_path: Path) -> tuple[Path, Path]:
    source = Path(__file__).resolve().parents[1] / "examples" / "data"
    counts, metadata = tmp_path / "counts.csv", tmp_path / "metadata.csv"
    shutil.copyfile(source / "counts.csv", counts)
    shutil.copyfile(source / "metadata.csv", metadata)
    return counts, metadata
