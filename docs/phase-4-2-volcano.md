# Phase 4.2：Volcano Plot Skill

## 设计与范围

Volcano 消费已有 DEG Skill 的完整 `deg_results.csv`，不读取 counts/metadata、不拟合模型、
不调用 DESeq2、不重算 pvalue/padj，也不修改源文件。Python 负责验证、分类、标签排序；
固定 R 脚本只使用基础绘图函数生成 PNG，因此没有新 Python/R 包依赖。

已检查现有 DEG 列为 gene_id、baseMean、log2FoldChange、lfcSE、stat、pvalue、padj。
本 Skill 要求其中 gene_id、log2FoldChange、pvalue、padj 四列；允许列换序和其他额外列。
输出绘图数据只保留这四个原始字段，加上派生绘图字段，不复制其余 DEG 列。

为什么不直接复用 raw counts 验证：DEG 结果的 NA 和小数是合法统计输出，不能套用“无缺失非负整数”。
复用公共 csv_rows、输入快照、目录锁、日志、SkillError、Rscript runner 和 summary 最后发布机制；
只新增结果表专属检查。PNG 检查从 PCA 移至公共 plot_artifacts，不改变 PCA 检查或统计方法。

## 数据流

```text
已有完整 DEG CSV
→ 新/空输出目录 + 运行日志
→ 输入快照 + 参数/列/数值检查
→ Python 分类、确定有限绘图坐标、选择标签
→ volcano_plot_data.csv（暂存目录）
→ Rscript plot_volcano.R（仅渲染）
→ 检查退出码、stdout/stderr、PNG、绘图数据未被改写、实际点数
→ 发布 volcano_plot.png 和 volcano_plot_data.csv
→ 最后发布 summary.json
```

完整 DEG CSV 应来自真实 DEG 分析；程序验证格式和内容，但无法仅凭 CSV 证明其来源。
不要用 deg_filtered.csv 代替完整表，否则图缺少未筛选基因背景，汇总也只针对该子集。
本次 n_total 始终指输入结果表行数，并非最初 count matrix 的基因数。

## 参数与分类

`run_volcano(deg_result_file, output_dir, padj_threshold=0.05, log2fc_threshold=1.0,
top_n_labels=10, *, timeout=600, rscript_executable="Rscript")`。

- 路径使用 pathlib.Path；相对路径按调用时工作目录解析。
- padj_threshold：有限数值，严格 0 < threshold < 1，两个端点均不接受。
- log2fc_threshold：有限数值，>= 0。
- top_n_labels：非负整数，0 表示不标注；布尔值不是合法数值参数。
- timeout：有限正数。R 仅用于绘图，不需要 DESeq2。

分类互斥且穷尽所有输入行：

| 类别 | 条件 |
|---|---|
| Up | padj < 阈值，log2FC >= 阈值，且 log2FC > 0 |
| Down | padj < 阈值，log2FC <= -阈值，且 log2FC < 0 |
| NS | 其余情况，含缺失值及 log2FC=0 |

当 log2FC 阈值为 0，log2FC=0 不具有上下调方向，因此归 NS，避免两个条件重叠。
padj 恰好等于阈值时为 NS；非零 log2FC 恰好达到阈值且 padj 满足要求时可以 Up/Down。
标签只从 Up/Down 选择，顺序为 padj 升序、abs(log2FC) 降序、gene_id 升序，最多 top_n_labels 个。

### NA、零值与极小数

- 缺失标记沿用项目约定：空值、NA、N/A、NaN、NULL、None，不区分大小写。
- padj 缺失或 log2FC 缺失：归 NS、保留 CSV 行、记录 excluded_reason，不画点、不填补统计值。
- 全部无法画点：仍生成带说明文字的图，n_plotted=0，并返回 NO_PLOTTABLE_GENES 警告。
- NS 仅表示未满足本次分类条件，**不是“证明无差异”**；其中可能包含无法评估的基因。
- pvalue=0 合法；纵轴用 padj，不会对 pvalue 取对数。
- padj=0 保持源值 0，仅在绘图列设置有限高度：
  `max(10, -log10(padj_threshold)+1, 最大正 padj 对应高度+1)`。
  这是显示约定，不是重新估算 padj，不能把该高度反算成真实 padj。
