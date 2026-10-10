# Phase 4.4：GO Enrichment Skill

本阶段只实现 GO over-representation analysis（ORA）。不重跑 DESeq2，不实现 KEGG、GSEA、Agent 或 API。
统计由真实 R 程序完成；Python 负责输入契约、背景构建、调用和输出验收。

## 1. 为什么采用这个设计

- 面向非模式物种：优先消费用户 gene-to-GO mapping，绝不默认 human/mouse。
- 标准 organism annotation package 是另一种注释来源，本版**不提供其自动加载接口**。
  如果使用这类数据库，先导出匹配本次 gene ID 命名空间的自定义映射，再使用相同接口。
- 本版使用 R 自带 `stats::fisher.test(alternative="greater", conf.int=FALSE)`，不安装 clusterProfiler。
  对 2×2 列联表，它通过超几何分布计算单侧过度富集概率，是用户允许的成熟等效检验实现；不是自写超几何算法。
  多重校正调用 `stats::p.adjust(method="BH")`。
  [R Fisher 文档](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/fisher.test.html)、
  [R p.adjust 文档](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/p.adjust.html)。
- 不宣称与 clusterProfiler 默认结果逐行相同：本版不使用其默认 gene-set size filter，且明确定义自己的 BH family。
- 仅新增 Python 依赖 `openpyxl`，用于只读 XLSX；R 侧只使用自带包。

## 2. 输入格式

### 目标基因 gene_list

支持 UTF-8 `.txt`（每行一个 ID，可有 `gene_id` 表头），或 CSV/TSV 中的 `gene_id` 列。
可以直接传已有 `deg_filtered.csv`。这是**已经选定的列表**：本 Skill 不再按 DEG padj/log2FC 筛选。
不要把完整 `deg_results.csv` 当作前景，除非真的想把所有行作为目标。
GO 的 pvalue/padj 参数只控制**富集结果**，不是 DEG 选择阈值。

ID 原样匹配，不删除 `rna-` 前缀，不删除 `.1` 后缀，不做 gene symbol/转录本 ID 转换。
ID 必须是非空文本，无首尾空白、制表符、换行或分号；输出用分号连接命中 ID。
重复目标 ID 去重并警告；空列表明确报错。

### 自定义 gene-to-GO mapping

推荐 UTF-8 TSV（CSV 也支持），一行一个关联：

```text
gene_id	go_id
geneA	GO:0006950
geneA	GO:0006281
geneB	GO:0006950
```

可选列：`ontology`（BP/MF/CC）与 `go_name`（GO term 名称）。
同一个 term 的非空名称/ontology 必须一致。重复 gene/GO pair 去重。
`go_id` 为空、NA 或 `-` 视为未注释，跳过并记录数量；其他非法 GO 值报错。
GO ID 校验规则是 `GO:` + 7 位数字。**本版不联网核实 term 是否存在、是否过时，也不检查用户提供的 GO 名称是否准确。**

### eggNOG-mapper

兼容原始 `.emapper.annotations`、TSV（Tab 分隔）、CSV，以及 `.xlsx`。

- 使用 `query` 或 `#query` 作为本项目 gene ID。
- 使用 `GOs` 列，以逗号或分号拆分多 GO；`-` 表示未注释。
- 自动跳过开头 `#`/`##` 说明行，但保留真正的 `#query` 表头。
- XLSX 采用只读模式；单工作表自动选择，多工作表必须传 `--mapping-sheet`。
- 不使用 `seed_ortholog`、`Preferred_name` 替换 query。
- 不将 eggNOG 的蛋白质 `Description` 错当成 GO term 名称。
- eggNOG 中 query 如果是转录本/蛋白 ID，而 DEG 是 gene-level ID，需要用户提供明确转换关系；不做猜测或自动合并。

已只读验证用户提供的 workbook：Sheet1 前两行是说明，第 3 行是表头，`query` 在 A 列，`GOs` 在 J 列。
完整解析得到 35,951 条数据行、4,191,346 个唯一关联、23,492 个不同 GO ID，其中 8,244 行没有 GO。
这只是格式兼容性验证，**不是对黑鲷做了富集分析**，也不代表已验证注释的生物学正确性。

### ontology

默认 `ALL`，对有效背景中的全部 term 共同 BH 校正。
BP/MF/CC 必须有完整 `ontology` 列信息；原始 eggNOG GOs 本身无法表达 ontology，因此这类输入使用 ALL。
缺失 ontology 时指定 BP/MF/CC 会报 `ONTOLOGY_INFORMATION_REQUIRED`，不会悄悄丢弃不明确的 term。
GO 名称缺失时使用 GO ID 作图，ontology 记为 `UNKNOWN`，不会猜测补全。

