"""PCA-specific contracts; exhaustive numeric CSV edge cases remain in shared tests."""

import csv
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from result_helpers import assert_result_contract

from bulk_rnaseq.cli import main
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.pca import run_pca
from bulk_rnaseq.validation import validate_pca_inputs
from plot_helpers import fixture_png


def fake_pca_process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
    """Fault-injection fixture only; real numerical results require integration tests."""
    assert kwargs["check"] is False and kwargs["capture_output"] is True
    assert not kwargs.get("shell", False)
    output = Path(command[command.index("--output-dir") + 1])
    metadata = Path(command[command.index("--metadata") + 1])
    column = command[command.index("--group-column") + 1]
    with metadata.open() as handle:
        groups = {row["sample_id"]: row[column] for row in csv.DictReader(handle)}
    with (output / "pca_coordinates.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample", "PC1", "PC2", "group"])
        for sample, x, y in zip(("c1", "c2", "t1", "t2"), (-3, -1, 1, 3), (1, -1, -1, 1)):
            writer.writerow([sample, x, y, groups[sample]])
    (output / "pca_variance.json").write_text(json.dumps({"PC1": 100 * 20 / 24, "PC2": 100 * 4 / 24}))
    (output / "pca_plot.png").write_bytes(fixture_png())
    return subprocess.CompletedProcess(command, 0,
        "R_VERSION=test-fixture\nDESEQ2_VERSION=test-fixture\nN_GENES_VST=2\nN_GENES_PCA=2\n", "")


def test_normal_pca_output(input_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_pca_process)
    result = run_pca(*input_files, "condition", tmp_path / "pca output")
    summary = json.loads(result.summary_json.read_text())
    assert_result_contract(result, "rnaseq.pca")
    assert summary["operation"] == "pca" and summary["status"] == "succeeded"
    assert summary["metrics"]["n_samples"] == 4 and summary["metrics"]["n_groups"] == 2
    assert summary["metrics"]["pc1_variance"] == pytest.approx(83.333333)
    assert summary["parameters"]["transformation"] == "varianceStabilizingTransformation"
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    assert (result.output_dir / "run.log").is_file()
    assert (result.output_dir / "r_stdout.log").is_file()
    assert not (result.output_dir / "failure.json").exists()


