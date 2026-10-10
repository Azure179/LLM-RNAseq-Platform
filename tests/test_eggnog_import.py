"""Synthetic import fixtures; no R, LLM, or biological enrichment results."""

from io import BytesIO
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bulk_rnaseq.go_mapping import read_go_mapping
from bulk_rnaseq.kegg_mapping import read_kegg_mapping
from bulk_rnaseq.workspace import FileImportService, FileRole, ProjectError, ProjectManager
from bulk_rnaseq.workspace.ui_adapter import import_upload, read_download, read_mapping_download
from test_workspace_ui import service, apptest_ipc, APP, DATA, element, open_files


EGGNOG = ("## eggNOG fixture\n#query\tGOs\tKEGG_Pathway\tDescription\n"
          "g1\tGO:0000001,GO:0000002\tko00010,map00010\tProtein not term name\n"
          "g2\tGO:0000002\tmap00020;map00010\tAnother protein\n"
          "g3\t-\t-\tUnannotated\n")


def upload(service, content=EGGNOG, **options):
    return import_upload(service, "First", "fixture.emapper.annotations", BytesIO(content.encode()), "eggnog", **options)


def test_generate_bind_reopen_download_and_context(service):
    filename, content = DATA["count_matrix"]
    assert import_upload(service, "First", filename, BytesIO(content), "count_matrix").status == "succeeded"
    result = upload(service)
    assert result.status == "succeeded", result
    reopened = FileImportService(ProjectManager(service.manager.root))
    project = reopened.manager.open_project("First")
    records = []
    for role, filename in ((FileRole.GO_MAPPING, "gene_to_go.tsv"), (FileRole.KEGG_MAPPING, "gene_to_kegg.tsv")):
        relative = getattr(project.metadata.files, role.value)
        path = project.root / relative
        assert path.name == filename and path.is_file()
        info = reopened.get_imported_file_info("First", role)
        assert info["source_kind"] == "eggnog"
        assert info["summary"]["coverage"]["fraction"] == 1
        assert info["summary"]["source_unannotated_rows"] == 1
        assert reopened.validate_imported_file("First", role).status == "succeeded"
        name, data = read_mapping_download(reopened, "First", role, expected_binding=relative)
        assert name == filename and data == path.read_bytes()
        assert read_download(reopened, "First", role, expected_binding=relative)[1] == EGGNOG.encode()
        records.append(json.loads((path.parent.parent / "import.json").read_text()))
    assert records[0]["batch_id"] == records[1]["batch_id"]
    assert records[0]["source_sha256"] == records[1]["source_sha256"]
    go = read_go_mapping(project.root / project.metadata.files.go_mapping)
    kegg = read_kegg_mapping(project.root / project.metadata.files.kegg_mapping)
    assert len(go.pairs) == 3 and len(kegg.pairs) == 3
    assert all(value == ("", "") for value in go.terms.values())
    assert set(kegg.terms) == {"map00010", "map00020"}
    context = reopened.build_execution_context("First")
    assert context.bindings["rnaseq.go"]["gene_to_go_mapping"] == project.metadata.files.go_mapping
    assert context.bindings["rnaseq.kegg"]["gene_to_pathway_mapping"] == project.metadata.files.kegg_mapping
    assert reopened.get_imported_file_info("Second", FileRole.GO_MAPPING)["status"] == "missing"
    assert not list((project.root / "runtime").iterdir())


def test_confirmed_priority_and_custom_mode_coexist(service):
    for role in ("go_mapping", "kegg_mapping"):
        filename, data = DATA[role]
        assert import_upload(service, "First", filename, BytesIO(data), role).status == "succeeded"
    before = service.manager.open_project("First")
    assert upload(service).error_code == "FILE_ROLE_ALREADY_BOUND"
    assert service.manager.open_project("First").metadata == before.metadata
    assert upload(service, replace=True).status == "succeeded"
    for role in ("go_mapping", "kegg_mapping"):
        assert (before.root / getattr(before.metadata.files, role)).is_file()
    # Explicit independent override still works, without changing the other role.
    kegg_binding = service.manager.open_project("First").metadata.files.kegg_mapping
    filename, data = DATA["go_mapping"]
    assert import_upload(service, "First", filename, BytesIO(data), "go_mapping", replace=True).status == "succeeded"
    assert service.get_imported_file_info("First", "go_mapping")["source_kind"] == "direct_upload"
    assert service.manager.open_project("First").metadata.files.kegg_mapping == kegg_binding


@pytest.mark.parametrize("content,code", [
    ("", "MAPPING_FORMAT_ERROR"),
    (EGGNOG.replace("GO:0000001", "GO:bad"), "INVALID_GO_ID"),
    (EGGNOG.replace("map00020", "K00020"), "INVALID_PATHWAY_ID"),
    (EGGNOG.replace("KEGG_Pathway", "KEGG_ko"), "PATHWAY_MAPPING_REQUIRED"),
    ("query\tGOs\tKEGG_Pathway\ng1\tGO:0000001\t-\n", "NO_PATHWAY_ASSOCIATIONS"),
    ("query\tGOs\tKEGG_Pathway\ng1\t-\tmap00010\n", "MAPPING_FORMAT_ERROR"),
    ("query\tGOs\tKEGG_Pathway\ng1\tGO:0000001\n", "MAPPING_FORMAT_ERROR"),
    ("gene_id\tgo_id\tpathway_id\ng1\tGO:0000001\tmap00010\n", "EGGNOG_FORMAT_REQUIRED"),
])
def test_bad_annotation_never_partially_replaces(service, content, code):
    assert upload(service).status == "succeeded"
    before = service.manager.open_project("First").metadata
    result = upload(service, content, replace=True)
    assert result.status == "failed" and result.error_code == code
    assert service.manager.open_project("First").metadata == before


