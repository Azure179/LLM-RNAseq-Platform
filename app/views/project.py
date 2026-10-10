import streamlit as st
from app.components.common import finish_action, show_user_error
from app.state import WorkspaceState, widget_key
from bulk_rnaseq.workspace import FileImportService
from bulk_rnaseq.workspace.result_manager import ResultManager, SKILL_LABELS


def render(state: WorkspaceState, service: FileImportService) -> None:
    project = state.current_project
    st.subheader("Project")
    st.text(project.metadata.project_name)
    st.text(project.metadata.description or "No description.")
    st.caption(f"Created: {project.metadata.created_at}")
    st.caption(f"Updated: {project.metadata.updated_at}")
    st.code(f"workspace/{project.key}", language=None)
    st.caption("项目路径以逻辑相对路径展示；重命名只改变显示名称，不移动目录。")
    runs = ResultManager(service.manager, project.key).list_runs()
    total, succeeded, failed = st.columns(3)
    total.metric("Recorded runs / requests", len(runs))
    succeeded.metric("Successful analyses", sum(r.status == "succeeded" for r in runs))
    failed.metric("Failed", sum(r.status == "failed" for r in runs))
    if runs:
        st.caption("Latest: " + SKILL_LABELS.get(runs[0].skill_name, runs[0].skill_name) + " · " + runs[0].created_at)
    with st.form(widget_key(state, "rename_form")):
        name = st.text_input("Rename Project", value=project.metadata.project_name)
        if st.form_submit_button("Save name"):
            try:
                service.manager.rename_project(project.key, name)
                finish_action(state, service, {"status": "succeeded", "message": "Project renamed."})
            except Exception as error:
                show_user_error(error)
    st.caption("本阶段不提供 Delete Project，避免误删项目。")
