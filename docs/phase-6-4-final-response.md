# Phase 6.4：真实 SkillResult → DeepSeek 最终回复

验收日期：2026-09-19。完成并停止，不进入 Phase 7。

## 设计与本阶段新增概念

`User → 既有 ToolRunner → 真实 SkillResult → Formatter → DeepSeek.chat（无 tools）→ Grounding Gate → 最终回复`

- **数据最小化**：formatter 使用六个 Skill 的真实 summary 字段、真实阈值和 warnings。既不读取完整 CSV，也不发送输入矩阵、原始 stderr、主机路径或 provenance。output metadata 只有逻辑名称。
- **事实约束生成**：DeepSeek 用 JSON mode 返回 `{"final_response": "..."}`，用带标签的真实事实组成简洁中文段落。关键事实必须原样出现一次；只允许有限中性连接词和标点。因此不仅拒绝新数字，也拒绝把上调/下调数字交换、额外基因/通路/显著性/因果结论。该方式是受限输出协议，不是通用 NLP 幻觉检测器。
- **失败域隔离**：统计成功与总结成功分开记录。总结超时、非法 JSON 或 grounding 失败不会重算 DEG，也不会删除已有结果。错误、needs_input、unsupported 不付费润色。
- **成本与审计**：完整闭环最多两次请求，无自动重试；每次记录 model/usage/latency，完整结果汇总两次 usage。缺失的 usage 为 null，不冒充零消耗。

Codex 只实现代码和提供测试输入；真实路由与最终段落都由 DeepSeek API 返回。统计始终由现有本地 Skill 执行。

## 文件与职责

| 文件 | 作用 |
|---|---|
| `src/bulk_rnaseq/llm/result_formatter.py` | 六 Skill 的展示字段映射、数值类型/范围检查、限长上下文；不重建输入 schema 或统计逻辑 |
| `src/bulk_rnaseq/llm/final_response.py` | 第二次 API、严格 prompt、JSON 解析、grounding、本地失败回复、FinalResponseResult |
| `src/bulk_rnaseq/llm/single_request.py` | 组合既有 ToolRunner 和总结层；保存结果、日志、成本；不串联多个 Skill |
| `src/bulk_rnaseq/llm/run_complete.py` | 新的显式付费完整闭环 CLI，旧 `run_tool` 不变 |
| `tests/llm/test_final_response.py` | 46 个免费 mock/边界测试 |
| `tests/llm_integration/test_final_response_live.py` | 6 个显式开启的真实总结/真实 E2E 测试 |
| `README.md` | 更新当前阶段与运行入口 |
| 本文 | 设计、使用、真实验收、限制 |

未修改 R 脚本、DEG/PCA 等 Skill、Registry、Manifest、DeepSeek client、既有测试或 toy 数据；无新增依赖。

## Formatter 与返回结构

| Skill | 发送的主要事实 |
|---|---|
| DEG | genes_analyzed、genes_with_pvalue、significant_genes、upregulated_genes、downregulated_genes、真实 treatment/control 和阈值 |
| PCA | n_samples、n_groups、PC1/PC2 variance、group_column |
| Volcano | n_total、n_up、n_down、n_ns、n_plotted、阈值 |
| Heatmap | n_genes_requested、n_genes_used、n_samples、group_column、阈值 |
| GO/KEGG | n_query_input、n_query_mapped、n_universe、n_terms_tested、n_enriched、真实阈值；GO ontology |

不凭空补 summary 中没有的参数默认值；缺参数时发送“当前结果未提供…”事实。PCA 百分比仅在展示层四舍五入到两位小数，原始结果完全保留。

成功状态沿用现有项目的 `succeeded`，不是另外引入 `success`。
`FinalResponseResult` 包含 status、request_id、skill_name、final_response、model、usage、request_count、latency_seconds、grounded、verified_context、error_code、message。
`SingleRequestResult` 另保留完整 execution 与 final 两个阶段，以及合并后的 usage。

