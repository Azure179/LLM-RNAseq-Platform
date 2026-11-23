"""Offline invariants for the reviewed runtime locks and new-result-only receipt."""
import csv
import json
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from bulk_rnaseq import run_io
from bulk_rnaseq.models import DegRunResult
from bulk_rnaseq.result_schema import SkillResult


ROOT = Path(__file__).resolve().parents[1]


def test_linux_and_windows_locks_cover_declared_direct_dependencies():
    requirements = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    for path, is_dev in (("container/python-constraints.txt", False),
                         ("container/locks/python-windows-dev.txt", True)):
        pins = {}
        for line in (ROOT / path).read_text().splitlines():
            if line.startswith("#"):
                continue
            pin, wheel_hash = line.split()
            assert re.fullmatch("--hash=sha256:[0-9a-f]{64}", wheel_hash)
            name, version = pin.split("==")
            assert name not in pins
            pins[name] = version
        for value in requirements:
            req = Requirement(value)
            assert pins[canonicalize_name(req.name)] in req.specifier
        assert ("pytest" in pins) == is_dev


def test_r_lock_identifies_source_and_archive_content():
    with (ROOT / "container/locks/r-linux.tsv").open() as stream:
        header = next(stream)
        entries = list(csv.DictReader(stream, delimiter="\t"))
    assert "Bioconductor=" in header
    rows = {row["package"]: row for row in entries}
    assert len(rows) == len(entries)
    assert rows["DESeq2"]["source"] == rows["clusterProfiler"]["source"] == "bioc"
    assert rows["ggplot2"]["source"] == "CRAN"
    for row in entries:
        assert row["version"]
        if row["source"] == "R":
            assert row["sha256"] == "-"
        else:
            assert re.fullmatch("[0-9a-f]{64}", row["sha256"])


def test_environment_receipt_only_attached_on_new_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(run_io, "__file__", str(tmp_path / "run_io.py"))
    result = DegRunResult("new", tmp_path, tmp_path / "deg.csv", tmp_path / "filtered.csv",
                          tmp_path / "summary.json", "", "")
    summary = result._legacy_dict()
    first = run_io.publish_result(result, summary).to_dict()
    assert "environment" not in first["metadata"]["provenance"]
    receipt = {"scope": "image_build_verified", "python_lock_sha256": "a" * 64}
    (tmp_path / "_verified_environment.json").write_text(json.dumps(receipt))
    new = run_io.publish_result(result, summary).to_dict()
    assert new["metadata"]["provenance"]["environment"] == receipt
    assert "environment" not in SkillResult.from_dict(first).metadata["provenance"]
