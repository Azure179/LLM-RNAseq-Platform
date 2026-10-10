# LLM-driven Bulk RNA-seq Analysis Platform

这是一个按阶段开发的、面向求职展示和工程化学习的 Bulk RNA-seq Skill 项目。最终目标是让 LLM 通过结构化接口选择并调用可靠的生物信息学分析操作；真正的统计计算始终由 DESeq2、clusterProfiler 等实际程序完成。

## 当前状态

```yaml
Current milestone: Phase 7.8 Preview
Status: Active Development / NOT READY for stable release
```

这是 Phase 7.8.1 修复前的公开预览快照，不是稳定发布。

**Known blocker:** Workflow-generated DEG results cannot yet be reliably reused across subsequent chat requests for new Volcano/GO/KEGG analyses.

**Planned fix:** Phase 7.8.1 — Result Reuse & Lineage Hotfix（尚未开始）。

当前已实现 DEG/DESeq2、VST/PCA、Volcano、Heatmap、基于自定义映射的 GO/KEGG；标准化 RNA-seq Skills、Pydantic Schema / Manifest / Registry、DeepSeek Tool Calling、多 Skill Workflow 规划、DAG 校验与串行执行、Streamlit 本地工作区、项目/文件管理、Results Browser、Controlled Customization，以及 pytest / E2E 测试。历史结果复用仍有上述入口缺口；不支持任意动态代码执行。

Phase 7.8 已实际进行真实数据浏览器 E2E；最终免费回归 **1191 passed / 96 skipped**，真实 R 专项 **37 passed**。完整结果与限制见 [Phase 7 E2E Report](PHASE7_E2E_REPORT.md)，结论仍是 **NOT READY**；报告保留修复前的错误排序记录，不能用于科研结论。

### Public preview / 数据与密钥边界

- 本仓库只发布软件、测试、既有小型 synthetic/toy 示例及文字验收报告；不包含真实科研矩阵、注释大表、用户 workspace、运行结果或日志。
- `.env.example` 不含密钥。使用者在本机复制为 `.env` 后填写自己的 DeepSeek key；不得提交 `.env`。
- 普通 `pytest` 不调用付费 API 或真实 R；只有显式 `--run-llm` / `--run-r` 才允许对应集成测试。
- `Real_DATA_TEST/` 保留 runner、相对路径配置和说明，但真实 `input/`、`results/`、`logs/` 不发布。克隆后这些真实数据命令不能直接复现，须使用者准备合法可用的数据；报告中的本地 artifact 链接也不会随仓库提供。
- 下方各 Phase 条目保留当时的阶段记录（包括旧的“未验收/尚无 Workflow”描述），不代表最新状态；当前验收以本节和 Phase 7 E2E 报告为准。

### 历史开发记录

新增 **Phase 7.7：Streamlit Workflow + Customization UI**：同一 Assistant 聊天框由真实 DeepSeek 分流单 Skill、Workflow、已有结果定制；支持真实步骤状态、grounded 工作流总结、歧义来源选择后续接、定制图片直接预览，以及 Results 的 Analysis / Workflow / Customization 分组。刷新不重跑，原结果保留；不开放动态代码。详见 [使用方法与验收报告](docs/phase-7-7-workflow-customization-ui.md)。

新增 **Phase 7.6：Controlled Customization**：为 Volcano/Heatmap 增加通用绘图参数；GO/KEGG 复用已有富集表重绘；基于真实注释对已有显著 DEG 做关键词、方向与排序筛选。真实 DeepSeek 只生成白名单决策，由本地校验后执行固定代码。**不开放任意 Python/R/shell，不修改统计核心，不新增定制聊天 UI**。详细参数、后端调用和验收见 [Phase 7.6](docs/phase-7-6-controlled-customization.md)。

新增 **Phase 7.5：Workflow Final Response**：只读取已执行的 WorkflowResult，经现有 Skill formatter 压缩后由真实 DeepSeek 生成总结；逐步骤事实校验拒绝编造数字、名称及错误状态，部分成功明确说明，全失败本地解释不调用 API。无 Workflow/R 重跑、无新增 UI。详见 [Phase 7.5 设计、调用与真实 API 验收](docs/phase-7-5-workflow-final-response.md)。

