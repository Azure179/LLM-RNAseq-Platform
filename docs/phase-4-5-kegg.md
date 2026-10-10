# Phase 4.5：KEGG Enrichment Skill

本阶段增加离线 KEGG pathway over-representation analysis（ORA），不调用 DESeq2，不实现 GSEA、LLM 或在线 KEGG REST。
GO/KEGG 共享成熟 R `stats::fisher.test(alternative="greater")` 与 BH 校正实现，不手写超几何检验。

## 设计与数据流

```text
目标 gene list + 用户 gene-to-pathway mapping + 当前分析背景
→ 文件格式和命名空间验证
→ pathway 编号规范化、关联去重（gene ID 不变）
→ 有效 query / universe / pathway 基因集合
→ Python 调用 Rscript，共享 ORA 检验和绘图
→ 输出计数、命中基因、概率、筛选规则和 PNG 验收
→ 发布 CSV、PNG、summary.json
```

第一版只有**用户映射模式**。不要求 human/mouse，不将任意输入 gene ID 当成 KEGG gene ID。
不实现 organism/keyType 自动查询，也不从单独 KO ID 猜测 pathway。
已经有可靠 organism 注释的用户也应先导出明确 gene-to-pathway 映射，再使用此接口。

## 输入格式

### gene_list

- UTF-8 文本每行一个 ID，允许 `gene_id` 表头；或 CSV/TSV 的 `gene_id` 列。
- 已有 DEG 的 `deg_filtered.csv` 可以直接作为目标列表。
- 不再次按 DEG 阈值筛选。完整 `deg_results.csv` 应作为分析背景，而不是误当成前景。
- ID 原样精确匹配；保留前缀和 `.1` 版本后缀。缺失、首尾空白、控制字符和分号被拒绝。
- 转录本/蛋白 ID 与 gene-level ID 不一致时，调用者负责提供真实转换关系。本版不自动折叠或猜测。

### 自定义 gene-to-pathway mapping

CSV/TSV/XLSX，必需列 `gene_id`、`pathway_id`，可选 `pathway_name`。
**每行一个关联**，同一基因可多行；重复关联只计一次。

```text
gene_id	pathway_id
geneA	map00010
geneA	map00020
geneB	map00010
```

名称必须来自真实注释。没有名称时输出/图中使用 ID，不凭记忆或 AI 编造名称。
同一个 canonical pathway 的不同非空名称发生冲突时，返回 `MAPPING_CONFLICT`。
空、NA、`-` pathway 视为未注释并记录警告；其他非法编号报错，不静默忽略。

### eggNOG-mapper

支持原始 `.emapper.annotations`（Tab 分隔）、TSV、CSV 和 XLSX。

- `query` / `#query` → 项目 gene ID。
- `KEGG_Pathway` → pathway，逗号或分号分隔多值。
- 跳过开头注释行，识别真正的 `#query` 表头。
- XLSX 只读；单工作表自动选择，多工作表使用 `--mapping-sheet` 明确选择。
- `KEGG_ko` 是 KO 注释，**不是 pathway 列**。只有它而没有 KEGG_Pathway 时返回 `PATHWAY_MAPPING_REQUIRED`。
- 某一行有 KO、但 KEGG_Pathway 为 `-`，仍是“没有 pathway 映射”。
- `Description` 描述蛋白功能，不拿它命名 pathway。

此前提供的黑鲷 Excel 已完整只读验证：Sheet1 第 3 行表头，A 列为 query，M 列为 KEGG_Pathway。
解析 35,951 数据行，19,972 行无 pathway；参考编号统一后有 83,294 个唯一关联、393 个 pathway。
这是格式兼容验证，**不是对黑鲷的真实 DEG 做了富集分析**。

## Pathway ID 映射规则

