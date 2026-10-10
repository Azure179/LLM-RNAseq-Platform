# Bulk RNA-seq AI Skill Package：Phase 0 设计

## 1. 设计目标与核心边界

本项目把可靠的 Bulk RNA-seq 程序封装成具有稳定输入、输出和错误契约的分析操作。Python 负责参数模型、输入验证、流程编排、外部进程管理、日志和结果封装；R/Bioconductor 负责 DESeq2、clusterProfiler 等实际生物信息学计算；LLM 在后续阶段只负责理解意图、生成结构化请求、选择允许的操作和解释真实结果。

建议把整个仓库作为一个可安装的 `bulk-rnaseq` Skill 包，而不是为 DEG、PCA、火山图等各建一个互相独立的仓库。仓库内部的每个分析能力有独立操作契约，并在 Phase 6 暴露为受控工具。这样可以共享验证、日志、R 进程调用和结果模型，也不会让 LLM 获得任意执行 shell 命令的权限。

输入验证属于所有分析操作必须经过的基础层。它可以提供独立的 `validate` CLI 命令，但不应成为一个可被 LLM 选择跳过的可选步骤。

## 2. 推荐的最终目录结构

下面是项目完成 Phase 8 后的目标结构，不应在 Phase 1 一次性创建所有空目录和占位文件；各目录应随对应阶段逐步加入。

```text
bulk-rnaseq-skill/
├── SKILL.md                         # Phase 6：Skill 入口、适用场景、约束和工具说明
├── agents/
│   └── openai.yaml                  # Phase 6：Skill 的展示和调用元数据
├── pyproject.toml                   # Phase 1：Python 包、依赖、CLI、pytest 和格式化配置
├── README.md                        # Phase 8：安装、架构、使用、Demo 和限制
├── LICENSE                          # Phase 1：开源许可证
├── .gitignore                       # Phase 1：忽略环境、缓存、日志和分析输出
├── .dockerignore                    # Phase 8：Docker 构建上下文排除规则
├── Dockerfile                       # Phase 8：Python、R、Bioconductor 和应用运行环境
├── renv.lock                        # Phase 2：锁定 R/Bioconductor 依赖版本
├── docs/
│   ├── phase-0-design.md            # Phase 0：本设计文档
│   ├── contracts.md                 # Phase 3：输入、输出、错误码和文件格式契约
│   └── architecture.md              # Phase 7/8：最终组件和执行流程说明
├── src/
│   └── bulk_rnaseq/
│       ├── __init__.py              # Phase 1：包版本和公开接口
│       ├── cli.py                   # Phase 2：命令行入口
│       ├── models.py                # Phase 3：请求、结果和错误的 Pydantic 模型
│       ├── validation.py            # Phase 3：通用及生物学输入验证
│       ├── exceptions.py            # Phase 3：稳定的领域异常和错误码
│       ├── logging_config.py        # Phase 3：控制台与文件日志配置
│       ├── r_runner.py              # Phase 2/3：安全执行 Rscript 并捕获退出码/stdout/stderr
│       ├── registry.py              # Phase 6：工具名到受控处理函数的注册表
│       ├── workflow.py              # Phase 7：有限状态分析工作流
│       ├── skills/
│       │   ├── __init__.py
│       │   ├── deg.py               # Phase 2：DESeq2 差异表达操作
│       │   ├── pca.py               # Phase 4：变换、PCA 坐标和图
│       │   ├── volcano.py           # Phase 4：火山图
│       │   ├── heatmap.py           # Phase 4：表达热图
│       │   ├── enrichment.py        # Phase 4：GO 和 KEGG 富集
│       │   └── gsea.py              # Phase 4：GSEA
│       ├── r_scripts/
│       │   ├── deseq2_deg.R         # Phase 2：DESeq2 的真实计算
│       │   ├── transform_pca.R      # Phase 4：VST、PCA 和可复用变换矩阵
│       │   ├── volcano.R            # Phase 4：火山图绘制
│       │   ├── heatmap.R            # Phase 4：热图绘制
│       │   ├── enrichment.R         # Phase 4：clusterProfiler GO/KEGG
│       │   └── gsea.R               # Phase 4：clusterProfiler GSEA
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── planner.py            # Phase 5：自然语言转结构化分析计划
│       │   └── schemas.py            # Phase 5：LLM Structured Output 模型
│       └── api/
│           ├── __init__.py
│           └── app.py                # Phase 8：FastAPI 应用和端点
├── tests/
│   ├── unit/                         # Phase 3：无需 R 的快速单元测试
│   ├── integration/                  # Phase 2/3：需要 R 和 DESeq2 的集成测试
│   └── fixtures/                     # 小型、可追踪来源的 count/metadata 测试数据
└── examples/
    ├── data/                         # 可公开的小型演示输入
    ├── requests/                     # 标准 JSON 请求示例
    └── expected/                     # 只保存稳定、可验证的小型预期结果
```

