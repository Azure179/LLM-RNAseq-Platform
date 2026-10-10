"""DEG-to-plot contracts. Handwritten rows are test cases, not real DEG results."""

from collections import Counter
import csv
import json
import math
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from result_helpers import assert_result_contract

from bulk_rnaseq.cli import main
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.skills.volcano import run_volcano
from bulk_rnaseq.volcano_data import prepare_plot_data, read_deg_results
from plot_helpers import fixture_png


@pytest.fixture
def deg_file(tmp_path: Path) -> Path:
    path = tmp_path / "DEG input.csv"
    path.write_text("gene_id,log2FoldChange,pvalue,padj\n"
                    "up,1,0.001,0.01\ndown,-1,0.001,0.01\n"
                    "boundary,2,0.01,0.05\nsmall,0.5,0.001,0.01\n"
                    "zero,2,0,0\nmissing,3,0.1,NA\n"
                    "no_effect,NA,NA,NA\nexact_zero,0,0,0\n", encoding="utf-8")
    return path


def fake_renderer(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
    assert Path(command[1]).name == "plot_volcano.R"
    assert "--counts" not in command and "--metadata" not in command
    assert kwargs["capture_output"] is True and not kwargs.get("shell", False)
    with Path(command[command.index("--data") + 1]).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    Path(command[command.index("--plot") + 1]).write_bytes(fixture_png())
    count = sum(row["plotted"] == "true" for row in rows)
    return subprocess.CompletedProcess(command, 0, f"R_VERSION=test-fixture\nN_PLOTTED={count}\n", "")


def test_normal_volcano(deg_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock(side_effect=fake_renderer)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", runner)
    original = deg_file.read_bytes()
    result = run_volcano(deg_file, tmp_path / "plot", top_n_labels=2)
    runner.assert_called_once()
    assert deg_file.read_bytes() == original
    summary = json.loads(result.summary_json.read_text())
    assert_result_contract(result, "rnaseq.volcano")
    assert summary["status"] == "succeeded" and summary["operation"] == "volcano"
    with result.plot_data_csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    totals = Counter(row["category"] for row in rows)
    metrics = summary["metrics"]
    assert (metrics["n_total"], metrics["n_up"], metrics["n_down"], metrics["n_ns"]) == (8, 2, 1, 5)
    assert [metrics[f"n_{name.lower()}"] for name in ("Up", "Down", "NS")] == [totals[n] for n in ("Up", "Down", "NS")]
    assert metrics["n_total"] == sum(totals.values())
    assert metrics["n_plotted"] == 6 and metrics["n_excluded"] == 2
    assert metrics["n_labels"] == sum(bool(row["label"]) for row in rows) == 2
    by_gene = {row["gene_id"]: row for row in rows}
    assert by_gene["missing"]["padj"] == "NA" and by_gene["missing"]["neg_log10_padj"] == ""
    assert by_gene["missing"]["plotted"] == "false"
    assert by_gene["zero"]["padj"] == "0" and math.isfinite(float(by_gene["zero"]["neg_log10_padj"]))
    assert by_gene["exact_zero"]["category"] == "NS"
    assert {w["code"] for w in summary["warnings"]} == {"ZERO_PADJ_DISPLAY", "GENES_NOT_PLOTTED"}
    assert summary["provenance"]["input_sha256"]["deg_result_file"] == sha256(deg_file)
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    for name, digest in summary["provenance"]["artifact_sha256"].items():
        assert sha256(result.output_dir / name) == digest
    assert (result.output_dir / "run.log").is_file()
    assert (result.output_dir / "r_stdout.log").is_file()
    assert not (result.output_dir / "failure.json").exists()


@pytest.mark.parametrize("case,code", [
    ("missing", "FILE_NOT_FOUND"), ("log2FoldChange", "DEG_COLUMNS_MISSING"),
    ("padj", "DEG_COLUMNS_MISSING"), ("gene_id", "DEG_COLUMNS_MISSING"), ("pvalue", "DEG_COLUMNS_MISSING"),
    ("negative_padj", "DEG_VALUE_INVALID"), ("large_pvalue", "DEG_VALUE_INVALID"),
    ("infinite_lfc", "DEG_VALUE_INVALID"), ("bad_number", "DEG_VALUE_INVALID"),
    ("duplicate", "DUPLICATE_ID"), ("width", "DEG_FORMAT_ERROR"), ("empty", "DEG_EMPTY"),
    ("missing_pvalue", "DEG_VALUE_INVALID"), ("underflow", "DEG_VALUE_INVALID"),
])
def test_invalid_input(case: str, code: str, deg_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = deg_file.read_text()
    if case == "missing":
        deg_file.unlink()
    else:
        if case in {"gene_id", "log2FoldChange", "pvalue", "padj"}:
            content = content.replace(case, "wrong", 1)
        elif case == "negative_padj":
            content = content.replace("0.001,0.01", "0.001,-0.01", 1)
        elif case == "large_pvalue":
            content = content.replace("0.001,0.01", "1.5,0.01", 1)
        elif case == "infinite_lfc":
            content = content.replace("up,1,", "up,Inf,")
        elif case == "bad_number":
            content = content.replace("up,1,", "up,abc,")
        elif case == "duplicate":
            content = content.replace("down,", "up,")
        elif case == "width":
            content = content.replace("up,1,", "up,extra,1,")
        elif case == "empty":
            content = content.splitlines()[0] + "\n"
        elif case == "missing_pvalue":
            content = content.replace("0.001,0.01", "NA,0.01", 1)
        elif case == "underflow":
            content = content.replace("0.001,0.01", "0,1e-400", 1)
        deg_file.write_text(content)
    runner = Mock(side_effect=AssertionError("Invalid inputs must not reach the renderer"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", runner)
    output = tmp_path / "failure"
    with pytest.raises(SkillError) as caught:
        run_volcano(deg_file, output)
    assert caught.value.code == code and caught.value.operation == "volcano"
    runner.assert_not_called()
    assert json.loads((output / "failure.json").read_text())["error"]["code"] == code
    assert not (output / "summary.json").exists()


@pytest.mark.parametrize("parameter,value", [
    ("padj_threshold", 0), ("padj_threshold", 1), ("padj_threshold", -0.1),
    ("padj_threshold", 1.1), ("padj_threshold", float("nan")), ("padj_threshold", True),
    ("log2fc_threshold", -1), ("log2fc_threshold", float("inf")),
    ("top_n_labels", -1), ("top_n_labels", 1.5), ("top_n_labels", True), ("timeout", 0),
])
def test_invalid_parameters(parameter: str, value: object, deg_file: Path, tmp_path: Path) -> None:
    with pytest.raises(SkillError) as caught:
        run_volcano(deg_file, tmp_path / "invalid", **{parameter: value})
    assert caught.value.code == "INVALID_PARAMETER" and caught.value.operation == "volcano"


def test_zero_threshold_and_stable_labels(deg_file: Path) -> None:
    records = read_deg_results(deg_file)
    rows, metrics, height = prepare_plot_data(records, 0.05, 0, 2)
    assert metrics["n_up"] == 3 and metrics["n_down"] == 1
    assert next(r for r in rows if r["gene_id"] == "exact_zero")["category"] == "NS"
    reversed_rows, _, _ = prepare_plot_data(records[::-1], 0.05, 0, 2)
    assert {r["label"] for r in rows} == {r["label"] for r in reversed_rows}
    assert height is not None and math.isfinite(height)
    rows, metrics, _ = prepare_plot_data(records, 0.05, 1, 0)
    assert metrics["n_labels"] == 0 and not any(r["label"] for r in rows)


def test_tiny_positive_padj_and_zero(deg_file: Path) -> None:
    deg_file.write_text("gene_id,log2FoldChange,pvalue,padj\ntiny,2,0,5e-324\nzero,-2,0,0\n")
    rows, _, height = prepare_plot_data(read_deg_results(deg_file), 0.05, 1, 10)
    assert 323 < float(rows[0]["neg_log10_padj"]) < height
    assert rows[0]["padj_zero_adjusted"] == "false"


def test_missing_effect_and_all_zero_padj(deg_file: Path) -> None:
    deg_file.write_text("gene_id,log2FoldChange,pvalue,padj\nmissing,NA,0,0\nup,2,0,0\n")
    rows, metrics, height = prepare_plot_data(read_deg_results(deg_file), 0.05, 1, 10)
    assert rows[0]["category"] == "NS" and rows[0]["excluded_reason"] == "log2fc_missing"
    assert metrics["n_plotted"] == 1 and metrics["n_up"] == 1 and height == 10


@pytest.mark.parametrize("case,code", [
    ("exit", "RSCRIPT_FAILED"), ("timeout", "RSCRIPT_TIMEOUT"), ("absent_r", "RSCRIPT_NOT_FOUND"),
    ("missing_plot", "OUTPUT_MISSING"), ("bad_png", "OUTPUT_INVALID"),
    ("changed_data", "OUTPUT_INVALID"), ("wrong_count", "OUTPUT_INVALID"),
])
def test_render_failures(case: str, code: str, deg_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def process(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        if case == "exit":
            return subprocess.CompletedProcess(command, 7, "started", "rendering failed")
        if case == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"started", stderr=b"slow")
        if case == "absent_r":
            raise FileNotFoundError("test R missing")
        result = fake_renderer(command, **kwargs)
        plot = Path(command[command.index("--plot") + 1])
        if case == "missing_plot":
            plot.unlink()
        elif case == "bad_png":
            plot.write_bytes(b"not an image")
        elif case == "changed_data":
            Path(command[command.index("--data") + 1]).write_text("changed")
        elif case == "wrong_count":
            result.stdout = "R_VERSION=test-fixture\nN_PLOTTED=999\n"
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    output = tmp_path / "failure"
    with pytest.raises(SkillError) as caught:
        run_volcano(deg_file, output)
    assert caught.value.code == code and caught.value.operation == "volcano"
    assert not list(output.glob("volcano_*")) and not (output / "summary.json").exists()
    assert (output / "failure.json").is_file()


def test_output_preserved(deg_file: Path, tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "summary.json"
    sentinel.write_text("old result")
    with pytest.raises(SkillError) as caught:
        run_volcano(deg_file, output)
    assert caught.value.code == "OUTPUT_NOT_EMPTY" and sentinel.read_text() == "old result"
    with pytest.raises(SkillError) as caught:
        run_volcano(deg_file, deg_file)
    assert caught.value.code == "OUTPUT_NOT_DIRECTORY"


def test_cli(deg_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["volcano"]) == 2
    assert json.loads(capsys.readouterr().out)["operation"] == "volcano"
    args = ["volcano", "--deg-result-file", str(deg_file), "--output-dir", str(tmp_path / "cli")]
    assert main([*args, "--unknown"]) == 2
    assert json.loads(capsys.readouterr().out)["operation"] == "volcano"
    assert main([*args, "--padj-threshold", "-1"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "INVALID_PARAMETER"
    assert "Traceback" not in captured.out + captured.err
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_renderer)
    args[-1] = str(tmp_path / "cli_success")
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
