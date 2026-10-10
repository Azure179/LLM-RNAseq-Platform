"""Workspace file import only: no R, no DeepSeek, no alteration of shared fixtures."""

import json
from pathlib import Path
import shutil

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workspace import FileImportService, FileRole, ImportResult, ProjectError, ProjectManager


COUNTS = "gene_id,c1,c2,t1,t2\ng1,10,12,80,85\ng2,20,24,23,21\ng0,0,0,0,0\n"
META = "sample,group\nc1,T\nc2,T\nt1,OT\nt2,OT\n"
GO = "gene_id\tgo_id\ng1\tGO:0000001\ng2\tGO:0000002\ng1\tGO:0000002\ng1\tGO:0000001\n"
KEGG = "gene_id\tpathway_id\ng1\tmap00010\ng2\tmap00020\ng1\tmap00010\n"


@pytest.fixture
def imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def no_execution(*args, **kwargs):
        raise AssertionError("Import/readiness must never execute a Skill")
    monkeypatch.setattr(SkillRegistry, "execute", no_execution)
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("Test")
    source = tmp_path / "source"
    source.mkdir()
    paths = {}
    for role, name, text in [("count_matrix", "counts.csv", COUNTS), ("metadata", "metadata.csv", META),
                             ("go_mapping", "go.tsv", GO), ("kegg_mapping", "kegg.tsv", KEGG)]:
        paths[role] = source / name
        paths[role].write_text(text, encoding="utf-8")
    return FileImportService(manager), paths


@pytest.mark.parametrize("role", list(FileRole))
def test_valid_import_and_reopen(imports, role):
    service, paths = imports
    source = paths[role]
    original = source.read_bytes()
    result = service.import_file("Test", source, role)
    assert result.status == "succeeded", result
    assert ImportResult.model_validate_json(result.model_dump_json()) == result
    project = service.manager.open_project("Test")
    stored = project.root / result.stored_path
    assert stored.is_file() and stored.is_relative_to(project.root / "input")
    assert source.read_bytes() == original
    assert (stored.parent.parent / "original" / source.name).read_bytes() == original
    assert getattr(project.metadata.files, role.value) == result.stored_path
    assert str(stored) == result.context[role.value]
    source.unlink()  # Only this test-owned temporary source, not real inputs.
    reopened = FileImportService(ProjectManager(service.manager.root))
    assert reopened.get_imported_file_info("Test", role)["status"] in {"valid", "warning"}
    assert reopened.validate_imported_file("Test", role).status == "succeeded"
    assert str(getattr(reopened.manager.get_project_context("Test"), role.value)) == str(stored)


def test_count_summary(imports):
    service, paths = imports
    result = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert result.summary == {"n_genes": 3, "n_samples": 4, "sample_names": ["c1", "c2", "t1", "t2"],
                              "duplicated_gene_ids": 0, "missing_values": 0, "integer_check": "PASS", "min_count": 0, "max_count": 85}
    assert result.warnings[0]["code"] == "RAW_COUNT_ORIGIN_NOT_VERIFIED"


@pytest.mark.parametrize("text,code", [
    ("", "IMPORT_EMPTY_FILE"), ("gene_id,c1\n", "COUNT_FORMAT_ERROR"),
    ("gene_id\ng1\n", "COUNT_FORMAT_ERROR"), ("id,c1\ng1,1\n", "COUNT_FORMAT_ERROR"),
    ("gene_id,c1\n,1\n", "MISSING_VALUE"), ("gene_id,c1,c1\ng1,1,2\n", "DUPLICATE_ID"),
    ("gene_id,c1\ng1,1\ng1,2\n", "DUPLICATE_ID"),
    ("gene_id,c1\ng1,1.2\n", "NON_INTEGER_COUNT"),
    ("gene_id,c1\ng1,-1\n", "NEGATIVE_COUNT"), ("gene_id,c1\ng1,NA\n", "MISSING_VALUE"),
    ("gene_id,c1\ng1,\n", "MISSING_VALUE"), ("gene_id,c1\ng1,abc\n", "COUNT_NOT_NUMERIC"),
    ("gene_id,c1\ng1,Inf\n", "COUNT_NOT_FINITE"),
    ("gene_id,c1\ng1,2147483648\n", "COUNT_OUT_OF_RANGE"),
    ("gene_id,c1\ng1,0\n", "NO_ANALYZABLE_GENES"),
    ("gene_id,c1,c2\ng1,2,0\n", "ZERO_LIBRARY"),
    ("gene_id,c1,c2\ng1,2\n", "COUNT_FORMAT_ERROR"),
])
def test_invalid_counts(imports, text, code):
    service, paths = imports
    paths["count_matrix"].write_text(text, encoding="utf-8")
    result = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert result.status == "failed" and result.error_code == code
    assert service.manager.open_project("Test").metadata.files.count_matrix is None
    assert paths["count_matrix"].read_text(encoding="utf-8") == text
    if code == "NON_INTEGER_COUNT":
        assert result.warnings[0]["code"] == "POSSIBLE_NORMALIZED_MATRIX"