`outputs/`、`.venv/`、R 本地库、缓存和运行日志是运行时内容，应被 `.gitignore` 排除。R 脚本作为 Python 包资源安装，Python 使用 `importlib.resources` 定位它们，避免依赖当前工作目录或硬编码绝对路径。

## 3. 第一版 MVP 的边界

MVP 在 Phase 3 结束时达到“可展示、可重复验证”的状态：

```text
count matrix + metadata + JSON request
  -> Python 验证
  -> Python 以参数列表调用 Rscript
  -> R/DESeq2 计算
  -> Python 检查进程和产物
  -> deg_results.tsv + summary.json + logs
```

### MVP 支持

- 单因素、两个指定水平的差异表达，例如 `condition: treated vs control`。
- 输入是 raw、非负、整数 gene-level count matrix；明确拒绝 TPM、FPKM 和已经标准化的数据。
- metadata 至少包含 `sample_id` 和设计因子列。
- count matrix 的样本列和 metadata 的 `sample_id` 必须一一对应，顺序由程序显式重排，不依赖输入的偶然顺序。
- 每个比较水平要求生物学重复。第一版建议至少 2 个样本/水平，并在文档中明确小样本统计功效有限。
- 明确的 numerator/denominator，使用 DESeq2 的显式 contrast，避免结果方向含糊。
- 输出所有接受检验的基因，而不是只输出显著基因。
- 使用 `padj` 和可配置的 `alpha` 统计显著基因数；`padj = NA` 保留并单独计数。
- 所有外部程序调用检查 exit code，并记录 stdout/stderr。
- 失败时返回稳定错误码，不生成看似成功的伪结果。

### MVP 不支持

- FASTQ 比对、定量、转录本组装或从 FASTQ 开始的上游流程。
- TPM/FPKM 差异检验。
- 多因素设计、配对设计、批次校正、交互项、时间序列和 LRT。
- 自动猜测物种、基因 ID 类型、设计公式或对照组。
- PCA、绘图、富集、GSEA、LLM、Tool Calling、Agent、API、Docker。
- 大规模任务队列、数据库、用户权限、云存储和前端。

## 4. 统一输入输出设计

### 4.1 通用请求信封

所有操作最终复用一个请求外壳，具体字段由 `operation` 对应的参数模型验证。路径相对于请求文件所在目录或显式工作目录解析；结果中优先记录相对于本次运行目录的路径以便迁移。

```json
{
  "schema_version": "1.0",
  "operation": "differential_expression",
  "inputs": {},
  "parameters": {},
  "output_dir": "outputs/demo-deg"
}
```

不接受 LLM 生成任意命令、R 表达式或 shell 片段。`operation` 必须来自注册表白名单，参数必须通过对应模型。

### 4.2 通用结果信封

```json
{
  "schema_version": "1.0",
  "run_id": "generated-id",
  "operation": "differential_expression",
  "status": "succeeded",
  "parameters": {},
  "artifacts": {
    "deg_table": "deg_results.tsv"
  },
  "metrics": {},
  "warnings": [],
  "provenance": {
    "package_version": "...",
    "r_version": "...",
    "deseq2_version": "..."
  },
  "error": null
}
```

失败结果使用 `status: "failed"`，`artifacts` 为空或只列出已经验证完整的产物，并返回如下错误对象：

```json
{
  "code": "SAMPLE_MISMATCH",
  "message": "Count matrix and metadata contain different sample IDs.",
  "details": {
    "missing_in_metadata": ["sample_04"],
    "missing_in_counts": []
  }
}
```