## 3. 背景 universe：必须明确

以下二选一：

1. `--universe`：用户明确提供本次可入选的分析背景基因列表。
2. `--analysis-genes`：提供当前分析基因列表，或已有完整 `deg_results.csv`。
   对标准完整 DEG CSV，复用 DEG reader，保留有限 `padj` 和 `log2FoldChange` 的行。
   这会排除未检验/独立过滤等产生 NA、无法按 padj 入选的基因；不会仅保留显著 DEG。
   对普通 ID 列表，用户负责确认它们确实有资格进入目标列表。

随后只保留所选 ontology 下有 GO 注释的基因：

```text
有效 universe = 声明的可入选背景 ∩ 所选 ontology 有 GO 注释的基因
有效 query    = 用户目标基因 ∩ 所选 ontology 有 GO 注释的基因
```

所有目标 ID 必须先属于声明背景，否则 `GENES_OUTSIDE_UNIVERSE`。注释不全的目标/背景基因被排除并报告数量。
目标与映射没有交集时报 `NO_MAPPED_GENES`。
只给 gene list 和 mapping **不能推断当前 RNA-seq 分析背景**，因此缺少上述两个参数时报 `BACKGROUND_REQUIRED`。
不会默认整个物种的所有注释基因，也不会把目标列表自身偷换成背景。

注意：显式 universe 也应是**有资格被选为目标的基因**，而非全部基因组。
若你用 pvalue 而非 padj 选择前景，应自行提供相应的 eligible universe；`--analysis-genes` 完整 DEG 模式针对 padj 选择策略。

## 4. 统计与输出

对每个背景中出现的 GO term，R 构建以下列联表并调用单侧 Fisher 检验：

| | 属于 term | 不属于 term |
|---|---:|---:|
| query | k | n−k |
| universe 中非 query | M−k | N−M−n+k |

N 为有效背景基因数，n 为有效目标数，M 为背景内 term 基因数，k 为命中数。
BH 校正覆盖**所有背景 term（含 k=0）**。筛选条件：`pvalue <= pvalue_threshold AND padj <= padj_threshold AND k > 0`。
默认两个阈值都是 0.05，支持闭区间 [0,1]；不限制 term 大小，不自动扩展 GO ancestors，不移除 GO 根节点。
因此映射是直接注释还是已经传播到祖先的注释，会影响结果，调用方应明确并保持一致。

| 文件 | 含义 |
|---|---|
| `go_enrichment.csv` | 通过双阈值的 term；按 padj、pvalue、GO ID 升序 |
| `go_all_results.csv` | 全部检验及 BH 结果，方便检查空结果和多重检验范围 |
| `go_dotplot.png` | 前 20 个显著 term；x=gene ratio，点大小随命中数增加，颜色=-log10(BH) |
| `go_barplot.png` | 同一批 term 的目标命中基因数 |
| `query_used.csv` | 实际参与统计的前景 ID |
| `universe_used.csv` | 实际参与统计的背景 ID |
| `mapping_used.csv` | 背景内实际 GO 映射 |
| `summary.json` | 统一成功契约、参数、方法、背景来源、数量、警告、版本和 SHA256 |
| `run.log`, `r_stdout.log`, `r_stderr.log` | 运行与外部进程诊断 |

表格列：`go_id, description, ontology, overlap_count, query_size, term_size, universe_size, gene_ratio, bg_ratio, fold_enrichment, pvalue, padj, genes`。
gene_ratio=k/n，bg_ratio=M/N，fold_enrichment=(k/n)/(M/N)。`genes` 用分号连接。
绘图对 0 padj 的 -log10 显示值设置有限下限，**不修改 CSV 中的概率**。

空富集是成功状态：表头保留、`n_enriched=0`、警告 `NO_ENRICHED_TERMS`、两张解释性说明图。
格式错误/执行失败是失败状态：结构化 SkillError，日志与 `failure.json`，不发布成功 summary 或半套结果。

## 5. 运行

在项目根目录执行，无需激活虚拟环境，避免 PowerShell Activate.ps1 策略问题：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe main.py go --help

# 明确为模拟关联，只验证软件与统计检验，不作生物学解释。
.\.venv\Scripts\python.exe main.py go `
  --gene-list examples/go/synthetic_gene_list.txt `
  --gene-to-go-mapping examples/go/synthetic_mapping.tsv `
  --universe examples/go/synthetic_universe.txt `
  --output-dir outputs/my_go_demo
