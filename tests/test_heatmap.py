"""Heatmap contracts with explicitly synthetic expression/DEG fixtures."""

import csv
import json
import math
from pathlib import Path
from statistics import mean, stdev
import subprocess
from unittest.mock import Mock

import pytest
from result_helpers import assert_result_contract

from bulk_rnaseq.cli import main
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.heatmap import run_heatmap
from plot_helpers import fixture_png


@pytest.fixture
def heatmap_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    expression, deg, metadata = (tmp_path / name for name in ("vst.csv", "deg.csv", "metadata.csv"))
    expression.write_text("gene_id,c1,c2,t1,t2\ng1,-1,0,2,3\ng2,5,4,1,2\ng3,8,8,8,8\n")
    deg.write_text("gene_id,log2FoldChange,pvalue,padj\ng1,2,0.0001,0.001\ng2,-3,0.0001,0.001\n"
                   "g3,2,0,0\nabsent,2,0.00001,0.0001\nmissing,NA,NA,NA\n")
    metadata.write_text("sample_id,cohort\nt2,002\nc1,001\nt1,002\nc2,001\n")
    return expression, deg, metadata


def renderer(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
    assert Path(command[1]).name == "plot_heatmap.R"
    assert kwargs["capture_output"] and not kwargs.get("shell", False)
    matrix = Path(command[command.index("--matrix") + 1])
    with matrix.open(newline="") as handle:
        rows = list(csv.reader(handle))
    Path(command[command.index("--plot") + 1]).write_bytes(fixture_png())
    return subprocess.CompletedProcess(command, 0,
        f"R_VERSION=test-fixture\nN_GENES={len(rows)-1}\nN_SAMPLES={len(rows[0])-1}\n", "")


def test_normal_and_fewer_degs(heatmap_inputs: tuple[Path, Path, Path], tmp_path: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    expression, deg, metadata = heatmap_inputs
    originals = [p.read_bytes() for p in heatmap_inputs]
    process = Mock(side_effect=renderer)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    result = run_heatmap(deg, metadata, "cohort", tmp_path / "out", normalized_expression=expression)
    process.assert_called_once()  # Supplied VST never reaches normalization.
    assert [p.read_bytes() for p in heatmap_inputs] == originals
    summary = json.loads(result.summary_json.read_text())
    assert_result_contract(result, "rnaseq.heatmap")
    assert summary["status"] == "succeeded" and summary["operation"] == "heatmap"
    metrics = summary["metrics"]
    assert (metrics["n_genes_requested"], metrics["n_genes_used"], metrics["n_samples"]) == (50, 2, 4)
    assert metrics["group_column"] == "cohort" and metrics["n_groups"] == 2
    assert summary["sample_groups"] == {"c1": "001", "c2": "001", "t1": "002", "t2": "002"}
    with (result.output_dir / "selected_genes.csv").open() as handle:
        selected = list(csv.DictReader(handle))
    assert [r["gene_id"] for r in selected] == ["g2", "g1"]
    with (result.output_dir / "heatmap_matrix.csv").open() as handle:
        matrix = list(csv.reader(handle))
    assert matrix[0] == ["gene_id", "c1", "c2", "t1", "t2"]
    for row in matrix[1:]:
        z = list(map(float, row[1:]))
        assert mean(z) == pytest.approx(0, abs=1e-12) and stdev(z) == pytest.approx(1)
    codes = {w["code"] for w in summary["warnings"]}
    assert {"CONSTANT_GENES_SKIPPED", "FEWER_GENES_THAN_REQUESTED", "DEG_MISSING_STATISTICS", "DEG_NOT_IN_EXPRESSION"} <= codes
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    assert (result.output_dir / "normalized_expression.csv").read_bytes() == expression.read_bytes()


@pytest.mark.parametrize("case,expected", [
    ("no_common", "NO_COMMON_GENES"), ("mismatch", "SAMPLE_MISMATCH"),
    ("missing_group", "GROUP_COLUMN_NOT_FOUND"), ("na", "MISSING_VALUE"),
    ("inf", "EXPRESSION_VALUE_INVALID"), ("bad_number", "EXPRESSION_VALUE_INVALID"),
    ("constant", "NO_PLOTTABLE_GENES"), ("no_sig", "NO_SIGNIFICANT_GENES"),
    ("duplicate", "DUPLICATE_ID"), ("one_sample", "INSUFFICIENT_SAMPLES"),
    ("expression_missing", "FILE_NOT_FOUND"), ("deg_missing", "FILE_NOT_FOUND"), ("metadata_missing", "FILE_NOT_FOUND"),
])
def test_invalid_inputs(case: str, expected: str, heatmap_inputs: tuple[Path, Path, Path],
                        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expression, deg, metadata = heatmap_inputs
    text = expression.read_text()
    column = "cohort"
    if case == "no_common":
        expression.write_text(text.replace("g1", "x1").replace("g2", "x2").replace("g3", "x3"))
    elif case == "mismatch":
        metadata.write_text(metadata.read_text().replace("c1", "other"))
    elif case == "missing_group":
        column = "absent"
    elif case in {"na", "inf", "bad_number"}:
        expression.write_text(text.replace("g1,-1,", f"g1,{dict(na='NA', inf='Inf', bad_number='abc')[case]},"))
    elif case == "constant":
        expression.write_text("gene_id,c1,c2,t1,t2\ng1,8,8,8,8\n")
    elif case == "no_sig":
        deg.write_text("gene_id,log2FoldChange,pvalue,padj\ng1,0.1,0.2,0.5\n")
    elif case == "duplicate":
        expression.write_text(text.replace("g2,", "g1,"))
    elif case == "one_sample":
        expression.write_text("gene_id,c1\ng1,1\n")
    elif case == "expression_missing":
        expression.unlink()
    elif case == "deg_missing":
        deg.unlink()
    elif case == "metadata_missing":
        metadata.unlink()
    process = Mock(side_effect=AssertionError("Invalid inputs must not reach R"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    output = tmp_path / "out"
    with pytest.raises(SkillError) as caught:
        run_heatmap(deg, metadata, column, output, normalized_expression=expression)
    assert caught.value.code == expected and caught.value.operation == "heatmap"
    process.assert_not_called()
    assert not (output / "summary.json").exists()
    assert json.loads((output / "failure.json").read_text())["error"]["code"] == expected


@pytest.mark.parametrize("top_n", [0, -1, 1.5, True, float("nan")])
def test_invalid_top_n(top_n: object, heatmap_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    expression, deg, metadata = heatmap_inputs
    with pytest.raises(SkillError) as caught:
        run_heatmap(deg, metadata, "cohort", tmp_path / "out", normalized_expression=expression, top_n=top_n)
    assert caught.value.code == "INVALID_PARAMETER"


@pytest.mark.parametrize("both", [False, True])
def test_exclusive_inputs(both: bool, heatmap_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    expression, deg, metadata = heatmap_inputs
    with pytest.raises(SkillError) as caught:
        run_heatmap(deg, metadata, "cohort", tmp_path / "out", normalized_expression=expression if both else None,
                    raw_counts=expression if both else None)
    assert caught.value.code == "INVALID_PARAMETER"


@pytest.mark.parametrize("case,code", [("failed", "RSCRIPT_FAILED"), ("missing", "OUTPUT_MISSING"),
    ("changed", "OUTPUT_INVALID"), ("bad_png", "OUTPUT_INVALID"), ("wrong_count", "OUTPUT_INVALID")])
def test_render_failure(case: str, code: str, heatmap_inputs: tuple[Path, Path, Path],
                         tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expression, deg, metadata = heatmap_inputs
    def process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        if case == "failed":
            return subprocess.CompletedProcess(command, 9, "started", "test failure")
        result = renderer(command, **kwargs)
        plot = Path(command[command.index("--plot") + 1])
        if case == "missing":
            plot.unlink()
        elif case == "bad_png":
            plot.write_bytes(b"broken")
        elif case == "changed":
            Path(command[command.index("--matrix") + 1]).write_text("tampered")
        elif case == "wrong_count":
            result.stdout = "R_VERSION=test\nN_GENES=999\nN_SAMPLES=4\n"
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    output = tmp_path / "out"
    with pytest.raises(SkillError) as caught:
        run_heatmap(deg, metadata, "cohort", output, normalized_expression=expression)
    assert caught.value.code == code
    assert not list(output.glob("*.csv")) and not (output / "summary.json").exists()


def test_raw_path_and_stage_failure(input_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    counts, metadata = input_files
    deg = tmp_path / "deg.csv"
    deg.write_text("gene_id,log2FoldChange,pvalue,padj\ng1,2,0.001,0.01\n")
    calls: list[str] = []
    def process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(Path(command[1]).name)
        if calls[-1] == "normalize_vst.R":
            assert "--vst-module" in command
            Path(command[command.index("--output") + 1]).write_text("gene_id,c1,c2,t1,t2\ng1,1,2,5,6\ng2,2,3,1,2\n")
            return subprocess.CompletedProcess(command, 0, "R_VERSION=test\nDESEQ2_VERSION=test\nN_GENES_VST=2\n", "vst warning")
        return subprocess.CompletedProcess(command, 5, "render started", "render failed")
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    with pytest.raises(SkillError) as caught:
        run_heatmap(deg, metadata, "condition", tmp_path / "out", raw_counts=counts)
    assert caught.value.code == "RSCRIPT_FAILED" and calls == ["normalize_vst.R", "plot_heatmap.R"]
    stderr = (tmp_path / "out" / "r_stderr.log").read_text()
    assert "vst warning" in stderr and "render failed" in stderr


def test_cli_and_existing_output(heatmap_inputs: tuple[Path, Path, Path], tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    expression, deg, metadata = heatmap_inputs
    assert main(["heatmap"]) == 2
    assert json.loads(capsys.readouterr().out)["operation"] == "heatmap"
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", renderer)
    args = ["heatmap", "--normalized-expression", str(expression), "--deg-result-file", str(deg),
            "--metadata", str(metadata), "--group-column", "cohort", "--output-dir", str(tmp_path / "out"), "--top-n", "1"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
    assert main(args) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "OUTPUT_NOT_EMPTY"
    assert "Traceback" not in captured.out + captured.err
