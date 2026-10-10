# Phase 4 真实 RNA-seq 集成验证报告

最新更新：按用户指定的 Asc 命名规则，GO BP / KEGG 现已成功重跑，分别得到 **70 个显著 term / 10 条显著通路**，有效背景分别为 16,072 / 10,002 个基因。新结果位于 `results/asc_mapped/05_go_retry/` 和 `results/asc_mapped/06_kegg/`；汇总为该目录的 `enrichment_rerun_summary.json`。未重跑真实 DEG，没有覆盖原失败记录。修复了公共 Python CSV 验收器的 128 KiB 大字段限制，R 统计逻辑不变，完整回归 252 passed。新映射仍标记为命名规则对应、未经 GTF/GFF 验证。

下面是首次运行的历史报告，原状态和当时的阻塞事实保留；它不是最新 GO/KEGG 的执行状态。

日期：2026-09-15。结论：**partial，不能标记为六个 Skill 全部通过。**

真实 PCA、DEG、Volcano、Heatmap 成功；GO/KEGG 实际调用了已有 Skill，但在映射验证阶段返回 `NO_MAPPED_GENES`，没有执行富集检验，没有生成或伪造显著通路数量。
首次结果位于 `results/`，复跑位于 `results/recheck/`；两次结果均保留。项目停在 Phase 4，未进入 Phase 5。

## A. 数据检查

| 项目 | 实测结果 |
|---|---|
| 原始 count 基因数 | 24,581 |
| 原始样本数 | 9 |
| 原始组别/每组样本数 | O=3、OT=3、T=3 |
| 本次比较 | OT vs T |
| 本次样本数 | 6，OT=3、T=3 |
| OT 样本 | OT-3.bam、OT-4.bam、OT-5.bam |
| T 样本 | T-1.bam、T-2.bam、T-3.bam |
| 本比较未使用样本 | O-1.bam、O-2.bam、O-3.bam；保留原件及全样本标准副本 |
| 非负整数 counts、缺失值、ID 唯一性 | 通过现有公共验证；未舍入或补值 |
| metadata/count 样本集合 | 全 9 样本及 OT/T 子集均完全匹配 |
| 组别/重复数/PCA 样本下限 | 通过；组内各 3 个样本。独立生物学重复身份仍应由实验记录确认 |
| OT/T 中非全零基因 | 24,310；271 个全零基因按已有统计流程排除 |
| GO 精确映射覆盖率（全部输入基因） | 0 / 24,581；ALL 注释与 BP 均为 0 |
| KEGG 精确映射覆盖率（全部输入基因） | 0 / 24,581 |

通过的是 count/metadata 验证，**不是所有富集前提**。

格式适配依据实际文件内容：`gene_count_matrix.xls` 是 TSV（首列表头 Geneid）；`metadata.csv` 是 XLSX，Sheet1 的 A1:B9 为无表头样本/组别数据。标准化只生成新副本，没有覆盖原始文件。

## B. 每个 Skill 的真实结果

| Skill | 状态 | 真实指标与输出 |
|---|---|---|
| PCA | 成功 | PC1=85.9088594454781%，PC2=4.76667160361138%；24,310 个 variable VST genes；coordinates/variance/PNG/summary 齐全 |
| DEG | 成功 | full results 24,310 行；其中 24,206 个基因有 pvalue；104 行 padj 缺失 |
| DEG 分类 | 已核对 | Up=6,474；Down=7,387；DEG=13,861；NS=10,449 |
| Volcano | 成功 | 分类与 DEG 一致；PNG、plot_data、summary 齐全；实际绘制 24,206 / 24,310 行 |
| Heatmap | 成功 | 使用 Top 50 基因、6 样本；normalized_expression、行 Z-score matrix、selected_genes、PNG、summary 齐全 |
| GO BP | 映射失败 | query=13,861；mapped DEG=0 / 13,861；`NO_MAPPED_GENES`；显著 term 数未知（null） |
| KEGG | 映射失败 | query=13,861；mapped DEG=0 / 13,861；`NO_MAPPED_GENES`；显著 pathway 数未知（null） |

NS 按已有 Volcano 分类定义，包括 104 个 padj 缺失的基因，不应解释为这些基因都得到了“不显著”的有效统计检验。另有 271 个全零基因不在 full DEG results 中。

Volcano 真实输入包含 4 个 padj=0 和 104 个 padj 缺失值：零值仅使用有限显示高度，不改原始统计值；缺失行保留为 NS 但不绘点。图形没有因此崩溃。

### 背景 universe 审计