- 正 padj 直接计算 -log10；支持例如 1e-300、5e-324，不将它们按 0 处理。
- 超出浮点可表示范围的输入（如正数 1e-400）明确报 DEG_VALUE_INVALID，避免悄悄下溢变成 0。
- Inf/-Inf、概率不在 [0,1]、非数字、重复 gene_id 或列宽错误均拒绝。
- padj 有值而 pvalue 缺失属于不一致输入，会拒绝。

DESeq2 的 padj 缺失可能来自独立过滤，pvalue 缺失也有其他统计原因，不应把 NA 强制填成 1。
参见 [DESeq2 官方说明](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#why-are-some-p-values-set-to-na)。

## 运行

在项目根目录使用 PowerShell，无需激活虚拟环境。下面直接消费已经存在的 Phase 3 结果：

```powershell
.\.venv\Scripts\python.exe main.py volcano `
  --deg-result-file outputs/phase3_verified_20260910/deg_results.csv `
  --output-dir outputs/my_volcano_01 `
  --padj-threshold 0.05 `
  --log2fc-threshold 1 `
  --top-n-labels 10
```

每次更换输出目录。若当前终端的 PATH 中找不到 Rscript，在命令末尾追加：

```powershell
--rscript Rscript
```

该路径仅为本机示例，不写死在源码中。Python 直接调用：

```python
from pathlib import Path
from bulk_rnaseq.skills import run_volcano

result = run_volcano(
    deg_result_file=Path("outputs/phase3_verified_20260910/deg_results.csv"),
    output_dir=Path("outputs/my_volcano_api_01"),
    padj_threshold=0.05,
    log2fc_threshold=1.0,
    top_n_labels=10,
)
result.to_dict()
```

## 输出契约

| 文件 | 用途 |
|---|---|
| volcano_plot.png | 红色 Up、蓝色 Down、灰色 NS；阈值线、分类计数、已绘制/总行数；1200×900 |
| volcano_plot_data.csv | 四个原始字段、category、neg_log10_padj、plotted、label、padj_zero_adjusted、excluded_reason |
| summary.json | schema_version=1.0、operation=volcano、status=succeeded、metrics、artifacts、warnings、provenance |
| run.log | Python 运行和警告日志 |
| r_stdout.log / r_stderr.log | 基础 R 渲染器的真实标准输出和诊断 |
| failure.json | 运行失败且目录可用时的结构化错误，不会同时保留成功摘要 |

metrics 包含 n_total、n_up、n_down、n_ns、n_plotted、n_excluded、n_padj_missing、n_log2fc_missing、n_padj_zero、n_labels。
分类总数严格满足 n_total=n_up+n_down+n_ns；NA 行也包含在总数中。
图例计数针对全部 CSV 行，实际点数见图底部 Shown 和 n_plotted。
原始四列的单元格文本保持不变；新 CSV 的引号/换行格式不保证与源文件字节一致，源文件本身不写入。

artifacts 路径相对于 output_dir，结果对象也提供绝对 Path。
provenance 记录源 CSV 快照、R 绘图脚本和产物的 SHA-256，以及当前包/R 版本。
不声称本次执行过 DESeq2，因此不伪填本次 deseq2_version；上游版本/contrast 请查 DEG 的 summary.json。

新增输入错误码 DEG_COLUMNS_MISSING、DEG_FORMAT_ERROR、DEG_VALUE_INVALID、DEG_EMPTY；
其他文件、目录、参数、进程、产物错误码复用现有机制。CLI 输出 JSON，失败非零退出，无未处理 traceback。

## 文件清单与新工程概念

| 文件 | 作用 |
|---|---|
| src/bulk_rnaseq/volcano_data.py | 新增 DEG 结果验证、互斥分类、零/NA 处理和稳定标签选择 |
| src/bulk_rnaseq/skills/volcano.py | 新增独立编排、产物核验和统一摘要 |
| src/bulk_rnaseq/r_scripts/plot_volcano.R | 新增纯绘图脚本，不导入 DESeq2 |
| src/bulk_rnaseq/plot_artifacts.py | 从 PCA 提取公共 PNG 结构检查 |
| src/bulk_rnaseq/pca_artifacts.py | 改为导入公共 PNG 检查，规则不变 |
| src/bulk_rnaseq/models.py | 新增 VolcanoRunResult，同一结果信封 |
| src/bulk_rnaseq/cli.py | volcano 子命令；集中处理命令对应的错误 operation |
| src/bulk_rnaseq/skills/__init__.py | 导出 run_volcano / VolcanoRunResult |
| src/bulk_rnaseq/__init__.py、pyproject.toml | 版本 0.4.0；更新 R 集成测试标记说明 |
| tests/test_volcano.py | 参数、数据、分类、进程错误、不可变输入、CLI 与产物测试 |
| tests/integration/test_real_volcano.py | 真实基础 R 绘图，正常/零值/NA/无可绘制点 |
| tests/plot_helpers.py、tests/test_pca.py | 提取并复用已有模拟 PNG 夹具；不改 PCA 测试断言 |
| tests/conftest.py | 将 opt-in 测试提示从 DESeq2 改为 R，以涵盖纯绘图 |
| README.md、docs/contracts.md、本文件 | 更新用法、职责边界与验收说明 |

本阶段重点：**统计值与显示值分离**（零值裁切不修改原 padj），以及**分析与展示分离**
（绘图只能消费结果，不能触发上游统计）。改变图阈值只重新分类已有结果，不重算独立过滤或多重检验校正。

## 测试

```powershell
# 全部默认回归，不调用 R
.\.venv\Scripts\python.exe -m pytest

# 仅 Volcano 单元测试
.\.venv\Scripts\python.exe -m pytest tests/test_volcano.py

# 仅 Volcano 真实渲染，不执行 DEG/PCA，也不调用 DESeq2
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests/integration/test_real_volcano.py --run-r `
  --rscript Rscript
```

测试中的手写 DEG 行和模拟 PNG 明确属于故障注入夹具，不是分析结果。
真实渲染测试执行基础 R 并核对只调用 plot_volcano.R。源文件前后不变、分类总数、零值有限高度、
NA 行保留、输出文件生成、绘图数据未被渲染器改写、失败清理均有断言。

验收：上述测试通过；已有真实 DEG 输出可生成三项产物；PNG 轴、图例和标签可读；
summary 分类数量与 CSV 一致；原始 DEG 文件哈希保持不变；未运行 DESeq2。

### 本机验收记录（2026-09-11）

- 包版本 0.4.0，editable 安装已更新；未增加运行时依赖。
- 默认回归：135 passed、6 skipped（真实 R 测试默认跳过）。
- 单独开启 Volcano 真实基础 R 渲染测试：3 passed；没有运行 DEG/PCA 的真实统计测试。
- 示例输入：已有 `outputs/phase3_verified_20260910/deg_results.csv`。
- 示例输出：`outputs/volcano_phase42_demo`，status=succeeded，R exit code=0，stderr 为空。
- 真实输入共 30 行：Up=9、Down=9、NS=12，n_plotted=30、n_labels=10，与绘图 CSV 一致。
- 已视觉检查 PNG 的轴、图例、阈值线和标签。
- 源 DEG CSV 运行前后 SHA-256 一致：
  `a8e3ed6c8da49240d06a8f8d1c99b9c13746e236fbd1295d636e48f094681002`。
- 原 deseq2_deg.R 哈希与 Phase 3/4.1 一致，未修改统计逻辑。

这份已有 DEG 结果源自项目的模拟 count matrix 经真实 DESeq2 计算；火山图只展示这些已有数值，
不是新的 DEG 分析，也不构成真实生物学结论。验收进程临时清除环境注入的 LANG/LC_ALL/LC_CTYPE，
未修改系统区域设置；源代码没有覆盖用户的环境变量。

## 限制

- 仅固定 CSV 列名、PNG 和少量参数；不接收 count matrix，不做基因注释或自定义字段映射。
- 所有分类均相对于上游 contrast；不能从 CSV 单独识别处理组、对照组和真实生物学含义。
- 小幅标签分离仅适合少量标签，极长 ID 或大量标签仍可能重叠/裁切，可减少 top_n_labels。
- 零 padj 的显示高度由当前输入决定，不宜把不同图的零值点高度作数值比较。
- 不重算 padj、独立过滤或 log2FC shrinkage；更改绘图阈值不等于重新做不同参数的 DEG 分析。
- PNG 自动验证为结构检查，不是完整像素内容证明，正式展示还应检查图形。
- 仍需要基础 Rscript，但不需要为 Volcano 安装 DESeq2 或其他绘图库。

Phase 4.2 完成后停止，不进入 Heatmap。