```

输出目录必须新建或为空，每次运行换一个目录；若 Rscript 不在 PATH，追加 `--rscript "你的Rscript.exe路径"`。

对你自己的真实数据（替换示例路径）：

```powershell
.\.venv\Scripts\python.exe main.py go `
  --gene-list outputs/my_deg/deg_filtered.csv `
  --analysis-genes outputs/my_deg/deg_results.csv `
  --gene-to-go-mapping data/my_species.emapper.annotations.xlsx `
  --ontology ALL `
  --output-dir outputs/my_go
```

不要把项目模拟基因 g1/g2 或 gene_001 与黑鲷 rna-Asc... 注释混用。

Python：

```python
from pathlib import Path
from bulk_rnaseq.skills import run_go_enrichment

result = run_go_enrichment(
    gene_list=Path("outputs/my_deg/deg_filtered.csv"),
    gene_to_go_mapping=Path("data/my_mapping.tsv"),
    analysis_genes=Path("outputs/my_deg/deg_results.csv"),
    output_dir=Path("outputs/my_go_python"),
)
payload = result.to_dict()
```

## 6. 测试与验收

```powershell
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests -q
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests/integration/test_real_go.py -q --run-r
```

必要时测试命令同样追加 `--rscript "你的Rscript.exe路径"`。

- 真实 R 正常输入生成全部文件；不调用 DESeq2。
- 20 个背景/5 个目标的模拟关联：指定 5 个基因的精确 p=1/15504，3 个 term 的 BH=3/15504。
- 无映射、缺列、非法 GO、缺文件、错误背景、错误参数必须在调用 R 前被拒绝。
- R 非零退出、缺少输出、计数不符、PNG 损坏或准备数据被改动，不能被判定成功。
- 空富集是正常成功，不是异常；输入文件保持不变。
- 原 DEG/PCA/Volcano/Heatmap 测试继续通过。

2026-09-12 本机验收：Python 3.13.2 / R 4.6.1 / openpyxl 3.1.5，
完整 `pytest -c pyproject.toml tests --run-r --rscript ...`：**209 passed**；`pip check` 无依赖冲突。
正常与空结果示例分别保存于 `outputs/phase44_go_verified_20260912`、`outputs/phase44_go_empty_20260912`，
已目视检查 PNG。原 DEG 核心 R 脚本 SHA256 未变。
本机 R 可能报告 `Setting LC_*=C.UTF-8 failed` 启动警告：这次进程返回 0 且产物验收通过，
警告保留在 `r_stderr.log` 和 summary 的 `R_DIAGNOSTICS` 中；未改动全局 locale。

## 7. 本阶段新增工程概念与文件

重点理解**输入适配器**（不同文件格式 → 同一映射）、**背景契约**（显式分母）、
**统计输出验收**（计数/阈值/文件与真实输入一致）与**溯源**（实际集合、版本、hash）。不需要逐行记忆 R 绘图代码。

- `go_mapping.py`：只读文本/XLSX 适配、注释格式与 ID 校验。
- `go_data.py`：gene list、参数、有效 query/universe 准备。
- `go_artifacts.py`：统计产物结构、分母、命中基因、PNG 和进程摘要验收。
- `skills/go_enrichment.py`：复用运行生命周期编排，不在 Python 计算检验概率。
- `r_scripts/go_enrichment.R`：真实 Fisher/BH、完整/筛选表、两张图。
- `models.py`, `cli.py`, `skills/__init__.py`：增加统一结果类型、go 命令、公开函数。
- `pyproject.toml`, `requirements.txt`, `__init__.py`：Excel 依赖及 0.6.0 版本。
- `tests/test_go.py`, `tests/integration/test_real_go.py`：契约/故障模拟与真实 R 检验。
- `examples/go/`：明确标注的模拟前景、背景和映射；**不是实际基因注释**。
- 本文、README 和 contracts：格式、科学边界、操作说明。

## 8. 当前限制

- 不提供标准 organism package 自动加载、在线 GO 查询、GO DAG 传播、过时 ID 修复。
- BP/MF/CC 需要用户补充 ontology；eggNOG 原始 GOs 默认 ALL。
- 不做 GO 冗余简化、长度/表达检测概率偏差校正，也不解决 ORA 的基因选择偏差；正式研究应评估这些假设。
- 输入 ID 必须同一物种、同一注释版本、同一分析单位；不自动将转录本合并成基因。
- 上下调一起输入意味着“整体 DEG 富集”，不能据此推断通路激活/抑制；需要方向解释时分别提供上调/下调列表。
- XLSX 只读流式扫描，但关联集合与 R 映射仍需载入内存；数百万关联需足够内存，尚未做大规模性能验收。
- 图为静态基础图，显示最多 20 个 term；完整结果以 CSV 为准。

Phase 4.4 完成后停止。未实现 KEGG、GSEA 或其他后续阶段。