新增 **Phase 7.4：Workflow Executor**：已校验计划 → 串行 Registry 执行 → 真实输出绑定 → WorkflowResult。支持 DEG 子集、独立分支继续、失败下游跳过、指定历史 run 复用、检查点与校验和追踪；不调用 Planner，不新增 Workflow UI。详见 [Phase 7.4 调用方式、真实 R 测试与限制](docs/phase-7-4-workflow-executor.md)。

新增 **Phase 7.3：Plan Validator + Dependency Resolver**：统一 PlanValidator 复用原依赖图检查，重查真实 ProjectContext 的文件可用性，成功时返回独立的 validated_plan；Planner 已使用同一入口。补齐同组对比、整数类型及损坏上下文的拒绝机制。**只 Validate + Resolve，不执行、不补分析、不调用 API/R**。详见 [Phase 7.3 设计、错误码与测试](docs/phase-7-3-plan-validator.md)。

新增 **Phase 7.2：DeepSeek Workflow Planner**：真实自然语言 → DeepSeek JSON → 原 WorkflowPlan/Pydantic → 原依赖图校验。真实 Planner 测试 8/8 通过，支持 needs_input / unsupported；不执行任何 Skill、不自动修复计划，也没有新增 UI 工作流入口。方向不明的“比较 OT 和 T”需要明确 comparison 上下文。详见 [Phase 7.2 设计、真实测试与调用方式](docs/phase-7-2-workflow-planner.md)。

新增 **Phase 7.1：Workflow Model + Dependency Graph**：统一 Step/Plan/Result 模型，从现有 Manifest 生成数据依赖图，校验输入引用、历史 run 接口、DEG 子集语义、拓扑顺序和循环。**本阶段不调用 DeepSeek/R、不执行 Skill，也未新增 UI 多步骤功能**。详见 [Phase 7.1 设计、示例与验收](docs/phase-7-1-workflow-model.md)。下述 Phase 6.5 浏览器验收记录仍保留，不以后台单元测试代替真实 UI 验收。

Metadata 默认可直接填写：导入 count matrix 后，Files 自动列出样本名，用户填写组名并保存，系统生成并绑定标准 Metadata。首次不猜分组，已有分组可恢复；原文件上传为可选方式。详见 [样本分组表单](docs/sample-group-form.md)。

Count/Metadata 导入已支持内容识别分隔符和已知表头别名适配：如 `Geneid` + Tab、文本 `.xls` 或后缀写成 `.csv` 的 Tab 表，自动生成标准 CSV 分析副本，原文件与数据值保持不变。详见 [输入格式自动适配](docs/input-format-adaptation.md)。

Files 页新增 **eggNOG mapper（推荐）**：一次导入原始 annotations，生成并绑定 GO/KEGG 标准 TSV；原独立 mapping 上传仍保留。已有绑定需明确确认替换，两项同时验证后原子更新。Gene ID 不自动改名，coverage 如实报告。详见 [eggNOG 导入说明](docs/eggnog-import.md)。

当前已实现 **Phase 6.5.5：Results Browser**：按项目、Skill 和 run 浏览真实结果，筛选状态，查看摘要、参数、图片、有限行数表格及 JSON，并下载原文件；Chat 与 Results 使用真实 run ID 关联。
**浏览器手工验收尚未完成**：computer-use 因无法可靠识别 Windows 浏览器 URL 终止操作；不把 AppTest 结果当成浏览器实测。
六类已有真实结果已通过 Streamlit AppTest 预览、原文件下载校验和聊天定位检查；本阶段没有调用 API/R，也没有重新计算统计结果。
本阶段全量免费回归：**829 passed、60 skipped**；随后补充十万行表格测试，Results 专项 **26 passed**；依赖检查通过。普通 pytest 不调用 API/R。
Results 使用说明、真实结果及当时的验收边界见 [Phase 6.5.5](docs/phase-6-5-5-results-browser.md)。

Windows 日常使用：在项目文件夹双击 **`Start_RNAseq.cmd`**。本地启动窗口会启动服务，确认就绪后自动打开浏览器；分析期间保持启动窗口打开，结束后点击“退出本地程序”。不需要激活虚拟环境或修改 PowerShell 执行策略。

