# LLM-driven Bulk RNA-seq Analysis Platform

这是一个用自然语言组织 Bulk RNA-seq 分析的本地工作区。

这个项目的目的，是想把平时使用的分析脚本整理成一套接口清楚、结果可追溯、能够测试的软件，同时探索大语言模型在生信分析中适合承担什么工作。

基本思路是：**让模型理解需求，让分析程序负责计算，让本地代码检查两者之间的交接。** DeepSeek 负责选择任务、提取参数和解释已得到的结果；DESeq2 和固定的 R/Python 程序负责统计分析。模型不能自行生成基因列表、修改统计结果或执行任意代码。

这也是工程化学习和作品集项目。除了分析能否跑通，我更关心输入错误能否被及时发现、一个结果能否追溯到它使用的数据，以及程序在失败后是否仍然可信。

## 项目状态

```yaml
Current milestone: Phase 7.8 Preview
Status: Active Development / NOT READY for stable release
```

目前提供 [v0.7.0-preview](https://github.com/Azure179/LLM-RNAseq-Platform/releases/tag/v0.7.0-preview) 作为公开预览，保留下一轮修复前的项目状态。它适合了解设计、运行示例和参与测试，还不是可以直接用于正式使用的稳定版本。

**当前主要问题：** Workflow 中生成的 DEG 结果，还不能可靠地被后续聊天请求复用于新的 Volcano、GO 或 KEGG 分析。已有结果可能没有正确进入下一次请求的执行上下文，从而被提示缺少输入。

下一步是 **Phase 7.8.1 — Result Reuse & Lineage Hotfix**，重点修复历史结果选择与来源追踪。这个修复尚未开始。

完整测试过程见 [Phase 7 E2E 报告](PHASE7_E2E_REPORT.md)。报告保留了失败案例和修复前的错误排序记录，最终结论仍是 **NOT READY**，没有把局部测试通过当作整体完成。

## 现在可以做什么

### RNA-seq 分析

| 功能 | 输入与处理方式 | 主要输出 |
|---|---|---|
| DEG | raw integer counts + metadata，使用 DESeq2 做两组比较 | 完整结果、显著 DEG、分析摘要 |
| PCA | raw counts 经 DESeq2 VST 后进行样本 PCA | 坐标、解释方差、PNG |
| Volcano | 读取已有完整 DEG 结果，不重跑 DESeq2 | 火山图、分类数据 |
| Heatmap | 已有 DEG + VST 矩阵；也可通过公共模块从 raw counts 生成 VST | 热图、绘图矩阵、选中基因 |
| GO enrichment | gene list + gene-to-GO mapping + 明确背景 | 富集表、点图、条形图 |
| KEGG enrichment | gene list + gene-to-pathway mapping + 明确背景 | 富集表、点图、条形图、ID 对照记录 |

GO/KEGG 面向自定义注释，也考虑了非模式物种的使用场景，不默认使用 human/mouse 数据库。当前统计实现是基础 R 的单侧 Fisher 检验和 BH 校正，不依赖 clusterProfiler，也不声称与其默认结果完全一致。

### 自然语言与工作流

- 通过 DeepSeek API 解析需求，支持 Structured Intent、Tool Calling 和多步骤 Workflow 规划。
- 使用已有 Skill Schema 和 Manifest 生成工具描述，通过 Registry 统一调用分析功能。
- 在本地检查业务参数、输入文件、输出绑定、依赖关系和 DAG，再按拓扑顺序串行执行。
- 上游失败时跳过依赖它的步骤；一个分支失败时，不阻止其他独立分支继续运行。
- 根据实际 SkillResult / WorkflowResult 生成总结，并检查关键事实是否与结果一致。

例如，下面的请求会形成一个分支工作流：

> 以 T 为对照比较 OT，画火山图，并对上调差异基因做 KEGG 富集。

```text
       DEG
      /   \
 Volcano  KEGG(up)
```

KEGG 使用 DEG 的上调基因集合，不依赖火山图，也不由模型编写 gene list。

### 本地工作区与结果定制

Streamlit 界面支持创建和切换项目、导入文件、填写样本分组、聊天分析，以及浏览和下载结果。项目、输入绑定、聊天记录和分析历史保存在本机，重启后可以重新打开。

对已有结果，可以在允许的参数范围内修改图形配色、标签和 Top N，或者按真实注释筛选、排序 DEG。定制会生成新结果并记录来源，不覆盖原始分析。GO/KEGG 的 Top N 重绘只读取已有富集表，不重新做统计检验。

## 我怎样划分模型与程序的职责

```text
用户需求 + 项目上下文
        ↓
DeepSeek：识别任务、提取参数、生成计划
        ↓
本地校验：Schema、输入、依赖、来源、DAG
        ↓
Skill Registry → 固定 R / Python 程序
        ↓
SkillResult / WorkflowResult
        ↓
DeepSeek：基于已验证摘要组织回复
        ↓
Chat + Results Browser
```

这里有几个我一直保留的边界：

- **统计输入不能混用。** DESeq2 接收 raw integer counts，不接收 TPM/FPKM；PCA 和 Heatmap 不直接画 raw counts。
- **执行权留在本地。** 模型只填写允许的业务参数，不能自由指定 Rscript、临时目录、API key 或 shell command。
- **富集背景必须明确。** 使用本次分析中有资格进入目标集合、且可映射到注释的基因，不默认整个物种的注释库。
- **结果缺失就承认缺失。** 无显著富集结果不等于运行失败；缺少比较方向时要求补充，不猜 treatment/control。
- **摘要不能代替证据。** 不把完整表达矩阵或大型结果表塞进 prompt；当前总结主要使用受限摘要事实，不能据此推断生物学机制或因果关系。

## 安装与启动

### 环境要求

- Python 3.13.x
- R 和可用的 `Rscript`
- Bioconductor `DESeq2`，用于 DEG、PCA 和从 raw counts 开始的 Heatmap
- Git
- 使用聊天功能时，需要自己的 DeepSeek API key 和网络连接

Python 依赖由项目配置安装，包括 Pydantic、httpx、Streamlit、pandas 和 openpyxl。单独运行已有分析 Skill 不需要 DeepSeek key。

### 安装 Python 环境

克隆仓库并进入项目目录：

```bash
git clone https://github.com/Azure179/LLM-RNAseq-Platform.git
cd LLM-RNAseq-Platform
```

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

这里直接使用虚拟环境中的 Python，不需要激活脚本，也不需要修改 PowerShell 执行策略。

Linux/macOS 可用以下方式安装；完整桌面使用流程目前主要在 Windows 上验证：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

### 检查 R 环境

```powershell
Rscript --version
Rscript -e "library(DESeq2); packageVersion('DESeq2')"
```

如果尚未安装 DESeq2，在 R 中运行：

```r
if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}
BiocManager::install("DESeq2")
```

### 配置 DeepSeek

将 `.env.example` 复制为项目根目录的 `.env`，在本机编辑：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

在 `DEEPSEEK_API_KEY` 后填入自己的 key。模型名可按账户实际可用模型调整，其他选项见 [.env.example](.env.example) 和 [客户端说明](docs/phase-6-0-deepseek-client.md)。`.env` 已被 Git 忽略。

### 打开工作区

完成环境安装后，Windows 可以双击 `Start_RNAseq.cmd`。启动窗口会打开本地浏览器页面；使用期间保持启动窗口运行，结束后通过窗口退出。

也可以从项目根目录启动：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

默认地址是 `http://127.0.0.1:8501`。双击启动器遇到端口占用时会尝试其他本地端口，以启动窗口显示为准。这是本机应用，不是持续在线的网站；停止服务后，页面就无法访问。

## 导入自己的数据

1. 在左侧创建项目，进入 **Files**。
2. 导入 gene-level raw count matrix。界面会识别支持的分隔符和表头别名，保存原文件，再生成规范分析副本。
3. 根据识别到的样本名填写分组并保存；也可以上传已有 metadata。程序不会根据样本名猜测实验分组。
4. 需要富集分析时，优先导入 eggNOG-mapper annotations；也可以分别提供 GO/KEGG mapping。
5. 检查文件状态、样本匹配和注释覆盖率，然后在 **Assistant** 提出需求。

导入适配只处理格式，不会把非整数表达量“转换”为 raw counts。eggNOG 的 query 必须与表达矩阵中的 gene ID 对应；蛋白、转录本和 gene ID 不一致时，需要先准备有依据的映射关系，不能靠改前后缀猜测。

直接使用 CLI 时，count CSV 的第一列为 `gene_id`，其余列为样本名；metadata 至少包含 `sample_id` 和 `condition`。UI 支持的别名与自动适配规则见 [输入格式说明](docs/input-format-adaptation.md)。

常用请求可以这样写，请将组名换成自己的真实分组：

> 帮我看一下这些样本的 PCA，检查整体表达模式。
>
> 以 T 为对照比较 OT，画火山图，并对上调基因做 KEGG 富集。
>
> 把刚才的火山图上调改成红色，下调改成蓝色，标注 Top20。

尽量明确比较方向。只说“比较 OT 和 T”时，系统可能要求补充谁是实验组、谁是对照组。受当前历史结果复用问题影响，完成 Workflow 后再单独要求基于其 DEG 做新的分析，仍可能被提示缺少上游输入。

分析完成后，在 **Results** 中查看 Workflow、各步骤或定制记录，选择图表预览并下载。表格默认预览前 100 行，下载的是完整原文件。查看已有结果不需要调用 DeepSeek。

相关说明：[样本分组](docs/sample-group-form.md) · [eggNOG 导入](docs/eggnog-import.md) · [工作区与定制](docs/phase-7-7-workflow-customization-ui.md)

## 不使用聊天，直接运行示例

仓库中的 `examples/` 是小型合成数据，用来学习接口和检查环境，不是科研结果。以下 PowerShell 命令从项目根目录执行；每次运行请使用新的或空的输出目录。

### DEG → Volcano

```powershell
.\.venv\Scripts\python.exe main.py deg `
  --counts examples/data/counts.csv `
  --metadata examples/data/metadata.csv `
  --treatment treatment `
  --control control `
  --output-dir outputs/demo_deg

.\.venv\Scripts\python.exe main.py volcano `
  --deg-result-file outputs/demo_deg/deg_results.csv `
  --output-dir outputs/demo_volcano
```

默认显著 DEG 条件为 `padj < 0.05` 且 `abs(log2FoldChange) >= 1`。正 log2FC 表示 treatment 相对 control 上调。这是对检验结果的后置筛选，不是以 log2FC=1 为原假设边界的检验。

DEG 输出包括完整 `deg_results.csv`、筛选后的 `deg_filtered.csv`、`summary.json` 和运行日志。Volcano 使用完整结果表，不重新运行 DESeq2。零 padj 只在绘图时调整显示高度，原始统计值不变。

### PCA 与 Heatmap

```powershell
.\.venv\Scripts\python.exe main.py pca `
  --counts examples/data/counts.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --output-dir outputs/demo_pca

.\.venv\Scripts\python.exe main.py heatmap `
  --counts examples/data/counts.csv `
  --deg-result-file outputs/demo_deg/deg_results.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --top-n 50 `
  --output-dir outputs/demo_heatmap
```

PCA 对 VST 后的变异基因做中心化分析。Heatmap 从显著 DEG 中选择 Top genes，绘图矩阵为行 Z-score；不足指定数量时使用实际可用基因。已有 VST 矩阵时，可用 `--normalized-expression` 替换 `--counts`，不要同时提供两者。

### GO 与 KEGG 的独立示例

这里使用各自配套的合成 gene list、mapping 和 universe，不依赖上面的 DEG 示例。

```powershell
.\.venv\Scripts\python.exe main.py go `
  --gene-list examples/go/synthetic_gene_list.txt `
  --gene-to-go-mapping examples/go/synthetic_mapping.tsv `
  --universe examples/go/synthetic_universe.txt `
  --output-dir outputs/demo_go

.\.venv\Scripts\python.exe main.py kegg `
  --gene-list examples/kegg/synthetic_gene_list.txt `
  --gene-to-pathway-mapping examples/kegg/synthetic_mapping.tsv `
  --universe examples/kegg/synthetic_universe.txt `
  --output-dir outputs/demo_kegg
```

真实分析请使用与 DEG 选择规则相符的背景。完整 `deg_results.csv` 可通过 `--analysis-genes` 提供为候选背景；不要误把完整结果表当成已筛选的前景。具体过滤、缺失值、ontology 和 pathway ID 规则见 [GO 文档](docs/phase-4-4-go.md) 与 [KEGG 文档](docs/phase-4-5-kegg.md)。

更多参数可通过 `main.py <skill> --help` 查看。Python 调用接口和统一结果格式分别见 [输入输出契约](docs/contracts.md)、[SkillResult](docs/skill-result-schema.md) 和 [Registry](docs/phase-5-3-registry.md)。

## 测试与真实数据验证

测试分成离线测试、真实 R 测试和真实 DeepSeek 测试。

```powershell
# 默认测试：不调用真实 API 或 R
.\.venv\Scripts\python.exe -m pytest -q

# 显式启用真实 R 分析
.\.venv\Scripts\python.exe -m pytest --run-r -q

# 显式启用真实 DeepSeek API，产生费用
.\.venv\Scripts\python.exe -m pytest --run-llm -q

# 同时允许真实 API 和 R 集成测试
.\.venv\Scripts\python.exe -m pytest --run-llm --run-r -q
```

真实集成测试需要相应依赖和输入，不是克隆后全部都能直接运行。Rscript 不在 PATH 时，可以按测试入口支持的 `--rscript` 参数指定路径。mock/fixture 只用于软件行为测试，不作为生物学结果。

Phase 7.8 的验证记录：

- 默认测试：**1,191 passed / 96 skipped**。
- 真实 R 专项：**37 passed**。
- 真实数据：24,581 genes、9 samples、3 groups；测试了分支 Workflow、PCA/DEG 独立任务、结果定制、注释筛选、下载、隔离和重启恢复。
- 发现并修复了多组项目的两组 DEG 输入、过滤后排序来源、定制警告展示等问题，并添加回归测试。
- 历史 DEG 的跨聊天复用仍有缺口，因此没有将整个阶段标为通过。

这些数字对应一次已记录的测试，不是对任意数据集的正确性保证。详细请求、真实结果、失败记录、耗时和 token usage 都在 [E2E 报告](PHASE7_E2E_REPORT.md) 中。

## 数据与隐私

原始输入、分析产物和项目历史默认保存在本地。使用聊天时，请求和精简分析上下文会发送给 DeepSeek，因此这不是完全离线的系统；不适合未经授权将敏感研究信息发送到外部服务的场景。
公开仓库不包含真实科研矩阵、注释大表、用户 workspace、运行日志或真实 API key。`Real_DATA_TEST/` 只保留测试脚本、相对路径配置和文字报告，真实输入与结果未公开。报告中的本地 artifact 路径不代表仓库提供了这些文件。

## 还需要完善的地方

优先修复 Result Reuse & Lineage，再继续扩展功能。目前需要明确的限制包括：

- 历史 Workflow DEG 在后续聊天中的复用尚不可靠；不能简单取“最新结果”来绕过比较方向和来源确认。
- 注释筛选是文字匹配，不是功能验证。中文关键词不一定匹配英文注释，精确 gene ID 也不等于 gene symbol。
- 直接 DEG Skill 仍是简单两组设计，不支持 batch correction 或复杂 design formula；Workflow 层可为明确的两组比较派生输入快照。
- Workflow 当前串行执行；没有自动重试、自主 Agent 或任意 Python/R 代码生成执行。
- 当前是本地 Streamlit MVP，没有 FastAPI、Docker 部署、多用户或身份认证。
- 结果摘要的事实校验是有限约束，不等同于全面生物学审查。正式研究仍需要检查输入、统计假设、注释来源和完整结果。

欢迎通过 [Issues](https://github.com/Azure179/LLM-RNAseq-Platform/issues) 反馈可复现的问题。请附上操作步骤、错误码和经过脱敏的最小示例，不要提交 API key 或未获授权公开的科研数据。

## License

[MIT](LICENSE)