@pytest.mark.parametrize("case,expected", [
    ("counts_missing", "FILE_NOT_FOUND"), ("metadata_missing", "FILE_NOT_FOUND"),
    ("mismatch", "SAMPLE_MISMATCH"), ("column_missing", "GROUP_COLUMN_NOT_FOUND"),
    ("fraction", "NON_INTEGER_COUNT"), ("negative", "NEGATIVE_COUNT"),
    ("missing_count", "MISSING_VALUE"), ("all_missing_groups", "MISSING_VALUE"),
    ("some_missing_groups", "MISSING_VALUE"), ("too_few_samples", "INSUFFICIENT_SAMPLES"),
    ("too_few_genes", "INSUFFICIENT_GENES"), ("invalid_metadata", "METADATA_FORMAT_ERROR"),
    ("empty_group_column", "INVALID_PARAMETER"), ("invalid_timeout", "INVALID_PARAMETER"),
])
def test_invalid_pca(case: str, expected: str, input_files: tuple[Path, Path], tmp_path: Path,
                     monkeypatch: pytest.MonkeyPatch) -> None:
    counts, metadata = input_files
    c, m = counts.read_text(), metadata.read_text()
    column, timeout = "condition", 600
    if case == "counts_missing":
        counts.unlink()
    elif case == "metadata_missing":
        metadata.unlink()
    elif case == "mismatch":
        metadata.write_text(m.replace("c1", "absent"))
    elif case == "column_missing":
        column = "not_present"
    elif case == "fraction":
        counts.write_text(c.replace("10,", "10.2,"))
    elif case == "negative":
        counts.write_text(c.replace("10,", "-10,"))
    elif case == "missing_count":
        counts.write_text(c.replace("10,", "NA,"))
    elif case == "all_missing_groups":
        metadata.write_text(m.replace("control", "NA").replace("treatment", "NA"))
    elif case == "some_missing_groups":
        metadata.write_text(m.replace("c1,control", "c1,"))
    elif case == "too_few_samples":
        counts.write_text("gene_id,c1,t1\ng1,10,80\ng2,20,23\n")
        metadata.write_text("sample_id,condition\nc1,control\nt1,treatment\n")
    elif case == "too_few_genes":
        counts.write_text("\n".join(c.splitlines()[:2]) + "\n")
    elif case == "invalid_metadata":
        metadata.write_text(m.replace("sample_id", "wrong"))
    elif case == "empty_group_column":
        column = ""
    elif case == "invalid_timeout":
        timeout = float("nan")
    runner = Mock(side_effect=AssertionError("Invalid PCA input must not reach R"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", runner)
    output = tmp_path / "failure"
    with pytest.raises(SkillError) as caught:
        run_pca(counts, metadata, column, output, timeout=timeout)
    assert caught.value.code == expected
    assert caught.value.to_dict()["operation"] == "pca"
    runner.assert_not_called()
    failure = json.loads((output / "failure.json").read_text())
    assert failure["operation"] == "pca" and failure["error"]["code"] == expected
    assert not (output / "summary.json").exists()


def test_custom_groups_and_order(input_files: tuple[Path, Path]) -> None:
    counts, metadata = input_files
    metadata.write_text("sample_id,cohort\nt2,C\nc1,A\nt1,B\nc2,A\n")
    overview = validate_pca_inputs(counts, metadata, "cohort")
    assert overview.sample_groups == ("A", "A", "B", "C")
    # Singleton annotations are allowed: PCA is not a DEG contrast.


def test_single_group_is_valid(input_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    counts, metadata = input_files
    metadata.write_text(metadata.read_text().replace("treatment", "control"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_pca_process)
    result = run_pca(counts, metadata, "condition", tmp_path / "single")
    summary = json.loads(result.summary_json.read_text())
    assert summary["metrics"]["n_groups"] == 1
    assert any(warning["code"] == "SINGLE_GROUP" for warning in summary["warnings"])


@pytest.mark.parametrize("case,expected", [
    ("nonzero", "RSCRIPT_FAILED"), ("timeout", "RSCRIPT_TIMEOUT"),
    ("missing", "OUTPUT_MISSING"), ("bad_samples", "OUTPUT_INVALID"),
    ("bad_variance", "OUTPUT_INVALID"), ("ratio", "OUTPUT_INVALID"),
    ("bad_plot", "OUTPUT_INVALID"), ("missing_provenance", "OUTPUT_INVALID"),
])
def test_pca_execution_failures(case: str, expected: str, input_files: tuple[Path, Path], tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    def process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        if case == "nonzero":
            return subprocess.CompletedProcess(command, 8, "started", "PCA_ERROR: simulated R error")
        if case == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial", stderr=b"timed out")
        result = fake_pca_process(command, **kwargs)
        stage = Path(command[command.index("--output-dir") + 1])
        if case == "missing":
            (stage / "pca_plot.png").unlink()
        elif case == "bad_samples":
            path = stage / "pca_coordinates.csv"
            path.write_text(path.read_text().replace("c1,", "not_a_sample,"))
        elif case == "bad_variance":
            (stage / "pca_variance.json").write_text('{"PC1": 120, "PC2": 10}')
        elif case == "ratio":
            (stage / "pca_variance.json").write_text('{"PC1": 80, "PC2": 20}')
        elif case == "bad_plot":
            (stage / "pca_plot.png").write_bytes(b"Not a PNG")
        elif case == "missing_provenance":
            result.stdout = ""
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    output = tmp_path / "run"
    with pytest.raises(SkillError) as caught:
        run_pca(*input_files, "condition", output)
    assert caught.value.code == expected and caught.value.operation == "pca"
    assert json.loads((output / "failure.json").read_text())["artifacts"] == {}
    assert not list(output.glob("pca_*"))
    assert not (output / "summary.json").exists()
    if case in {"nonzero", "timeout"}:
        assert (output / "r_stderr.log").read_text()


def test_pca_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["pca"]) == 2
    assert json.loads(capsys.readouterr().out)["operation"] == "pca"
    code = main(["pca", "--counts", "absent.csv", "--metadata", "absent.csv",
                 "--group-column", "condition", "--output-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1 and json.loads(captured.out)["operation"] == "pca"
    assert "Traceback" not in captured.err + captured.out
    code = main(["pca", "--counts", "absent.csv", "--metadata", "absent.csv",
                 "--group-column", "condition", "--output-dir", str(tmp_path / "out"), "--unknown"])
    failure = json.loads(capsys.readouterr().out)
    assert code == 2 and failure["operation"] == "pca" and failure["error"]["code"] == "INVALID_ARGUMENT"


def test_pca_preserves_existing_directory(input_files: tuple[Path, Path], tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "summary.json").write_text("old result")
    with pytest.raises(SkillError) as caught:
        run_pca(*input_files, "condition", output)
    assert caught.value.code == "OUTPUT_NOT_EMPTY" and caught.value.operation == "pca"
    assert (output / "summary.json").read_text() == "old result"
