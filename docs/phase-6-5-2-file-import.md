# Phase 6.5.2：File Import & Data Binding

本阶段仅实现文件导入后端。没有 Streamlit、聊天页面、结果浏览器、Workflow、R/DeepSeek 调用或新的统计分析。

## 设计与修改范围

`用户选择文件 → 本地识别建议 → 用户确认 FileRole → 复制并验证 → 原子绑定 → ProjectContext / Readiness`

| 文件 | 职责 |
|---|---|
| `src/bulk_rnaseq/workspace/file_models.py`（新增） | 四个 canonical FileRole，Pydantic ImportResult / FileInspection |
| `src/bulk_rnaseq/workspace/import_validation.py`（新增） | 调用已有验证器、CSV 适配副本、摘要、样本匹配、mapping coverage、识别建议 |
| `src/bulk_rnaseq/workspace/file_import.py`（新增） | 导入/替换/解绑/重验证/文件状态 API，版本保存、摘要索引、SHA-256、审计日志 |
| `src/bulk_rnaseq/workspace/readiness.py`（新增） | 现有 Manifest 依赖计算、Project → Phase 6 ExecutionContext 桥接 |
| `src/bulk_rnaseq/workspace/manager.py`（修改） | 新增存储层原子绑定提交；并发时间戳检查；提交后不再通过可能失败的重新读取来决定提交状态 |
| `src/bulk_rnaseq/workspace/models.py`（修改） | 角色列表从 FileRole 派生；原 project.json schema 不变 |
| `src/bulk_rnaseq/workspace/__init__.py`（修改） | 导出新的公共接口 |
| `src/bulk_rnaseq/validation.py`（修改） | 抽出单文件 `scan_count_matrix` / `scan_metadata`，原 Skill 的最小样本及生物学检查保持不变 |
| `tests/test_file_import.py`（新增） | 隔离临时目录的免费单元测试；禁止 Registry.execute；含 50,000 基因规模检查 |
| `examples/workspace_import_test.py`（新增） | 显式真实文件导入测试；创建新 Workspace 项目，不覆盖已有项目 |
| `README.md`、本文 | 本阶段状态、操作示例、验收和限制 |

没有修改 R 脚本、Skill 统计逻辑、LLM 路由、原有 toy/test data 或原有单元测试。没有新增依赖。

数据保存遵循本次使用的 spreadsheets 技能中的原始研究数据保留原则：原件与适配副本明确分离，不补造缺失值、不四舍五入 counts。

## 新增工程概念

### 1. 文件角色与结构化结果

FileRole 只有 `count_matrix`、`metadata`、`go_mapping`、`kegg_mapping`。
导入必须显式提供角色。`detect_file_role` / `inspect_file` 仅检查表头给出建议：
`suggested_role, candidates, confidence, reasons, columns`。confidence 是规则分值，不是统计概率；复杂 eggNOG 同时含 GO/KEGG 时返回多个候选、建议角色为空。**不调用 DeepSeek，不把识别当作验证通过。**

ImportResult 沿用项目的 `succeeded / failed` 状态拼写，而非新建另一套 success 拼写。包含：

```json
{
  "status": "succeeded",
  "action": "import",
  "role": "count_matrix",
  "original_filename": "counts.csv",
  "stored_path": "input/count_matrix/<version>/analysis/count_matrix.csv",
  "validation": {},
  "summary": {},
  "warnings": [],
  "error_code": null,
  "message": "",
  "details": {},
  "context": {}
}
```

它是**文件操作结果**，不是 RNA-seq SkillResult；不会伪造 `rnaseq.import` Skill。warnings 使用 code/message/details，便于 UI 展示和机器判断。失败返回结构化信息，不直接抛出预期的文件格式错误 traceback。
Project 查询或非法业务参数等管理接口仍沿用 `ProjectError`，调用者可通过 `.to_dict()` 展示。

### 2. 单文件验证与交叉验证分开