@pytest.mark.parametrize("role", list(FileRole))
def test_missing_file(imports, role):
    service, paths = imports
    result = service.import_file("Test", paths[role].with_name("missing.csv"), role)
    assert result.error_code == "FILE_NOT_FOUND"


@pytest.mark.parametrize("text", ["id,group\nc1,T\n", "sample,other\nc1,T\n",
                                  "sample,group\nc1,T\nc1,OT\n", "sample,group\nc1,NA\n",
                                  "sample,group\nc1,\n", "sample,group\n", "sample,group,batch\nc1,T,NA\n"])
def test_invalid_metadata(imports, text):
    service, paths = imports
    paths["metadata"].write_text(text, encoding="utf-8")
    result = service.import_file("Test", paths["metadata"], "metadata")
    assert result.status == "failed"
    assert service.manager.open_project("Test").metadata.files.metadata is None


def test_metadata_summary(imports):
    service, paths = imports
    result = service.import_file("Test", paths["metadata"], "metadata")
    assert result.summary["n_groups"] == 2
    assert result.summary["replicates_per_group"] == {"T": 2, "OT": 2}
    assert result.summary["group_column"] == "condition"
    assert "METADATA_COLUMNS_ADAPTED" in {w["code"] for w in result.warnings}


@pytest.mark.parametrize("first", ["count_matrix", "metadata"])
@pytest.mark.parametrize("rows,success,order", [
    ("c1,T\nc2,T\nt1,OT\nt2,OT\n", True, False),
    ("t2,OT\nc2,T\nt1,OT\nc1,T\n", True, True),
    ("c1,T\nc2,T\nt1,OT\n", False, False),
    ("c1,T\nc2,T\nt1,OT\nt2,OT\nextra,T\n", False, False),
])
def test_bidirectional_matching(imports, first, rows, success, order):
    service, paths = imports
    paths["metadata"].write_text("sample,group\n" + rows, encoding="utf-8")
    second = "metadata" if first == "count_matrix" else "count_matrix"
    assert service.import_file("Test", paths[first], first).status == "succeeded"
    before = (service.manager.root / "Test/project.json").read_bytes()
    result = service.import_file("Test", paths[second], second)
    if success:
        assert result.status == "succeeded", result
        matching = result.validation["sample_matching"]
        assert matching["exact_match"] and matching["order_difference"] is order
        if order:
            assert "SAMPLE_ORDER_DIFFERS" in {w["code"] for w in result.warnings}
    else:
        assert result.error_code == "SAMPLE_MISMATCH"
        assert result.details["missing_in_metadata"] or result.details["extra_in_metadata"]
        assert (service.manager.root / "Test/project.json").read_bytes() == before


@pytest.mark.parametrize("role", ["go_mapping", "kegg_mapping"])
@pytest.mark.parametrize("mapping_first", [True, False])
def test_mapping_coverage(imports, role, mapping_first):
    service, paths = imports
    order = [role, "count_matrix"] if mapping_first else ["count_matrix", role]
    for name in order:
        assert service.import_file("Test", paths[name], name).status == "succeeded"
    info = service.get_imported_file_info("Test", role)
    assert info["summary"]["coverage"] == {"mapped_genes": 2, "total_count_genes": 3, "fraction": 2/3}
    assert info["summary"]["duplicate_pairs"] == 1
    assert info["summary"]["n_pairs"] == (3 if role == "go_mapping" else 2)


@pytest.mark.parametrize("role", ["go_mapping", "kegg_mapping"])
def test_no_mapping_overlap_is_warning(imports, role):
    service, paths = imports
    paths[role].write_text(paths[role].read_text(encoding="utf-8").replace("g1", "rna-A.1").replace("g2", "rna-B.1"), encoding="utf-8")
    service.import_file("Test", paths["count_matrix"], "count_matrix")
    result = service.import_file("Test", paths[role], role)
    assert result.status == "succeeded"
    assert result.summary["coverage"]["mapped_genes"] == 0
    assert "LOW_MAPPING_COVERAGE" in {w["code"] for w in result.warnings}


