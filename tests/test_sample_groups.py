"""Local user grouping; UI/backend tests never infer groups or run analysis."""

from io import BytesIO

import pytest
from streamlit.testing.v1 import AppTest

from app.state import STATE_KEY
from bulk_rnaseq.workspace import FileImportService, ProjectManager, ProjectError
from bulk_rnaseq.workspace.ui_adapter import import_upload, read_download
from test_workspace_ui import service, apptest_ipc, APP, DATA, element, open_files


GROUPS = {"c1": "T", "c2": "T", "t1": "OT", "t2": "OT"}


def add_counts(service):
    name, data = DATA["count_matrix"]
    result = import_upload(service, "First", name, BytesIO(data), "count_matrix")
    assert result.status == "succeeded"
    return service.get_sample_groups("First")


def test_counts_required_and_no_inferred_groups(service):
    with pytest.raises(ProjectError) as error:
        service.get_sample_groups("First")
    assert error.value.code == "COUNT_MATRIX_REQUIRED"
    revision = service.manager.open_project("First").metadata.updated_at
    assert service.save_sample_groups("First", {}, expected_updated_at=revision).error_code == "COUNT_MATRIX_REQUIRED"
    draft = add_counts(service)
    assert [row["sample_id"] for row in draft["samples"]] == list(GROUPS)
    assert all(row["group"] == "" for row in draft["samples"])
    assert not draft["metadata_bound"]


def test_save_restore_context_download_and_no_execution(service):
    draft = add_counts(service)
    # Input dict order cannot change the count-derived sample order.
    result = service.save_sample_groups("First", dict(reversed(list(GROUPS.items()))),
                                        expected_updated_at=draft["expected_updated_at"])
    assert result.status == "succeeded", result
    assert result.summary["replicates_per_group"] == {"T": 2, "OT": 2}
    assert result.summary["sample_names"] == list(GROUPS)
    assert result.validation["sample_matching"]["exact_match"]
    restored = FileImportService(ProjectManager(service.manager.root))
    assert {row["sample_id"]: row["group"] for row in restored.get_sample_groups("First")["samples"]} == GROUPS
    assert restored.get_project_readiness("First")["rnaseq.pca"]["ready"]
    assert restored.get_project_readiness("First")["rnaseq.deg"]["data_ready"]
    context = restored.build_execution_context("First")
    assert context.bindings["rnaseq.deg"]["metadata_path"] == result.stored_path
    assert "treatment" not in context.confirmed_parameters["rnaseq.deg"]
    name, data = read_download(restored, "First", "metadata", expected_binding=result.stored_path)
    assert name == "user_sample_groups.csv" and b"c1,T" in data
    assert restored.get_imported_file_info("Second", "metadata")["status"] == "missing"


@pytest.mark.parametrize("value,code", [("", "MISSING_GROUP"), ("NA", "MISSING_GROUP"),
    (None, "MISSING_GROUP"), (1, "MISSING_GROUP"), (" T", "INVALID_GROUP"),
    ("T ", "INVALID_GROUP"), ("T\nOT", "INVALID_GROUP"), ("T\tOT", "INVALID_GROUP")])
def test_invalid_groups_preserve_previous_metadata(service, value, code):
    draft = add_counts(service)
    assert service.save_sample_groups("First", GROUPS, expected_updated_at=draft["expected_updated_at"]).status == "succeeded"
    before = service.manager.open_project("First").metadata
    result = service.save_sample_groups("First", {**GROUPS, "c1": value},
                                        expected_updated_at=before.updated_at, replace=True)
    assert result.error_code == code
    assert service.manager.open_project("First").metadata == before


@pytest.mark.parametrize("groups", [{}, {"c1": "T"}, {**GROUPS, "other": "O"}, ["T", "OT"]])
def test_sample_set_must_match(service, groups):
    draft = add_counts(service)
    assert service.save_sample_groups("First", groups, expected_updated_at=draft["expected_updated_at"]).error_code == "SAMPLE_MISMATCH"


def test_explicit_replace_keeps_previous_version(service):
    draft = add_counts(service)
    first = service.save_sample_groups("First", GROUPS, expected_updated_at=draft["expected_updated_at"])
    revision = service.get_sample_groups("First")["expected_updated_at"]
    changed = {sample: "Control" if group == "T" else "Treatment" for sample, group in GROUPS.items()}
    assert service.save_sample_groups("First", changed, expected_updated_at=revision).error_code == "FILE_ROLE_ALREADY_BOUND"
    second = service.save_sample_groups("First", changed, expected_updated_at=revision, replace=True)
    assert second.status == "succeeded" and second.stored_path != first.stored_path
    project = service.manager.open_project("First")
    assert (project.root / first.stored_path).is_file()


