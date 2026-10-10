"""Chat widgets only; controller owns dispatch, persistence and dependencies."""

from pathlib import Path
from uuid import uuid4

import streamlit as st

from app.components.common import show_user_error
from app.state import WorkspaceState, refresh_project_state, reset_file_widgets
from bulk_rnaseq.workspace import FileImportService
from bulk_rnaseq.workspace.chat_controller import ChatController
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.result_manager import ResultManager, display_value


STAGES = {"understanding": "Understanding request", "selected": "Selected analysis",
          "validating": "Validating project inputs", "running": "Running analysis",
          "summarizing": "Preparing result summary", "completed": "Completed",
          "routing": "Understanding request type", "planning": "Planning workflow",
          "planning_customization": "Planning customization", "customizing": "Reusing source result",
          "step_pending": "○ Planned", "step_running": "Running", "step_succeeded": "✓ Completed",
          "step_failed": "✗ Failed", "step_skipped": "— Skipped"}


def render(state: WorkspaceState, service: FileImportService) -> None:
    project_key = state.current_project.key
    controller = ChatController(service.manager, Path(__file__).resolve().parents[2] / ".env")
    st.caption("支持单项分析、多步骤 Workflow 和已有结果定制。DeepSeek 规划，本地校验并串行执行；不自动重试，不执行动态代码。")
    if state.chat_error:
        show_user_error(state.chat_error)
    if not state.chat_messages:
        st.info("可以输入：帮我画一下样本 PCA。或：以 T 为对照，比较 OT 的差异表达。请使用自己 metadata 中的真实组名。")
        st.caption("需要补充信息时，请重新完整描述请求；历史聊天不作为隐式参数。")
    for row in state.chat_messages:
        with st.chat_message(row["role"]):
            st.text(row["content"])
            result = row.get("result", {})
            if row.get("skill_name"):
                st.caption(f"Analysis: {row['skill_name']} · Status: {row['status']} · Run: {row.get('run_id') or '—'}")
            if result.get("error_code"):
                with st.expander("Technical details"):
                    st.code(result["error_code"], language=None)
                    if result.get("details"):
                        st.json(result["details"])
            if result.get("artifacts"):
                st.caption("Analysis outputs: " + ", ".join(item["name"] for item in result["artifacts"]))
            if result.get("steps"):
                for step in result["steps"]:
                    marker = {"succeeded": "✓", "failed": "✗", "skipped": "—", "pending": "○"}.get(step["status"], "…")
                    st.text(f"{marker} {step['step_id']} · {step['skill_name']} · {step['status']}")
            if result.get("changed_parameters"):
                st.caption("Changes（实际验证的参数）")
                for key, value in result["changed_parameters"].items():
                    st.text(f"{key}: {display_value(value)}")
            if result.get("source_options"):
                options = {item["run_id"]: item["label"] for item in result["source_options"]}
                selected_source = st.selectbox("Source result", list(options), index=None,
                    format_func=lambda value: options[value], key="source:" + row["request_id"], disabled=state.chat_busy)
                if st.button("Continue with selected result", key="continue:" + row["request_id"], disabled=state.chat_busy or selected_source is None):
                    state.pending_chat = {"request_id": row["request_id"], "text": result["user_message"],
                        "project_key": project_key, "source_run_id": selected_source, "state": "queued"}
                    state.chat_busy = True
                    st.rerun()
            if result.get("customization_id") and result.get("run_id") and result.get("status") == "succeeded":
                for warning in result.get("warnings", []):
                    st.warning(f"{warning['code']}: {warning['message']}")
                    if warning.get("details"):
                        st.json(warning["details"], expanded=False)
                try:
                    browser = ResultManager(service.manager, project_key)
                    pictures = [a for a in browser.list_artifacts(result["run_id"]) if a.kind == "IMAGE" and not a.error_code]
                    if pictures:
                        st.image(browser.preview_image(result["run_id"], pictures[0].artifact_id), width="stretch")
                except Exception:
                    st.caption("图片预览暂不可用，请在 Results 检查文件状态。")
            if result.get("run_id") or result.get("backend_request_id") or result.get("workflow_id") or result.get("customization_id"):
                if st.button("View Results", key="chat_results:" + row["request_id"], disabled=state.chat_busy):
                    state.selected_result = result.get("backend_request_id")
                    state.selected_run_id = result.get("run_id") or result.get("backend_request_id")
                    state.selected_collection_id = result.get("workflow_id") or result.get("customization_id")
                    state.selected_tab = "Results"
                    reset_file_widgets(state)
                    st.rerun()
    user_text = st.chat_input("描述分析流程或已有结果的定制需求…", disabled=state.chat_busy or bool(state.chat_error), max_chars=4000)
    if user_text and state.pending_chat is None:
        state.pending_chat = {"request_id": str(uuid4()), "text": user_text, "project_key": project_key, "state": "queued"}
        state.chat_busy = True
        st.rerun()
    pending = state.pending_chat
    if pending and pending["state"] == "queued":
        pending["state"] = "submitted"
        with st.status("Preparing project context…", expanded=True) as status:
            def progress(stage: str, name: str | None) -> None:
                label = STAGES.get(stage, stage) + (f": {name}" if name else "")
                status.update(label=label)
                st.write(label)
            options = {"source_run_id": pending["source_run_id"]} if pending.get("source_run_id") else {}
            result = controller.submit(pending["project_key"], pending["text"], pending["request_id"], progress=progress, **options)
            status.update(label=result.status, state="error" if result.status == "failed" else "complete", expanded=False)
        state.pending_chat = None
        state.chat_busy = False
        refresh_project_state(state, service)
        if not any(row.get("request_id") == result.request_id and row["role"] == "assistant" for row in state.chat_messages):
            state.notice = {"status": result.status, "error_code": result.error_code,
                            "message": result.assistant_message + "\n" + "\n".join(item["message"] for item in result.warnings)}
        st.rerun()
    elif pending and pending["state"] == "submitted":
        st.warning("请求已提交，可能仍在运行或曾中断。不会因刷新自动重跑；请检查 Results 和日志。")
    with st.popover("Clear Chat"):
        st.caption("仅清空聊天展示并归档历史，不删除 inputs、results 或请求防重记录。")
        confirm = st.checkbox("确认仅清空当前项目聊天", disabled=state.chat_busy)
        if st.button("Clear conversation", disabled=state.chat_busy or not confirm):
            try:
                ChatStore(service.manager, project_key).clear(confirm=True)
                refresh_project_state(state, service)
                st.rerun()
            except Exception as error:
                show_user_error(error)
