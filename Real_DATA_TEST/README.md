# Phase 4 真实数据集成测试

这是现有六个 Skill 的独立集成测试，不是新分析模块，不接入 LLM。
首次测试的表达分析已运行，GO/KEGG 当时因 gene ID 无交集返回结构化错误，历史事实保留于 `REPORT.md`。后续已按用户指定的 Asc 命名规则完成映射，并成功重跑 GO/KEGG，见下节。

## 最新：GO / KEGG 已成功重跑

仅复用首次真实 DEG，未重跑真实数据的 DESeq2。输入仍是 13,861 个 DEG，背景候选仍是完整 DEG 中统计可用的 24,206 个基因。

| 分析 | 映射后 query | 有效 universe | 检验条目 | 显著条目 | 成功目录 |
|---|---:|---:|---:|---:|---|
| GO BP | 9,757 | 16,072 | 14,404 | 70 | `results/asc_mapped/05_go_retry/` |
| KEGG | 5,896 | 10,002 | 393 | 10 | `results/asc_mapped/06_kegg/` |

GO pvalue/padj <= 0.05，KEGG padj <= 0.05；使用已有 R Fisher 单侧检验及 BH，不更改统计方法。两项成功目录都有 enrichment CSV、all_results CSV、dotplot、barplot、query_used、universe_used、mapping_used 和 summary。输出和来源哈希已复核，四张图已查看。
总记录：`results/asc_mapped/enrichment_rerun_summary.json`，明确保留命名规则来源和 `reference_validated=false`。

这次 GO 首次重跑暴露了 Python CSV 默认 128 KiB 字段限制：包含许多重叠基因的单元格过长，而 R 实际计算成功。已修复公共 `enrichment_artifacts.py`，基于实际 query ID 和 term metadata 长度设置临时读取上限，读取后恢复，并保留原有成员、比例、概率及过滤校验。没有截断基因、跳过验收或修改 R 脚本。
该次失败仍保留在 `results/asc_mapped/05_go/`，修复后的结果使用 `05_go_retry/`，未覆盖失败记录。
新增 `tests/test_enrichment_large_fields.py`，覆盖 GO/KEGG 的正常大字段、错误成员及异常超长字段；完整回归 **252 passed**（含实际 R 小数据测试）。

## 后续补充：用户指定的 Asc ID 映射

新增 `build_asc_crosswalk.py`：严格匹配 `rna-(Asc[0-9]+)\.([1-9][0-9]*)`，转换为 `gene-` 加捕获的 Asc ID，保留前导零，并要求目标在 count matrix 中存在。
例如 `rna-Asc00001.1` 和 `rna-Asc00001.2` 都对应 `gene-Asc00001`。这遵循用户选择的命名约定，记录 `basis=user_requested_name_rule`、`reference_validated=false`，不是 GTF/GFF 验证结果。

- `input/asc_crosswalk/gene_id_mapping.tsv`：可复用的两列 `gene_id/query` 对照表。
- 同目录 `query_mapping_audit.tsv`：每个 query 的候选 ID、匹配状态；`multi_transcript_genes.tsv`：多转录本基因；`count_genes_without_query.tsv`：未覆盖的 count genes；`summary.json`：规则、数量、源/输出哈希。
- `input/prepared_asc/`：新标准 count、metadata、GO/KEGG mapping、准备记录及 `mapping_coverage.json`。原来的 `input/` 派生文件不覆盖。
- `config/real_test_config.asc_mapped.json`：新配置，连接上述 crosswalk 和新标准输入，预留结果目录 `results/asc_mapped/`。原配置不变。

实际匹配 35,951 / 35,951 个 query，覆盖 22,812 / 24,581 个 count gene（92.80%）；7,158 个基因有多个 query，1,769 个 count gene 没有 query。目标不存在、格式不符均为 0，重复 query 会被拒绝。
多个转录本的 GO/KEGG 关联由现有 reader 合并并去重，不累加 counts，不把同一基因重复计入 query/universe。

| 映射 | count gene 覆盖 | 当前 DEG 覆盖 |
|---|---|---|
| GO BP（沿用当前 ontology） | 16,187 / 24,581（65.85%） | 9,757 / 13,861（70.39%） |
| KEGG | 10,100 / 24,581（41.09%） | 5,896 / 13,861（42.54%） |

ID 对应成功不表示每个基因都有 GO/KEGG 注释；不会为了提高覆盖率补造 term/pathway。原有 GO ontology 的未解析/obsolete 策略未变。

对照表和新标准输入已经生成，不要再次执行生成命令覆盖它们。之后运行新配置：

```powershell
.venv/Scripts/python.exe Real_DATA_TEST/run_real_data_test.py --config config/real_test_config.asc_mapped.json --reuse-prepared --results-dir results/asc_mapped_full_02
```