### Prompt 与 grounding

System prompt 明确只使用 VERIFIED_RESULT；禁止分析执行、伪造、额外数值/名称/显著性/因果解释。原用户文本仍发送给 DeepSeek，但不能越过结果解释协议。
模型必须把 formatter 提供的带标签事实原样编入段落。去除这些事实后，只允许白名单中性连接词与标点；任何其他内容返回 `LLM_RESPONSE_VALIDATION_FAILED`，最终正文不放行。

这比仅检查“某个数字是否在 context 中”更严格，可拒绝数字交换或附加“细胞增殖增强”等无依据结论；相应代价是表达风格固定、不能自由释义。

### 三类特殊结果

- **warning**：发送 message/code/可用 count，不发送任意 details；最终由本地原样附加，确保模型不能遗漏。原始完整 warning 仍在 SkillResult 中。超过上下文上限则明确拒绝，不静默截断。
- **empty success**：GO/KEGG n_enriched=0 时加入“分析成功，但在当前阈值下未检测到显著富集结果”，grounding 要求保留，不允许写成失败。
- **failed**：本地返回“分析未执行成功 [error_code]：message”，无第二次 API。统计成功但报告写入失败时，也不能被后续总结掩盖为成功。

## 如何运行

从项目根目录执行，无需激活 PowerShell venv。配置沿用被忽略的 `.env` 和原有 DEEPSEEK_* 环境变量。

```powershell
# 免费：默认不访问 DeepSeek、不调用 R
.\.venv\Scripts\python.exe -m pytest -q

# 仅本阶段离线测试
.\.venv\Scripts\python.exe -m pytest tests/llm/test_final_response.py -q

# 显式付费：4 个已有真实结果总结测试，不重新运行 R
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_final_response_live.py --run-llm -q

# 显式付费 + R：全部6项，包含真实 KEGG 空分支和完整 DEG E2E
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_final_response_live.py --run-llm --run-r -q

# 用户完整闭环：真实数据，最多两次 API；输出新建 UUID，不覆盖 Phase 4/6.3
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_complete "比较 OT 和 T 的差异表达，OT作为实验组，T作为对照组。" --context Real_DATA_TEST/config/tool_execution_context.json --output-root Real_DATA_TEST/results/phase_6_4

# 同一真实上下文也可选择一个 PCA Tool
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_complete "画一下样本 PCA。" --context Real_DATA_TEST/config/tool_execution_context.json --output-root Real_DATA_TEST/results/phase_6_4
```

每个完整 run 下：`skill/` 保留真实分析文件；`tool_execution.json`/`tool_run.log` 是原执行审计；`final_response.json`/`final_response.log` 是本阶段新增审计。
总结失败时如需再试，可加载同一 canonical SkillResult 调用 `FinalResponseBuilder.build()`；不要为了重试总结再次运行完整 CLI。

## 实际验收记录

默认全量测试：**572 passed, 53 skipped**。新增离线测试：**46 passed**。
真实 API 本阶段测试：**6 passed**（27.41 秒），模型全部为 **deepseek-flash**，无失败用例、无 API 自动重试。

| Case | 真实来源/结果 | Prompt | Completion | Total |
|---|---|---:|---:|---:|
| DEG 总结 | Phase 4 真实 DEG；6474 上调、7387 下调 | 450 | 90 | 540 |
| PCA 总结 | Phase 4 真实 PCA；85.91%、4.77% | 411 | 51 | 462 |
| GO 总结 | 已校正映射的真实 GO；9757 mapped，70 significant | 562 | 83 | 645 |
| warning | 真实 GO unmapped/dedup/background/R warnings 全保留 | 566 | 82 | 648 |
| KEGG empty | 本轮真实 R 运行；padj=0，0 significant | 638 | 90 | 728 |
| DEG E2E（两次请求） | 新的真实 Tool Call + DESeq2 + 最终回复 | 3202 | 145 | 3347 |
| **合计（7次请求）** | 非估计值，均取自 API usage | **5829** | **541** | **6370** |