- Count：CSV/TSV；第一列 `gene_id`；至少一个样本；检查标识符、重复基因/样本、行宽、缺失、数值、有限值、非负整数、R integer 范围、全零矩阵和零文库。摘要包含 genes/samples/sample_names、重复与缺失数、整数检查、最小/最大值。
- 整数值不能证明数据是 raw counts，所以成功仍返回 `RAW_COUNT_ORIGIN_NOT_VERIFIED`。小数返回 `NON_INTEGER_COUNT` 和可能归一化数据的 warning；绝不自动取整。
- Metadata：支持 `sample,group` 与原项目的 `sample_id,condition`。可显式选择 `sample_column` / `group_column`；默认优先已有 condition。选中的列在**另一个副本**中改为 sample_id/condition，不重写原件。列名转换产生歧义则拒绝。
- Metadata 检查样本唯一、分组存在、全部单元格缺失情况、行宽，返回 groups、replicates。额外列的缺失值仍按旧验证器严格拒绝。
- 无论先导入谁，只要两者都存在，就比较 sample 集合。报告 exact_match、missing_in_metadata、extra_in_metadata、order_difference。集合不同拒绝新绑定；集合相同但顺序不同只 warning，Skill 继续按名字对齐。
- 导入允许单样本文件，但不表示能运行 PCA/DEG；执行准备检查继续使用原来的样本/重复数约束。

### 3. GO/KEGG 格式与 coverage

复用 `read_go_mapping` / `read_kegg_mapping`，不维护第二套格式规则：

- GO 自定义列 `gene_id,go_id`，GO ID 严格为 `GO:` 加 7 位数字；可含 ontology/go_name。
- KEGG 自定义列 `gene_id,pathway_id`，遵循现有 pathway 命名规则；KO 编号本身不等于 pathway，不能凭 KO 自动推测 pathway。
- eggNOG 支持 query/#query 与 GOs、KEGG_Pathway，兼容 CSV/TSV/annotations 文本及 XLSX。多工作表 XLSX 需指定 mapping_sheet。
- 一个基因多个 term/pathway 允许；重复 gene–term 对按旧 reader 计一次并 warning。未注释行按旧规则跳过并计数；缺少 gene ID 或非法 GO/pathway ID 拒绝。
- coverage = **当前 count matrix 中有 mapping 的唯一基因 / count matrix 所有唯一基因**，不是 enrichment background，也不是 DEG coverage。
- 低于 50% 只 warning，包括 0 交集；50% 是质量提示规则，不是统计阈值。不自动修改 rna-/gene- 前缀、版本号或物种命名。
- 先导入 mapping 后导入 counts 也支持；重新查询 info 会基于当前绑定重新计算 coverage，避免替换 counts 后展示旧比例。

### 4. 先验证、后提交；历史版本保留

```text
workspace/<project>/
  project.json
  input/<role>/<uuid>/
    original/<original_filename>
    analysis/<role>.csv          # 仅 counts/metadata 的适配副本
    import.json                 # 验证摘要、基因索引、来源 hash、转换说明
  logs/import.log
  results/
  reports/
  runtime/
```

原文件复制而非移动，保存后不再依赖源目录。`project.json` 只保存 input/ 下的 POSIX 相对绑定；运行时 Context 才解析绝对路径。

已绑定同角色时，不显式 replace 就返回 `FILE_ROLE_ALREADY_BOUND`。替换先在 runtime 完成复制、验证、样本检查，再发布唯一 UUID 目录，最后原子切换 project.json。
使用 ProjectManager 的写锁和 updated_at 比较，拒绝覆盖并发修改。**新文件验证失败、复制失败或提交前写入失败，旧文件与旧绑定不变。**

采用“旧版本原位保留”，而不是移动到 archive：保留历史日志里的路径有效，作用等同归档。remove 仅解除绑定，保留项目副本和用户原文件。失败提交可能留下未绑定版本；当前不做自动清理。
该版本 `replace_file` 等同 `import_file(..., replace=True)`：未绑定时也允许导入。