如终端找不到 Rscript，追加 `--rscript "Rscript.exe的实际路径"`。此完整分析命令未执行；本次只单独重跑了 GO/KEGG。已有 `results/asc_mapped/` 不为空，完整重跑必须使用新的输出目录。总 summary 会明确记录 `NAME_RULE_MAPPING` warning。
新增规则测试包含前导零、多转录本、错误格式、目标不存在、重复 query 和原文件不变；独立自检共 15 项。
新文件已通过现有 GO/KEGG reader 和 query/background 准备检查：GO BP 有效 query=9,757、universe=16,072；KEGG 有效 query=5,896、universe=10,002。这些是检验前输入数量，不是显著结果。原有 Python 回归本次 229 passed，17 个需要 R 的用例未运行。

## 文件职责

| 文件/目录 | 用途 |
|---|---|
| `run_real_data_test.py` | 固定顺序调用现有 Skill、依赖失败隔离、汇总日志/时间/指标/输出校验 |
| `prepare_inputs.py` | 此数据集的格式适配、两组样本选择、调用现有 mapping readers；不实现统计检验 |
| `config/real_test_config.json` | 相对路径及分析参数；JSON 使用标准库，不增加 YAML 依赖 |
| `self_check.py` | 独立 runner 控制流自检；测试替身只在临时目录使用，不伪装成真实分析结果 |
| `verify_real_outputs.py` | 只读检查真实输出、文件哈希、DEG/Volcano 分类、Heatmap VST Z-score 和重复运行一致性 |
| `input/` | 原始文件、派生的标准输入、准备记录、GO ontology 与映射审计 |
| `results/` | 首次真实运行的全部输出；`recheck/` 是独立复跑，未覆盖首次结果 |
| `logs/real_data_test.log` | 汇总追加日志；各 Skill 另有 `run.log`、R stdout/stderr 或 `failure.json` |
| `REPORT.md` | 本次真实运行的结果、问题和 Phase 5 readiness |
| `.gitignore` | 默认不提交真实数据、运行结果和日志；不删除本地文件 |

## 输入适配：保留原文件

- `gene_count_matrix.xls` 实为 UTF-8 制表符文本。仅将表头 `Geneid` 标准化为 `gene_id`，不改 gene/sample ID，不舍入 counts。
- 用户提供的 `metadata.csv` 实为 XLSX（Sheet1），没有表头，只有样本、分组两列。用已有 openpyxl 只读读取。
- 为避免覆盖原始 `metadata.csv`，标准文本副本命名为 `metadata.normalized.csv`。
- 全部 9 个样本保留在 `count_matrix.all_samples.csv` / `metadata.all_samples.csv`，并先完成公共验证。
- 当前配置显式选择 OT/T 各 3 个样本，输出 `count_matrix.csv` / `metadata.normalized.csv`；O 组不属于本次两组比较。
- 标准 metadata 包含 `sample_id,group,condition`。`condition` 与 `group` 值相同，仅兼容现有 DEG 固定接口。
- 默认参数：DEG padj < 0.05 且 abs(log2FC) >= 1；Heatmap top 50；GO BP，pvalue/padj <= 0.05；KEGG padj <= 0.05。

`input/preparation.json` 记录原始/派生文件 SHA256、完整样本/基因检查结果及准备配置。已有派生输入拒绝覆盖。

## 注释来源、ID 和背景

GO 使用 eggNOG 的 `query/GOs`，标准映射输出列：`gene_id,go_id,ontology,go_name`（TSV）。
KEGG 使用 eggNOG 的 `query/KEGG_Pathway`，输出列：`gene_id,pathway_id,pathway_name`（TSV）。
两者均通过现有 Skill 的 mapping readers 解析，而不是另写 enrichment 算法。

KEGG 名称来自用户的 `kopathway_Description.txt`。现有 reader 将参考通路 `koNNNNN/mapNNNNN` 统一为 `mapNNNNN`；这不是推断 gene ID，也不是把 KO 的 K 编号当通路。转换记录在 `pathway_id_aliases.tsv`，无名称的通路保留 ID，不编造名称。