`127.0.0.1` 指当前电脑；程序停止时，该地址无法打开。它不是长期在线的公网网站。分析与文件保存在本机，DeepSeek 仍需要联网。当前双击入口使用浏览器显示现有界面，并非打包好的独立桌面聊天窗口。

启动日志位于 `logs/local_app/`。8501 已占用时会在 8502–8510 选择空闲端口，以启动窗口显示的网址为准。

开发者也可继续从命令行启动：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

打开 `http://127.0.0.1:8501`，从左侧创建或选择项目，再到 Files 页选择文件；无需在命令行填写数据路径。
在 Assistant 输入“帮我画一下样本 PCA”或“以 T 为对照比较 OT，画火山图，并对上调基因做 KEGG”。已有图可输入“把刚才火山图上调改红色，下调蓝色，标 Top20”。请使用自己 metadata 的真实组名，并明确比较方向。多个可能的来源会要求选择；发送会调用收费的 DeepSeek API，并可能串行运行多个本地 Skill。
查看已有结果不会调用 DeepSeek：选择项目后进入 **Results**，选择 Skill → Analysis run → 文件类别 → Result file。可从 Assistant 的 **View Results** 直接定位对应分析。**Download** 下载原始文件；表格预览默认 100 行、最多 1000 行，不代表完整表已载入。
详见 [Phase 6.5.4：聊天用法、工程设计、真实测试与限制](docs/phase-6-5-4-chat.md)。基础上传界面见 [Phase 6.5.3](docs/phase-6-5-3-streamlit-ui.md)。

Phase 6.5.2：File Import & Data Binding 已完成。
新增 FileImportService：counts/metadata/GO/KEGG 导入、验证、双向样本匹配、coverage、可恢复版本替换、Context 桥接与 Manifest-driven readiness。
真实导入项目 `workspace/Real_DATA_UI_TEST` 已完成：24,581 genes、6 samples，GO coverage 69.44%、KEGG 41.09%；没有运行 R 或 DeepSeek。
本阶段新增测试 **99 passed**；全量免费回归 **755 passed、54 skipped**（显式 R/API 测试未开启，另有 1 项 Windows 符号链接权限跳过）。
仅四类输入不等于所有 Skill Ready：Heatmap/Volcano 仍需 DEG result，GO/KEGG 仍需 gene list 与背景。没有虚构上游结果。
详见 [Phase 6.5.2：导入 API、验证、文件版本、测试和限制](docs/phase-6-5-2-file-import.md)。

Phase 6.5.1 的 Project Manager 继续负责创建/打开/列出/重命名/可恢复删除项目、保存资料及项目路径上下文。
已有空的 `workspace/Demo_Project` 示例仍保留，用户数据目录被 Git 忽略；也可将 workspace 放在软件仓库外。
详见 [Phase 6.5.1：目录、API、相对路径、测试与限制](docs/phase-6-5-1-project-manager.md)。

Phase 6.4：SkillResult → DeepSeek Final Response 已完成。
真实 DeepSeek → 单 Tool → 本地校验 → Registry → 实际 SkillResult → 第二次 DeepSeek → grounding 校验已打通。
Phase 6.4 当时的普通 pytest **572 passed、53 skipped**；该阶段 6 个真实 API 测试通过（7 次请求，包含真实数据 DESeq2 完整闭环）。
总结只发送精简 summary，采用受限事实陈述；warnings 本地原样附加，不传大表、不生成生物学机制推断。
没有 Workflow 或自动上游计算；路径和执行配置仍由本地系统管理。
详见 [Phase 6.4：新增模块、grounding 边界、真实回复和成本](docs/phase-6-4-final-response.md)。

Phase 6.3 的原有单 Tool 命令保持兼容。
详见 [Phase 6.3：执行上下文、操作命令、真实结果与成本](docs/phase-6-3-single-tool.md)。

Phase 6.2 已完成六个 Tool 自动生成和独立 API Schema Acceptance Test。
详见 [Phase 6.2：Tool Adapter、参数边界与真实验收](docs/phase-6-2-tool-adapter.md)。

