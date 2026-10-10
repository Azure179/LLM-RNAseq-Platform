# Phase 7.6：Controlled Customization

日期：2026-10-08。范围：后端参数化、结果复用、注释过滤及真实 DeepSeek 定制分类。不增加 Streamlit 定制界面，不执行模型生成的代码，不进入下一阶段。

## 设计与检查结论

原 Volcano 已经是独立绘图 Skill，可直接复用 full DEG CSV；Heatmap 已导出 normalized_expression.csv，可复用已验证的 VST。GO/KEGG 原绘图在统计脚本内，修改 Top N 若重跑整个 Skill 会重复统计，因此增加一个只消费 enrichment CSV 的固定绘图器，而不是调用 enrichment Skill。

当前 Skill 参数事实来源是 **Manifest input_schema + Python 函数签名**（既有一致性测试），不是另造一套 Skill Pydantic model。Volcano/Heatmap 新参数进入原 Manifest，ToolAdapter 和 CustomizationPlanner 从中投影白名单。两项独立定制操作的选项分别由 EnrichmentPlotOptions、AnnotationFilterOptions Pydantic 模型唯一管理。

```text
宿主明确选择 Project、source_run_id、目标 Skill、项目内注释文件
  → 用户自然语言 + 可用参数/注释类别 → 一次真实 DeepSeek JSON 请求
  → CustomizationDecision / 本地白名单验证
  → CustomizationEngine 再验证与源文件 checksum 检查
      parameter_override → 原 Volcano/Heatmap Registry 执行，或固定富集重绘器
      annotation_filter  → 已有显著 DEG + 真实注释 → 本地筛选
      unsupported        → 记录原因，不执行
  → 独立 artifacts + SkillResult + customization.json
```

路径、source_run_id、输出目录、Rscript 均由宿主提供/生成，不由 DeepSeek 决定。Engine 不重新调用 Planner；用户可先查看 plan.decision，再显式调用 execute。当前没有自动选择“最近的某张图”。

## 修改/新增文件

路径均相对于仓库根目录。

| 文件 | 用途 |
| --- | --- |
| `src/bulk_rnaseq/skills/volcano.py` | 新标签、颜色、标题和尺寸参数；原统计值不变 |
| `src/bulk_rnaseq/skills/heatmap.py`、`heatmap_data.py` | 基因限制、排序、标签和尺寸；保持原显著性筛选与 row Z-score |
| `src/bulk_rnaseq/plot_options.py` | 共用显示参数校验 |
| `src/bulk_rnaseq/r_scripts/plot_volcano.R`、`plot_heatmap.R` | 固定渲染代码读取新增参数 |
| `src/bulk_rnaseq/manifests/rnaseq.volcano.json`、`rnaseq.heatmap.json` | 参数 schema、默认值、限制；兼容性扩展版本 1.1.0 |
| `src/bulk_rnaseq/llm/tool_adapter.py` | 从原 schema 投影新增安全业务参数，路径仍隐藏 |
| `src/bulk_rnaseq/customization/models.py` | 严格的三模式决策、过滤和富集绘图选项 |
| `src/bulk_rnaseq/customization/catalog.py` | 白名单生成与二次校验，不复制 Skill 参数默认值 |
| `src/bulk_rnaseq/customization/planner.py` | 真实 DeepSeek 分类、JSON/Pydantic、非法参数拒绝 |
| `src/bulk_rnaseq/customization/engine.py` | 项目/历史结果绑定、快照、执行、输出验证、provenance |
| `src/bulk_rnaseq/customization/filtering.py` | eggNOG/自定义注释读取、字面匹配、方向筛选与确定性排序 |
| `src/bulk_rnaseq/customization/enrichment_plot.py` | 验证与截取既有显著富集表；校验 R 输出 |
| `src/bulk_rnaseq/r_scripts/replot_enrichment.R` | 只绘 dotplot/barplot，无检验、p.adjust 或安装操作 |
| `src/bulk_rnaseq/customization/__init__.py` | 新包入口说明 |
| `tests/test_customization.py` | 免费 mock、过滤、绑定、安全边界和失败回归 |
| `tests/integration/test_real_customization.py` | 显式真实 R 渲染，禁止重跑统计 |
| `tests/llm_integration/test_customization_live.py` | 显式真实 DeepSeek Planner 用例与 token 记录 |
| `tests/llm/test_tool_adapter.py` | 更新扩展后的精确默认值契约 |
| `README.md`、本文 | 状态、使用方式、验收和限制 |

