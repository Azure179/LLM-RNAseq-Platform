"""Render backend readiness; do not invent requirements in the UI."""

import streamlit as st
from app.state import WorkspaceState


def render_readiness(state: WorkspaceState, *, compact: bool = False) -> None:
    for name, entry in state.project_readiness.items():
        label = name.removeprefix("rnaseq.").upper()
        if entry["ready"]:
            st.caption(f"✓ {label} · Ready")
        elif entry["errors"]:
            st.caption(f"✗ {label} · Input error")
        elif entry["data_ready"]:
            st.caption(f"○ {label} · Needs parameters")
        else:
            st.caption(f"○ {label} · Incomplete")
        if not compact and (entry["missing"] or entry["errors"]):
            st.write("Missing: " + ", ".join(entry["missing"]) if entry["missing"] else "Input validation failed.")
            for error in entry["errors"]:
                st.warning(error["message"])
    if not compact:
        st.caption("Readiness 来自现有 Manifest 与导入服务。输入齐备不代表 R 拟合一定成功；本页面不执行分析。")
