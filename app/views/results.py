"""Read-only presentation; discovery and byte access belong to ResultManager."""

import pandas as pd
import streamlit as st

from app.components.common import show_user_error
from app.state import WorkspaceState, reset_file_widgets, widget_key
from bulk_rnaseq.workspace import FileImportService, ProjectError
from bulk_rnaseq.workspace.result_manager import ResultManager, RunInfo, SKILL_LABELS, display_value
from bulk_rnaseq.workspace.ui_adapter import format_file_size


CARDS = {
    "rnaseq.deg": [("Analyzed genes", "genes_analyzed"), ("Up", "upregulated_genes"), ("Down", "downregulated_genes"), ("NS", "n_ns")],
    "rnaseq.pca": [("Samples", "n_samples"), ("Groups", "n_groups"), ("PC1 variance (%)", "pc1_variance"), ("PC2 variance (%)", "pc2_variance")],
    "rnaseq.volcano": [("Total genes", "n_total"), ("Up", "n_up"), ("Down", "n_down"), ("NS", "n_ns")],
    "rnaseq.heatmap": [("Genes used", "n_genes_used"), ("Samples", "n_samples")],
    "rnaseq.go": [("Input genes", "n_query_input"), ("Mapped genes", "n_query_mapped"), ("Significant terms", "n_enriched")],
    "rnaseq.kegg": [("Input genes", "n_query_input"), ("Mapped genes", "n_query_mapped"), ("Significant pathways", "n_enriched")],
    "rnaseq.filter_by_annotation": [("Source DEGs", "n_source_genes"), ("Matched genes", "n_matched_genes"), ("Annotated genes", "n_annotated_genes")],
    "rnaseq.enrichment_plot": [("Available terms", "n_available"), ("Displayed terms", "n_displayed")],
}
STATUS_LABELS = {"succeeded": "Completed", "failed": "Failed", "needs_input": "Needs input",
                 "unsupported": "Unsupported", "validated": "Validated only", "unknown": "Unverified"}


def show_cards(run: RunInfo) -> None:
    cards = CARDS.get(run.skill_name, [])
    if run.status != "succeeded" or not cards:
        return
    for column, (label, field) in zip(st.columns(len(cards)), cards):
        value = run.summary.get(field)
        column.metric(label, f"{value:.2f}" if isinstance(value, float) else str(value) if value is not None else "未提供")
    if run.skill_name == "rnaseq.deg" and "n_ns" not in run.summary:
        st.caption("原始 summary 未单独记录 NS；有有效 pvalue 的基因数见下方完整摘要。")


def show_table(manager: ResultManager, run: RunInfo, artifact_id: str, key: str) -> None:
    limit = st.selectbox("Preview rows", [100, 20, 50, 500, 1000], key=key + ":rows")
    preview = manager.preview_table(run.run_id, artifact_id, limit=limit)
    frame = preview.frame
    available = len(frame)
    padj_column = next((c for c in ("padj", "p.adjust") if c in frame), None)
    if run.skill_name in {"rnaseq.go", "rnaseq.kegg"} and padj_column:
        order = pd.to_numeric(frame[padj_column], errors="coerce").sort_values(kind="stable", na_position="last").index
        frame = frame.loc[order]
        st.caption(f"{padj_column} 升序，仅对已读取的预览行排序。")
    if run.skill_name == "rnaseq.deg" and "log2FoldChange" in frame:
        choice = st.selectbox("DEG direction (preview only)", ["All", "Up", "Down"], key=key + ":direction")
        if choice != "All":
            if artifact_id != "filtered_csv":
                st.info("按上下调筛选请先选择 filtered_csv（已有显著 DEG 表）；完整表保持原样。")
            else:
                lfc = pd.to_numeric(frame["log2FoldChange"], errors="coerce")
                frame = frame.loc[lfc > 0 if choice == "Up" else lfc < 0]
                st.caption("仅按已有显著 DEG 表的效应方向筛选当前预览；不重新判断显著性。")
    preferred = (["gene_id", "baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj"]
                 if run.skill_name == "rnaseq.deg" else ["go_id", "pathway_id", "ID", "description", "Description", "gene_ratio", "GeneRatio", "bg_ratio", "BgRatio", "pvalue", "padj", "p.adjust", "overlap_count", "Count"])
    columns = [c for c in preferred if c in frame] + [c for c in frame if c not in preferred]
    displayed = frame[columns].apply(lambda column: column.map(lambda value: value[:500] + "…" if len(value) > 500 else value))
    st.dataframe(displayed, hide_index=True, width="stretch")
    st.caption("超长单元格仅显示前 500 字符；下载包含完整原始内容。")
    if preview.truncated:
        st.caption(f"Showing first {available} source rows; total rows not scanned. 当前显示 {len(frame)} 行。")
    else:
        st.caption(f"Showing {len(frame)} of {preview.total_rows} rows（已到文件末尾）。")