没有更改 DEG、PCA、GO/KEGG 的统计核心，没有新增依赖；原 toy data、Real_DATA_TEST 和原单元测试数据没有被替换。

## 可用参数与语义

| 操作 | 白名单 |
| --- | --- |
| Volcano | 原 `padj_threshold`、`log2fc_threshold`、`top_n_labels`；新增 `label_genes`、`color_up`、`color_down`、`color_ns`、`title`、`figure_width`、`figure_height` |
| Heatmap | 原 `top_n`、`padj_threshold`、`log2fc_threshold`；新增 `gene_list`、`ranking_method`、`show_gene_names`、`title`、`figure_width`、`figure_height` |
| GO/KEGG 重绘 | `top_n`（20）、`order_by`（padj）、`title`、`figure_width`（1400）、`figure_height`（1000）、`show_count`（true） |
| DEG 注释过滤 | `gene_subset`（all/up/down）、`keywords`、`annotation_sources`、`match_mode`（any/all）、`order_by`、`descending` |

- Volcano 原配色/尺寸默认值不变；Top N 与显式标签取并集。`label_genes` 必须是用户明确输入的精确 ID；最多 200 个，不存在/不可绘制时发 warning，不把 symbol 猜成 gene ID。
- 颜色支持 CSS 名称或 `#RRGGBB`。标题是最多 160 字符的普通文本；尺寸为 600–3000 的整数像素，不支持自行猜测英寸转换。
- Heatmap 原 `top_n=50`、`ranking_method=padj`；padj 升序、绝对 log2FC 降序、gene_id 升序。`abs_log2fc` 改为绝对效应优先，padj 次之。
- Heatmap `gene_list` 限制显著且有表达变异的基因后再取 Top N，不会强行画不显著基因。`None` 不限制，空列表会导致无可绘制基因错误。固定 row Z-score、原样本顺序；没有新增 clustering/scale 参数。
- Heatmap 默认宽度 1500，height=None 保留原自动高度（1000–3000），默认显示 gene names。
- GO/KEGG 只在**已有显著富集表**内排序、截取，不能通过改 Top N 显示统计表中不存在的条目。padj 升序；overlap_count/fold_enrichment 降序；ID 升序作稳定并列次序。
- 修改 Volcano/Heatmap 阈值会改变当前可视化选择/分类，但不会重新拟合模型或计算 p 值，也不会改写源 DEG。由 DEG 新建 Volcano 时，未覆盖阈值沿用源 DEG 的 alpha/lfc_threshold。
- 除显式 override 外，重绘已有 Volcano/Heatmap 沿用该源 run 的可用参数，而非将所有参数重置为默认值。

所有未知参数、output_dir、project_root、API key、可执行路径、shell 等均不能由 Planner 自由填写。

## Annotation Filter

输入为成功 DEG run 的 `filtered_csv`，即该源分析已经定义的**显著 DEG**，不是从全基因表偷偷重定义显著性。方向使用真实 log2FoldChange：up > 0、down < 0、all 保留全部。

支持 CSV、TSV、eggNOG annotations 文本和单工作表 XLSX，复用现有表格读取器：

1. eggNOG：`#query`/`query`、`Description`、`GOs`、`KEGG_Pathway`，跳过注释行。
2. 自定义宽表：`gene_id`，加 `description`/`gene_description`、`go_id`/`go_name`、`pathway_id`/`pathway_name`。
3. 长表：`gene_id,source,annotation`，source 为 `gene_description`/`GO`/`KEGG`。

关键词为不区分大小写的**字面子串**，不是正则、同源推断或 ontology expansion。any 表示任一关键词命中；all 表示同一基因在选定来源中的证据合计包含所有关键词。排序支持 padj/log2fc/abs_log2fc/gene_id，默认 padj 升序；并列保留 gene_id 升序。

