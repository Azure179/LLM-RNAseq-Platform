"""Offline adapter/state and Streamlit AppTest checks, not browser automation."""

from io import BytesIO
from pathlib import Path
import socket

import pytest
from streamlit.testing.v1 import AppTest

from app.state import (STATE_KEY, WorkspaceState, clear_current_project, initialize_state,
                       refresh_project_state, set_current_project, widget_key)
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workspace import FileImportService, ProjectError, ProjectManager
from bulk_rnaseq.workspace.ui_adapter import (describe_download, format_file_size, import_upload,
                                             list_project_results, read_download)


DATA = {
    "count_matrix": ("counts.csv", b"gene_id,c1,c2,t1,t2\ng1,1,2,30,40\ng2,11,15,18,22\n"),
    "metadata": ("meta.csv", b"sample,group\nc1,T\nc2,T\nt1,OT\nt2,OT\n"),
    "go_mapping": ("go.tsv", b"gene_id\tgo_id\ng1\tGO:0000001\ng2\tGO:0000002\n"),
    "kegg_mapping": ("kegg.tsv", b"gene_id\tpathway_id\ng1\tmap00010\ng2\tmap00020\n"),
}
APP = Path(__file__).resolve().parents[1] / "app/main.py"
_NATIVE_CONNECT = socket.socket.connect


@pytest.fixture
def apptest_ipc(monkeypatch):
    """Windows asyncio needs a socketpair. Permit only this self-owned IPC pair.

    The global network guard remains installed for all ordinary connect calls;
    no HTTP/DeepSeek address can be supplied to this zero-argument helper.
    """
    def local_pair():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            sender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                _NATIVE_CONNECT(sender, listener.getsockname())
                receiver, _ = listener.accept()
                return receiver, sender
            except BaseException:
                sender.close()
                raise
    monkeypatch.setattr(socket, "socketpair", local_pair)
    with pytest.raises(AssertionError, match="Network blocked"):
        socket.create_connection(("api.deepseek.com", 443))


