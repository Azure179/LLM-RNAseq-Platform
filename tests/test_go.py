"""GO contracts using explicitly synthetic associations and mocked R outputs."""

import csv
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from result_helpers import assert_result_contract

from bulk_rnaseq.cli import main
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.go_artifacts import GO_COLUMNS
from bulk_rnaseq.go_data import read_gene_list
from bulk_rnaseq.go_data import prepare_go
from bulk_rnaseq.go_mapping import read_go_mapping
from bulk_rnaseq.skills.go_enrichment import run_go_enrichment
from plot_helpers import fixture_png


@pytest.fixture
def go_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    query, mapping, background = (tmp_path / name for name in ("query.txt", "mapping.tsv", "background.txt"))
    query.write_text("g1\ng2\ng3\ng4\ng5\n", encoding="utf-8")
    background.write_text("".join(f"g{i}\n" for i in range(1, 21)), encoding="utf-8")
    mapping.write_text("gene_id\tgo_id\tontology\tgo_name\n" + "".join(
        f"g{i}\t{term}\tBP\tSynthetic association set\n"
        for i in range(1, 21) for term in (["GO:0008150", "GO:0006950"] if i <= 5 else ["GO:0008150", "GO:0006281"])
    ), encoding="utf-8")
    return query, mapping, background