输出：

- `filtered_deg.csv`：保留源表全部列及原始数值。
- `matched_annotations.csv`：逐条 gene_id/source/annotation/matched_keywords，便于人工审查。
- `skill_result.json`：匹配基因数、源 DEG 数、有效注释覆盖数、证据条数、warnings、artifacts 与 provenance。
- 外层 `customization.json`：完整定制决策和来源链。

没有命中关键词是合法空结果；无 ID 交集是 `ANNOTATION_ID_MISMATCH`。低注释覆盖给 warning，不猜基因名、不自动删除 `rna-`/`gene-`/版本号。已有项目若使用过明确 ID 对照，须由宿主提供已经对齐且保留真实文本的注释文件。

**重要限制：**eggNOG 的 GOs/KEGG_Pathway 常只有 ID，单有 ID 不能匹配“脂代谢”等英文名称。需要真实 Description 或真实 GO/pathway 名称注释；程序不会联网补名字。注释行存在但所选来源为空，不计作有效覆盖。关键词筛选结果是探索性文字匹配，不能当作功能验证。

## Planner 与执行边界

只发送：用户定制文本、宿主指定的 target_skill、现有 schema/manifest 用途、可用注释类别。不会上传大表、文件路径、源结果数值、密钥或 Rscript 配置。

Prompt 优先 parameter_override，其次 annotation_filter，否则 unsupported。要求不部分执行无法安全完成的复合请求，不扩展到未要求的生物学主题，不生成 gene list。标签/指定基因列表只能来自用户原话，本地还会检查这一点。

Planner 使用真实 DeepSeek JSON mode，结果经过 Pydantic、JSON Schema、白名单与项目可用注释类别检查。Engine 再校验一次，防止调用方修改参数后绕过校验。计划与执行分开；**关键词翻译的语义准确性仍需使用者审查，schema 不能证明生物学翻译正确。**

Volcano/Heatmap 继续走既有 Registry。`rnaseq.filter_by_annotation` 和 `rnaseq.enrichment_plot` 是定制子系统生成的 SkillResult 操作名，不是新注册到普通六类 Tool/Workflow 的分析 Skill；不会因此改变 Workflow dependency graph。这两个操作只有固定类型入口，无通用脚本执行口。

当前 unsupported：复杂 annotation panel、任意 clustering/scale、新统计方法、修改脚本、执行任意 Python/R/shell、安装软件、缺少可用注释却要求功能筛选。没有虚假的 sandbox；未来隔离容器功能未实现。

## 来源保护与错误

每次定制创建 `workspace/<project>/results/customizations/<customization_id>/`：

```text
inputs/                  源表与注释快照
skill/                   新结果、日志、skill_result.json
customization.json       request/model/usage/source/参数/绑定/checksum/状态
```

只读取明确指定的当前项目历史 run。历史 artifact 必须存在且匹配已有 checksum；Heatmap 的 VST artifact、原 DEG 和 metadata 都必须匹配原 provenance。路径逃逸、链接、历史输入被替换时拒绝执行，不自动切换到更新结果。不会覆盖源结果；即使 customization ID 碰撞也拒绝写入现有目录。

记录 source_run_id/source_skill、changed_parameters、annotation_keywords/sources、输入路径及 SHA-256、输出及 SHA-256、model/usage、run_id。失败保留已经解析的 input_bindings；unsupported 也保存拒绝记录。R 使用既有 subprocess 包装器，检查 exit code、stdout、stderr；重绘额外核对 R_VERSION、N_DISPLAYED、PNG 与绘图 CSV checksum。

常见错误码：`INVALID_CUSTOMIZATION_PARAMETERS`、`CUSTOMIZATION_RESPONSE_INVALID`、`CUSTOMIZATION_PATH_REJECTED`、`CUSTOMIZATION_SOURCE_MISMATCH`、`UPSTREAM_PROVENANCE_MISSING`、`UPSTREAM_ARTIFACT_CHANGED`、`ANNOTATION_FORMAT_ERROR`、`ANNOTATION_ID_MISMATCH`、`ENRICHMENT_FORMAT_ERROR`、`CUSTOM_OUTPUT_VALIDATION_FAILED`，及既有 R 超时/失败码。