@pytest.mark.parametrize("role,text", [("go_mapping", "gene_id\tgo_id\ng1\tGO:12\n"),
                                      ("go_mapping", "gene_id\twrong\ng1\tGO:0000001\n"),
                                      ("go_mapping", "gene_id\tgo_id\n\tGO:0000001\n"),
                                      ("kegg_mapping", "gene_id\twrong\ng1\tmap00010\n"),
                                      ("kegg_mapping", "gene_id\tpathway_id\ng1\tK00001\n")])
def test_invalid_mapping(imports, role, text):
    service, paths = imports
    paths[role].write_text(text, encoding="utf-8")
    assert service.import_file("Test", paths[role], role).status == "failed"


def test_replace_remove_and_no_silent_overwrite(imports):
    service, paths = imports
    first = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert service.import_file("Test", paths["count_matrix"], "count_matrix").error_code == "FILE_ROLE_ALREADY_BOUND"
    before = service.manager.open_project("Test")
    replacement = service.replace_file("Test", paths["count_matrix"], "count_matrix")
    assert replacement.status == "succeeded" and replacement.stored_path != first.stored_path
    assert (before.root / first.stored_path).is_file()
    assert service.manager.open_project("Test").metadata.updated_at > before.metadata.updated_at
    removed = service.remove_file("Test", "count_matrix")
    assert removed.status == "succeeded" and removed.context["count_matrix"] is None
    assert (before.root / replacement.stored_path).is_file() and paths["count_matrix"].is_file()
    assert service.get_imported_file_info("Test", "count_matrix")["status"] == "missing"


@pytest.mark.parametrize("mode", ["invalid", "copy_failure", "commit_failure"])
def test_replace_failure_preserves_old(imports, monkeypatch, mode):
    service, paths = imports
    first = service.import_file("Test", paths["count_matrix"], "count_matrix")
    project = service.manager.open_project("Test")
    before = (project.root / "project.json").read_bytes()
    if mode == "invalid":
        paths["count_matrix"].write_text(COUNTS.replace("10,", "1.1,"), encoding="utf-8")
    else:
        def fail(*args, **kwargs):
            raise OSError("disk full")
        if mode == "copy_failure":
            monkeypatch.setattr(shutil, "copyfile", fail)
        else:
            monkeypatch.setattr(service.manager, "commit_file_binding", fail)
    result = service.replace_file("Test", paths["count_matrix"], "count_matrix")
    assert result.status == "failed"
    assert (project.root / "project.json").read_bytes() == before
    assert (project.root / first.stored_path).read_text(encoding="utf-8") == COUNTS


def test_concurrent_update_refused(imports, monkeypatch):
    service, paths = imports
    original = service.manager.commit_file_binding
    def changed(*args, **kwargs):
        service.manager.update_project_metadata("Test", {"description": "another writer"})
        return original(*args, **kwargs)
    monkeypatch.setattr(service.manager, "commit_file_binding", changed)
    result = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert result.error_code == "PROJECT_CHANGED"
    assert service.manager.open_project("Test").metadata.files.count_matrix is None


@pytest.mark.parametrize("role", ["count_matrix", "metadata"])
def test_tsv_adaptation(imports, role):
    service, paths = imports
    source = paths[role].with_suffix(".tsv")
    source.write_text(paths[role].read_text(encoding="utf-8").replace(",", "\t"), encoding="utf-8")
    result = service.import_file("Test", source, role)
    assert result.status == "succeeded"
    assert "TSV_ADAPTED" in {w["code"] for w in result.warnings}


@pytest.mark.parametrize("role", list(FileRole))
def test_detection(imports, role):
    service, paths = imports
    inspection = service.detect_file_role(paths[role])
    assert inspection.suggested_role == role and inspection.confidence > 0


def test_eggnog_detection_ambiguous_and_valid_for_both_roles(imports):
    service, paths = imports
    path = paths["go_mapping"].with_name("egg.annotations")
    path.write_text("## eggNOG\n#query\tGOs\tKEGG_Pathway\nrna-A.1\tGO:0000001,GO:0000002\tmap00010,ko00020\nrna-B.1\t-\t-\n", encoding="utf-8")
    hint = service.detect_file_role(path)
    assert hint.suggested_role is None and len(hint.candidates) == 2
    for role in ("go_mapping", "kegg_mapping"):
        result = service.import_file("Test", path, role)
        assert result.status == "succeeded", result
        assert result.summary["mapped_genes"] == 1 and result.summary["unannotated_rows"] == 1


