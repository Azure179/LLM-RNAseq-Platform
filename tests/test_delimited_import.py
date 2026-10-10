"""Input-format compatibility only; never relax raw-count validation."""

import csv
import json

import pytest
from streamlit.testing.v1 import AppTest

from bulk_rnaseq.workspace import FileImportService, ProjectManager
from test_file_import import imports
from test_workspace_ui import service, apptest_ipc, APP, element, open_files


@pytest.mark.parametrize("suffix,delimiter", [
    (".csv", "\t"), (".tsv", ","), (".xls", "\t"), (".txt", ";"), (".csv", ","),
])
@pytest.mark.parametrize("header", ["Geneid", "Gene_ID", " gene id "])
def test_count_auto_adaptation_preserves_values(imports, suffix, delimiter, header):
    service, paths = imports
    path = paths["count_matrix"].with_suffix(suffix)
    text = delimiter.join([header, "sample-A.bam", "sample-B.bam"]) + "\n"
    text += delimiter.join(["gene-Asc00001", "001", "2"]) + "\n"
    text += delimiter.join(["rna-Asc00002.1", "3", "4"]) + "\n"
    path.write_text(text, encoding="utf-8-sig", newline="")
    original = path.read_bytes()
    assert service.inspect_file(path).suggested_role == "count_matrix"
    result = service.import_file("Test", path, "count_matrix")
    assert result.status == "succeeded", result
    project = service.manager.open_project("Test")
    stored = project.root / result.stored_path
    with stored.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [["gene_id", "sample-A.bam", "sample-B.bam"],
                    ["gene-Asc00001", "001", "2"], ["rna-Asc00002.1", "3", "4"]]
    assert result.summary["n_genes"] == 2 and result.summary["integer_check"] == "PASS"
    assert any(w["code"] == "COUNT_HEADER_ADAPTED" for w in result.warnings)
    record = json.loads((stored.parent.parent / "import.json").read_text())
    assert (project.root / record["original_path"]).read_bytes() == original == path.read_bytes()
    reopened = FileImportService(ProjectManager(service.manager.root))
    assert reopened.validate_imported_file("Test", "count_matrix").status == "succeeded"


def test_metadata_case_and_delimiter_with_reordered_samples(imports):
    service, paths = imports
    assert service.import_file("Test", paths["count_matrix"], "count_matrix").status == "succeeded"
    paths["metadata"].write_text(" Sample_ID \t Group \nt2\tOT\nc2\tT\nt1\tOT\nc1\tT\n", encoding="utf-8")
    result = service.import_file("Test", paths["metadata"], "metadata")
    assert result.status == "succeeded", result
    assert result.validation["sample_matching"]["order_difference"]
    assert result.summary["sample_names"] == ["t2", "c2", "t1", "c1"]
    assert result.summary["replicates_per_group"] == {"OT": 2, "T": 2}


@pytest.mark.parametrize("value,code", [
    ("1.5", "NON_INTEGER_COUNT"), ("-1", "NEGATIVE_COUNT"), ("NA", "MISSING_VALUE"),
    ("", "MISSING_VALUE"), ("Inf", "COUNT_NOT_FINITE"), ("abc", "COUNT_NOT_NUMERIC"),
])
def test_conversion_does_not_bypass_validation_or_replace(imports, value, code):
    service, paths = imports
    assert service.import_file("Test", paths["count_matrix"], "count_matrix").status == "succeeded"
    before = service.manager.open_project("Test").metadata
    path = paths["count_matrix"].with_suffix(".xls")
    path.write_text(f"Geneid\tc1\tc2\ng1\t{value}\t2\n", encoding="utf-8")
    result = service.replace_file("Test", path, "count_matrix")
    assert result.error_code == code
    assert service.manager.open_project("Test").metadata == before


@pytest.mark.parametrize("content,code", [
    ("Geneid,a\ng1,1,2\n", "COUNT_FORMAT_ERROR"),
    ("Geneid,gene_id\ng1,2\n", "DUPLICATE_ID"),
    ("Geneid,a,a\ng1,2,3\n", "DUPLICATE_ID"),
    ("Geneid,a\ng1,2\ng1,3\n", "DUPLICATE_ID"),
    ("id,a\ng1,2\n", "COUNT_FORMAT_ERROR"),
    ("Geneid,a;b\ng1,2;3\n", "AMBIGUOUS_DELIMITER"),
    ("Geneid,a\n\"g1,2\n", "IMPORT_FORMAT_ERROR"),
])
def test_ambiguous_or_invalid_tables_are_not_guessed(imports, content, code):
    service, paths = imports
    paths["count_matrix"].write_text(content, encoding="utf-8")
    assert service.import_file("Test", paths["count_matrix"], "count_matrix").error_code == code


@pytest.mark.parametrize("content", [b"\xd0\xcf\x11\xe0binary", b"PK\x03\x04binary"])
def test_binary_excel_is_not_misread_as_text(imports, content):
    service, paths = imports
    path = paths["count_matrix"].with_suffix(".xls")
    path.write_bytes(content)
    result = service.import_file("Test", path, "count_matrix")
    assert result.error_code == "IMPORT_FORMAT_UNSUPPORTED"
    assert "Excel" in result.message


def test_preamble_quoted_separator_and_zero_copy_original(imports):
    service, paths = imports
    paths["count_matrix"].write_text('\n# Program: featureCounts\nGeneid,"sample,one",sample2\ng1,2,3\n', encoding="utf-8")
    result = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert result.status == "succeeded", result
    assert result.summary["sample_names"] == ["sample,one", "sample2"]


def test_count_and_metadata_ids_are_not_trimmed(imports):
    service, paths = imports
    paths["count_matrix"].write_text("Geneid,a\n g1,2\n", encoding="utf-8")
    assert service.import_file("Test", paths["count_matrix"], "count_matrix").error_code == "INVALID_IDENTIFIER"
    paths["metadata"].write_text("Sample,Group\n s1,T\n", encoding="utf-8")
    assert service.import_file("Test", paths["metadata"], "metadata").status == "failed"


def test_ui_upload_text_xls_and_misnamed_csv(service, apptest_ipc):
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    text = b"Geneid\tc1\tc2\ng1\t1\t2\n"
    element(at.file_uploader, "Count Matrix file").set_value(("counts.xls", text, "text/plain")).run()
    element(at.button, "Import Count Matrix").click().run()
    assert not at.exception and not at.error
    element(at.file_uploader, "Metadata file").set_value(("meta.csv", b"Sample\tGroup\nc2\tT\nc1\tT\n", "text/plain")).run()
    element(at.button, "Import Metadata").click().run()
    assert not at.exception and not at.error
    assert service.get_imported_file_info("First", "metadata")["validation"]["sample_matching"]["exact_match"]
