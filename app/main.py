"""Start with: streamlit run app/main.py (from the repository root)."""

from pathlib import Path
import logging
import os
import sys

# Streamlit executes this entry as a script. Keep application imports independent
# of the launcher's sys.path; no user-specific absolute path is embedded.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import streamlit as st

from app.components.common import show_notice, show_user_error
from app.components.project_header import render_header
from app.components.sidebar import render_sidebar
from app.state import TABS, clear_current_project, initialize_state, refresh_project_state, widget_key
from app.views import assistant, files, project, results
from bulk_rnaseq.workspace import FileImportService, ProjectError, ProjectManager


def main() -> None:
    st.set_page_config(page_title="RNA-seq Workspace", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")
    # Host-only optional setting, never a user-supplied path or an LLM parameter.
    root = Path(os.environ.get("RNASEQ_WORKSPACE_ROOT", str(REPOSITORY_ROOT / "workspace")))
    service = FileImportService(ProjectManager(root))
    state = initialize_state(st.session_state)
    if state.current_project:
        try:
            latest = service.manager.open_project(state.current_project.key)
            if latest.metadata.updated_at != state.current_project.metadata.updated_at:
                refresh_project_state(state, service)
        except ProjectError as error:
            clear_current_project(state)
            state.notice = error.to_dict()
    render_sidebar(state, service)
    show_notice(state)
    if state.current_project is None:
        st.subheader("RNA-seq Workspace")
        st.info("No project selected. Create or open a project to get started.")
        st.caption("从左侧创建或打开项目。随后在 Files 页选择输入文件，无需输入本机数据路径。")
        return
    render_header(state)
    if state.chat_busy:
        assistant.render(state, service)
        return
    panes = st.tabs(TABS, default=state.selected_tab, key=widget_key(state, "tabs"), on_change="rerun")
    for name, pane in zip(TABS, panes):
        if pane.open:
            state.selected_tab = name
            with pane:
                if name == "Assistant":
                    assistant.render(state, service)
                elif name == "Results":
                    results.render(state, service)
                elif name == "Files":
                    files.render(state, service)
                else:
                    project.render(state, service)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.getLogger(__name__).exception("Workspace UI failure")
        show_user_error(error)
