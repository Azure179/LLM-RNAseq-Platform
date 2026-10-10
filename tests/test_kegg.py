"""KEGG input/runner/output contracts; mock results are explicitly synthetic fixtures."""

import csv
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from result_helpers import assert_result_contract

from bulk_rnaseq.cli import main
from bulk_rnaseq.enrichment_inputs import prepare_enrichment
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.kegg_mapping import read_kegg_mapping
from bulk_rnaseq.skills.kegg_enrichment import KEGG_COLUMNS, run_kegg_enrichment
from plot_helpers import fixture_png


@pytest.fixture
def kegg_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    query, mapping, background = (tmp_path / name for name in ("query.txt", "mapping.tsv", "background.txt"))
    query.write_text("".join(f"g{i}\n" for i in range(1, 6)))
    background.write_text("".join(f"g{i}\n" for i in range(1, 21)))
    mapping.write_text("gene_id\tpathway_id\n" + "".join(
        f"g{i}\t{term}\n" for i in range(1, 21)
        for term in (["map00010", "map01100"] if i <= 5 else ["map00020", "map01100"])))
    return query, mapping, background


def fake_kegg(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
    assert Path(command[1]).name == "kegg_enrichment.R"  # Never re-run GO or DESeq2.
    assert kwargs["capture_output"] and not kwargs.get("shell", False)
    opt = dict(zip(command[2::2], command[3::2]))
    output = Path(opt["--output"])
    rows = [
        ["map00010", "map00010", 5, 5, 5, 20, 1, .25, 4, 1 / 15504, 3 / 15504, "g1;g2;g3;g4;g5"],
        ["map00020", "map00020", 0, 5, 15, 20, 0, .75, 0, 1, 1, ""],
        ["map01100", "map01100", 5, 5, 20, 20, 1, 1, 1, 1, 1, "g1;g2;g3;g4;g5"],
    ]
    enriched = [r for r in rows if r[2] > 0 and r[9] <= float(opt["--pvalue"]) and r[10] <= float(opt["--padj"])]
    for name, data in (("kegg_all_results.csv", rows), ("kegg_enrichment.csv", enriched)):
        with (output / name).open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(KEGG_COLUMNS)
            writer.writerows(data)
    for name in ("kegg_dotplot.png", "kegg_barplot.png"):
        (output / name).write_bytes(fixture_png())
    return subprocess.CompletedProcess(command, 0, f"R_VERSION=fixture\nSTATS_VERSION=fixture\nN_TERMS_TESTED=3\nN_ENRICHED={len(enriched)}\n", "")


@pytest.mark.parametrize("empty", [False, True])
def test_normal_and_empty(empty: bool, kegg_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = [p.read_bytes() for p in kegg_files]
    process = Mock(side_effect=fake_kegg)
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    result = run_kegg_enrichment(*kegg_files[:2], tmp_path / "out", universe=kegg_files[2], padj_threshold=0 if empty else .05)
    process.assert_called_once()
    assert [p.read_bytes() for p in kegg_files] == before
    summary = json.loads(result.summary_json.read_text())
    assert_result_contract(result, "rnaseq.kegg")
    assert summary["operation"] == "kegg_enrichment" and summary["status"] == "succeeded"
    assert summary["metrics"]["n_query_mapped"] == 5 and summary["metrics"]["n_universe"] == 20
    assert summary["metrics"]["n_enriched"] == (0 if empty else 1)
    assert summary["methods"]["background_source"] == "explicit_universe"
    assert "enrichment_common.R" in summary["provenance"]["r_scripts_sha256"]
    for name in summary["artifacts"].values():
        assert (result.output_dir / name).is_file()
    with (result.output_dir / "kegg_all_results.csv").open() as handle:
        assert all(r["description"] == r["pathway_id"] for r in csv.DictReader(handle))


@pytest.mark.parametrize("case,expected", [
    ("query_missing", "FILE_NOT_FOUND"), ("mapping_missing", "FILE_NOT_FOUND"), ("background_missing", "FILE_NOT_FOUND"),
    ("no_mapping", "NO_MAPPED_GENES"), ("outside", "GENES_OUTSIDE_UNIVERSE"), ("empty_background", "GENE_LIST_EMPTY"),
    ("bad_header", "MAPPING_FORMAT_ERROR"), ("bad_width", "MAPPING_FORMAT_ERROR"), ("ko_id", "INVALID_PATHWAY_ID"),
    ("missing_gene", "INVALID_IDENTIFIER"), ("no_pathways", "NO_PATHWAY_ASSOCIATIONS"), ("ko_only", "PATHWAY_MAPPING_REQUIRED"),
    ("mixed", "MIXED_PATHWAY_NAMESPACES"), ("different_species", "MIXED_PATHWAY_NAMESPACES"),
])
def test_invalid_inputs(case: str, expected: str, kegg_files: tuple[Path, Path, Path], tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    query, mapping, background = kegg_files
    if case.endswith("_missing"):
        {"query_missing": query, "mapping_missing": mapping, "background_missing": background}[case].unlink()
    elif case == "no_mapping":
        query.write_text("absent\n")
        background.write_text(background.read_text() + "absent\n")
    elif case == "outside":
        query.write_text("outside\n")
    elif case == "empty_background":
        background.write_text("gene_id\n")
    elif case == "bad_header":
        mapping.write_text("gene\tpathway\ng1\tmap00010\n")
    elif case == "bad_width":
        mapping.write_text("gene_id\tpathway_id\ng1\tmap00010\textra\n")
    elif case == "ko_id":
        mapping.write_text("gene_id\tpathway_id\ng1\tko:K01512\n")
    elif case == "missing_gene":
        mapping.write_text("gene_id\tpathway_id\n\tmap00010\n")
    elif case == "no_pathways":
        mapping.write_text("query\tKEGG_Pathway\ng1\t-\n")
    elif case == "ko_only":
        mapping.write_text("#query\tKEGG_ko\ng1\tko:K01512\n")
    elif case == "mixed":
        mapping.write_text("gene_id\tpathway_id\ng1\tmap00010\ng2\thsa00010\n")
    elif case == "different_species":
        mapping.write_text("gene_id\tpathway_id\ng1\thsa00010\ng2\tmmu00010\n")
    process = Mock(side_effect=AssertionError("Invalid input must not reach R"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    with pytest.raises(SkillError) as caught:
        run_kegg_enrichment(query, mapping, tmp_path / "out", universe=background)
    assert caught.value.code == expected and caught.value.operation == "kegg_enrichment"
    process.assert_not_called()
    assert not (tmp_path / "out" / "summary.json").exists()


def test_eggnog_aliases(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.emapper.annotations"
    path.write_text("## annotation fixture\n#\n#query\tKEGG_ko\tKEGG_Pathway\tDescription\n"
                    "rna-001.1\tko:K01512\tko00620,map00620,path:map00620\tprotein description\n"
                    "rna-002.1\tko:K15014\t-\tother protein\n## footer\n")
    mapping = read_kegg_mapping(path)
    assert mapping.pairs == {("rna-001.1", "map00620")}
    assert mapping.terms["map00620"][0] == ""  # Never use protein description.
    assert mapping.n_duplicate_pairs == 2 and mapping.n_unannotated_rows == 1
    assert ("ko00620", "map00620") in mapping.aliases


def test_custom_name_and_namespace(tmp_path: Path) -> None:
    path = tmp_path / "mapping.csv"
    path.write_text("gene_id,pathway_id,pathway_name\n001,path:hsa00010,User-supplied label\n")
    mapping = read_kegg_mapping(path)
    assert mapping.pairs == {("001", "hsa00010")} and mapping.namespace == "hsa"
    assert mapping.terms["hsa00010"][0] == "User-supplied label"
    path.write_text(path.read_text() + "002,hsa00010,Conflicting label\n")
    with pytest.raises(SkillError) as caught:
        read_kegg_mapping(path)
    assert caught.value.code == "MAPPING_CONFLICT"


def test_effective_background(kegg_files: tuple[Path, Path, Path]) -> None:
    mapping = read_kegg_mapping(kegg_files[1])
    prepared = prepare_enrichment(frozenset({"g1", "unmapped"}), frozenset({"g1", "g2", "g6", "unmapped"}), mapping.pairs)
    assert prepared.query == {"g1"} and prepared.universe == {"g1", "g2", "g6"}
    assert prepared.term_genes["map00010"] == {"g1", "g2"}
    assert prepared.metrics["n_query_unmapped"] == 1


@pytest.mark.parametrize("parameters", [{"padj_threshold": -1}, {"padj_threshold": 2}, {"padj_threshold": True},
                                         {"padj_threshold": float("nan")}, {"timeout": 0}, {"pvalue_threshold": float("inf")}])
def test_parameters(parameters: dict, kegg_files: tuple[Path, Path, Path], tmp_path: Path) -> None:
    with pytest.raises(SkillError) as caught:
        run_kegg_enrichment(*kegg_files[:2], tmp_path / "out", universe=kegg_files[2], **parameters)
    assert caught.value.code == "INVALID_PARAMETER"


def test_xlsx_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "mapping.xlsx"
    path.touch()
    book = Mock()
    book.sheetnames = ["Sheet1"]
    sheet = Mock()
    sheet.iter_rows.return_value = iter([("## header notes", None), ("#", None), ("query", "KEGG_Pathway"),
                                        ("rna-001.1", "ko00010,map00010")])
    book.__getitem__ = Mock(return_value=sheet)
    load = Mock(return_value=book)
    monkeypatch.setattr("openpyxl.load_workbook", load)
    assert read_kegg_mapping(path).pairs == {("rna-001.1", "map00010")}
    load.assert_called_once_with(path, read_only=True, data_only=False)
    book.close.assert_called_once()


@pytest.mark.parametrize("kind", ["r_fail", "missing", "png", "count", "input_changed"])
def test_execution_errors(kind: str, kegg_files: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def faulty(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        if kind == "r_fail":
            return subprocess.CompletedProcess(command, 2, "started", "synthetic failure")
        result = fake_kegg(command, **kwargs)
        stage = Path(command[command.index("--output") + 1])
        if kind == "missing":
            (stage / "kegg_enrichment.csv").unlink()
        elif kind == "png":
            (stage / "kegg_dotplot.png").write_bytes(b"bad image")
        elif kind == "count":
            path = stage / "kegg_all_results.csv"
            path.write_text(path.read_text().replace(",5,5,5,20,", ",4,5,5,20,"))
        else:
            (stage / "universe_used.csv").write_text("changed\n")
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", faulty)
    with pytest.raises(SkillError) as caught:
        run_kegg_enrichment(*kegg_files[:2], tmp_path / "out", universe=kegg_files[2])
    assert caught.value.code == {"r_fail": "RSCRIPT_FAILED", "missing": "OUTPUT_MISSING"}.get(kind, "OUTPUT_INVALID")
    assert not (tmp_path / "out" / "summary.json").exists()


def test_cli_background_required(kegg_files: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = main(["kegg", "--gene-list", str(kegg_files[0]), "--gene-to-pathway-mapping", str(kegg_files[1]),
                 "--output-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1 and "Traceback" not in captured.err
    payload = json.loads(captured.out)
    assert payload["operation"] == "kegg_enrichment" and payload["error"]["code"] == "BACKGROUND_REQUIRED"