错误信息应区分用户输入错误、环境/依赖错误、R 执行错误和产物验证错误。不要把完整 traceback 当作面向用户的错误消息；traceback 写入日志。

### 4.3 各操作契约

#### `validate_inputs`（基础服务，可有独立 CLI，但不能被分析流程跳过）

- 输入：count matrix 路径、metadata 路径、设计因子、比较水平，以及文件格式设置。
- 检查：文件存在且可读；表头和 ID 唯一；counts 为非负整数；样本集合一致；设计因子存在且无缺失；比较水平存在；每个水平有重复；至少存在可分析的非全零基因。
- 输出：`validation_report.json`，包含 `valid`、输入维度、样本/水平计数、warnings 和结构化 errors。
- 不做：不改变原始文件，不自动修复/猜测设计，不执行统计分析。

#### `differential_expression`

- 输入：raw count matrix、metadata、`design_factor`、`numerator`、`denominator`、`alpha`，后续可加入明确受支持的 DESeq2 参数。
- MVP count matrix 格式：第一列 `gene_id`，其余列名是唯一 `sample_id`；每个单元格为非负整数。
- MVP metadata 格式：必须有唯一的 `sample_id` 和 `design_factor` 对应列；允许保留额外列，但 MVP 不把它们加入设计。
- 输出：`deg_results.tsv`，至少包含 `gene_id`、`baseMean`、`log2FoldChange`、`lfcSE`、`stat`、`pvalue`、`padj`；`summary.json` 包含输入基因数、实际检验数、`padj` 缺失数、显著/上调/下调数、参数和软件版本。
- 方向定义：正 `log2FoldChange` 表示 numerator 相对 denominator 上调。

MVP 请求示例：

```json
{
  "schema_version": "1.0",
  "operation": "differential_expression",
  "inputs": {
    "count_matrix": "../data/counts.tsv",
    "metadata": "../data/metadata.tsv"
  },
  "parameters": {
    "design_factor": "condition",
    "numerator": "treated",
    "denominator": "control",
    "alpha": 0.05
  },
  "output_dir": "../../outputs/demo-deg"
}
```

#### `pca`

- 输入：raw counts、metadata、用于着色/分组的 metadata 列、可选 top variable gene 数量。
- 计算：DESeq2 VST 之后进行 PCA；不得直接对 raw counts 或 TPM 混用同一契约。
- 输出：`pca_coordinates.tsv`、`pca_plot.png`、`summary.json`；坐标表包含样本、PC 坐标和解释方差。
- 可复用中间产物：`vst_matrix.tsv` 可由内部变换步骤产生，但不把不可移植的内存对象作为公共接口。

#### `volcano_plot`

- 输入：经过列验证的 DEG table、`padj_threshold`、`abs_log2fc_threshold`、可选标注基因列表。
- 输出：`volcano_plot.png`、用于审计的分类计数和 `summary.json`。
- 约束：阈值和颜色分类必须写入结果；不重新进行差异检验。

#### `heatmap`

- 输入：raw counts + metadata，或者已经验证的 VST matrix；基因列表或明确的 top-N 选择规则；注释列。
- 计算：使用 VST/适当变换后的表达值，按基因做 z-score 仅用于可视化。
- 输出：`heatmap.png`、`heatmap_matrix.tsv`、`summary.json`。
- 约束：区分“用于绘图的行缩放值”和可做统计分析的表达量，不能将 z-score 回用于 DEG。

#### `go_enrichment`

- 输入：经过验证的 DEG table 或显式 gene list、物种、`key_type`、GO ontology、显著性与方向筛选规则、背景基因集合。
- 输出：`go_enrichment.tsv`、可选图、`summary.json`，包含输入 ID 数、成功映射数、背景集合和数据库/包版本。
- 约束：不猜测物种和 ID 类型；默认背景应来自本次实际被检验的基因，而不是整个基因组。

#### `kegg_enrichment`

- 输入：gene list、KEGG organism code、ID 类型/转换规则、筛选规则和背景基因。
- 输出：`kegg_enrichment.tsv`、可选图、`summary.json`，报告 ID 映射率和数据来源版本/访问时间。
- 约束：网络或数据库不可用时明确失败，不复用旧结果冒充本次结果。

#### `gsea`