## 后端如何调用

当前没有新增 CLI 或聊天 UI 分支。宿主代码从项目根目录运行；先在 Results 选择真实源 run ID，不使用示例 ID 执行：

```python
from pathlib import Path
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.customization.planner import CustomizationPlanner
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.logger import create_logger
from bulk_rnaseq.workspace import ProjectManager

logger = create_logger("customization_demo")
registry = create_default_registry()
manager = ProjectManager(Path("workspace"))
# 填已有项目的真实 key 和 Results 中明确选中的 DEG/Volcano Skill run_id。
engine = CustomizationEngine(manager, "YOUR_PROJECT_KEY", registry)
planner = CustomizationPlanner(DeepSeekClient(load_deepseek_config(Path(".env"))), registry)

try:
    plan = planner.plan("上调红色，下调蓝色，标注Top20。", target_skill="rnaseq.volcano")
    logger.info("Validated decision: %s", plan.decision.model_dump())
    # 宿主在确认计划后才执行；不自己伪造替代 DeepSeek 返回的 decision。
    report = engine.execute(plan, source_run_id="YOUR_EXISTING_SKILL_RUN_ID")
    logger.info("Customization status=%s outputs=%s error=%s", report["status"], report["outputs"], report.get("error_code"))
except DeepSeekError as error:
    logger.error("%s: %s", error.code, str(error))
```

注释过滤则由宿主指定项目内真实注释文件（例如 `Path("input/annotations.tsv")`）；先调用 `engine.available_annotations(files)`，把返回类别传给 `planner.plan(..., target_skill="rnaseq.deg", available_annotations=sources)`，然后 `engine.execute(plan, source_run_id=..., annotation_files=files)`。不把这些路径发送给 DeepSeek。

GO/KEGG 选择对应的原 enrichment run；Heatmap 选择原 Heatmap run。本版不能把任意外部 CSV 假装成有 provenance 的历史结果。

## 验证命令

不需要激活 PowerShell 虚拟环境：

```powershell
# 免费、无 API/R
.\.venv\Scripts\python.exe -m pytest -q

# 只做受限定制的 mock 与真实固定 R 渲染
.\.venv\Scripts\python.exe -m pytest tests/test_customization.py tests/integration/test_real_customization.py --run-r -q

# 显式付费，7 个真实 Planner 请求，不执行 Skill/R
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_customization_live.py --run-llm -q
```

API 配置仍来自 `.env`。真实返回按新 UUID 保存在 `outputs/deepseek_tests/phase_7_6/*/planner_test.json`；不记录 API Key。

## 验收记录

真实 R 测试使用明确标记的合成 fixture 验证渲染与数据衔接，不声称产生新的真实生物学结论；本阶段没有重跑 Real_DATA_TEST。另显式执行了已有 Heatmap/GO/KEGG 的合成数据回归，其中原 Heatmap raw-count 路径仍会运行正常 VST；这不是 customization 重算统计。

| 实际命令/范围 | 结果 |
| --- | --- |
| `pytest -q` 全量免费回归 | **1163 passed, 89 skipped**，约 76 秒 |
| `tests/test_customization.py` + 新真实 R 测试 + `tests/test_manifests.py --run-r` | **86 passed**，约 8 秒（31 个新免费测试、8 个新真实 R 测试、47 个 Manifest 测试） |
| 新真实 R + 原 real_volcano/real_heatmap/real_go/real_kegg `--run-r` | **22 passed**，约 27 秒 |
| `tests/llm_integration/test_customization_live.py --run-llm` 最终一轮 | **7 passed**，约 8 秒 |

89 项跳过是默认不开启真实 API/R 及 1 项 Windows 符号链接权限测试，不是已通过的真实调用。真实 R 模块已覆盖默认尺寸、600×600 最小尺寸、空富集与源数据不变；mock 覆盖超时、非零退出、缺图、stdout 计数不符、schema 拒绝、历史来源/目录碰撞等。

真实 DeepSeek 最后一轮逐项：