@pytest.mark.parametrize("replace,sheet", [("yes", None), (True, ""), (False, 1), (False, "Sheet1")])
def test_invalid_import_options(service, replace, sheet):
    assert upload(service, replace=replace, mapping_sheet=sheet).status == "failed"
    assert service.manager.open_project("First").metadata.files.go_mapping is None


def test_commit_failure_preserves_pair(service, monkeypatch):
    assert upload(service).status == "succeeded"
    before = service.manager.open_project("First").metadata
    def fail(*args, **kwargs):
        raise ProjectError("PROJECT_CHANGED", "Fixture concurrent update")
    monkeypatch.setattr(service.manager, "commit_file_bindings", fail)
    assert upload(service, replace=True).error_code == "PROJECT_CHANGED"
    assert service.manager.open_project("First").metadata == before


def test_atomic_commit_cas(service):
    before = service.manager.open_project("First").metadata
    service.manager.update_project_metadata("First", {"description": "Concurrent change"})
    with pytest.raises(ProjectError) as error:
        service.manager.commit_file_bindings("First", {FileRole.GO_MAPPING: None, FileRole.KEGG_MAPPING: None},
                                             expected_updated_at=before.updated_at)
    assert error.value.code == "PROJECT_CHANGED"
    assert service.manager.open_project("First").metadata.description == "Concurrent change"


def test_gene_ids_unchanged_low_coverage_and_stale_download(service):
    filename, data = DATA["count_matrix"]
    import_upload(service, "First", filename, BytesIO(data), "count_matrix")
    result = upload(service, EGGNOG.replace("g1", "rna-Asc001.1").replace("g2", "rna-Asc002.1"))
    assert result.status == "succeeded"
    assert result.summary["go_mapping"]["coverage"]["fraction"] == 0
    assert any(w["code"] == "LOW_MAPPING_COVERAGE" for w in result.warnings)
    before = service.manager.open_project("First")
    assert ("rna-Asc001.1", "GO:0000001") in read_go_mapping(before.root / before.metadata.files.go_mapping).pairs
    assert upload(service, replace=True).status == "succeeded"
    with pytest.raises(ProjectError):
        read_mapping_download(service, "First", "go_mapping", expected_binding=before.metadata.files.go_mapping)


def test_xlsx_and_source_integrity(service, tmp_path):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Annotations"
    sheet.append(["#query", "GOs", "KEGG_Pathway"])
    sheet.append(["g1", "GO:0000001", "ko00010"])
    workbook.create_sheet("Other")
    path = tmp_path / "annotation.xlsx"
    workbook.save(path)
    assert service.import_eggnog("First", path).error_code == "MAPPING_SHEET_REQUIRED"
    assert service.import_eggnog("First", path, mapping_sheet="Annotations").status == "succeeded"
    project = service.manager.open_project("First")
    record = service._record(project, FileRole.GO_MAPPING)
    assert record["mapping_sheet"] is None and record["source_sheet"] == "Annotations"
    assert service.validate_imported_file("First", "go_mapping").status == "succeeded"
    (project.root / record["original_path"]).write_bytes(b"changed test fixture")
    assert service.get_imported_file_info("First", "go_mapping")["error_code"] == "IMPORTED_FILE_CHANGED"


def test_streamlit_eggnog_upload_confirm_error_download_and_restart(service, apptest_ipc):
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    assert at.file_uploader[0].label == "eggNOG annotations file"
    element(at.file_uploader, "eggNOG annotations file").set_value(("fixture.annotations", EGGNOG.encode(), "text/plain")).run()
    element(at.button, "Import eggNOG → GO + KEGG").click().run()
    assert not at.exception and not at.error
    assert {d.label for d in at.download_button} >= {"Download generated gene_to_go.tsv", "Download generated gene_to_kegg.tsv"}
    before = service.manager.open_project("First").metadata
    element(at.file_uploader, "eggNOG annotations file").set_value(("bad.annotations", b"query\tGOs\tKEGG_Pathway\ng1\tbad\tmap00010\n", "text/plain")).run()
    assert element(at.button, "Import eggNOG → GO + KEGG").disabled
    element(at.checkbox, "Confirm Replace GO + KEGG with eggNOG").check().run()
    element(at.button, "Import eggNOG → GO + KEGG").click().run()
    assert at.error and not at.exception
    assert service.manager.open_project("First").metadata == before
    reopened = AppTest.from_file(APP, default_timeout=15).run()
    element(reopened.selectbox, "Projects").select("First").run()
    open_files(reopened)
    assert any(d.label == "Download generated gene_to_go.tsv" for d in reopened.download_button)
