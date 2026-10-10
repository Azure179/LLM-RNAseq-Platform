"""Only UI wiring: uploaded bytes go through the backend transport adapter."""

import streamlit as st

from app.components.common import FILE_LABELS, ROLE_LABELS, finish_action, show_user_error
from app.components.sample_groups import render_sample_groups
from app.state import WorkspaceState, reset_file_widgets, widget_key
from bulk_rnaseq.workspace import FileImportService, FileRole
from bulk_rnaseq.workspace.ui_adapter import format_file_size, import_upload, read_download, read_mapping_download


def render_eggnog(state: WorkspaceState, service: FileImportService) -> None:
    """Recommended upload route; all parsing and binding remain in the service."""
    project = state.current_project
    with st.expander("eggNOG mapper · Recommended / 推荐", expanded=True):
        st.caption("优先方式：上传一次 eggNOG annotations，自动生成并绑定 GO + KEGG mapping。下方独立 mapping 上传仍可用。")
        st.caption("需要 query/#query、GOs、KEGG_Pathway 列；KEGG_ko 不能替代 pathway。Gene ID 保持原样，请检查与 counts 的 coverage。")
        upload = st.file_uploader("eggNOG annotations file", type=["annotations", "tsv", "txt", "csv", "xlsx"],
                                  max_upload_size=300, key=widget_key(state, "upload:eggnog"))
        sheet = st.text_input("eggNOG XLSX sheet (if needed)", key=widget_key(state, "sheet:eggnog"))
        bound = bool(project.metadata.files.go_mapping or project.metadata.files.kegg_mapping)
        confirmed = not bound
        if bound:
            st.info("当前已有 mapping。确认后两项绑定优先使用本次 eggNOG 生成的文件；旧版本保留，不自动合并。")
            confirmed = st.checkbox("Confirm Replace GO + KEGG with eggNOG", key=widget_key(state, "replace:eggnog"))
        if st.button("Import eggNOG → GO + KEGG", disabled=upload is None or not confirmed,
                     key=widget_key(state, "submit:eggnog")):
            with st.spinner("Validating eggNOG and generating both mappings…"):
                result = import_upload(service, project.key, upload.name, upload, "eggnog",
                                       replace=bound, mapping_sheet=sheet or None)
            if result.status == "failed":
                show_user_error(result.model_dump(mode="json"))
            else:
                finish_action(state, service, result.model_dump(mode="json"))