Phase 6.1 的 20/20 真实意图用例已验收通过，只提取 Intent，不执行 Skill。
详见 [Phase 6.1：命令、Schema、失败修复与真实 Token 记录](docs/phase-6-1-structured-intent.md)。

```powershell
# 显式单次付费请求；API Key 仍从本地 .env 获取
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.request_intent "以 T 作为 control，分析 OT 相对于 T 的变化。"

# 免费测试
.\.venv\Scripts\python.exe -m pytest -q

# 显式单次付费请求 + 一个真实 Skill；使用已准备的真实输入，新建 UUID 输出目录
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_tool "以 T 为对照比较 OT 的差异表达。" --context Real_DATA_TEST/config/tool_execution_context.json

# Phase 6.4：最多两次付费请求 + 一个真实 Skill + grounded 最终总结
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_complete "以 T 为对照比较 OT 的差异表达。" --context Real_DATA_TEST/config/tool_execution_context.json --output-root Real_DATA_TEST/results/phase_6_4

# 显式付费：只跑 20 个 Intent 测试，不执行 RNA-seq 分析
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_intent_live.py --run-llm -q --tb=short

# 显式付费：Phase 6.2 仅一次 tools schema 接受测试
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_tool_schema_live.py --run-llm -q --tb=short
```

配置入口是项目根目录 `.env`（已忽略），共享模板为 `.env.example`。
Phase 6.0 客户端的普通 chat 行为保持不变，Phase 6.1 通过可选 JSON mode 接入意图解析。
普通 `pytest` 免费且阻断网络；仅显式 `--run-llm` 测试或手动连接/Intent/Tool 命令允许 API 请求。
详见 [DeepSeek 配置、连接命令、错误处理与验收状态](docs/phase-6-0-deepseek-client.md)。

新增小型 Registry：注册/查询/列出 Skill，读取 Manifest 与 input schema，并通过统一 execute 返回 SkillResult。
默认绑定六个现有函数；GSEA 仅可查询描述，不可执行。复用 jsonschema 做运行时结构校验，因此将它提升为运行时依赖。
Phase 5.3 没有修改统计逻辑或接入 LLM/API。详见 [Registry 使用、调用链与验收说明](docs/phase-5-3-registry.md)。

Phase 5.2 新增七份静态 JSON 能力描述和共享 JSON Schema：六个现有 Skill 为 implemented，GSEA 明确标记未实现、不可调用。
描述包含调用/禁用情境、真实参数默认值、输入语义、运行依赖和上下游产物。
详见 [Manifest 格式、限制与验收说明](docs/phase-5-2-manifests.md)。

六个现有 Skill 共用标准库 `SkillResult`：统一 inputs/outputs/summary/warnings/metadata 和错误字段，
Python 返回、CLI JSON 与新生成的 summary 内容一致。保留 `succeeded` 状态、原路径属性和 metrics/artifacts/error 等兼容字段。
Phase 5.1 未改变统计逻辑，未新增依赖、GSEA、LLM 或 API；历史结果文件不改写。
详见 [结果契约、兼容边界与验证方法](docs/skill-result-schema.md)。

Phase 4.5 增加离线 KEGG 富集，兼容 eggNOG 文本 / Excel 和自定义 gene-to-pathway 映射，显式记录背景。
识别 KEGG_Pathway 而非 KEGG_ko；ko/map 参考编号去重，gene ID 原样匹配，缺少名称时显示 ID。
详见 [KEGG 输入、映射规则与验收说明](docs/phase-4-5-kegg.md)。
使用真实 R `stats::fisher.test` + BH；前景需配套 `--universe` 或 `--analysis-genes`，不将整个注释库作为默认背景。
详见 [GO 输入、背景、运行与验收说明](docs/phase-4-4-go.md)。
详见 [Heatmap 使用与设计说明](docs/phase-4-3-heatmap.md)、[Volcano 说明](docs/phase-4-2-volcano.md)、[PCA 说明](docs/phase-4-1-pca.md)、[输入输出契约](docs/contracts.md)
和 [Phase 3 验收说明](docs/phase-3-verification.md)。

已经具备：