def show_artifacts(manager: ResultManager, run: RunInfo, state: WorkspaceState) -> None:
    if not run.artifacts:
        st.info("No output artifacts for this request.")
        return
    st.dataframe([{"File": a.filename, "Type": a.kind,
                   "Size": format_file_size(a.size_bytes) if a.size_bytes is not None else "—",
                   "State": a.error_code or "Available"} for a in run.artifacts], hide_index=True, width="stretch")
    groups = {"Figures": {"IMAGE"}, "Tables": {"TABLE"}, "JSON / Metadata": {"JSON"}, "Other files": {"TEXT", "UNKNOWN"}}
    key = widget_key(state, "artifact:" + run.run_id)
    options = [label for label, kinds in groups.items() if any(a.kind in kinds for a in run.artifacts)]
    category = st.radio("Artifact category", options, horizontal=True, key=key + ":category")
    artifacts = [a for a in run.artifacts if a.kind in groups[category]]
    by_id = {a.artifact_id: a for a in artifacts}
    selected = st.selectbox("Result file", list(by_id), format_func=lambda value: by_id[value].filename,
                            key=key + ":" + category)
    artifact = by_id[selected]
    if artifact.error_code:
        st.warning("Artifact file is missing." if artifact.error_code == "ARTIFACT_MISSING" else "结果路径不允许访问。")
        return
    try:
        if artifact.kind == "IMAGE":
            st.image(manager.preview_image(run.run_id, selected), caption=artifact.filename, width="stretch")
        elif artifact.kind == "TABLE":
            show_table(manager, run, selected, key + ":" + selected)
        elif artifact.kind == "JSON":
            with st.expander("JSON preview (compact)"):
                st.json(manager.preview_json(run.run_id, selected), expanded=1)
                st.caption("展示时省略内部过程输出、绝对路径和过长数组；下载保留原文件。")
        else:
            st.caption("此类型提供原文件下载，不直接显示运行日志或未知格式。")
    except ProjectError as error:
        show_user_error(error)
    # Deferred callable: read and revalidate only at the actual download request.
    def download_bytes() -> bytes:
        return manager.download(run.run_id, selected)[1]
    if artifact.size_bytes is not None and artifact.size_bytes <= 300 * 1024 * 1024:
        st.download_button("Download " + artifact.filename, data=download_bytes, file_name=artifact.filename,
                           mime="application/octet-stream", on_click="ignore", key=key + ":download:" + selected)
    else:
        st.info("该文件超过 300 MiB 浏览器下载上限。")