BP 命名空间来自 [GO 官方 go-basic.obo](https://current.geneontology.org/ontology/go-basic.obo)，下载版本 `releases/2026-07-26`，实际哈希记录于 preparation 和总 summary。GO 官方推荐使用 [go-basic](https://geneontology.org/docs/download-ontology/)。
本适配器不补充 ancestor，不自动解决 alt_id/obsolete 替换；当前 ontology 中找不到或已 obsolete 的 term 排除在标准化映射之外，逐项写入 `go_unresolved_terms.tsv` 并产生 warning。缺少 ontology 时不会悄悄把 BP 改成 ALL。

首次测试的主要限制：counts 是 `gene-Asc…`，eggNOG query 是 `rna-Asc….1`，未经转换的精确交集为 0。
默认不按字符串相似性删除前缀/后缀。本次后续命名转换已经用户明确指定，详情见上节；如果需要参考注释验证，应使用同一注释版本的 GTF/GFF 或权威两列表：

```text
gene_id<TAB>query
```

将真实对照文件放入 `input/`，在配置中填写 `gene_id_mapping`。一个 query 不能映射到多个 count gene；多个 query 对应同一 gene 时，经公共 reader 去重合并 gene-term 关联。

背景不是整个注释数据库，也不是只用 DEG：直接传完整 `deg_results.csv` 给现有 enrichment Skill 的 `analysis_genes`。
现有 reader 保留有限 padj/log2FoldChange 的统计可选择基因，再与当前 GO BP / KEGG mapping 取交集。复跑已导出 `analysis_background_gene_list.csv` 及总 summary 的 `background` 审计。
富集的成熟实现沿用项目当前的 R `stats::fisher.test(alternative="greater")` + BH，不安装或重写 clusterProfiler 方法。

## 实际数据流

```text
原始文件 → 标准 CSV + 公共 validation
          ├─ run_pca → 公共 DESeq2 blind VST → PCA
          └─ run_deg → DESeq2 OT vs T → full/filtered DEG
                        ├─ run_volcano（只读 full DEG）
                        ├─ run_heatmap（full DEG + 公共 VST → Top 50 行 Z-score）
                        └─ 真实 filtered DEG → gene list
                             + full DEG → eligible background
                             + mapping → run_go_enrichment / run_kegg_enrichment
```

PCA 暂未导出 VST；Heatmap 复用同一个 `vst_common.R` 重新计算。两者使用同样的六个样本和 VST 参数，不直接对 raw counts 作热图。

## 运行

项目根目录、Python 3.13 虚拟环境、已安装项目 requirements 和现有 R 依赖是前提。
使用虚拟环境解释器可避开 PowerShell 激活脚本限制。

全新派生输入及空 results 时：

```powershell
python Real_DATA_TEST/run_real_data_test.py
```

本目录已完成首轮和复跑，不能直接覆盖。保留输入并再次执行，应选择新的 results 子目录：

```powershell
.venv/Scripts/python.exe Real_DATA_TEST/run_real_data_test.py --reuse-prepared --results-dir results/run_03
```

若当前终端找不到 `Rscript`，在命令后追加 `--rscript "Rscript.exe的实际路径"`。它是运行时参数，不在代码/配置中写死本机绝对路径。使用 `Get-Command Rscript` 可检查 PATH；已有终端不一定读到了后来安装 R 后的 PATH。

拿到真实 crosswalk 后，更新 JSON 的 `gene_id_mapping`，用新的派生目录和输出目录重新准备，保留旧数据：

```powershell
.venv/Scripts/python.exe Real_DATA_TEST/run_real_data_test.py --prepared-dir input/prepared_with_crosswalk --results-dir results/with_crosswalk
```

所有配置路径相对于 `Real_DATA_TEST`，不依赖终端当前目录。`--prepared-dir` 必须在 input 下，`--results-dir` 必须在 results 下；配置改变不能复用旧准备缓存。

## 失败语义和输出

- validation/DEG hard failure：停止依赖它的步骤；不删除已完成结果。
- PCA 失败不阻止 DEG；Volcano/Heatmap 失败不阻止独立 enrichment；GO 失败不阻止 KEGG。
- annotation 准备失败不阻止独立表达分析，但不能用不完整 mapping 执行 enrichment。
- 映射无交集：`NO_MAPPED_GENES`，是失败，不是“0 个显著 term”。总 summary 对未得到的富集数量使用 `null`。
- 有效空富集：已有 Skill 正常返回空表/说明图，状态记作 `empty`，可继续。
- DEG 选择为空：不伪造空查询富集统计，记录未执行原因。
- 退出码约定：0=success（可含 valid empty），1=hard/integration failure，2=partial。
- 每步使用真实 Skill summary 的 metrics，并验证其 artifact SHA256；汇总 summary 原子写入，包含耗时、warning、失败列表及文件位置。
- R exit code/stdout/stderr/timeout 由现有公共 runner 处理；控制台不输出未经处理的 Python traceback，详细内部诊断保留日志。

首次结果位于 `01_pca`、`02_deg`、`03_volcano`、`04_heatmap`、`05_go`、`06_kegg`。
GO/KEGG 当前只有失败诊断，无成功 CSV/PNG，这是正确暴露阻塞，不是漏写成功文件。

## 验证命令

```powershell
.venv/Scripts/python.exe Real_DATA_TEST/self_check.py
.venv/Scripts/python.exe Real_DATA_TEST/verify_real_outputs.py --compare-to results/recheck
.venv/Scripts/python.exe -m pytest -c pyproject.toml tests
```

完整原有回归（含真实 R 小数据集成用例，和本数据集测试不同）：

```powershell
.venv/Scripts/python.exe -m pytest -c pyproject.toml tests --run-r --rscript "Rscript.exe的实际路径"
```

当前目录没有修改或替换 `tests/`、toy data、现有 Skill 或任何 R 统计脚本。自动校验使用正常解释器运行，不要使用 `python -O` 禁用断言。

## 验收边界

表达分析、图形、衔接和故障隔离已得到真实数据验证；GO/KEGG 的真实 enrichment 成功路径仍未通过。
拿到权威 gene/query 对照、复核 GO 注释版本和未解析 term 策略后，需再次跑通富集并核验有效背景和结果，才能把六 Skill 的真实端到端验证标记为通过。此任务停在 Phase 4。