- Python `src` package 结构
- TOML 基础配置读取
- 命令行启动入口
- 最小控制台日志
- smoke tests
- Python `run_deg()` 编排函数
- 固定 R 脚本调用和进程结果检查
- 使用 DESeq2 的两组差异表达分析
- 完整 `deg_results.csv`、筛选 `deg_filtered.csv` 和 `summary.json` 输出
- 小型演示 count matrix 和 metadata
- PCA 坐标、解释方差、PNG 和统一 summary.json
- Volcano 分类数据、PNG 和统一 summary.json
- Heatmap、行 Z-score 矩阵、选定基因、可复用 VST 矩阵和统一 summary.json
- GO 富集表、点图、条形图、实际背景/映射、统一 summary.json
- KEGG 富集表、点图、条形图、实际背景/映射、pathway ID 转换记录与统一 summary.json

当前没有实现 GSEA、多 Tool Workflow、最终 LLM 总结、Web API 或 Docker。GO/KEGG 共享基础 R ORA 模块；LLM 当前支持客户端、独立 Intent、Tool Schema 和单 Tool 执行。

## 环境要求

- Python 3.13.x
- R 及 `Rscript`
- Bioconductor `DESeq2`（DEG/PCA/raw-count Heatmap 使用；Volcano 和已有 VST 的 Heatmap 只需要基础 R）
- Git
- `openpyxl`（读取 GO 注释 XLSX；随 `pip install -e .` 安装）；GO 只需基础 R，不需 clusterProfiler。

确认 Python 版本：

```powershell
python --version
```

输出应为 `Python 3.13.x`。如果 Windows 上使用 Python Launcher，也可以运行：

```powershell
py -3.13 --version
```

确认 R 和 DESeq2：

```powershell
Rscript --version
Rscript -e "library(DESeq2); packageVersion('DESeq2')"
```

如果尚未安装 DESeq2，请先在 R 中执行：

```r
if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}
BiocManager::install("DESeq2")
```

## 创建开发环境

在项目根目录执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

如果 PowerShell 执行策略禁止激活脚本，可以不激活，直接把以下命令中的
`python` 替换为 `.\.venv\Scripts\python.exe`。

Linux/macOS 对应命令：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

## 启动项目

安装 editable package 后，可以选择任一入口：

```powershell
python main.py
python -m bulk_rnaseq --config config/default.toml
bulk-rnaseq --config config/default.toml
```

成功时会输出启动日志和 `deg`、`pca`、`volcano`、`heatmap` 子命令提示。

查看版本：

```powershell
bulk-rnaseq --version
```

## 运行最小 DEG Demo

输入格式：

- count matrix 是 CSV，第一列必须是 `gene_id`，其余列是样本 ID。
- count 必须是 raw、非负整数，不能使用 TPM/FPKM。
- metadata 是 CSV，必须包含 `sample_id` 和 `condition`。
- MVP 只允许 control 和 treatment 两个 condition，每组至少两个生物学重复。

运行自带的 30 基因、6 样本 demo：

```powershell
python main.py deg `
  --counts examples/data/counts.csv `
  --metadata examples/data/metadata.csv `
  --treatment treatment `
  --control control `
  --output-dir outputs/my_phase3_run
```

如果 `Rscript` 没有加入 PATH，可以显式传入：

```powershell
python main.py deg `
  --counts examples/data/counts.csv `
  --metadata examples/data/metadata.csv `
  --treatment treatment `
  --control control `
  --output-dir outputs/my_phase3_run `
  --rscript "C:\Program Files\R\R-x.y.z\bin\Rscript.exe"
```

也可以直接调用 Python API：

```python
from pathlib import Path

from bulk_rnaseq.skills import run_deg


