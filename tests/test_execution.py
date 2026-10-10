"""Fault injection tests: synthetic rows are test fixtures, never analysis evidence."""

import csv
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from result_helpers import assert_result_contract

from bulk_rnaseq.artifacts import COLUMNS
from bulk_rnaseq.cli import main
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.deg import run_deg


ROWS = [["g1", "40", "2", "0.2", "10", "0.001", "0.01"],
        ["g2", "22", "0.2", "0.1", "2", "0.01", "0.02"]]


def fake_process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
    """Only used via monkeypatch to exercise Python's output validation."""
    assert "shell" not in kwargs
    for flag, rows in (("--output", ROWS), ("--filtered-output", ROWS[:1])):
        path = Path(command[command.index(flag) + 1])
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
    return subprocess.CompletedProcess(command, 0, "R_VERSION=test-fixture\nDESEQ2_VERSION=test-fixture\n", "")


def test_verified_success(input_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_process)
    output = tmp_path / "output with spaces"
    result = run_deg(*input_files, "treatment", "control", output)
    summary = json.loads(result.summary_json.read_text())
    assert_result_contract(result, "rnaseq.deg")
    assert summary["status"] == "succeeded"
    assert summary["metrics"]["significant_genes"] == 1  # g2 passes padj but not the LFC threshold
    assert summary["metrics"]["padj_significant_genes"] == 2
    assert len(summary["provenance"]["input_sha256"]["count_matrix"]) == 64
    assert result.filtered_csv.is_file()
    assert (output / "run.log").is_file()
    assert (output / "r_stdout.log").is_file()
    assert not (output / "failure.json").exists()
    assert not list(output.glob(".work-*"))
    assert not (output / ".run.lock").exists()


@pytest.mark.parametrize("kind,expected", [
    ("nonzero", "RSCRIPT_FAILED"), ("timeout", "RSCRIPT_TIMEOUT"),
    ("missing", "OUTPUT_MISSING"), ("header", "OUTPUT_INVALID"),
    ("nan", "OUTPUT_INVALID"), ("range", "OUTPUT_INVALID"),
    ("wrong_genes", "OUTPUT_INVALID"), ("duplicate_gene", "OUTPUT_INVALID"),
    ("filtered", "OUTPUT_INVALID"), ("provenance", "OUTPUT_INVALID"),
    ("internal", "INTERNAL_ERROR"),
])
def test_execution_failure(kind: str, expected: str, input_files: tuple[Path, Path],
                           tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        if kind == "nonzero":
            return subprocess.CompletedProcess(command, 7, "R started", "R failed")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial output", stderr=b"timeout diagnostic")
        if kind == "internal":
            raise RuntimeError("private traceback detail")
        result = fake_process(command, **kwargs)
        path = Path(command[command.index("--output") + 1])
        if kind == "missing": path.unlink()
        elif kind == "header": path.write_text("bad,columns\n1,2\n")
        elif kind == "nan": path.write_text(path.read_text().replace("0.2", "NaN"))
        elif kind == "range": path.write_text(path.read_text().replace("0.001", "1.2"))
        elif kind == "wrong_genes": path.write_text(path.read_text().replace("g1", "other"))
        elif kind == "duplicate_gene": path.write_text(path.read_text().replace("g2", "g1"))
        elif kind == "filtered":
            filtered = Path(command[command.index("--filtered-output") + 1])
            filtered.write_text(",".join(COLUMNS) + "\n")
        elif kind == "provenance": result.stdout = "missing versions"
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    output = tmp_path / "output"
    with pytest.raises(SkillError) as caught:
        run_deg(*input_files, "treatment", "control", output)
    assert caught.value.code == expected
    failure = json.loads((output / "failure.json").read_text())
    assert failure["artifacts"] == {}
    assert "private traceback detail" not in json.dumps(failure)
    assert not (output / "summary.json").exists()
    assert not (output / "deg_results.csv").exists()
    if kind in {"nonzero", "timeout"}:
        assert (output / "r_stdout.log").read_text()
        assert (output / "r_stderr.log").read_text()
    if kind == "internal":
        assert "private traceback detail" in (output / "run.log").read_text()


@pytest.mark.parametrize("mode,expected", [("file", "OUTPUT_NOT_DIRECTORY"),
    ("existing", "OUTPUT_NOT_EMPTY"), ("locked", "OUTPUT_BUSY"), ("permission", "OUTPUT_NOT_WRITABLE")])
def test_output_protection(mode: str, expected: str, input_files: tuple[Path, Path],
                           tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output"
    if mode == "file": output.write_text("keep me")
    elif mode == "existing":
        output.mkdir()
        (output / "summary.json").write_text("old success must remain untouched")
    elif mode == "locked":
        output.mkdir()
        (output / ".run.lock").write_text("other run")
    elif mode == "permission":
        original = Path.mkdir
        def denied(path: Path, *args: object, **kwargs: object) -> None:
            if path == output: raise PermissionError("test permission failure")
            original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "mkdir", denied)
    process = Mock()
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    with pytest.raises(SkillError) as caught:
        run_deg(*input_files, "treatment", "control", output)
    assert caught.value.code == expected
    process.assert_not_called()
    if mode == "existing":
        assert (output / "summary.json").read_text() == "old success must remain untouched"
    if mode == "locked":
        assert (output / ".run.lock").read_text() == "other run"


def test_cli_json_error_without_traceback(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = main(["deg", "--counts", str(tmp_path / "absent.csv"), "--metadata", "absent.csv",
                 "--treatment", "treatment", "--control", "control", "--output-dir", str(tmp_path / "output")])
    captured = capsys.readouterr()
    assert code == 1
    assert json.loads(captured.out)["error"]["code"] == "FILE_NOT_FOUND"
    assert "Traceback" not in captured.err + captured.out


def test_cli_missing_arguments(capsys: pytest.CaptureFixture) -> None:
    assert main(["deg"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_ARGUMENT"


def test_failure_to_publish_summary(input_files: tuple[Path, Path], tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from bulk_rnaseq.run_io import write_json
    def fail_summary(path: Path, payload: dict) -> None:
        if path.name == "summary.json": raise OSError("disk failure")
        write_json(path, payload)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_process)
    monkeypatch.setattr("bulk_rnaseq.run_io.write_json", fail_summary)
    output = tmp_path / "output"
    with pytest.raises(SkillError) as caught:
        run_deg(*input_files, "treatment", "control", output)
    assert caught.value.code == "IO_ERROR"
    assert not (output / "summary.json").exists()
    assert not (output / "deg_results.csv").exists()
    assert not (output / "deg_filtered.csv").exists()
    assert (output / "failure.json").exists()