def render(state: WorkspaceState, service: FileImportService) -> None:
    st.subheader("Input Files")
    st.caption("选择角色对应的文件，然后点击 Import。替换须确认；Remove 只解绑，不删除原件或历史版本。")
    project = state.current_project
    render_eggnog(state, service)
    for role in FileRole:
        label = ROLE_LABELS[role.value]
        info = state.file_status[role.value]
        bound = getattr(project.metadata.files, role.value) is not None
        with st.expander(f"{label} · {FILE_LABELS[info['status']]}", expanded=role in {FileRole.COUNT_MATRIX, FileRole.METADATA}):
            if role == FileRole.METADATA:
                render_sample_groups(state, service)
            if info.get("original_filename"):
                st.code(info["original_filename"], language=None)
            if info.get("source_kind") == "eggnog":
                st.caption("Active source: eggNOG-derived mapping. 独立 mapping 替换需明确确认；不会自动切回旧版本。")
                # Capture this loop iteration's role/binding, not the final iteration.
                def generated_download(selected_role=role, binding=info["stored_path"]) -> bytes:
                    return read_mapping_download(service, project.key, selected_role, expected_binding=binding)[1]
                filename = "gene_to_go.tsv" if role == FileRole.GO_MAPPING else "gene_to_kegg.tsv"
                st.download_button("Download generated " + filename, data=generated_download,
                                   file_name=filename, mime="text/tab-separated-values", on_click="ignore",
                                   key=widget_key(state, "generated:" + role.value))
            download = info.get("download")
            if download:
                st.caption(format_file_size(download["size_bytes"]) + " · Original file / 已保存文件")
            if info["status"] == "missing":
                st.info("Metadata 尚未保存：导入 counts 后填写上方分组即可。" if role == FileRole.METADATA else f"{label} not imported.")
            elif info["status"] == "invalid":
                show_user_error(info)
            for warning in info.get("warnings", []):
                st.warning(warning["message"])
            if info.get("summary"):
                st.json({"summary": info["summary"], "validation": info.get("validation", {})}, expanded=False)
            types = ["csv", "tsv", "txt", "xls"] if role in {FileRole.COUNT_MATRIX, FileRole.METADATA} else ["csv", "tsv", "txt", "xlsx", "annotations"]
            if role == FileRole.METADATA:
                st.caption("可选：已有 Metadata 文件也可使用下方上传入口；不是必需步骤。")
            if role in {FileRole.COUNT_MATRIX, FileRole.METADATA}:
                st.caption("自动识别逗号 / Tab / 分号；兼容文本 .xls。保留原文件并生成标准 CSV；二进制 Excel 请先导出 UTF-8 文本。")
            upload = st.file_uploader(f"{label} file", type=types, max_upload_size=300,
                                      key=widget_key(state, f"upload:{role.value}"))
            options = {}
            with st.popover(f"{label} import options"):
                if role == FileRole.METADATA:
                    sample = st.text_input("Sample column (blank = auto)", key=widget_key(state, "sample_column"))
                    group = st.text_input("Group column (blank = auto)", key=widget_key(state, "group_column"))
                    options = {"sample_column": sample or None, "group_column": group or None}
                elif role in {FileRole.GO_MAPPING, FileRole.KEGG_MAPPING}:
                    sheet = st.text_input("XLSX sheet (if needed)", key=widget_key(state, f"sheet:{role.value}"))
                    options = {"mapping_sheet": sheet or None}
                else:
                    st.caption("Require raw non-negative integer counts, not TPM/FPKM. Geneid / Gene_ID / gene id 自动规范为 gene_id；不修改基因 ID 或样本名。")
            if upload is not None:
                confirmed = not bound
                if bound:
                    st.warning(f"A {label} is already imported.")
                    confirmed = st.checkbox(f"Confirm Replace {label}", key=widget_key(state, f"replace:{role.value}"))
                action, cancel = st.columns(2)
                if action.button(f"{'Replace' if bound else 'Import'} {label}", disabled=not confirmed,
                                 key=widget_key(state, f"submit:{role.value}")):
                    with st.spinner(f"Validating {label}…"):
                        result = import_upload(service, project.key, upload.name, upload, role, replace=bound, **options)
                    if result.status == "failed":
                        show_user_error(result.model_dump(mode="json"))
                    else:
                        notice = result.model_dump(mode="json")
                        notice["message"] = f"{label} imported."
                        finish_action(state, service, notice)
                if cancel.button(f"Cancel {label}", key=widget_key(state, f"cancel:{role.value}")):
                    reset_file_widgets(state)
                    st.rerun()
            if download:
                if st.button(f"Prepare {label} download", key=widget_key(state, f"prepare:{role.value}")):
                    try:
                        filename, data = read_download(service, project.key, role, expected_binding=download["stored_path"])
                        state.download = (role.value, filename, data)
                    except Exception as error:
                        show_user_error(error)
                if state.download and state.download[0] == role.value:
                    st.download_button(f"Download {label}", state.download[2], file_name=state.download[1],
                                       on_click="ignore", key=widget_key(state, f"download:{role.value}"))
            if bound:
                confirm_remove = st.checkbox(f"Confirm Remove {label} binding", key=widget_key(state, f"remove_confirm:{role.value}"))
                if st.button(f"Remove {label}", disabled=not confirm_remove, key=widget_key(state, f"remove:{role.value}")):
                    result = service.remove_file(project.key, role)
                    if result.status == "failed":
                        show_user_error(result.model_dump(mode="json"))
                    else:
                        notice = result.model_dump(mode="json")
                        notice["message"] = f"{label} binding removed; file versions retained."
                        finish_action(state, service, notice)