result = run_deg(
    count_matrix_path=Path("examples/data/counts.csv"),
    metadata_path=Path("examples/data/metadata.csv"),
    treatment="treatment",
    control="control",
    output_dir=Path("outputs/my_phase3_run"),
)
print(result.to_dict())
```

成功后生成：

```text
outputs/my_phase3_run/
├── deg_results.csv
├── deg_filtered.csv
├── summary.json
├── run.log
├── r_stdout.log
└── r_stderr.log
```

`deg_results.csv` 包含 DESeq2 的完整结果列：`gene_id`、`baseMean`、
`log2FoldChange`、`lfcSE`、`stat`、`pvalue`、`padj`。正的
`log2FoldChange` 表示 treatment 相对 control 上调。

`summary.json` 包含运行状态、输入与参数、样本和基因数量、显著基因统计、
R/DESeq2 版本、输入/脚本/产物 SHA-256，以及捕获的 R stdout/stderr。

`deg_filtered.csv` 使用 `padj < 0.05` 且 `abs(log2FoldChange) >= 1`（默认值），
没有 DEG 时保留表头，仍可成功。可以使用 `--alpha` 和 `--lfc-threshold` 更改阈值。
这是对 Wald 检验结果的后置筛选，不是以 log2FC=1 为原假设边界的统计检验。

输出目录必须不存在或为空，每次重跑请换一个目录。已有 Phase 2 输出不会被覆盖。
`summary.json` 最后写入，只有它的 `status=succeeded` 才代表本次完成。
失败时 CLI 返回非零退出码、stdout 返回结构化 JSON，日志写入 stderr/文件。
如果已成功占用输出目录，则额外保存 `failure.json`；输出目录不可用时只能返回 CLI/Python 错误。
R 默认最长运行 600 秒，可用 `--timeout` 调整。

## 运行 PCA

PCA 使用 raw count CSV 和同样的 metadata，分组列由 `--group-column` 指定。
至少 3 个样本，允许一个或多个分组；分组仅用于着色，不进入 blind VST 或 PCA 计算。

```powershell
.\.venv\Scripts\python.exe main.py pca `
  --counts examples/data/counts.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --output-dir outputs/my_pca_01
```

每次选择新的输出目录。输出 pca_coordinates.csv、pca_variance.json、pca_plot.png、
summary.json 和日志。真实运行示例保存位置为 `outputs/pca_phase41_demo_v2`。
VST 之后对所有变异基因做中心化 PCA；不将 raw counts 直接输入 PCA。

## 运行 Volcano

使用已有完整 DEG CSV，而不是筛选后的 DEG 子集；不重新执行 DESeq2。

```powershell
.\.venv\Scripts\python.exe main.py volcano `
  --deg-result-file outputs/phase3_verified_20260910/deg_results.csv `
  --output-dir outputs/my_volcano_01 `
  --padj-threshold 0.05 `
  --log2fc-threshold 1 `
  --top-n-labels 10
```

示例输入是本项目已生成的真实 DEG 结果；可替换为你的完整 DEG CSV。
若 Rscript 不在 PATH，追加 `--rscript` 与实际可执行文件路径。
输出 volcano_plot.png、volcano_plot_data.csv、summary.json 和日志；每次使用新/空目录。
padj=NA 的行保留但不画点；padj=0 仅调整显示高度，原值不变。
已验收示例在 `outputs/volcano_phase42_demo`，详细接口与限制见 [Volcano 说明](docs/phase-4-2-volcano.md)。

仅运行 Volcano 真实渲染测试，不调用 DESeq2：

```powershell
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests/integration/test_real_volcano.py --run-r
```

## 运行 Heatmap

使用已有 DEG 结果和 raw counts，经公共 VST 后绘制，不重算 DEG：

```powershell
.\.venv\Scripts\python.exe main.py heatmap `
  --counts examples/data/counts.csv `
  --deg-result-file outputs/phase3_verified_20260910/deg_results.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --output-dir outputs/my_heatmap_01 `
  --top-n 50
```

也可以将 `--counts ...` 替换为 `--normalized-expression ...`，输入已有 VST CSV，不再归一化。
两者不能同时提供。示例可复用 `outputs/heatmap_phase43_demo/normalized_expression.csv`。
缺少 Rscript 时追加 `--rscript` 实际路径；每次选择新/空输出目录。

输出 heatmap.png、heatmap_matrix.csv（实际行 Z-score）、selected_genes.csv、summary.json，
并额外保存 normalized_expression.csv（完整 VST，不是 Z-score）。
默认选 padj < 0.05、abs(log2FC) >= 1 的 Top DEGs；不足 50 时使用实际数量并提示。
第一版不聚类。请勿把 raw counts/TPM/FPKM 当作 VST 输入。

