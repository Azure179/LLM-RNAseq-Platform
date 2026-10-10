# eggNOG mapper 一次导入 GO / KEGG mapping

## 用户操作

在当前 Project 的 Files 页，优先使用最上方 **eggNOG mapper · Recommended / 推荐**：

1. 选择 eggNOG `.emapper.annotations` / TSV / TXT / CSV / XLSX 文件。
2. 多工作表 XLSX 需填写工作表名。
3. 点击 **Import eggNOG → GO + KEGG**。
4. 已有任一 mapping 时，必须先勾选 **Confirm Replace GO + KEGG with eggNOG**。
5. 在下方 GO Mapping / KEGG Mapping 中查看 coverage、来源和警告，并用 **Download generated …** 下载标准 TSV。

原来的 GO/KEGG 独立上传入口继续存在。推荐入口优先展示；确认 eggNOG 导入后，两项当前绑定都使用生成文件。每个角色仍只有一个活动绑定，不会自动合并 eggNOG 和自定义注释。用户仍可明确确认、单独替换为自定义 mapping，或只解绑其中一项；不会自动恢复旧版本。

## 格式与科学边界

必须有 `query`（或 `#query`）、`GOs`、`KEGG_Pathway` 列。支持 eggNOG 注释头部的 `##` 注释行，以及注释字段中的多个逗号/分号分隔 ID。复用现有 GO/KEGG reader 校验和去重；未注释的 `-` 行不生成关联。

输出：

- `gene_to_go.tsv`：`gene_id<TAB>go_id`，每行一个关联。
- `gene_to_kegg.tsv`：`gene_id<TAB>pathway_id`，每行一个关联。

KEGG `koNNNNN` / `mapNNNNN` 使用现有规范化规则归一到参考 `mapNNNNN`。**KEGG_ko 的 K 编号不是 pathway，不能替代 KEGG_Pathway。**

不把蛋白 Description 当作 GO term 或 pathway 名称，不访问网络补齐名称，不假设物种。GO 名称和 ontology 仍由已有 enrichment 依赖处理。导入只生成注释关联，不执行富集、不生成 gene list、不选择背景；GO/KEGG Ready 仍需满足原来的执行依赖。

这一入口要求两类关联都至少有一条合法记录；一类缺失/为空/非法时，整个双 mapping 导入失败，原绑定不变。如果注释只含 GO 或只含 KEGG，请使用下方对应的独立入口。

### Gene ID 不自动改名

转换保留原 query ID。`rna-Asc….1` 不会自动变为 `gene-Asc…`；这需要有依据的 transcript-to-gene crosswalk，而不是文件格式转换。原先 Real_DATA_TEST 的 Asc crosswalk 流程不被修改。

如果 counts 与 eggNOG ID 体系不同，coverage 会如实降低，显示 LOW_MAPPING_COVERAGE，不伪造匹配。此时可以继续使用既有 crosswalk 产生的自定义 GO/KEGG mapping；本次未增加 UI crosswalk 上传或自动 ID 转换功能。

## 工程设计

保留四个既有文件角色，不为一个原始注释文件另建重复的执行参数。新增 import_eggnog 服务：原始文件先做稳定快照，两个解析器生成独立标准表，沿用 validate_file / enrich_file_info 校验与覆盖率统计。

每个角色保存独立不可变版本：

```text
input/go_mapping/{batch_id}/
  original/{原 eggNOG 文件}
  analysis/gene_to_go.tsv
  import.json
input/kegg_mapping/{同一 batch_id}/
  original/{原 eggNOG 文件}
  analysis/gene_to_kegg.tsv
  import.json
```

两个 import.json 记录共同 batch_id、原文件 hash、生成文件 hash、原工作表、source_kind、旧 binding 和源数据行数。原件分别保存在两个版本中，因此可能有额外磁盘占用；这与现有单角色校验/下载/恢复结构兼容。

全部准备好后，通过一次带版本检查（compare-and-swap）的 project.json 更新同时发布两个绑定。异常或并发修改不会造成只有一项绑定更新；已经落盘但未发布的版本可能保留为未绑定目录，不执行破坏性回滚。旧原件和旧 mapping 不删除。

输入检查、执行上下文和重启恢复继续走现有 FileImportService。UI 只负责上传与展示，不包含注释解析或统计代码。

## 修改文件

- `src/bulk_rnaseq/workspace/eggnog_import.py`：双 mapping 生成、原件与来源记录、原子发布。
- `src/bulk_rnaseq/workspace/file_import.py`：服务入口与来源显示。
- `src/bulk_rnaseq/workspace/manager.py`：多绑定原子更新，旧单文件更新复用它。
- `src/bulk_rnaseq/workspace/ui_adapter.py`：复用上传传输路径、生成 mapping 的安全下载。
- `app/views/files.py`：推荐 eggNOG 入口、替换确认、生成文件下载。
- `tests/test_eggnog_import.py`：双格式共存、替换、失败不半更新、持久化、coverage、格式错误、XLSX、下载和 UI 组件测试。
- `tests/test_workspace_ui.py`：上传入口由四个扩展为五个，保留原四类完整测试。
- README / 本说明：使用方法与限制。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_eggnog_import.py tests/test_file_import.py tests/test_workspace_ui.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

默认测试不调用 API/R。AppTest 覆盖真实 UI 代码与上传控件，但不是浏览器鼠标手工验收。

## 本次实测（2026-09-29）

- 导入与 UI 相关专项：143 passed。
- 全量回归：849 passed、60 skipped，48.33 秒。API/R 显式测试未开启；另有 Windows 符号链接权限测试跳过。
- 使用现有 `Real_DATA_TEST/input/Acanthopagrus_schlegelii.emapper.annotations.tsv` 和 `count_matrix.csv`，经正式 FileImportService 在临时项目中验证，不修改已有项目或真实原件。
- 35,951 条注释记录；生成 4,191,346 条 GO 关联（27,707 个原始 query ID，23,492 个 GO ID）及 83,294 条 KEGG 关联（15,979 个原始 query ID，393 个 pathway ID）。这些是注释关联数，不是显著富集数。
- 原始 query 与 24,581 个 count gene ID 的精确 coverage 均为 0，正确产生警告。这套数据仍需此前的 Asc crosswalk，不能直接将本次生成表用于有效的富集。
- counts 导入和 eggNOG 转换共约 43.38 秒；临时测试项目由测试结束清理。没有留下新的用户项目，没有 R 或 DeepSeek 调用。
- 本次未尝试浏览器鼠标验收，不将组件测试报告成真实浏览器测试。
