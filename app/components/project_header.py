import streamlit as st
from app.state import WorkspaceState


def render_header(state: WorkspaceState) -> None:
    project = state.current_project
    st.subheader(project.metadata.project_name)
    st.caption("Bulk RNA-seq Project · Local workspace · Phase 6.5.3")
    counts = state.file_status.get("count_matrix", {}).get("summary", {})
    meta = state.file_status.get("metadata", {}).get("summary", {})
    inputs = sum(item["status"] in {"valid", "warning"} for item in state.file_status.values())
    columns = st.columns(4)
    for column, label, value in zip(columns, ("Samples", "Groups", "Genes", "Imported inputs"),
                                    (counts.get("n_samples", "—"), meta.get("n_groups", "—"), counts.get("n_genes", "—"), f"{inputs} / 4")):
        column.metric(label, f"{value:,}" if isinstance(value, int) else value)
    st.caption("File status: " + ("Ready (warnings may apply)" if inputs == 4 else "Incomplete") + " · Analysis readiness is shown separately.")
    if state.context_error:
        st.warning("有已绑定文件缺失或不可访问，请在 Files 页替换或解绑。")