def render(state: WorkspaceState, service: FileImportService) -> None:
    st.subheader("Results")
    manager = ResultManager(service.manager, state.current_project.key)
    if st.button("Refresh Results"):
        reset_file_widgets(state)
        st.rerun()
    all_runs = manager.list_runs()
    for message in manager.diagnostics:
        st.warning(message)
    collections = manager.list_collections()
    result_type = st.selectbox("Result type", ["All", "Analysis", "Workflow", "Customization"], key=widget_key(state, "result_type"))
    visible_collections = [c for c in collections if result_type in {"All", c["type"]}]
    if visible_collections:
        by_id = {c["id"]: c for c in visible_collections}
        selected = state.selected_collection_id
        selected = st.selectbox("Workflow / Customization", list(by_id), index=list(by_id).index(selected) if selected in by_id else None,
            format_func=lambda key: f"{by_id[key]['type']} · {key} · {by_id[key]['status']}", key=widget_key(state, "collection"))
        if selected:
            state.selected_collection_id = selected
            item = by_id[selected]
            st.caption(f"{item['type']} ID: {selected} · {item['status']}")
            if item["type"] == "Workflow":
                for step in item["members"]:
                    st.text(f"{step['step_id']} · {step['skill_name']} · {step['status']} · depends on: {', '.join(step['dependencies']) or '—'}")
                    if step.get("error_code"):
                        st.caption(step["error_code"] + ": " + str(display_value(step.get("message") or "")))
                    if step.get("run_id") and st.button("Open " + step["step_id"], key=widget_key(state, selected + step["step_id"])):
                        state.selected_run_id = step["run_id"]
                        reset_file_widgets(state)
                        st.rerun()
            else:
                st.text("Based on: " + str(item["source_run_id"]))
                st.text("Mode: " + item["mode"])
                for key, value in item["changed_parameters"].items():
                    st.text(f"{key}: {display_value(value)}")
                if item.get("reason"):
                    st.text(str(display_value(item["reason"])))
                if item.get("run_id") and st.button("Open customized result", key=widget_key(state, selected + ":open")):
                    state.selected_run_id = item["run_id"]
                    reset_file_widgets(state)
                    st.rerun()
    if not all_runs:
        st.info("No analysis results yet.")
        return
    left, middle, right = st.columns(3)
    skill = left.selectbox("Skill", ["All", *SKILL_LABELS], format_func=lambda v: SKILL_LABELS.get(v, v), key=widget_key(state, "result_skill"))
    status = middle.selectbox("Status", ["All", "Success", "Failed", "Warning"], key=widget_key(state, "result_status"))
    order = right.selectbox("Sort", ["Newest first", "Oldest first"], key=widget_key(state, "result_sort"))
    runs = manager.list_runs(skill_name=None if skill == "All" else skill,
                             status={"All": None, "Success": "succeeded", "Failed": "failed", "Warning": "warning"}[status],
                             newest_first=order == "Newest first")
    runs = [r for r in runs if result_type in {"All", r.result_type}]
    if not runs:
        st.info("No runs match these filters.")
        return
    st.dataframe([{"Type": r.result_type, "Skill": SKILL_LABELS.get(r.skill_name, r.skill_name), "Run ID": r.run_id,
                   "Time (UTC)": r.created_at, "Status": STATUS_LABELS.get(r.status, r.status),
                   "Warning": r.has_warning, "Comparison": r.comparison} for r in runs], hide_index=True, width="stretch")
    ids = list(dict.fromkeys(r.run_id for r in runs))
    selected = state.selected_run_id
    if selected not in ids and state.selected_result:
        matches = [r.run_id for r in runs if r.backend_request_id == state.selected_result]
        selected = matches[0] if len(matches) == 1 else None
    chosen = st.selectbox("Analysis run", ids, index=ids.index(selected) if selected in ids else 0,
                          key=widget_key(state, "result_run"))
    state.selected_run_id = chosen
    try:
        run = manager.get_run(chosen)
        st.subheader(SKILL_LABELS.get(run.skill_name, run.skill_name))
        st.caption(f"Run: {run.run_id} · {STATUS_LABELS.get(run.status, run.status)} · {run.created_at}")
        if run.workflow_id:
            st.caption(f"Workflow: {run.workflow_id} · Step: {run.step_id}")
        if run.customization_id:
            st.caption(f"Customized: {run.customization_id} · Based on: {run.source_run_id}")
        if run.time_source != "recorded":
            st.caption("时间来自文件修改时间，原记录没有分析开始时间。")
        if run.comparison:
            st.text(run.comparison)
        if run.triggered_by:
            st.text("Triggered by: " + run.triggered_by)
        if run.status == "failed":
            st.error("Analysis failed: " + (run.error_code or "UNKNOWN_ERROR"))
            st.text(display_value(run.message or "未提供错误说明。"))
            st.caption("Stage: " + (run.stage or "未记录"))
        elif run.status != "succeeded":
            st.info("此请求没有完成统计分析：" + STATUS_LABELS.get(run.status, run.status))
        if run.final_response_status == "failed" and run.status == "succeeded":
            st.warning("分析成功，但聊天总结失败；真实分析结果仍可查看。")
        for warning in run.warnings:
            st.warning(str(display_value(warning["message"])))
        for warning in run.browser_warnings:
            st.warning(warning)
        if run.status == "succeeded" and run.skill_name in {"rnaseq.go", "rnaseq.kegg"} and run.summary.get("n_enriched") == 0:
            st.info("Analysis completed successfully. 当前阈值下未检测到显著富集结果。")
        show_cards(run)
        with st.expander("Analysis parameters"):
            st.json(display_value(run.parameters), expanded=1)
        with st.expander("Full summary"):
            st.json(display_value(run.summary), expanded=1)
        show_artifacts(manager, run, state)
    except ProjectError as error:
        show_user_error(error)