KEGG 空分支使用真实 gene list、prepared_asc 映射、原 DEG 背景，只有测试实例阈值改为 0；不是篡改 summary 得到空结果。实际输入13861、mapped5896、有效背景10002、393条通路检验，输出表和图真实生成。正常默认阈值仍为0.05，先前10条显著通路结果未修改。

DEG 完整闭环 request_id：`d76afa05-6bb7-4acd-8eeb-60beaeea5eb6`。
DeepSeek 真实参数为 treatment=OT、control=T；DESeq2 exit code=0；输入24581基因、6样本，分析24310基因，有有效pvalue的24206基因；13861个DEG。
输入 hash 未变；新 full DEG CSV 与 Phase 4 基线 SHA256 相同：`3bd1e05a80461ac59eacf08527410c8c5452d3ad724596dabf6dca91d9398c11`。
完整闭环 API 分别消耗 2801（选 Tool）和 546（总结）tokens；执行阶段约17.01秒，总结阶段约1.66秒。

### DeepSeek 的真实最终回复示例（未由 Codex 改写）

DEG：

> 分析已成功完成，结果如下：分析基因数为24310，有有效pvalue的基因数为24206，差异基因数为13861，上调基因数为6474，下调基因数为7387，实验组为OT，对照组为T，padj阈值（严格小于）为0.05，绝对log2FC阈值（大于等于）为1.0。完整结果请查看本地输出。

PCA：

> 分析已成功完成，结果如下：样本数为6，分组数为2，PC1解释方差为85.91%，PC2解释方差为4.77%，分组列为group。完整结果请查看本地输出。

两者最终交付文本均另附真实 warning：`R_DIAGNOSTICS: Inspect r_stderr.log.`，完整正文可在审计 JSON 中查看。

### 本轮证据位置

- E2E：`Real_DATA_TEST/results/phase_6_4/d76afa05-6bb7-4acd-8eeb-60beaeea5eb6/final_response.json`
- 六项测试：`outputs/deepseek_tests/phase_6_4/` 下独立 UUID 子目录的 `summary_test.json` / `e2e_test.json`。
- KEGG 空结果：该目录下 `c3567fba-1bdd-43c9-892e-514778f300b7/real_empty_kegg/`。
- 默认 pytest 网络保护和 `--run-r` 门控未修改。

## 问题、限制与验收边界

1. 旧 Skill 字段不统一，由展示映射适配，没有更改原统计字段或默认阈值。
2. 发现并在新组合层防范“执行报告失败被总结覆盖为成功”；增加离线回归测试。
   另修正新 formatter 的过强假设：既有 DEG 允许 lfc_threshold=0，故不能无条件要求上调+下调=全部筛选基因；遵循原有统计契约并增加边界测试。
3. R 既有 `C.UTF-8` locale startup warning 仍存在，exit0、结果 hash 不变；保留 warning，不修改用户系统 locale。
4. 本阶段支持六个 Skill 的 summary formatter，但只真实验收了 DEG/PCA/GO 总结、KEGG empty 和 DEG 全闭环；没有声称每个 Skill 都重新做过完整 E2E。
5. **首版不发送 top genes / GO terms / pathways**，也不解释具体条目。本轮 GO 测试验证真实数量与禁止出现任何未提供的条目，不声称完成具体 GO 名称解读测试。以后可增量添加受控、限量事实，不能把大表直接交给模型。
6. summary 的可信来源是已验证的本地 Skill；此模块不会重新计算或鉴定外部伪造的 SkillResult。协议验证确保回复不超出所提供事实，不等于验证生物学机制。
7. warnings 英文原文保留；中文自由改写、详细生物学解读、自动重试、multi-tool 均不在本阶段。

在上述明确的 summary-only 范围内，Phase 6.4 单 Tool 闭环验收通过。没有进入 Phase 7。
