# Phase 4.3：Heatmap Skill

## 范围与复用设计

Heatmap 使用已有完整 DEG 结果选择基因，不重新执行差异检验，不重新计算 pvalue/padj。
表达输入必须二选一：已有 VST 矩阵，或 raw counts。不能把 TPM/FPKM、原始 counts 或仅做文库
大小归一化的 counts 冒充 VST 输入；程序可以检查数值，不能单凭 CSV 证明归一化来源。

PCA 原先把 VST 写在 transform_pca.R 内部。本次提取 vst_common.R，PCA 与 Heatmap 的 raw-count
路径共同调用它：排除全零基因、DESeqDataSet(~1)、默认 ratio size factors、
varianceStabilizingTransformation(blind=TRUE, fitType=parametric)。方法不变。
VST 使用完整可分析矩阵，不在选定的 Top DEGs 上重新拟合。

已有 VST 输入完全跳过该步骤，只调用基础 R 绘图。两种路径都导出 normalized_expression.csv，
便于以后改 top_n 时复用。公共 DEG 表读取从 volcano_data 移到 deg_data，旧导入仍兼容。
metadata 检查从公共 counts 验证中提取，DEG/PCA/Heatmap 共享同一套样本对应和缺失值规则。
没有新增 Python/R 包依赖。

## 数据流

```text
raw counts ──公共 blind VST──┐
                            ├→ 完整 VST 矩阵 → 与已有 DEG 结果取交集
已有 VST 矩阵 ──────────────┘                     ↓
                                     显著性/效应阈值筛选
                                              ↓
                              排序 → 排除恒定基因 → Top N
                                              ↓
                                    逐基因 Z-score
                                              ↓
                           基础 R 绘图 + metadata 分组注释
                                              ↓
                          核验产物 → 最后发布 summary.json
```

排序：padj 升序 → abs(log2FoldChange) 降序 → gene_id 升序（平局时保证确定性）。
默认 padj < 0.05、abs(log2FC) >= 1；log2FC=0 不选入，即使效应阈值设为 0。
padj=0 合法；DEG padj/log2FC 缺失行不参与筛选，但会记录数量。
基因 ID 必须精确匹配，不自动去版本后缀或转换 ID。

第一版不聚类，基因按上述排序，样本保留表达矩阵列顺序；metadata 允许换序，会按样本 ID 对齐。
分组只用于注释色条，不参与 VST、DEG 筛选或 Z-score 计算。

## VST 与热图缩放的区别

VST 是 RNA-seq 数据变换，降低表达均值与方差之间的依赖；它与 PCA 使用同一方法。
逐行 Z-score 是展示步骤：每个基因减去跨样本均值，再除以跨样本标准差（ddof=1）。
颜色表示该基因相对自身平均表达的高低，不能用于比较不同基因的绝对表达量。
它不替代 VST，也不改变 DEG 的 log2FC、pvalue 或 padj。

