"""Offline negative controls for public CI artifacts and runtime-lock wiring."""
from pathlib import Path

import pytest

from scripts.ci.security import inspect_bytes, inspect_name

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", [".env", "workspace/research/project.json", "Real_DATA_TEST/input/data",
                                  "outputs/summary.json", "build/private.tar", "misc/expression.csv"])
def test_private_artifact_rejected(name):
    with pytest.raises(ValueError):
        inspect_name(name)


def test_secret_rejected_without_printing_value():
    token = b"sk-" + b"a" * 32
    with pytest.raises(ValueError) as exc:
        inspect_bytes("tests/llm/test_client.py", token)
    assert token.decode() not in str(exc.value)


def test_docker_build_consumes_both_locks():
    from container.context_audit import inventory
    assert "**/*.egg-info/" in (ROOT / ".dockerignore").read_text().splitlines()
    assert all(".egg-info/" not in item["path"] for item in inventory(ROOT)["files"])
    source = (ROOT / "Dockerfile").read_text()
    for required in ("--require-hashes", "-r /opt/rnaseq/container/python-constraints.txt",
                     "install_r.R /opt/rnaseq/container/r-linux.tsv",
                     "python /opt/rnaseq/container/verify_runtime.py"):
        assert required in source

