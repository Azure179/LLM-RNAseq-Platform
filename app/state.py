"""One session object; ordinary mappings make state transitions testable offline."""

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any

from bulk_rnaseq.workspace import FileImportService, FileRole, ProjectContext, ProjectError, ProjectSnapshot
from bulk_rnaseq.workspace.ui_adapter import describe_download, list_project_results
from bulk_rnaseq.workspace.chat_store import ChatStore


STATE_KEY = "workspace_ui"
TABS = ("Assistant", "Results", "Files", "Project")


@dataclass
class WorkspaceState:
    current_project: ProjectSnapshot | None = None
    current_project_context: ProjectContext | None = None
    project_readiness: dict[str, Any] = field(default_factory=dict)
    file_status: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    selected_tab: str = "Assistant"
    selected_result: str | None = None
    selected_run_id: str | None = None
    selected_collection_id: str | None = None
    notice: dict[str, Any] | None = None
    context_error: dict[str, Any] | None = None
    widget_generation: int = 0
    download: tuple[str, str, bytes] | None = None
    chat_messages: list[dict[str, Any]] = field(default_factory=list)
    pending_chat: dict[str, str] | None = None
    chat_busy: bool = False
    chat_error: dict[str, Any] | None = None


def initialize_state(session: MutableMapping[str, Any]) -> WorkspaceState:
    if STATE_KEY not in session:
        session[STATE_KEY] = WorkspaceState()
    return session[STATE_KEY]


def clear_current_project(state: WorkspaceState) -> None:
    state.current_project = None
    state.current_project_context = None
    state.project_readiness = {}
    state.file_status = {}
    state.results = []
    state.context_error = None
    state.selected_result = None
    state.selected_run_id = None
    state.selected_collection_id = None
    state.selected_tab = "Assistant"
    state.download = None
    state.notice = None
    state.chat_messages = []
    state.pending_chat = None
    state.chat_busy = False
    state.chat_error = None
    state.widget_generation += 1


def refresh_project_state(state: WorkspaceState, service: FileImportService) -> None:
    """Disk is authoritative; publish a consistent fresh snapshot to one session."""
    if state.current_project is None:
        return
    key = state.current_project.key
    state.current_project_context = None
    state.project_readiness = {}
    state.file_status = {}
    state.results = []
    state.download = None
    state.context_error = None
    try:
        project = service.manager.open_project(key)
    except ProjectError:
        clear_current_project(state)
        raise
    state.current_project = project
    # Missing/damaged bindings must stay visible so users can replace/remove.
    try:
        state.current_project_context = service.manager.get_project_context(key)
    except ProjectError as error:
        state.context_error = error.to_dict()
    for role in FileRole:
        info = service.get_imported_file_info(key, role)
        if info["status"] in {"valid", "warning"}:
            try:
                info["download"] = describe_download(service, key, role)
            except (ProjectError, OSError):
                info["download"] = None
        state.file_status[role.value] = info
    state.project_readiness = service.get_project_readiness(key)
    state.results = list_project_results(service.manager, key)
    try:
        state.chat_messages = ChatStore(service.manager, key).history()
        state.chat_error = None
    except ProjectError as error:
        state.chat_messages = []
        state.chat_error = error.to_dict()


def set_current_project(state: WorkspaceState, service: FileImportService, key: str) -> None:
    project = service.manager.open_project(key)
    clear_current_project(state)
    state.current_project = project
    refresh_project_state(state, service)


def widget_key(state: WorkspaceState, name: str) -> str:
    project = state.current_project.key if state.current_project else "none"
    return f"ui:{project}:{state.widget_generation}:{name}"


def reset_file_widgets(state: WorkspaceState) -> None:
    state.widget_generation += 1
    state.download = None