- 来源：当前比较的完整 `deg_results.csv`，不是 annotation 全库，也不是仅 DEG。
- 沿用现有 enrichment reader：有限 padj 和 log2FoldChange 的基因可作为背景候选。
- 背景候选数：24,206；从 full DEG 表排除 104 行。
- 与当前 BP/KEGG 映射取精确交集后均为 0；因此实际检验没有执行。
- 复跑导出 `results/recheck/analysis_background_gene_list.csv`，复跑总 summary 的 `background` 记录来源、规则、数量和路径。
- 未成功执行不能写成“富集 0 项”；当前 GO/KEGG 目录只有 `failure.json` 与日志，无成功表/图，这是预期的故障记录。

### 跨 Skill 数据衔接

1. PCA/DEG 使用相同 OT/T count 和 metadata。
2. Volcano 与 Heatmap 的输入哈希指向本次真实 `deg_results.csv`，没有第二套 DEG 计算。
3. PCA 未输出 VST 矩阵；Heatmap 调用现有公共 VST 模块重新转换同样 counts。两者的 `vst_common.R` 哈希一致，输入 counts 哈希一致。
4. Heatmap 实際数值与导出的 VST 行 Z-score 独立复算完全一致（本次数值差最大值 0.0）；Top 50 顺序与 padj 升序、abs(log2FC) 降序、gene_id 稳定排序一致。
5. 富集 gene list 直接来自本次 `deg_filtered.csv`；没有用手工列表替代真实 DEG。

### 图形检查与生物学解释边界

三张真实 PNG 已检查，坐标轴、标签、分组颜色、色标及基因标签均能正常显示。
PCA 中两组在 PC1 上明显分离，OT 组内分散程度高于 T 组；没有因此自动删除任何样本。

本次验证证明输入处理、内部数值一致性和计算可重复性，**不等同于与独立金标准分析完全一致，更不证明分组差异一定来自处理本身**。DEG 数较多，应结合实验设计、样本质量及可能混杂因素解释；本任务未更改设计公式、阈值或 DESeq2 方法来改变结果。

## C. 暴露的工程和数据问题

| 问题 | 影响 | 处理/剩余限制 |
|---|---|---|
| 文件扩展名与真实编码不符 | 直接交给 Skill CSV reader 会失败 | 数据集适配器按实际格式读取，新建标准 CSV；原文件不变 |
| metadata 无表头且使用 group | DEG 固定要求 sample_id/condition | 新副本增加表头并显式复制 group→condition；不改核心接口 |
| 输入有 3 组，DEG 只支持 2 组 | 不能将 9 样本直接塞入当前 DEG | 配置显式 comparison_only；先验证全部样本，再选择 OT/T 六个；不做三组 PCA |
| count 与 eggNOG ID 命名空间不一致 | GO/KEGG 零交集，真实富集无法执行 | 保留 ID，返回明确失败；仍需权威 gene/query 对照 |
| eggNOG 没有 GO ontology | 无法直接按 BP 筛选 | 下载官方 go-basic；不默认人类/小鼠，不把 BP 改成 ALL |
| 3,115 个 GO ID 未在当前活跃 ontology 主 ID 中解析 | 注释版本/obsolete/alt_id 差异可能影响 term 集 | 已逐条审计并警告；未自动替换或补 ancestor，继续正式富集前需复核策略 |
| 393 个 KEGG 通路中仅 388 个有用户提供的名称 | 5 个条目无法显示名称 | 保留真实 ID，不联网猜名、不伪造名称 |
| PCA 不导出 VST | Heatmap 多执行一次转换 | 复用公共模块，记录 warning；此次没有为优化做架构重构 |
| 各 Skill provenance 字段略有差异 | 通用校验器不能假定所有键名相同 | 独立校验器按实际 PCA 的 vst_module_sha256 和 Heatmap 的 r_scripts_sha256 对接；不重写全项目 schema |
| 新 runner 起初把 annotation 准备和 counts 验证强绑定 | 注释文件损坏会阻止本可运行的表达分析 | 已在本目录内隔离 annotation failure，新增自检；不改 Skill 统计逻辑 |
| Windows R 启动 locale 警告 | stderr 非空但不等于失败 | 四项 Skill 的 R exit code 均为 0；完整保存 LC_* 诊断，不静默吞掉或修改全局系统设置 |
| 当前执行终端未从 PATH 找到 Rscript | 默认可执行名无法启动 | 本次使用运行时 --rscript 指向已安装 R；代码/JSON 不写死用户路径 |
| 同目录重复运行可能污染旧结果 | 难以判断文件属于哪次运行 | 复用现有空目录预约机制，验证 OUTPUT_NOT_EMPTY；缓存用哈希核验；新结果/新准备目录分别隔离 |

缺少名称的 KEGG IDs：`map00072`, `map00281`, `map00471`, `map00472`, `map01130`。

没有发现本次要求修改现有 DEG 核心统计逻辑的明确 bug；既有 Skill 的输入契约较严格，主要兼容问题在数据接入边界。

### 性能