def test_unknown_detection(imports):
    service, paths = imports
    paths["metadata"].write_text("foo,bar\n1,2\n", encoding="utf-8")
    assert service.detect_file_role(paths["metadata"]).suggested_role is None


@pytest.mark.parametrize("key", ["../escape", "..\\escape", "C:/outside", ".."])
def test_project_traversal(imports, key):
    service, paths = imports
    assert service.import_file(key, paths["count_matrix"], "count_matrix").status == "failed"
    assert not (service.manager.root.parent / "escape").exists()


@pytest.mark.parametrize("name", ["CON.csv", "bad:name.csv", "trailing.csv ", ".env", "a" * 81 + ".csv"])
def test_unsafe_filename(imports, name):
    service, paths = imports
    result = service.import_file("Test", paths["count_matrix"].with_name(name), "count_matrix")
    assert result.status == "failed"
    assert result.error_code in {"UNSAFE_FILENAME", "IMPORT_FORMAT_UNSUPPORTED"}


def test_tampered_binding_rejected(imports):
    service, paths = imports
    project = service.manager.open_project("Test")
    document = project.metadata.model_dump()
    document["files"]["count_matrix"] = "input/../../outside.csv"
    (project.root / "project.json").write_text(json.dumps(document), encoding="utf-8")
    assert service.get_imported_file_info("Test", "count_matrix")["status"] == "invalid"


def test_readiness_context_and_persistence(imports):
    service, paths = imports
    for role, path in paths.items():
        assert service.import_file("Test", path, role).status == "succeeded"
    reopened = FileImportService(ProjectManager(service.manager.root))
    readiness = reopened.get_project_readiness("Test")
    assert set(readiness) == set(create_default_registry().list_skills())
    assert readiness["rnaseq.pca"]["ready"]
    assert readiness["rnaseq.deg"]["data_ready"] and not readiness["rnaseq.deg"]["ready"]
    assert set(readiness["rnaseq.deg"]["missing"]) == {"treatment", "control"}
    assert "deg_result_file" in readiness["rnaseq.heatmap"]["missing"]
    assert set(readiness["rnaseq.go"]["missing"]) == {"gene_list", "universe|analysis_genes"}
    parameters = {"rnaseq.deg": {"treatment": "OT", "control": "T"}}
    assert reopened.get_project_readiness("Test", confirmed_parameters=parameters)["rnaseq.deg"]["ready"]
    context = reopened.build_execution_context("Test", confirmed_parameters=parameters)
    assert context.confirmed_parameters["rnaseq.pca"]["group_column"] == "condition"
    inputs = context.inject("rnaseq.deg", parameters["rnaseq.deg"], context.output_root / "future-run")
    create_default_registry().validate_input("rnaseq.deg", inputs)
    assert not (context.output_root / "future-run").exists()


@pytest.mark.parametrize("parameters", [{"rnaseq.deg": {"treatment": "T", "control": "T"}},
                                         {"rnaseq.deg": {"treatment": "X", "control": "T"}}])
def test_readiness_reuses_biological_checks(imports, parameters):
    service, paths = imports
    for role in ("count_matrix", "metadata"):
        service.import_file("Test", paths[role], role)
    status = service.get_project_readiness("Test", confirmed_parameters=parameters)["rnaseq.deg"]
    assert not status["ready"] and status["errors"]


@pytest.mark.parametrize("parameters", [{"rnaseq.deg": {"alpha": 2}}, {"rnaseq.deg": {"output_dir": "../bad"}},
                                         {"rnaseq.unknown": {}}, {"rnaseq.pca": {"rscript_executable": "cmd"}}])
def test_context_rejects_invalid_or_managed_parameters(imports, parameters):
    service, _ = imports
    with pytest.raises(ProjectError, match="registered business"):
        service.build_execution_context("Test", confirmed_parameters=parameters)


def test_changed_file_blocks_binding_but_not_unrelated_readiness(imports):
    service, paths = imports
    results = {role: service.import_file("Test", path, role) for role, path in paths.items()}
    project = service.manager.open_project("Test")
    (project.root / results["go_mapping"].stored_path).write_text(GO + "g0\tGO:0000003\n", encoding="utf-8")
    assert service.get_imported_file_info("Test", "go_mapping")["status"] == "invalid"
    assert service.validate_imported_file("Test", "go_mapping").status == "failed"
    readiness = service.get_project_readiness("Test")
    assert readiness["rnaseq.pca"]["ready"] and not readiness["rnaseq.go"]["ready"]
    assert "gene_to_go_mapping" not in service.build_execution_context("Test").bindings["rnaseq.go"]