| 用户请求 | 实际返回要点 | 结果 |
| --- | --- | --- |
| 上调红色、下调蓝色 | parameter_override；red/blue | PASS |
| Top30 + foxl2/sox9 | parameter_override；30；精确两个 ID | PASS |
| KEGG Top15 | parameter_override；top_n=15 | PASS |
| 性别分化相关上调 DEG | annotation_filter；up；单一关键词 sex differentiation | PASS |
| 脂质代谢按 log2FC 从大到小 | annotation_filter；lipid metabolism；log2fc；descending=true | PASS |
| 复杂多层热图 annotation panel | unsupported | PASS |
| 修改源码并调用 shell 安装包 | unsupported | PASS |

这些请求确实发送给 DeepSeek；Codex 没有替代模型生成实际测试决策。真实 API 用例禁止 Registry/Engine/subprocess 执行。免费执行测试使用明确 mock 决策；真实 R 测试使用固定合成 fixture，不冒充真实模型端到端执行。

### API 成本记录

实际响应模型：`deepseek-flash`（来自 response.model）。本阶段共 2 轮 × 7 请求，没有自动重试。

| 范围 | requests | prompt_tokens | completion_tokens | total_tokens |
| --- | ---: | ---: | ---: | ---: |
| 初轮（审查发现关键词扩大） | 7 | 7456 | 501 | 7957 |
| 收紧 Prompt 后最终轮 | 7 | 7806 | 453 | 8259 |
| 本阶段累计 | **14** | **15262** | **954** | **16216** |

计数直接汇总本阶段 `planner_test.json` 的真实 usage，不估算费用金额。初轮宽松断言虽通过，但不能作为“没有语义扩大”的证明，最终验收采用第二轮更严格测试。

### 实际发现与修复

1. 扩展参数后原 ToolAdapter 的两项精确默认值测试失败：更新为新完整默认值契约，没有降低 schema 校验标准。
2. 第一轮真实 DeepSeek 会把一个主题扩为若干相关主题（性别分化→性别决定/性腺发育、脂代谢→脂肪酸/胆固醇等）。原宽松测试未发现；审查真实返回后收紧 Prompt，并强化真实测试只接受对应单一直接翻译。第二轮 7 个用例全部通过。没有修改第一次 API 记录来伪装原始返回。
3. 补齐失败时的输入绑定记录、unsupported 审计、UUID 碰撞不覆盖、源 checksum 变化拒绝和实际 CSV 落盘一致性检查。
4. 补齐所选来源的有效注释覆盖统计、R 渲染计数校验和 stderr warning，避免只因“文件存在”就判成功。

### 本阶段新增工程概念

- **参数化与白名单**：模型选择有限能力，不把代码当参数。
- **语义与执行分离**：DeepSeek 翻译请求；本地 schema、历史结果与真实文件决定能否执行。
- **派生结果与 provenance**：新结果引用 source_run_id，快照/checksum 保证可追溯且原结果不被覆盖。
- **展示与统计分离**：重新着色、标签、排序与 Top N 不应触发 DESeq2/Fisher/BH 的重复计算。
- **安全限制而非假 sandbox**：固定可信代码与受限路径降低风险，但不宣称提供操作系统级隔离。

## Phase 7.6 Verdict

**PASS（本文限定的后端 Controlled Customization 范围）。** 可参数化、可过滤、可复用、可追踪，所有已启用测试通过，统计核心与源结果保留；不执行动态生成代码。

尚未接入独立 Streamlit 定制聊天入口；不宣称完成 UI/E2E 交互验收。普通 ToolAdapter 虽已识别新增绘图参数，但它不是“自动找到并修改上一张图”的能力。完整定制应使用本文 Planner/Engine 接口及明确 source_run_id。

其他限制：固定布局下很多标签可能重叠；无 gene symbol 自动映射；无 GO/KEGG 名称联网补全；多工作表 XLSX 需先选定导出；历史文件缺 checksum 或被替换时拒绝执行而非猜测；没有反复定制 filtering/replot 子结果的通用链式工作流（可重新指定原 DEG/富集 run）。

本阶段结束。未来隔离容器里的动态 patch、UI 和下一阶段均未实现。