- 输入：覆盖所有合格基因的命名排序向量，优先使用 DESeq2 `stat`；显式物种、ID 类型、基因集来源和 GSEA 参数。
- 输出：`gsea_results.tsv`、可选 enrichment 图、`summary.json`。
- 约束：不能先按 `padj` 过滤再做 GSEA；必须检查重复 ID、缺失/无限 ranking 值、排序方向和映射率。

## 5. 项目整体数据流

### MVP（Phase 2–3）

```text
CLI + request.json
  -> 请求模型/路径解析
  -> 输入和生物学规则验证
  -> 创建独立 run 目录
  -> Python RRunner
  -> Rscript deseq2_deg.R（真实 DESeq2）
  -> 检查 exit code + stdout + stderr
  -> 检查 DEG 文件存在、非空、列完整
  -> Python 写 summary.json
  -> CLI 返回明确退出码
```

应把 R 进程参数作为参数列表传递，不通过 shell 拼接。建议先写入临时运行目录，全部产物验证成功后再确定为成功结果，避免半成品被误认为完成。

### 最终形态（Phase 5–8）

```text
用户自然语言 / CLI / FastAPI
  -> LLM 只生成 AnalysisPlan 结构化对象
  -> Pydantic 二次验证
  -> Tool Registry 只允许已注册操作
  -> Workflow 按有限状态执行
  -> 每个操作强制输入验证
  -> Python 调用固定 R 脚本
  -> 验证真实产物并形成 Result
  -> LLM 只基于 Result 解释，不补造缺失结果
  -> CLI / API 返回 JSON 和产物位置
```

安全边界位于工具注册表和参数模型：LLM 无权传入任意可执行程序、脚本路径或 R 代码。分析层在没有 LLM 的情况下也必须能由 CLI 独立运行和测试。

## 6. Phase 1–8 开发顺序与验收标准

### Phase 1：开发环境和项目结构

完成：创建最小 `src` 布局、`pyproject.toml`、包入口、`.gitignore`、许可证和最小测试配置；记录并检查 Python、Rscript 可用性。只创建近期需要的文件。

验收：

- 新环境可执行 editable install。
- `python -m pytest` 能运行一个最小 smoke test。
- `Rscript --version` 可被 Python 环境找到。
- 仓库无缓存、虚拟环境或输出文件被追踪。
- 尚无 DEG 或其他正式分析功能。

### Phase 2：最小版 DEG Skill

完成：实现 happy-path CLI、Python `RRunner`、固定的 `deseq2_deg.R`、小型 demo 输入和一次真实 DESeq2 运行；建立 R 依赖锁定方式。

验收：

- 使用 demo raw counts 和 metadata 可真实生成 `deg_results.tsv` 与 `summary.json`。
- log2FC 方向与 numerator/denominator 一致。
- Python 检查 Rscript 的 exit code、stdout 和 stderr。
- 破坏 R 命令时 CLI 非零退出，不输出成功状态。
- 不包含 PCA、绘图、富集、LLM 或 API。

### Phase 3：输入验证、异常处理、日志和测试

完成：加入 Pydantic 契约、领域错误码、统一日志、产物验证、单元测试与需要 R 的集成测试标记；补充 `contracts.md`。

验收：

- 对缺文件、重复 ID、样本不匹配、负数、小数、NA、错误水平、无重复、全零基因等情况给出稳定错误。
- 失败结果不会伪装为成功，CLI 使用约定的非零退出码。
- 每次运行保存关键参数、R stdout/stderr 和可定位的日志。
- 单元测试不依赖 R；集成测试可明确选择运行或跳过。
- `pytest` 全部通过，MVP 边界完成。

### Phase 4：扩展 RNA-seq Skills

按最小依赖顺序实现：VST/PCA -> volcano -> heatmap -> GO -> KEGG -> GSEA。每次只加入一个操作及测试，优先复用统一模型和 RRunner。

验收：

- 每个操作都有请求模型、结果模型、输入验证、固定 R 脚本、真实小型测试和可审计产物。
- PCA/heatmap 不直接使用 raw counts 作图；GSEA 不使用显著基因子集。
- 富集操作报告物种、ID 类型、映射率和背景集合。
- 一个操作失败不产生虚假的成功 summary。

### Phase 5：接入 LLM 和 Structured Output