文件名、相对路径沿用 ProjectManager 的便携路径策略，拒绝 traversal、保留设备名、符号链接/junction 等。源文件允许来自项目外，但目标始终在当前项目 input 下。
SHA-256 检查项目副本是否被意外修改；import.json 做结构检查。它们不是签名或对抗恶意本机用户的安全沙箱。

### 5. ProjectContext 与 ExecutionContext 不混为一谈

每次成功 import/replace/remove 返回已提交状态的刷新 Context，无全局缓存。重新打开项目可恢复文件绑定与导入记录。

`build_execution_context()` 将四种文件角色映射到**原 Skill 的真实参数名**，例如 count_matrix → DEG 的 count_matrix_path，metadata → metadata_path。所有参数约束、默认值、required 和 oneOf 仍来自现有 Registry/Manifest。
转换后的 metadata group_column=condition 是已知本地信息，注入 confirmed_parameters。treatment/control 必须由调用者明确提供，不从列顺序推测。

桥接结果是 Phase 6.3 已有的 ExecutionContext，可供后续 SingleRequestRunner 使用；本阶段只构造、检查它，不调用 runner。不存在或 hash 不匹配的文件不会被绑定给路由器；readiness 会报告问题。

### 6. Ready 不等于保证统计成功

`get_project_readiness()` 遍历已注册的六个 Skill，读取 Manifest schema 的 required / oneOf、默认值，再结合绑定与显式业务参数。
另外通过小型 preflight 函数映射复用 DEG/PCA 原有纯 Python 检查，不重新实现重复数规则。没有巨大 if/elif 依赖表。

- `ready`：当前输入和业务参数完整，已配置的 preflight 检查通过。
- `data_ready`：文件依赖完整且没有已检测错误；仍可能缺 treatment/control。
- `missing` / `missing_data`：使用真实 Skill 参数名；`universe|analysis_genes` 表示二选一。
- `errors`：已绑定文件损坏或生物学前提不满足等结构化错误。
- 不检查 R 包安装，不进行 VST/离散度拟合，不预留结果目录，不保证执行时磁盘可写。真正运行时 Skill 必须重新 validation。

**只导入四类文件不能让所有 Skill Ready。** Heatmap/Volcano 缺 DEG result；GO/KEGG 缺 gene list 和合法 universe。不能偷跑上游或伪造路径来满足界面上的绿色勾选。

## 如何调用

在项目根启动 Python（不必激活 venv）：

```powershell
.\.venv\Scripts\python.exe
```

随后可使用以下 Python API。source 是未来文件选择器返回的 Path；当前由调用者选择真实文件：

```python
from pathlib import Path
from bulk_rnaseq.workspace import ProjectManager, FileImportService, FileRole

manager = ProjectManager(Path("workspace"))
project = manager.create_project("My_Study")  # 已存在则用 open_project
imports = FileImportService(manager)
source = Path("examples/data/counts.csv")

hint = imports.inspect_file(source)
result = imports.import_file(project.key, source, FileRole.COUNT_MATRIX)
result.model_dump(mode="json")

imports.import_file(project.key, Path("examples/data/metadata.csv"), "metadata")
imports.get_imported_file_info(project.key, "metadata")
imports.get_project_readiness(project.key)  # DEG 不会猜测比较方向

context = imports.build_execution_context(
    project.key,
    confirmed_parameters={"rnaseq.deg": {"treatment": "treatment", "control": "control"}},
)
# 此处没有执行 Skill。分组名称必须与自己 metadata 中实际名称一致。
```

其他操作：`replace_file(key, new_source, role)`、`remove_file(key, role)`、`validate_imported_file(key, role)`、`detect_file_role(source)`。
UI 应展示 returned summary/warnings，不自行重新实现数据检查或 requirements。
项目状态可能是 missing / valid / warning / invalid；同步验证完成才发布绑定，因此无需持久化中间 imported 状态。

## 验证命令