@pytest.fixture
def service(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("UI must not execute Skills")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    manager = ProjectManager(tmp_path / "workspace")
    manager.create_project("First")
    manager.create_project("Second")
    return FileImportService(manager)


def upload(service, role="count_matrix", *, replace=False):
    filename, content = DATA[role]
    return import_upload(service, "First", filename, BytesIO(content), role, replace=replace)


@pytest.mark.parametrize("role", DATA)
def test_upload_adapter(service, role):
    result = upload(service, role)
    assert result.status == "succeeded", result
    project = service.manager.open_project("First")
    assert (project.root / result.stored_path).is_file()
    assert not list((project.root / "runtime").iterdir())
    assert describe_download(service, "First", role)["size_bytes"] == len(DATA[role][1])
    assert read_download(service, "First", role, expected_binding=result.stored_path) == DATA[role]


@pytest.mark.parametrize("name", ["../escape.csv", "..\\escape.csv", "C:\\file.csv", "/tmp/file.csv", "CON.csv", "a:b.csv"])
def test_unsafe_upload_name(service, name):
    assert import_upload(service, "First", name, BytesIO(b"data"), "count_matrix").error_code == "UNSAFE_FILENAME"


def test_upload_size_and_io_error(service, monkeypatch):
    import bulk_rnaseq.workspace.ui_adapter as adapter
    monkeypatch.setattr(adapter, "MAX_UPLOAD_BYTES", 2)
    assert upload(service).error_code == "UPLOAD_TOO_LARGE"
    class Broken(BytesIO):
        def read(self, *args):
            raise OSError("private path must not be shown")
    result = import_upload(service, "First", "counts.csv", Broken(), "count_matrix")
    assert result.status == "failed" and "private path" not in result.message


def test_replace_and_stale_download(service):
    first = upload(service)
    assert upload(service).error_code == "FILE_ROLE_ALREADY_BOUND"
    assert upload(service, replace=True).status == "succeeded"
    with pytest.raises(ProjectError, match="changed"):
        read_download(service, "First", "count_matrix", expected_binding=first.stored_path)
    previous = service.manager.open_project("First").metadata
    bad = import_upload(service, "First", "bad.csv", BytesIO(b"gene_id,a\ng1,2.3\n"), "count_matrix", replace=True)
    assert bad.status == "failed" and service.manager.open_project("First").metadata == previous


def test_download_not_cross_project_or_unbound(service):
    first = upload(service)
    with pytest.raises(ProjectError):
        read_download(service, "Second", "count_matrix", expected_binding=first.stored_path)
    service.remove_file("First", "count_matrix")
    with pytest.raises(ProjectError):
        read_download(service, "First", "count_matrix", expected_binding=first.stored_path)


def test_state_initialize_switch_refresh_and_restart(service):
    session = {}
    state = initialize_state(session)
    assert initialize_state(session) is state and state.current_project is None
    upload(service)
    set_current_project(state, service, "First")
    assert state.file_status["count_matrix"]["summary"]["n_genes"] == 2
    state.selected_result = "old"
    state.download = ("count_matrix", "old.csv", b"old")
    first_widget = widget_key(state, "upload")
    set_current_project(state, service, "Second")
    assert state.file_status["count_matrix"]["status"] == "missing"
    assert state.download is None and state.selected_result is None
    assert widget_key(state, "upload") != first_widget
    restarted = initialize_state({})
    set_current_project(restarted, FileImportService(ProjectManager(service.manager.root)), "First")
    assert restarted.file_status["count_matrix"]["summary"]["n_genes"] == 2
    service.manager.rename_project("First", "Renamed")
    refresh_project_state(restarted, service)
    assert restarted.current_project.metadata.project_name == "Renamed"
    service.remove_file("First", "count_matrix")
    refresh_project_state(restarted, service)
    assert restarted.current_project_context.count_matrix is None
    clear_current_project(restarted)
    assert not restarted.file_status and not restarted.project_readiness


def test_missing_file_still_repairable_in_state(service):
    result = upload(service)
    state = WorkspaceState()
    set_current_project(state, service, "First")
    (service.manager.root / "First" / result.stored_path).unlink()  # Only pytest temp fixture.
    refresh_project_state(state, service)
    assert state.current_project is not None and state.current_project_context is None
    assert state.file_status["count_matrix"]["status"] == "invalid"
    assert state.context_error


def test_results_read_only_and_bounded(service):
    root = service.manager.root / "First/results"
    assert list_project_results(service.manager, "First") == []
    (root / "a/nested").mkdir(parents=True)
    (root / "a/summary.json").write_text("{}")
    (root / "a/nested/table.csv").write_text("a,b\n1,2")
    (root / "empty").mkdir()
    rows = list_project_results(service.manager, "First")
    assert rows[0]["file_count"] == 2 and rows[0]["summary_exists"]
    assert rows[1]["file_count"] == 0
    assert any(r["truncated"] for r in list_project_results(service.manager, "First", max_entries=1))
    assert (root / "a/summary.json").read_text() == "{}"


@pytest.mark.parametrize("size,text", [(624, "624 B"), (15565, "15.2 KiB"), (4*1024**2, "4.0 MiB"), (1024**3, "1.0 GiB")])
def test_file_size(size, text):
    assert format_file_size(size) == text


def element(elements, label):
    return next(item for item in elements if item.label == label)


def open_files(at):
    state = at.session_state[STATE_KEY]
    at.session_state[widget_key(state, "tabs")] = "Files"
    at.run()
    assert not at.exception and not at.error


def test_streamlit_create_switch_tabs_and_errors(service, apptest_ipc):
    at = AppTest.from_file(APP, default_timeout=15).run()
    assert not at.exception and not at.error
    assert "No project selected" in at.info[0].value
    element(at.text_input, "Project Name").set_value("../bad")
    element(at.button, "Create").click().run()
    assert at.error and not at.exception
    element(at.text_input, "Project Name").set_value("UI_Test")
    element(at.button, "Create").click().run()
    assert at.session_state[STATE_KEY].current_project.key == "UI_Test"
    assert not at.exception and not at.error
    assert [tab.label for tab in at.tabs] == ["Assistant", "Results", "Files", "Project"]
    assert len(at.chat_input) == 1  # Phase 6.5.4 enables submission via the controller.
    element(at.selectbox, "Projects").select("First").run()
    assert at.session_state[STATE_KEY].current_project.key == "First"
    open_files(at)
    assert len(at.file_uploader) == 5
    assert at.file_uploader[0].label == "eggNOG annotations file"


def test_streamlit_upload_replace_remove_and_friendly_error(service, apptest_ipc):
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    for role, label in [("count_matrix", "Count Matrix"), ("metadata", "Metadata"), ("go_mapping", "GO Mapping"), ("kegg_mapping", "KEGG Mapping")]:
        filename, content = DATA[role]
        element(at.file_uploader, f"{label} file").set_value((filename, content, "text/plain")).run()
        element(at.button, f"Import {label}").click().run()
        assert not at.exception and not at.error
        assert at.session_state[STATE_KEY].file_status[role]["status"] in {"valid", "warning"}
    assert at.session_state[STATE_KEY].project_readiness["rnaseq.pca"]["ready"]
    assert not at.session_state[STATE_KEY].project_readiness["rnaseq.go"]["ready"]
    before = service.manager.open_project("First").metadata.files.count_matrix
    name, content = DATA["count_matrix"]
    element(at.file_uploader, "Count Matrix file").set_value((name, content, "text/csv")).run()
    assert element(at.button, "Replace Count Matrix").disabled
    element(at.checkbox, "Confirm Replace Count Matrix").check().run()
    element(at.button, "Replace Count Matrix").click().run()
    assert service.manager.open_project("First").metadata.files.count_matrix != before
    element(at.file_uploader, "Count Matrix file").set_value((name, b"gene_id,c1\ng1,2.3\n", "text/csv")).run()
    element(at.checkbox, "Confirm Replace Count Matrix").check().run()
    element(at.button, "Replace Count Matrix").click().run()
    assert at.error and not at.exception
    assert any("NON_INTEGER_COUNT" in code.value for code in at.code)
    element(at.checkbox, "Confirm Remove KEGG Mapping binding").check().run()
    element(at.button, "Remove KEGG Mapping").click().run()
    assert at.session_state[STATE_KEY].file_status["kegg_mapping"]["status"] == "missing"
    assert not at.exception


def test_streamlit_cancel_rename_results_and_new_session(service, apptest_ipc):
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    name, content = DATA["count_matrix"]
    element(at.file_uploader, "Count Matrix file").set_value((name, content, "text/csv")).run()
    element(at.button, "Cancel Count Matrix").click().run()
    assert all(item.value is None for item in at.file_uploader)
    assert service.manager.open_project("First").metadata.files.count_matrix is None
    state = at.session_state[STATE_KEY]
    at.session_state[widget_key(state, "tabs")] = "Project"
    at.run()
    element(at.text_input, "Rename Project").set_value("Renamed_UI")
    element(at.button, "Save name").click().run()
    assert not at.exception and not at.error
    assert at.session_state[STATE_KEY].current_project.metadata.project_name == "Renamed_UI"
    state = at.session_state[STATE_KEY]
    at.session_state[widget_key(state, "tabs")] = "Results"
    at.run()
    assert any("No analysis results" in item.value for item in at.info)
    reopened = AppTest.from_file(APP, default_timeout=15).run()
    assert reopened.session_state[STATE_KEY].current_project is None
    element(reopened.selectbox, "Projects").select("First").run()
    assert reopened.session_state[STATE_KEY].current_project.metadata.project_name == "Renamed_UI"


def test_streamlit_file_download_preparation(service, apptest_ipc):
    upload(service)
    at = AppTest.from_file(APP, default_timeout=15).run()
    element(at.selectbox, "Projects").select("First").run()
    open_files(at)
    element(at.button, "Prepare Count Matrix download").click().run()
    assert not at.exception and not at.error
    assert at.session_state[STATE_KEY].download[1:] == DATA["count_matrix"]
    assert any(item.label == "Download Count Matrix" for item in at.download_button)


def test_plain_ui_layer_has_no_analysis_or_file_mutation_code():
    import ast
    for path in (APP.parent).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("bulk_rnaseq.skills")
                assert (node.module or "") != "bulk_rnaseq.llm.client"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"execute", "write_text", "write_bytes", "unlink", "copyfile", "chat", "getenv"}