完成：定义有限的 `AnalysisPlan`；LLM 把用户语言转换为计划，Python 严格验证；结果解释只接收已验证的 result JSON。使用 mock 测试，不让普通测试依赖在线 API。

验收：

- 能把典型请求转换为合法计划。
- 缺少物种、对照或输入文件等关键字段时返回澄清需求，不猜测。
- 非法 operation 和参数被 Python 拒绝。
- 此阶段 LLM 只规划和解释，尚不自动执行工具。

### Phase 6：Tool Calling

完成：实现白名单工具注册表，将结构化 tool call 映射到 Python handler；加入根目录 `SKILL.md` 和 `agents/openai.yaml`；记录每次调用和返回。

验收：

- LLM 只能调用已注册工具及其 schema，不能指定 shell、R 代码或脚本路径。
- tool call 参数会再次经过模型和领域验证。
- 工具返回统一 Result，错误不会被吞掉。
- 在无 LLM 模式下仍能直接测试所有 handler。
- Skill 元数据和说明通过相应校验。

### Phase 7：Workflow / Agent

完成：实现显式有限状态工作流，例如 `plan -> validate -> execute -> verify -> explain`；设置最大步骤数、允许转换和失败终止条件。

验收：

- 一个端到端请求可依次运行需要的已注册操作并汇总真实结果。
- 中间产物和依赖关系明确，例如 DEG 可供 volcano/GSEA 使用。
- 验证或执行失败时立即进入失败状态，不让 LLM 声称完成。
- 没有无限循环、任意代码执行或无界自主行为。

### Phase 8：FastAPI 和 Docker 产品化

完成：提供最小 FastAPI 接口、健康检查、请求/响应 schema、Dockerfile、容器内 R/Bioconductor 环境、README 和可重复 Demo。

验收：

- API 与 CLI 复用同一业务函数，不复制分析逻辑。
- API 错误映射到合理 HTTP 状态和统一错误体。
- Docker image 可构建，容器健康检查通过。
- 挂载 demo 数据后可通过 CLI 或 API 完成真实 DESeq2 分析。
- README 从全新环境给出的命令可复现 Demo。
- 明确说明第一版同步 API 不适合长任务；真正多用户生产部署需要后续任务队列和持久化，而不是在本阶段伪装成生产级平台。

## 7. 当前不要做的内容

- 不创建所有最终目录的空壳和大量 `__init__.py`；只在阶段需要时创建。
- 不先做通用 DAG 引擎、插件系统、事件总线、微服务或复杂基类。
- 不为了“统一”把所有参数塞进一个巨大模型；使用通用信封加每个 operation 的参数模型。
- 不允许用户或 LLM 提交任意设计公式、R 代码、shell 命令或脚本路径。
- 不实现 FASTQ 下载、质控、STAR/Salmon、featureCounts 或 StringTie；它们属于未来独立的上游模块。
- 不加入数据库、队列、鉴权、云部署、前端和多租户。
- 不同时维护 Python 和 R 两套统计实现；真实生物信息学计算集中在 R/Bioconductor，Python 做工程编排。
- 不用 TPM/FPKM 运行 DESeq2，不自动猜测物种/基因 ID，不用全基因组替代实际检验基因作为富集背景。
- 不把 RDS 等语言内部对象作为唯一公共输出；公共产物使用 TSV/JSON/PNG 等可移植格式。
- 不把大型真实数据或生成输出提交到 Git；测试使用小型、来源清楚的数据。
- 不在普通测试里依赖真实 LLM API、网络或密钥；在线测试必须单独标记。
- 不在 Phase 0 写正式功能代码，也不在用户确认前开始 Phase 1。

## 8. Phase 0 验收清单

- [x] 明确 Python、R 和 LLM 的职责边界。
- [x] 明确 MVP 只做 raw counts + metadata -> DESeq2 -> DEG table + summary。
- [x] 明确单因素、双水平、有重复的 MVP 科学边界。
- [x] 定义统一请求、结果和错误信封。
- [x] 定义每个计划中分析操作的输入输出与关键科学约束。
- [x] 给出 Phase 1–8 的顺序和逐阶段验收条件。
- [x] 列出暂不实现的内容，避免过度设计。
- [x] 未写任何正式分析功能代码。

Phase 0 由用户确认后，下一步只进入 Phase 1。
