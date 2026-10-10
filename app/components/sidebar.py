import streamlit as st

from app.components.common import FILE_LABELS, ROLE_LABELS, show_user_error
from app.components.readiness import render_readiness
from app.state import WorkspaceState, clear_current_project, refresh_project_state, set_current_project, widget_key
from bulk_rnaseq.workspace import FileImportService


def render_sidebar(state: WorkspaceState, service: FileImportService) -> None:
    with st.sidebar:
        st.subheader("RNA-seq Workspace")
        st.caption("LOCAL · DATA & PROJECTS")
        if state.chat_busy:
            st.info("Analysis in progress. 项目操作暂时禁用。")
            return
        with st.popover("+ New Project", width="stretch"):
            with st.form("new_project", clear_on_submit=False):
                name = st.text_input("Project Name")
                description = st.text_area("Description", height=80)
                if st.form_submit_button("Create", width="stretch"):
                    try:
                        project = service.manager.create_project(name, description)
                        set_current_project(state, service, project.key)
                        state.notice = {"status": "succeeded", "message": "Project created."}
                        st.rerun()
                    except Exception as error:
                        show_user_error(error)
        projects = service.manager.list_projects()
        labels = {project.key: project.metadata.project_name for project in projects}
        keys = [None, *labels]
        current = state.current_project.key if state.current_project else None
        choice = st.selectbox("Projects", keys, index=keys.index(current) if current in keys else 0,
                              format_func=lambda key: labels[key] if key else "Select a project…",
                              key=widget_key(state, "project_selector"))
        if choice != current:
            if choice is None:
                clear_current_project(state)
            else:
                set_current_project(state, service, choice)
            st.rerun()
        if state.current_project:
            if st.button("Refresh from disk", width="stretch"):
                refresh_project_state(state, service)
                st.rerun()
            st.divider()
            st.caption("INPUT FILES")
            for role, info in state.file_status.items():
                st.caption(f"{FILE_LABELS[info['status']]} · {ROLE_LABELS[role]}")
            st.divider()
            st.caption("ANALYSIS READINESS")
            render_readiness(state, compact=True)
        st.divider()
        st.caption("One request · One Skill\n输入文件保留在本地；请求与精简结果发送给 DeepSeek。")