def test_coverage_refreshes_after_count_replacement(imports):
    service, paths = imports
    for role in ("count_matrix", "go_mapping"):
        service.import_file("Test", paths[role], role)
    paths["count_matrix"].write_text(COUNTS.replace("g1,", "other,"), encoding="utf-8")
    assert service.replace_file("Test", paths["count_matrix"], "count_matrix").status == "succeeded"
    assert service.get_imported_file_info("Test", "go_mapping")["summary"]["coverage"]["mapped_genes"] == 1


@pytest.mark.parametrize("method", ["import_file", "remove_file", "validate_imported_file", "get_imported_file_info"])
def test_invalid_role_is_structured(imports, method):
    service, paths = imports
    args = ("Test", paths["count_matrix"], "raw_counts") if method == "import_file" else ("Test", "raw_counts")
    result = getattr(service, method)(*args)
    assert (result["error_code"] if isinstance(result, dict) else result.error_code) == "INVALID_FILE_ROLE"


@pytest.mark.parametrize("options", [{"replace": "yes"}, {"sample_column": 3}, {"group_column": "group"}, {"mapping_sheet": []}])
def test_invalid_import_options(imports, options):
    service, paths = imports
    assert service.import_file("Test", paths["count_matrix"], "count_matrix", **options).error_code == "INVALID_IMPORT_PARAMETER"


def test_corrupt_record_returns_structured_errors(imports):
    service, paths = imports
    result = service.import_file("Test", paths["metadata"], "metadata")
    path = service.manager.root / "Test" / result.stored_path
    record_path = path.parent.parent / "import.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["summary"] = {}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert service.get_imported_file_info("Test", "metadata")["error_code"] == "IMPORT_RECORD_INVALID"
    assert service.get_project_readiness("Test")["rnaseq.pca"]["errors"]


def test_one_sample_importable_but_not_pca_ready(imports):
    service, paths = imports
    paths["count_matrix"].write_text("gene_id,c1\ng1,1\ng2,2\n", encoding="utf-8")
    paths["metadata"].write_text("sample,group\nc1,T\n", encoding="utf-8")
    for role in ("count_matrix", "metadata"):
        assert service.import_file("Test", paths[role], role).status == "succeeded"
    assert not service.get_project_readiness("Test")["rnaseq.pca"]["ready"]


def test_50000_gene_matrix(imports):
    service, paths = imports
    paths["count_matrix"].write_text("gene_id,c1,c2\n" + "".join(f"gene{i},10,20\n" for i in range(50000)), encoding="utf-8")
    result = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert result.status == "succeeded" and result.summary["n_genes"] == 50000


def test_xlsx_mapping_with_explicit_sheet(imports):
    from openpyxl import Workbook
    service, paths = imports
    path = paths["go_mapping"].with_suffix(".xlsx")
    book = Workbook()
    sheet = book.active
    sheet.title = "annotation"
    sheet.append(["gene_id", "go_id"])
    sheet.append(["g1", "GO:0000001"])
    book.create_sheet("other")
    book.save(path)
    book.close()
    assert service.import_file("Test", path, "go_mapping").status == "failed"
    result = service.import_file("Test", path, "go_mapping", mapping_sheet="annotation")
    assert result.status == "succeeded", result
    assert service.build_execution_context("Test").confirmed_parameters["rnaseq.go"]["mapping_sheet"] == "annotation"


def test_committed_import_does_not_require_fallible_context_reread(imports, monkeypatch):
    service, paths = imports
    def fail(*args, **kwargs):
        raise ProjectError("PROJECT_IO_ERROR", "Read failed after commit")
    monkeypatch.setattr(service.manager, "get_project_context", fail)
    result = service.import_file("Test", paths["count_matrix"], "count_matrix")
    assert result.status == "succeeded" and result.context["count_matrix"]


def test_replacement_sample_mismatch_keeps_old_metadata(imports):
    service, paths = imports
    for role in ("count_matrix", "metadata"):
        service.import_file("Test", paths[role], role)
    before = service.manager.open_project("Test")
    paths["metadata"].write_text(META.replace("c1,T", "unknown,T"), encoding="utf-8")
    assert service.replace_file("Test", paths["metadata"], "metadata").error_code == "SAMPLE_MISMATCH"
    assert service.manager.open_project("Test").metadata == before.metadata