def fake_go(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
    """Known fixture values, never used for real analysis or exported as biological evidence."""
    assert Path(command[1]).name == "go_enrichment.R"
    assert kwargs["capture_output"] and not kwargs.get("shell", False)
    options = dict(zip(command[2::2], command[3::2]))
    output = Path(options["--output"])
    rows = [
        ["GO:0006950", "Synthetic association set", "BP", 5, 5, 5, 20, 1, .25, 4, 1 / 15504, 3 / 15504, "g1;g2;g3;g4;g5"],
        ["GO:0006281", "Synthetic association set", "BP", 0, 5, 15, 20, 0, .75, 0, 1, 1, ""],
        ["GO:0008150", "Synthetic association set", "BP", 5, 5, 20, 20, 1, 1, 1, 1, 1, "g1;g2;g3;g4;g5"],
    ]
    filtered = [row for row in rows if row[3] > 0 and row[10] <= float(options["--pvalue"]) and row[11] <= float(options["--padj"])]
    for name, data in (("go_all_results.csv", rows), ("go_enrichment.csv", filtered)):
        with (output / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(GO_COLUMNS)
            writer.writerows(data)
    for name in ("go_dotplot.png", "go_barplot.png"):
        (output / name).write_bytes(fixture_png())
    return subprocess.CompletedProcess(command, 0, f"R_VERSION=test-fixture\nSTATS_VERSION=test-fixture\nN_TERMS_TESTED=3\nN_ENRICHED={len(filtered)}\n", "")


def test_normal_outputs(go_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    query, mapping, background = go_files
    before = [p.read_bytes() for p in go_files]
    process = Mock(side_effect=fake_go)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    result = run_go_enrichment(query, mapping, tmp_path / "out", analysis_genes=background)
    process.assert_called_once()
    assert [p.read_bytes() for p in go_files] == before
    summary = json.loads(result.summary_json.read_text())
    assert_result_contract(result, "rnaseq.go")
    assert summary["operation"] == "go_enrichment" and summary["status"] == "succeeded"
    assert summary["metrics"]["n_enriched"] == 1 and summary["metrics"]["n_universe"] == 20
    assert summary["methods"]["background_source"] == "analysis_genes"
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()


@pytest.mark.parametrize("case,code", [
    ("no_mapping", "NO_MAPPED_GENES"), ("bad_header", "MAPPING_FORMAT_ERROR"), ("bad_go", "INVALID_GO_ID"),
    ("missing_query", "FILE_NOT_FOUND"), ("missing_mapping", "FILE_NOT_FOUND"), ("missing_background", "FILE_NOT_FOUND"),
    ("outside", "GENES_OUTSIDE_UNIVERSE"), ("missing_id", "INVALID_IDENTIFIER"), ("empty_mapping", "MAPPING_FORMAT_ERROR"),
    ("unknown_ontology", "ONTOLOGY_INFORMATION_REQUIRED"), ("row_width", "MAPPING_FORMAT_ERROR"),
])
def test_invalid_inputs(case: str, code: str, go_files: tuple[Path, Path, Path], tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    query, mapping, background = go_files
    ontology = "ALL"
    if case == "no_mapping":
        query.write_text("unmapped\n")
        background.write_text(background.read_text() + "unmapped\n")
    elif case == "bad_header":
        mapping.write_text("gene\tgo\ng1\tGO:0006950\n")
    elif case == "bad_go":
        mapping.write_text(mapping.read_text().replace("GO:0006950", "GO:123"))
    elif case.startswith("missing_") and case != "missing_id":
        {"missing_query": query, "missing_mapping": mapping, "missing_background": background}[case].unlink()
    elif case == "outside":
        query.write_text("outside\n")
    elif case == "missing_id":
        mapping.write_text("gene_id\tgo_id\n\tGO:0006950\n")
    elif case == "empty_mapping":
        mapping.write_text("gene_id\tgo_id\ng1\t-\n")
    elif case == "unknown_ontology":
        mapping.write_text("query\tGOs\ng1\tGO:0006950\n")
        ontology = "BP"
    elif case == "row_width":
        mapping.write_text("gene_id\tgo_id\ng1\tGO:0006950\textra\n")
    process = Mock(side_effect=AssertionError("Invalid input must not reach R"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    with pytest.raises(SkillError) as caught:
        run_go_enrichment(query, mapping, tmp_path / "out", universe=background, ontology=ontology)
    assert caught.value.code == code and caught.value.operation == "go_enrichment"
    process.assert_not_called()
    assert not (tmp_path / "out" / "summary.json").exists()


@pytest.mark.parametrize("kwargs", [{"pvalue_threshold": -1}, {"padj_threshold": 1.1}, {"padj_threshold": float("nan")},
                                    {"pvalue_threshold": True}, {"timeout": 0}, {"ontology": "other"}])
def test_parameters(kwargs: dict, go_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    query, mapping, background = go_files
    with pytest.raises(SkillError) as caught:
        run_go_enrichment(query, mapping, tmp_path / "out", universe=background, **kwargs)
    assert caught.value.code == "INVALID_PARAMETER"


def test_background_required(go_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    with pytest.raises(SkillError, match="Provide universe") as caught:
        run_go_enrichment(*go_files[:2], tmp_path / "out")
    assert caught.value.code == "BACKGROUND_REQUIRED"


def test_full_deg_background(tmp_path: Path) -> None:
    deg = tmp_path / "deg.csv"
    deg.write_text("gene_id,log2FoldChange,pvalue,padj\n001,2,.001,.01\n002,.1,.8,.9\n003,0,.2,NA\n004,NA,NA,NA\n")
    genes, removed = read_gene_list(deg, analysis_background=True)
    assert genes == {"001", "002"} and removed == 2
    assert read_gene_list(deg)[0] == {"001", "002", "003", "004"}  # Gene-list tables are not auto-filtered.


def test_effective_universe_excludes_annotation_only_genes(go_files: tuple[Path, Path, Path]) -> None:
    query, mapping, background = go_files
    mapping.write_text(mapping.read_text() + "annotation_only\tGO:0006950\tBP\tSynthetic association set\n")
    prepared = prepare_go(frozenset({"g1", "unmapped"}), frozenset({"g1", "g2", "g6", "unmapped"}), read_go_mapping(mapping), "BP")
    assert prepared.query == {"g1"} and prepared.universe == {"g1", "g2", "g6"}
    assert prepared.term_genes["GO:0006950"] == {"g1", "g2"}
    assert prepared.metrics["n_query_unmapped"] == 1 and prepared.metrics["n_background_unmapped"] == 1


def test_custom_csv_and_conflict(tmp_path: Path) -> None:
    path = tmp_path / "mapping.csv"
    path.write_text("gene_id,go_id,ontology,go_name\n001,GO:0006950,BP,stress\n002,GO:0006950,BP,stress\n")
    assert ("001", "GO:0006950") in read_go_mapping(path).pairs
    path.write_text(path.read_text() + "003,GO:0006950,MF,stress\n")
    with pytest.raises(SkillError) as caught:
        read_go_mapping(path)
    assert caught.value.code == "MAPPING_CONFLICT"


def test_exclusive_background(go_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    with pytest.raises(SkillError) as caught:
        run_go_enrichment(*go_files[:2], tmp_path / "out", universe=go_files[2], analysis_genes=go_files[2])
    assert caught.value.code == "INVALID_PARAMETER"


def test_eggnog_text(tmp_path: Path) -> None:
    path = tmp_path / "demo.emapper.annotations"
    path.write_text("## emapper fixture\n#\n#query\tDescription\tGOs\n"
                    "rna-001.1\tprotein description\tGO:0006950,GO:0006281\n"
                    "rna-002.1\tother\t-\n"
                    "rna-001.1\tprotein description\tGO:0006950\n## footer\n", encoding="utf-8")
    mapping = read_go_mapping(path)
    assert mapping.pairs == {("rna-001.1", "GO:0006950"), ("rna-001.1", "GO:0006281")}
    assert mapping.terms["GO:0006950"] == ("", "")
    assert mapping.n_duplicate_pairs == 1 and mapping.n_unannotated_rows == 1


def test_xlsx_comments_and_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock read-only workbook interface; real reference workbook is also checked manually.
    path = tmp_path / "mapping.xlsx"
    path.touch()
    workbook = Mock()
    workbook.sheetnames = ["Sheet1"]
    worksheet = Mock()
    worksheet.iter_rows.return_value = iter([("## emapper metadata\n## comments", None), ("#", None),
                                            ("query", "GOs"), ("rna-001.1", "GO:0006950,GO:0006281")])
    workbook.__getitem__ = Mock(return_value=worksheet)
    load = Mock(return_value=workbook)
    monkeypatch.setattr("openpyxl.load_workbook", load)
    mapping = read_go_mapping(path)
    load.assert_called_once_with(path, read_only=True, data_only=False)
    workbook.close.assert_called_once()
    assert len(mapping.pairs) == 2


def test_corrupt_xlsx(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not a workbook")
    with pytest.raises(SkillError) as caught:
        read_go_mapping(path)
    assert caught.value.code == "MAPPING_FORMAT_ERROR"


def test_xlsx_requires_sheet_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "mapping.xlsx"
    path.touch()
    workbook = Mock()
    workbook.sheetnames = ["first", "second"]
    monkeypatch.setattr("openpyxl.load_workbook", Mock(return_value=workbook))
    with pytest.raises(SkillError) as caught:
        read_go_mapping(path)
    assert caught.value.code == "MAPPING_SHEET_REQUIRED"
    workbook.close.assert_called_once()


def test_r_failure(go_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "started", "test failure"))
    with pytest.raises(SkillError) as caught:
        run_go_enrichment(*go_files[:2], tmp_path / "out", universe=go_files[2])
    assert caught.value.code == "RSCRIPT_FAILED"
    assert (tmp_path / "out" / "r_stderr.log").read_text() == "test failure"
    assert not (tmp_path / "out" / "summary.json").exists()


@pytest.mark.parametrize("kind", ["missing", "bad_counts", "corrupt_plot", "modify_input"])
def test_bad_outputs(kind: str, go_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def corrupt(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        result = fake_go(command, **kwargs)
        output = Path(command[command.index("--output") + 1])
        if kind == "missing":
            (output / "go_enrichment.csv").unlink()
        elif kind == "bad_counts":
            path = output / "go_all_results.csv"
            path.write_text(path.read_text().replace(",5,5,5,20,", ",4,5,5,20,"))
        elif kind == "corrupt_plot":
            (output / "go_dotplot.png").write_bytes(b"broken")
        else:
            (output / "query_used.csv").write_text("changed\n")
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", corrupt)
    with pytest.raises(SkillError) as caught:
        run_go_enrichment(*go_files[:2], tmp_path / "out", universe=go_files[2])
    assert caught.value.code == ("OUTPUT_MISSING" if kind == "missing" else "OUTPUT_INVALID")
    assert not (tmp_path / "out" / "summary.json").exists()


def test_empty_enrichment(go_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_go)
    result = run_go_enrichment(*go_files[:2], tmp_path / "out", universe=go_files[2], padj_threshold=0)
    assert_result_contract(result, "rnaseq.go")
    summary = json.loads(result.summary_json.read_text())
    assert summary["metrics"]["n_enriched"] == 0
    assert "NO_ENRICHED_TERMS" in {warning["code"] for warning in summary["warnings"]}


def test_cli_structured_error(go_files: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = main(["go", "--gene-list", str(go_files[0]), "--gene-to-go-mapping", str(go_files[1]), "--output-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1 and "Traceback" not in captured.err
    assert json.loads(captured.out)["error"]["code"] == "BACKGROUND_REQUIRED"