## 运行测试

默认测试不运行 R；Heatmap 的完整验证方式见 [Phase 4.3 说明](docs/phase-4-3-heatmap.md)。

```powershell
python -m pytest
```

默认测试不需要 R，真实集成测试会明确跳过。故障注入测试使用明确标识的测试夹具，
不是生物学分析结果。真实 R 回归测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests --run-r
```

如果 Rscript 尚未出现在当前终端的 PATH，追加 `--rscript` 和实际 Rscript.exe 路径。
显式指定 `-c pyproject.toml tests` 可保证 pytest 在解析自定义选项前加载本项目配置。
选择 `--run-r` 后，缺少该测试所需依赖属于失败，不会被误当作通过；DEG/PCA/raw-count Heatmap 需要 DESeq2。

无需激活 PowerShell 虚拟环境：所有 `python` 命令均可替换为 `.\.venv\Scripts\python.exe`。

## 当前目录

```text
.
├── config/
│   └── default.toml
├── docs/
│   ├── phase-0-design.md
│   ├── contracts.md
│   ├── phase-3-verification.md
│   ├── phase-4-1-pca.md
│   ├── phase-4-2-volcano.md
│   ├── phase-4-3-heatmap.md
│   ├── phase-4-4-go.md
│   └── phase-4-5-kegg.md
├── examples/
│   ├── data/
│   │   ├── counts.csv
│   │   └── metadata.csv
│   ├── go/
│   │   ├── synthetic_gene_list.txt
│   │   ├── synthetic_universe.txt
│   │   └── synthetic_mapping.tsv
│   └── kegg/
│       ├── synthetic_gene_list.txt
│       ├── synthetic_universe.txt
│       └── synthetic_mapping.tsv
├── src/
│   └── bulk_rnaseq/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── exceptions.py
│       ├── models.py
│       ├── validation.py
│       ├── artifacts.py
│       ├── pca_artifacts.py
│       ├── plot_artifacts.py
│       ├── volcano_data.py
│       ├── deg_data.py
│       ├── heatmap_data.py
│       ├── go_mapping.py
│       ├── go_data.py
│       ├── go_artifacts.py
│       ├── kegg_mapping.py
│       ├── enrichment_inputs.py
│       ├── enrichment_artifacts.py
│       ├── run_io.py
│       ├── logging_config.py
│       ├── logger.py
│       ├── r_runner.py
│       ├── r_scripts/
│       │   ├── deseq2_deg.R
│       │   ├── transform_pca.R
│       │   ├── plot_volcano.R
│       │   ├── vst_common.R
│       │   ├── normalize_vst.R
│       │   ├── plot_heatmap.R
│       │   ├── go_enrichment.R
│       │   ├── kegg_enrichment.R
│       │   └── enrichment_common.R
│       └── skills/
│           ├── deg.py
│           ├── pca.py
│           ├── volcano.py
│           ├── heatmap.py
│           ├── go_enrichment.py
│           └── kegg_enrichment.py
├── tests/
│   ├── conftest.py
│   ├── test_validation.py
│   ├── test_execution.py
│   ├── test_artifacts.py
│   ├── test_pca.py
│   ├── test_volcano.py
│   ├── test_heatmap.py
│   ├── test_go.py
│   ├── test_kegg.py
│   ├── plot_helpers.py
│   ├── test_logger.py
│   ├── integration/
│   │   ├── test_real_deg.py
│   │   ├── test_real_pca.py
│   │   ├── test_real_volcano.py
│   │   ├── test_real_heatmap.py
│   │   ├── test_real_go.py
│   │   └── test_real_kegg.py
│   ├── test_deg.py
│   └── test_startup.py
├── .gitignore
├── .python-version
├── LICENSE
├── main.py
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt
```

完整阶段设计见 [`docs/phase-0-design.md`](docs/phase-0-design.md)。

## 下一阶段

当前停止在 Phase 7.7。新增功能通过 Streamlit AppTest，包括真实 API/R 链路；鼠标操作的浏览器手工验收仍是独立事项，不会自动进入下一阶段。