本次新增导入测试 **99 passed**；全量免费回归 **755 passed、54 skipped，20.31s**。
其中 53 项为未开启的真实 R/API 测试，1 项为当前 Windows 创建符号链接权限不足（junction 防护另有测试）。没有删除或放宽原有测试。

```powershell
# 新增导入测试；免费，无 R / DeepSeek
.\.venv\Scripts\python.exe -m pytest tests/test_file_import.py -q

# 全量免费回归，保留原来的显式 API/R opt-in 策略
.\.venv\Scripts\python.exe -m pytest -q

# 已完成的首次真实导入命令
.\.venv\Scripts\python.exe examples/workspace_import_test.py

# 项目已存在时不会覆盖；想复测请明确新项目名称
.\.venv\Scripts\python.exe examples/workspace_import_test.py --project-name Real_DATA_UI_TEST_2
```

## 真实数据验证（2026-09-19）

来源为 `Real_DATA_TEST/input/prepared_asc/` 中**此前真实流程已经准备好**的四个文件：count_matrix.csv、metadata.normalized.csv、gene_to_go.tsv、gene_to_kegg.tsv。
其中转录本/基因 ID 对齐是历史准备工作的产物，不是此次导入自动执行的新转换。源文件 SHA-256 导入前后均一致。

| 检查 | 真实结果 |
|---|---|
| 创建与重开 | workspace/Real_DATA_UI_TEST 成功；4 类绑定全部恢复 |
| Count matrix | 24,581 genes；6 samples；count 0–3,184,888；整数检查通过 |
| Metadata | OT 3、T 3；样本集合与顺序完全一致 |
| GO coverage | 17,069 / 24,581 = 69.44% |
| KEGG coverage | 10,100 / 24,581 = 41.09%，触发低覆盖 warning |
| GO 规模 | 3,439,093 mapping 行；2,078,412 唯一对；1,360,681 重复对按原 reader 去重 |
| KEGG 规模 | 83,294 行；52,309 唯一对；30,985 重复对 |
| 导入用时 | counts 0.351s；metadata 0.046s；约 206MB GO 16.145s；KEGG 0.466s |
| 整体导入与恢复检查 | 19.057s；不代表 RNA-seq 分析耗时 |
| PCA readiness | true |
| DEG readiness | 提供已有真实配置的 treatment=OT/control=T 后 true；不提供时仅 data_ready |
| Heatmap/Volcano | false：缺 deg_result_file |
| GO/KEGG | false：缺 gene_list、universe 或 analysis_genes |
| R / DeepSeek | 均 0 次；项目 results 为空 |

完整机器报告：`workspace/Real_DATA_UI_TEST/reports/file_import_test.json`。
日志：`workspace/Real_DATA_UI_TEST/logs/import.log` 与 `real_import_test.log`。

## 当前限制与停止点

- 无 UI/file uploader；当前是可供下一阶段调用的 Python 后端。
- counts/metadata 仅 UTF-8 CSV/TSV，不接受 Excel、FASTQ、自动 TPM 判定或复杂 design。
- mapping reader 会在内存中保存去重关联；真实 206MB GO 已验证，但未做超大文件内存压力测试。
- gene/sample 索引缓存避免每次 readiness 重建关联表；完整性检查仍要顺序读取文件 hash，不是常数时间；手动 validate 会重新完整解析。
- 旧版本与未绑定版本不自动清理，可能增加磁盘占用；没有历史结果扫描、自动 gene list/background 生成。
- Source 文件名沿用便携命名规则，上限 80 字符；更长名称需由用户另存副本。
- Context 是当时快照。替换或解绑后应重新构造 ExecutionContext，不长期复用旧对象。
- 不承诺“输入 Ready 就一定统计成功”；本次不重新验证 R/DeepSeek 连通性。

验收以真实依赖为准，不以“导入四个文件后所有 Skill 全绿”为准。文件导入、验证、绑定、恢复及数据保护通过；停止在 Phase 6.5.2，不进入 6.5.3。