def test_stale_form_and_during_generation_change(service, monkeypatch):
    draft = add_counts(service)
    service.manager.update_project_metadata("First", {"description": "Changed elsewhere"})
    assert service.save_sample_groups("First", GROUPS, expected_updated_at=draft["expected_updated_at"]).error_code == "PROJECT_CHANGED"
    draft = service.get_sample_groups("First")
    original_import = service.import_file
    def concurrent(*args, **kwargs):
        service.manager.update_project_metadata("First", {"description": "Changed during save"})
        return original_import(*args, **kwargs)
    monkeypatch.setattr(service, "import_file", concurrent)
    assert service.save_sample_groups("First", GROUPS, expected_updated_at=draft["expected_updated_at"]).error_code == "PROJECT_CHANGED"
    assert service.manager.open_project("First").metadata.files.metadata is None


def test_one_group_is_not_forced_into_two(service):
    draft = add_counts(service)
    result = service.save_sample_groups("First", {sample: "All" for sample in GROUPS}, expected_updated_at=draft["expected_updated_at"])
    assert result.status == "succeeded" and result.summary["n_groups"] == 1
    assert service.get_project_readiness("First")["rnaseq.pca"]["ready"]
    assert not service.get_project_readiness("First")["rnaseq.deg"]["ready"]


def test_corrupt_saved_metadata_is_not_prefilled(service):
    draft = add_counts(service)
    result = service.save_sample_groups("First", GROUPS, expected_updated_at=draft["expected_updated_at"])
    project = service.manager.open_project("First")
    (project.root / result.stored_path).write_text("corrupt fixture", encoding="utf-8")
    current = service.get_sample_groups("First")
    assert current["warning"] and all(row["group"] == "" for row in current["samples"])
    assert service.save_sample_groups("First", GROUPS, expected_updated_at=current["expected_updated_at"], replace=True).status == "succeeded"


def test_ui_no_file_needed_save_retry_restore_and_isolation(service, apptest_ipc):
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    assert not any(item.label == "Save sample groups" for item in at.button)
    filename, content = DATA["count_matrix"]
    element(at.file_uploader, "Count Matrix file").set_value((filename, content, "text/csv")).run()
    element(at.button, "Import Count Matrix").click().run()
    for sample in GROUPS:
        assert element(at.text_input, "Group for " + sample).value == ""
    element(at.button, "Save sample groups").click().run()
    assert at.error and not at.exception
    for sample, group in GROUPS.items():
        element(at.text_input, "Group for " + sample).set_value(group)
    element(at.button, "Save sample groups").click().run()
    assert not at.exception and not at.error
    assert at.session_state[STATE_KEY].project_readiness["rnaseq.pca"]["ready"]
    assert at.session_state[STATE_KEY].file_status["metadata"]["summary"]["n_groups"] == 2
    before = service.manager.open_project("First").metadata
    element(at.text_input, "Group for c1").set_value("O")
    element(at.button, "Save sample groups").click().run()
    assert service.manager.open_project("First").metadata == before
    element(at.checkbox, "Confirm Replace Metadata with these groups").check()
    element(at.button, "Save sample groups").click().run()
    assert not at.exception and not at.error
    restarted = AppTest.from_file(APP, default_timeout=15).run()
    element(restarted.selectbox, "Projects").select("First").run()
    open_files(restarted)
    assert element(restarted.text_input, "Group for c1").value == "O"
    element(restarted.selectbox, "Projects").select("Second").run()
    open_files(restarted)
    assert not any(item.label.startswith("Group for ") for item in restarted.text_input)


def test_ui_external_change_requires_refresh(service, apptest_ipc):
    add_counts(service)
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    for sample, group in GROUPS.items():
        element(at.text_input, "Group for " + sample).set_value(group)
    service.manager.update_project_metadata("First", {"description": "Concurrent update"})
    element(at.button, "Save sample groups").click().run()
    assert service.manager.open_project("First").metadata.files.metadata is None
    assert any("填写期间" in w.value for w in at.warning)
    element(at.button, "Refresh sample list").click().run()
    assert all(element(at.text_input, "Group for " + sample).value == "" for sample in GROUPS)