恒定基因的标准差为零，会跳过并用后续排名基因补足 Top N；仍不足时用实际数量并发出警告。
最终没有可绘制基因则明确失败，不生成空白成功图。支持一行基因的热图，不强行调用聚类。
参考 [DESeq2 变换与热图说明](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#heatmap-of-the-count-matrix)。

## 输入与参数

```python
run_heatmap(
    deg_result_file, metadata, group_column, output_dir,
    normalized_expression=None, raw_counts=None,
    top_n=50, padj_threshold=0.05, log2fc_threshold=1.0,
    timeout=600, rscript_executable="Rscript",
)
```

从 normalized_expression 开始，后续参数在实际 Python 接口中均为 keyword-only 参数。

- 两种表达路径必须且只能提供一种，所有路径使用 pathlib.Path。
- CSV 为 UTF-8，可带 BOM；表达矩阵第一列 gene_id，后续为唯一 sample ID，至少两个样本。
- VST 矩阵允许有限小数和负值，但不能有 NA、空值、Inf、非数字或重复 ID；全矩阵都会检查。
- raw counts 沿用公共的非负整数、R 整数范围、无缺失值和非零文库检查。
- metadata 需要 sample_id 和指定 group_column；样本集合必须完全一致。额外列也沿用无缺失规则。
- DEG 表需要 gene_id、log2FoldChange、pvalue、padj，复用 Volcano 的数值和缺失值规则。
- top_n 为正整数，不接受布尔值；0 < padj_threshold < 1；log2fc_threshold >= 0；timeout > 0。
- timeout 为每个 R 子进程的超时；raw 路径有 VST、绘图两个子进程，已有 VST 路径只有绘图。

错误使用 SkillError 和既有 JSON 信封，operation=heatmap。常见新增错误：
NO_COMMON_GENES、NO_SIGNIFICANT_GENES、NO_PLOTTABLE_GENES、EXPRESSION_FORMAT_ERROR、EXPRESSION_VALUE_INVALID。
样本不匹配、缺失值、组列不存在、非法参数、R 失败复用既有错误码。
CLI 失败返回非零退出码及 JSON，不暴露未处理 traceback。

## 如何运行

在项目根目录执行，不需要激活虚拟环境。使用已有 DEG CSV 和 raw counts：

```powershell
.\.venv\Scripts\python.exe main.py heatmap `
  --counts examples/data/counts.csv `
  --deg-result-file outputs/phase3_verified_20260910/deg_results.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --output-dir outputs/my_heatmap_01 `
  --top-n 50
```

复用本阶段示例已经产生的 VST，不再运行 normalization：

```powershell
.\.venv\Scripts\python.exe main.py heatmap `
  --normalized-expression outputs/heatmap_phase43_demo/normalized_expression.csv `
  --deg-result-file outputs/phase3_verified_20260910/deg_results.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --output-dir outputs/my_heatmap_reuse_01 `
  --top-n 10
```

如果 Rscript 不在当前终端 PATH，在命令末尾追加本机路径：

```powershell
--rscript Rscript
```

源码不硬编码此路径。每次使用新/空输出目录。Python 调用：

```python
from pathlib import Path
from bulk_rnaseq.skills import run_heatmap

result = run_heatmap(
    deg_result_file=Path("outputs/phase3_verified_20260910/deg_results.csv"),
    metadata=Path("examples/data/metadata.csv"),
    group_column="condition",
    output_dir=Path("outputs/my_heatmap_python_01"),
    normalized_expression=Path("outputs/heatmap_phase43_demo/normalized_expression.csv"),
    top_n=10,
)
result.to_dict()
```

## 输出

| 文件 | 作用 |
|---|---|
| heatmap.png | 实际行 Z-score 热图、基因/样本名称、分组色条、Z-score 色标；不聚类 |
| heatmap_matrix.csv | 实际上色数值；gene_id + 样本列；行顺序对应图中从上到下，列顺序从左到右 |
| selected_genes.csv | rank、gene_id、log2FoldChange、pvalue、padj；保留原始 DEG 四列值 |
| normalized_expression.csv | 完整 VST（raw 路径生成，已有 VST 路径保留输入快照）；未经行 Z-score |
| summary.json | status=succeeded、operation=heatmap、metrics、parameters、artifacts、warnings、provenance |
| run.log、r_stdout.log、r_stderr.log | 运行日志和各 R 阶段输出，后续阶段失败也保留前一阶段诊断 |
| failure.json | 失败且目录可用时的结构化错误；失败不发布成功摘要 |

metrics 包含 n_genes_requested、n_genes_used、n_samples、group_column、n_groups、交集数量、
显著候选数、可用数、缺失表达/恒定基因/缺失 DEG 统计值数量。sample_groups 保存最终样本→分组映射。
artifacts 路径相对 output_dir；结果对象提供绝对输出目录和 summary_json Path。
provenance 保存输入、所有使用的 R 脚本（含公共 VST）和产物的 SHA-256。
已有 VST 路径会提示 VST_INPUT_DECLARED，不伪造其生成软件或参数；raw 路径记录 DESeq2 版本和实际 VST 参数。

PNG 结构、渲染器报告的维度以及其未修改准备好的 CSV 均会检查。
图形仍需人工查看；“文件存在”不是对每个像素内容的证明。

## 文件变更

| 文件 | 职责/变更 |
|---|---|
| src/bulk_rnaseq/heatmap_data.py | 新增 VST 表验证、Top DEG 排序、常量行处理及 Z-score |
| src/bulk_rnaseq/skills/heatmap.py | 新增两种输入路径、分阶段进程日志、产物核验、统一摘要 |
| src/bulk_rnaseq/r_scripts/vst_common.R | 从 PCA 提取唯一公共 VST 实现 |
| src/bulk_rnaseq/r_scripts/normalize_vst.R | 新增可调用的 VST CSV 导出入口 |
| src/bulk_rnaseq/r_scripts/plot_heatmap.R | 新增纯绘图脚本，不做 DEG/VST 或聚类 |
| src/bulk_rnaseq/deg_data.py | 从 Volcano 提取公共 DEG 结果读取，规则不变 |
| src/bulk_rnaseq/volcano_data.py | 导入公共 DEG 读取，保留旧导入兼容性 |
| src/bulk_rnaseq/validation.py | 提取公共 metadata 验证，旧 counts 验证继续调用 |
| src/bulk_rnaseq/r_scripts/transform_pca.R、skills/pca.py | PCA 调用公共 VST，记录公共模块哈希；统计参数和输出含义不变 |
| src/bulk_rnaseq/models.py、skills/__init__.py | 增加并导出 HeatmapRunResult / run_heatmap |
| src/bulk_rnaseq/cli.py | heatmap 子命令、互斥表达输入、结构化错误归属 |
| tests/test_heatmap.py | 单元与故障注入测试 |
| tests/integration/test_real_heatmap.py | 真实 VST/绘图、已有 VST 复用、PCA 数值一致性、单基因热图 |
| src/bulk_rnaseq/__init__.py、pyproject.toml、tests/conftest.py | 版本 0.5.0 与测试依赖提示 |
| README.md、docs/contracts.md、本文件 | 使用说明、兼容说明、阶段验收 |

## 测试与验收

```powershell
# 全部默认回归（真实 R 测试明确跳过）
.\.venv\Scripts\python.exe -m pytest

# Heatmap 单元测试
.\.venv\Scripts\python.exe -m pytest tests/test_heatmap.py

# 开启 Heatmap/PCA/Volcano 真实回归，不重跑 DEG 差异检验
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests --run-r -k "not test_real_deg" `
  --rscript Rscript
```

验收：正常输入生成五项产物；无交集/样本不匹配/非法 top_n/表达 NA 被拒绝；
不足 Top N 正常输出且警告；没有可用基因明确失败；不会改写源文件或覆盖旧输出。
raw 路径和已导出 VST 的复用路径产生相同热图矩阵。PCA 坐标/解释方差与同一 VST 的
中心化矩阵特征关系一致；公共模块提取不改变原有统计方法。

## 本机验收记录（2026-09-11）

- 项目 0.5.0，Python 3.13.2、R 4.6.1、DESeq2 1.52.0；editable 安装已更新。
- 默认回归：163 passed、8 skipped（默认跳过真实 R 测试）。
- 开启真实 Heatmap/PCA/Volcano 回归：170 passed、1 deselected；未重跑 DEG 差异检验。
- 示例输出：outputs/heatmap_phase43_demo。输入为已有真实 DEG 结果和项目模拟 raw counts。
- 请求 50 个基因，实际使用 18 个；6 样本、2 组，完整 VST 30 基因。两个 R 阶段退出码均为 0，stderr 为空。
- 已视觉检查 heatmap.png：行列标签、分组注释和 Z-score 色标可读；图与 CSV 顺序一致。
- 输入 counts、metadata、DEG CSV 的当前哈希与运行时快照哈希一致，没有改写源文件。
- 共用 VST 提取后的 PCA 与 Phase 4.1 保存的解释方差一致：PC1=98.4012283956765%，PC2=1.11594639943625%。
- raw 输入和复用导出的 VST 输入生成相同 heatmap_matrix.csv，有真实集成测试验证。

真实 R 验收进程临时移除了环境注入的 LANG/LC_ALL/LC_CTYPE；没有更改系统区域设置或在源码中覆盖用户环境。
这些示例用于工程验证，不是真实实验的生物学结论。

## 当前限制

仅支持常规 gene-level CSV、固定方法和静态 PNG；无 batch correction、复杂 design、ID 转换或聚类参数。
两样本是展示缩放的最低条件，不保证 raw counts 足以拟合 VST。稀疏、极少基因或无变化数据仍可能失败。
blind VST 沿用 PCA，在强烈组间差异下可能压缩差异；本版不自动切换方法。
Top DEG 热图是基于已筛选差异基因的展示，不能把分组分离当作独立验证。
热图输入和 DEG 的实验来源是否一致，程序不能仅凭基因 ID 交集证明，需要用户核对来源。
极长名称、大量样本或大量基因未做复杂布局优化；可降低 top_n。

完成 Phase 4.3 后停止，不实现 enrichment。