| 阶段 | 首轮耗时（秒） | 复跑耗时（秒） |
|---|---:|---:|
| 准备/validation | 29.626 | 1.370（哈希核验后复用标准输入，再做公共验证） |
| PCA | 9.426 | 10.163 |
| DEG | 13.366 | 13.731 |
| Volcano | 1.217 | 1.174 |
| Heatmap | 8.695 | 8.336 |
| GO 到映射失败 | 17.526 | 18.430 |
| KEGG 到映射失败 | 1.606 | 1.792 |
| 总计 | 81.592 | 55.288 |

GO/KEGG 耗时不是成功 enrichment 的性能。大体量 GO 展开与解析是当前主要额外开销：标准 gene_to_go.tsv 为 209,637,072 bytes（约 209.6 MB），准备阶段的 annotation 耗时 28.201 秒。此次未做峰值内存、并发、长时间压力或其他机器基准测试；不能据此宣称已验证任意规模性能。

### 测试与可重复性

- 原有完整 pytest：**246 passed，51.58 秒**，包括真实 R 小数据集成用例；未删除、改写或替换这些测试。
- 新增独立 runner 自检：**11 passed**，覆盖硬失败依赖停止、绘图/GO 失败后的独立继续、有效空结果、文件哈希检查、异常结构化、不覆盖输入、路径越界、零映射以及注释故障隔离。
- 真实输出只读校验通过。
- 两次真实运行：四项成功 Skill 的所有 artifact SHA256 和 metrics **完全相同**，包括 CSV、JSON 数值文件和 PNG。
- 再次指定已有 results/recheck 时得到 `OUTPUT_NOT_EMPTY`，未覆盖文件。
- 原始用户文件 SHA256 在运行前后及最终复核一致。
- DEG R 脚本 SHA256 保持 `77939c5b001010b7b9b0e74dfc98b34dbcdd6df1194375ff911a5d83b570e3dd`。

## D. 修改分类与新增工程概念

所有本次代码/文档修改均在 `Real_DATA_TEST/`。`src/`、`tests/`、toy data 和现有 R 脚本未修改。

- **Compatibility improvement**：识别此数据集的 TSV/XLSX 实际格式；生成新表头；为 DEG 提供 condition；支持用户的 KEGG 名称表；借助官方 ontology 为 GO 补充真实 BP/MF/CC 信息。
- **明确的分析范围选择**：当前所有样本关系图与 DEG 均限 OT/T 六样本，不包括 O。此行为写在配置/README/summary，不是默默改变全样本分析。
- **明确的注释处理行为**：排除当前 ontology 中未解析/obsolete 主 ID，保留单独审计；没有自动 alt_id 替换、ancestor 扩展或 gene ID 改名。必须在后续解释中考虑该限制。
- **Bug fix（仅新 runner）**：隔离注释准备失败，补强未预料异常的最终状态记录与错误 operation；校验器适配实际 provenance 键名。
- **没有统计行为变更**：不改变 DESeq2 design、归一化、缺失/全零处理、DEG 阈值、PCA 基因选择、热图筛选规则或 enrichment 检验。

本阶段值得理解的工程概念仅有：

1. **适配器边界**：非标准外部数据在入口转换，核心 Skill 保持单一、明确的输入契约。
2. **可追溯性**：原件只读，派生输入/统计输出独立保存，SHA256 能证明数据衔接与未覆盖。
3. **依赖感知的故障隔离**：失败只阻断依赖它的步骤；失败、有效空结果和未执行具有不同语义。
4. **集成验证不同于单元测试**：单元测试检查局部边界，真实数据测试发现格式/规模/注释/接口组合问题；二者都应保留。

完整文件职责、配置和运行命令见 `README.md`。

## E. Phase 5 readiness

结论：**表达分析链已具备真实规模运行证据，但还不能以“六个 Skill 真实端到端全部验证通过”为前提进入 Phase 5。**

必须先完成：

1. 提供与本次计数使用的同一注释版本的 GTF/GFF，或权威 `gene_id → query` TSV；不能只猜测 `gene-Asc…` 与 `rna-Asc….1` 的对应。
2. 复核该对应的唯一性、GO ontology 版本及未解析 term 的处理策略；报告修正后的 counts/DEG 映射覆盖率。
3. 使用完整 DEG 的合理背景，真正执行 GO/KEGG enrichment；检查 query_used、universe_used、mapping_used、结果统计/图形/summary。有效空结果可以通过，但零映射失败不可以。

建议随后确认 R 可执行文件配置的可移植性；PCA 导出 VST、annotation 内存优化、更统一的 provenance schema 可列为后续改进，不需要现在堆新框架。

**需要用户提供的下一项信息：权威 GTF/GFF 或 gene/query 对照表的路径。未开始 Phase 5。**