KEGG pathway ID 由前缀和五位数字构成；`map` 是 reference map，`ko` 是突出 KO 的 reference map，
物种前缀表示对应物种的 pathway。见 [KEGG PATHWAY 官方说明](https://www.kegg.jp/kegg/pathway.html)。

为避免 eggNOG 将同一编号的 `ko` / `map` 两种表示作为两个假设检验，本项目作以下明确约定：

| 输入 | 本项目 canonical ID | 处理 |
|---|---|---|
| `map00620` | `map00620` | 保留 |
| `ko00620` | `map00620` | reference pathway 统一表示 |
| `path:map00620` | `map00620` | 去除数据库前缀 |
| `path:ko00620` | `map00620` | 去前缀并统一表示 |
| `hsa00010` | `hsa00010` | 保留物种前缀，不改成 map |
| `K01512` / `ko:K01512` | 无 | 拒绝：KO 不是 pathway |
| `M00001`、裸数字 `00010` | 无 | 拒绝：module 或不明确编号 |

原始 ID 与 canonical ID 对应关系保存到 `pathway_id_aliases.csv`。
同一基因同一 canonical pathway 只计一次；不把多个 KO 当成多个基因。
同次分析允许 reference `map/ko`，或**一个**物种前缀；拒绝 reference 与物种混合以及多个物种混合。
本版不接受 EC/reaction pathway 表示。编号验证是格式/命名空间验证，不联网验证编号是否有效、过时或名称是否准确。
物种前缀原样保留不代表已证明用户基因来自该物种，这仍是调用方责任。

## Background universe

二选一，不能为空：

- `--universe`：用户明确声明的、参与当前 RNA-seq 分析且有资格进入目标列表的基因。
- `--analysis-genes`：当前分析基因列表，或标准完整 DEG CSV。
  完整 DEG CSV 保留有限 padj/log2FC 的行，排除不能按 padj 进入目标列表的行；不是仅保留显著 DEG。

随后计算：

```text
有效 universe = 声明的分析背景 ∩ 有 pathway 映射的基因
有效 query    = 目标列表 ∩ 有 pathway 映射的基因
```

所有目标基因必须先属于声明背景，否则 `GENES_OUTSIDE_UNIVERSE`。
目标与映射没有交集返回 `NO_MAPPED_GENES`。映射整体无关联返回 `NO_PATHWAY_ASSOCIATIONS`。
未注释的前景和背景基因被排除，数量记录在 summary 中。
没有背景时报 `BACKGROUND_REQUIRED`，不默认整个物种注释，也不把目标列表自动当成背景。
前景与背景完全相同时可运行，但无法得到过度富集；返回正常空结果。

显式 universe 应有真实的入选资格。若前景不是按 DEG padj 选出，应自行提供对应的 eligible universe。

## 统计定义

R 对每个有效背景 pathway 构建 2×2 列联表，调用单侧 Fisher 检验：

| | 在 pathway 内 | 不在 pathway 内 |
|---|---:|---:|
| query | k | n−k |
| universe 中非 query | M−k | N−M−n+k |

N = 有效背景数量，n = 实际参与富集的目标数量，M = 背景内 pathway 基因数量，k = 命中数量。
BH 校正包括背景中**所有 pathway，含零命中的 pathway**。
不默认过滤 pathway 大小或剔除 overview maps；这些较宽泛的通路保留并参与校正。

默认 `padj_threshold=0.05`，可选 `pvalue_threshold=1.0`（默认不另加原始 p 限制）。
筛选为 `padj <= threshold AND pvalue <= threshold AND k > 0`；阈值为有限数且在 [0,1] 内。
不同于 GO 的默认双 0.05，这是本阶段“以 padj 为主要门槛”的明确接口约定。

## 输出与成功判断

| 文件 | 用途 |
|---|---|
| `kegg_enrichment.csv` | 通过阈值的富集结果，按 padj、pvalue、pathway ID 排序 |
| `kegg_all_results.csv` | 全部实际检验的 pathway 及 BH 结果 |
| `kegg_dotplot.png` | 前 20 个显著 pathway；x=gene ratio，颜色=-log10(padj)，点大小随命中数增加 |
| `kegg_barplot.png` | 同批 pathway 的命中基因数 |
| `query_used.csv` | 实际参与富集的目标基因 |
| `universe_used.csv` | 实际使用的背景基因 |
| `mapping_used.csv` | 有效背景中的规范化映射 |
| `pathway_id_aliases.csv` | 整份输入中原始 pathway ID 到 canonical ID 的对应关系 |
| `summary.json` | 统一成功契约、输入、参数、方法、metrics、警告、版本和 hash |

CSV 列为 `pathway_id, description, overlap_count, query_size, term_size, universe_size, gene_ratio, bg_ratio, fold_enrichment, pvalue, padj, genes`。
gene_ratio=k/n，bg_ratio=M/N，fold_enrichment=(k/n)/(M/N)；genes 用分号分隔。
缺少名称时 description=pathway_id，并记录 `PATHWAY_NAMES_UNAVAILABLE`。
0 padj 仅在图的 -log10 显示时限制范围，原始 CSV 概率不变。

结果信封沿用 `schema_version=1.0`、`status="succeeded"`、`operation="kegg_enrichment"`、`artifacts`、`error=null`。
重要 metrics：`n_query_input`, `n_query_mapped`, `n_background_input`, `n_universe`,
`n_query_unmapped`, `n_background_unmapped`, `n_terms_tested`, `n_enriched`。

R 返回码、stdout、stderr 均检查/保留。输出计数、命中 ID、比例、阈值筛选、PNG 和输入 hash 必须匹配。
没有显著 pathway 是成功：CSV 保留表头，`n_enriched=0`，两张说明图与 `NO_ENRICHED_TERMS` 警告。
执行或验收失败通过现有 SkillError 返回结构化错误，并保留日志和 failure.json，不发布成功 summary。
同目录只允许一次运行，避免覆盖和把旧结果当作新成功。

## 如何运行

项目根目录 PowerShell，无需激活虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe main.py kegg --help

# 明确为模拟 gene/pathway 关联，只用于验证，不是真实注释。
.\.venv\Scripts\python.exe main.py kegg `
  --gene-list examples/kegg/synthetic_gene_list.txt `
  --gene-to-pathway-mapping examples/kegg/synthetic_mapping.tsv `
  --universe examples/kegg/synthetic_universe.txt `
  --output-dir outputs/my_kegg_demo
```

真实数据示例（替换为自己的文件）：

```powershell
.\.venv\Scripts\python.exe main.py kegg `
  --gene-list outputs/my_deg/deg_filtered.csv `
  --analysis-genes outputs/my_deg/deg_results.csv `
  --gene-to-pathway-mapping data/my_species.emapper.annotations.xlsx `
  --padj-threshold 0.05 `
  --output-dir outputs/my_kegg
```

每次使用新的输出目录。Rscript 不在 PATH 时，追加 `--rscript "你的Rscript.exe完整路径"`。
不要将项目模拟 gene ID 与真实黑鲷 query 混用。

Python 独立调用：

```python
from pathlib import Path
from bulk_rnaseq.skills import run_kegg_enrichment

result = run_kegg_enrichment(
    gene_list=Path("outputs/my_deg/deg_filtered.csv"),
    gene_to_pathway_mapping=Path("data/my_mapping.tsv"),
    analysis_genes=Path("outputs/my_deg/deg_results.csv"),
    output_dir=Path("outputs/my_kegg_python"),
)
payload = result.to_dict()
```

## 测试与验收

```powershell
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests -q
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests/integration/test_real_kegg.py -q --run-r
# 全部真实 R 回归（同时需要已有 DESeq2）
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests -q --run-r
```

以上 R 测试同样可追加 `--rscript`。不启用 --run-r 的测试包含明确模拟的进程结果，不能代替真实统计测试。

验收要求：

- 正常与空结果均生成指定文件，输入不变。
- 无映射、错误格式、缺文件、不合理背景、非法参数在调用 R 前被拒绝。
- KO 不被误当 pathway，ko/map 重复不膨胀计数或多重检验数量。
- 真实 R 对模拟 20 个背景/5 个目标给出已知精确 p=1/15504，3 个 pathway 的 BH=3/15504。
- 验证 R 失败、缺少输出、错误计数、坏 PNG、准备数据被改动时不返回成功。
- 原 DEG/PCA/Volcano/Heatmap/GO 回归通过。

2026-09-14 本机验收：完整真实 R 回归 **246 passed**，`pip check` 无依赖冲突，CLI 版本 0.7.0。
DEG 核心 R 脚本 SHA256 与修改前一致。
正常与空结果示例分别在 `outputs/phase45_kegg_verified_20260914`、`outputs/phase45_kegg_empty_20260914`，
已目视检查点图、条形图及空结果说明图。
本机 R 有 `C.UTF-8` locale 启动警告，返回码为 0 且产物验收通过；警告保留于 stderr 日志和 summary，未改动全局 locale。

## 文件与本阶段新增工程概念

- `kegg_mapping.py`：KEGG 特有输入适配、ID 验证、规范化与命名空间隔离。
- `skills/kegg_enrichment.py`：独立 KEGG 运行编排与 summary。
- `r_scripts/kegg_enrichment.R`：轻量 R 入口。
- `enrichment_inputs.py`：从 GO 提取的文件读取、gene list、参数和背景准备；GO 保留兼容导入。
- `enrichment_artifacts.py`：GO/KEGG 公共统计产物验收；各 Skill 保留独立列名约定。
- `r_scripts/enrichment_common.R`：唯一的 Fisher/BH 和基础绘图实现；GO R 入口改为调用它。
- GO 对应文件只作上述复用调整，并将公共 R 脚本 hash 纳入溯源；不改变 DEG 核心统计。
- `models.py`, `cli.py`, `skills/__init__.py`：统一结果、命令行与公共接口。
- `pyproject.toml`, `__init__.py`：版本 0.7.0；没有新增第三方依赖。
- `tests/test_kegg.py`, `tests/integration/test_real_kegg.py`：故障契约与真实 R 测试。
- `examples/kegg/`：三份明确标注的模拟输入；README、contracts 与本文同步更新。

重点理解：**命名空间不是一个可随意删除的前缀**；只对明确等价的 reference 表示做统一。
**共享实现保留独立契约**：GO 与 KEGG 共用检验，而不是让 KEGG 假装是 GO。
**转换需要可审计**：保存 canonical 映射和实际背景，不能只留下图片。

## 当前限制

- 完全离线：没有 KEGG REST、KO-to-pathway 自动补全、数据库版本同步或名称自动查询。
- 只能验证 ID 格式和一致性，不证明物种、基因注释或 pathway 名称正确。
- 无基因长度/检测概率选择偏差校正，不做通路冗余合并，不自动排除全局 overview maps。
- 分析单位是用户给出的 gene ID；上/下调混合列表的 ORA 不能证明 pathway 激活或抑制。
- 固定前 20 个 term 的静态基础图，大型关联表仍需要内存。
- Phase 4.5 完成后停止，不进入 GSEA 或 LLM 阶段。
