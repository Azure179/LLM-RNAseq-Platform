"""Explicit significance boundaries and legitimate DESeq2 NA handling."""

import csv
from pathlib import Path

import pytest

from bulk_rnaseq.artifacts import COLUMNS, verify_results
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import InputOverview


@pytest.mark.parametrize("lfc,padj,expected", [
    ("1", "0.049", 1), ("-1", "0.049", 1), ("0.999", "0.049", 0),
    ("2", "0.05", 0), ("2", "NA", 0), ("", "", 0),
])
def test_boundaries_and_na(tmp_path: Path, lfc: str, padj: str, expected: int) -> None:
    full, filtered = tmp_path / "full.csv", tmp_path / "filtered.csv"
    row = ["g1", "40", lfc, "0.2", "2", "0.01", padj]
    for path, rows in ((full, [row]), (filtered, [row] if expected else [])):
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
    overview = InputOverview(1, ("c1", "c2", "t1", "t2"), frozenset({"g1"}), 2, 2)
    metrics = verify_results(full, filtered, overview, 0.05, 1)
    assert metrics["significant_genes"] == expected
    assert metrics["padj_na"] == (padj in {"", "NA"})


def test_input_read_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    input_files: tuple[Path, Path]) -> None:
    from bulk_rnaseq.skills.deg import run_deg
    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("Injected unreadable input")
    monkeypatch.setattr("bulk_rnaseq.run_io.shutil.copyfile", denied)
    with pytest.raises(SkillError) as error:
        run_deg(*input_files, "treatment", "control", tmp_path / "output")
    assert error.value.code == "INPUT_READ_ERROR"
