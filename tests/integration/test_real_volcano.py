"""Real base-R rendering of clearly synthetic test rows; no DESeq2 is executed."""

import csv
import json
from pathlib import Path
import subprocess

import pytest

from bulk_rnaseq.skills.volcano import run_volcano


@pytest.mark.integration
@pytest.mark.parametrize("case", ["normal", "zeros_and_na", "all_na"])
def test_real_volcano(case: str, tmp_path: Path, request: pytest.FixtureRequest,
                      monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "synthetic_test_deg.csv"
    body = {
        "normal": "001,2,0.001,0.01\n002,-2,0.001,0.01\n003,0,0.8,0.9\n",
        "zeros_and_na": "001,2,0,0\n002,-2,0,1e-300\n003,NA,NA,NA\n",
        "all_na": "001,1,0.1,NA\n002,NA,NA,NA\n",
    }[case]
    source.write_text("gene_id,log2FoldChange,pvalue,padj\n" + body)
    before = source.read_bytes()
    real_run = subprocess.run
    commands: list[tuple[str, ...]] = []

    def only_renderer(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        assert Path(command[1]).name == "plot_volcano.R"
        commands.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", only_renderer)
    result = run_volcano(source, tmp_path / "output", rscript_executable=request.config.getoption("--rscript"))
    assert len(commands) == 1 and source.read_bytes() == before
    summary = json.loads(result.summary_json.read_text())
    assert summary["status"] == "succeeded" and summary["operation"] == "volcano"
    assert result.plot_png.stat().st_size > 1000
    assert result.return_code == 0 and "R_VERSION=R version" in result.stdout
    assert "deseq2_version" not in summary["provenance"]
    with result.plot_data_csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["gene_id"] == "001"
    assert summary["metrics"]["n_total"] == len(rows)
    if case == "all_na":
        assert summary["metrics"]["n_plotted"] == 0
        assert "NO_PLOTTABLE_GENES" in {w["code"] for w in summary["warnings"]}
