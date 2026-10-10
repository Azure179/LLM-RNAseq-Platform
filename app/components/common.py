"""Presentation-only messages and a single refresh/rerun path."""

from typing import Any
import logging

import streamlit as st

from app.state import WorkspaceState, refresh_project_state, reset_file_widgets
from bulk_rnaseq.workspace import FileImportService, ProjectError


ROLE_LABELS = {"count_matrix": "Count Matrix", "metadata": "Metadata", "go_mapping": "GO Mapping", "kegg_mapping": "KEGG Mapping"}
FILE_LABELS = {"missing": "✗ Missing", "valid": "✓ Imported", "warning": "⚠ Warning", "invalid": "✗ Invalid"}
LOGGER = logging.getLogger(__name__)


def show_user_error(error: Exception | dict[str, Any]) -> None:
    if isinstance(error, ProjectError):
        data = error.to_dict()
    elif isinstance(error, dict):
        data = error
    else:
        LOGGER.error("UI operation failed: type=%s", type(error).__name__)
        data = {"error_code": "UI_OPERATION_FAILED", "message": "操作未完成，请刷新并检查文件或项目权限。"}
    st.error(data.get("message") or "操作未完成，原文件未被自动删除。")
    with st.expander("Technical details"):
        # Do not render exception repr, stack traces, absolute paths or arbitrary OS text.
        st.code(str(data.get("error_code", "UI_OPERATION_FAILED")), language=None)
        allowed = {"row", "field", "sample_id", "missing_in_metadata", "extra_in_metadata",
                   "missing_in_counts", "available", "requested", "group_column"}
        details = {key: value for key, value in data.get("details", {}).items() if key in allowed}
        if details:
            st.json(details)


def show_notice(state: WorkspaceState) -> None:
    if state.notice:
        notice = state.notice
        state.notice = None
        if notice["status"] == "failed":
            show_user_error(notice)
        else:
            st.success(notice.get("message") or "项目已更新。")
        for warning in notice.get("warnings", []):
            st.warning(warning["message"])


def finish_action(state: WorkspaceState, service: FileImportService, notice: dict[str, Any]) -> None:
    state.notice = notice
    refresh_project_state(state, service)
    reset_file_widgets(state)
    st.rerun()
