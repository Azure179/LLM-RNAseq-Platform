"""Local grouping form; no group inference and no file writes in the UI."""

import streamlit as st

from app.components.common import finish_action, show_user_error
from app.state import WorkspaceState, reset_file_widgets, widget_key
from bulk_rnaseq.workspace import FileImportService, ProjectError


def render_sample_groups(state: WorkspaceState, service: FileImportService) -> None:
    st.markdown("**填写样本分组（推荐，无需上传 Metadata）**")
    try:
        draft = service.get_sample_groups(state.current_project.key)
    except ProjectError as error:
        if error.code == "COUNT_MATRIX_REQUIRED":
            st.info(str(error))
        else:
            show_user_error(error)
        return
    if draft["warning"]:
        st.warning(draft["warning"])
    st.caption("样本名来自 count matrix，不可编辑。请逐个填写组名；不根据文件名猜分组，也不自动指定 treatment/control。")
    if st.button("Refresh sample list", key=widget_key(state, "groups:refresh")):
        reset_file_widgets(state)
        st.rerun()
    # Pin the source revision to this form's widgets, not the newest disk version
    # on submit. Changes made in another tab must not validate a stale form.
    revision_key = widget_key(state, "groups:revision")
    if revision_key not in st.session_state:
        st.session_state[revision_key] = draft["expected_updated_at"]
    expected = st.session_state[revision_key]
    if expected != draft["expected_updated_at"]:
        st.warning("项目在填写期间发生了变化，请点击 Refresh sample list 后重新填写。")
        return
    with st.form(widget_key(state, "groups:form")):
        groups = {}
        for index, row in enumerate(draft["samples"]):
            groups[row["sample_id"]] = st.text_input(
                "Group for " + row["sample_id"], value=row["group"],
                placeholder="例如 T / OT / O", key=widget_key(state, f"groups:{index}"))
        confirmed = not draft["metadata_bound"]
        if draft["metadata_bound"]:
            st.caption("保存将替换当前 Metadata，旧版本保留。此表单仅保存样本和分组，不保留上传表中的额外注释列。")
            confirmed = st.checkbox("Confirm Replace Metadata with these groups",
                                    key=widget_key(state, "groups:replace"))
        submitted = st.form_submit_button("Save sample groups", disabled=False)
    if submitted:
        if not confirmed:
            st.warning("请先确认替换当前 Metadata。")
            return
        result = service.save_sample_groups(state.current_project.key, groups,
                                            expected_updated_at=expected, replace=draft["metadata_bound"])
        if result.status == "failed":
            show_user_error(result.model_dump(mode="json"))
        else:
            finish_action(state, service, result.model_dump(mode="json"))
