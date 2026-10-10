# Phase 7.8 Real Data Full E2E / Phase 7 Final Report

验收记录：2026-10-09 至 2026-10-10（Asia/Hong_Kong）。最终结论：**NOT READY**。

真实多步骤计算、受控定制、重启恢复已经通过；聊天入口复用 Workflow 历史 DEG 的能力仍未通过。不能将单元测试全绿等同于完整产品验收通过。未进入 Phase 8。

## 1. 范围与证据

- 通过真实 Streamlit UI 创建 `workspace/Real_DATA_PHASE7_E2E/`，通过文件选择器导入四个真实输入。没有手工预建项目内部目录、修改 project.json 或复制分析结果。
- 实际自然语言经真实 DeepSeek 路由/规划，模型返回结构经本地验证，然后由 Registry 调用真实 R/Skills；Codex 没有构造这些在线业务决策或总结。
- 模拟故障/幻觉仅用于明确标注的离线测试，未用于替代正常真实 E2E。
- 原始输入、toy data、原单元测试、既有 Real_DATA_TEST/Real_DATA_UI_TEST 和全部历史结果均保留。
- `outputs/phase7_e2e/audit.json`：只读审计结果，包括逐请求文本、耗时、usage、Workflow/Customization ID、真实摘要、gene subset 精确集合校验及 SHA256 校验。
- `outputs/phase7_e2e/before_restart.json` 与 `after_restart.json`：重启前后审计，内容完全相同。恢复 UI 并浏览结果后的最终 audit 也相同。
- `outputs/phase7_e2e/isolation.json`：空隔离项目审计。
- `outputs/phase7_e2e/pca_browser.jpg`、`restored_volcano.jpg`：实际浏览器证据，不是生成的 UI 图片。
- 项目 runtime/chat_history.json、runtime/chat_requests/、results/workflows/、results/customizations/ 保留原始业务记录。输出和真实数据不应提交到公开 Git 仓库。

结构/哈希审计发现 0 个完整性错误，但这**不证明所有历史记录语义正确**。下文明确保留并标记一个修复前的错误来源记录。

## 2. 最终架构与用户路径

```text
Streamlit → Project Manager / File Import → Project Context
  → ChatController → DeepSeek Router
       ├─ Workflow Planner → Local Validator / Resolver → serial Executor
       │   → Skill Registry → real R/Python Skills → WorkflowResult
       │   → grounded DeepSeek Workflow Summary
       └─ Customization Planner → local capability/source validation
           → whitelisted Skill parameters / deterministic table transformation
  → persistent Chat + ResultManager → bounded preview / full artifact download
```

普通用户创建项目、上传 count/mapping、填写或导入分组，在 Assistant 输入需求，再到 Results 看图表并下载。无需在分析请求中输入本机路径。重启后需要从 Projects 重新选项目；无需重新上传或重新分析。

本次明确验证了多步骤规划，而非 Agent/动态代码执行。系统仍串行执行合法 DAG，无自动重试、任意代码、云部署或 Phase 8 功能。

## 3. 数据与真实结果

输入来自 `Real_DATA_TEST/input/gene_count_matrix.xls` 及 `prepared_asc/` 的 metadata、GO、KEGG mapping。`.xls` 原文件实际为分隔文本，导入服务转换为规范 CSV；元数据转换为内部 sample_id/condition 格式。

- 原始矩阵：24,581 genes，9 samples，非负整数 counts。
- O：O-1.bam / O-2.bam / O-3.bam。
- OT：OT-3.bam / OT-4.bam / OT-5.bam。
- T：T-1.bam / T-2.bam / T-3.bam。
- 每组 3 个样本；PCA 用全部 9 个样本，OT vs T DEG 只用明确选定的 6 个样本。
- GO mapping 原文件约 197 MiB，KEGG 约 3.9 MiB；导入与分析记录保留重复关联去重、未映射基因等警告。
- 映射沿用此前用户授权的 `rna-Asc… → gene-Asc…` 命名转换结果；本轮没有重新以参考基因组验证生物学 ID 等价关系，也没有自动发明基因别名。

| 分析 | 实际结果 |
|---|---|
| OT vs T DEG | analyzed 24,310；有效 pvalue 24,206；显著 13,861；up 6,474；down 7,387 |
| DEG 阈值 | padj < 0.05，abs(log2FoldChange) >= 1；方向 OT treatment / T control |
| Volcano OT vs T | total 24,310；up 6,474；down 7,387；NS 10,449；104 行缺失统计不绘制；4 个零 padj 用有限显示高度 |
| PCA | 9 samples / 3 groups；PC1 72.07%，PC2 14.99%；VST 后 PCA |
| KEGG(up) | query 6,474；mapped 2,877；effective background 10,002；tested 393；significant 15 |
| GO(down) | query 7,387；mapped 5,194；effective background 16,949；tested 20,353；significant 234；ontology ALL（实际默认值） |
| KEGG(down) | query 7,387；mapped 3,019；effective background 10,002；tested 393；significant 12 |
| O vs OT DEG | analyzed 23,609；有效 pvalue 23,450；显著 7,624；up 3,916；down 3,708 |
| O vs OT Volcano | NS 15,985；实际绘制 20,703；缺失 padj/log2FC 2,906 行保留为 NS、不绘制 |

NS 的定义采用 Volcano 的分类，不能把所有 NS 都解释为具有有效统计且确认无变化。DEG summary 没有单独提供 NS，UI 如实显示“未提供”。Enrichment 使用真实 DEG 的有效分析背景并映射，不用整个物种基因组默认背景。

## 4. 实际 E2E case 记录

PASS 表示本列明确写出的测试范围通过；LIMITATION/FAIL 不包装成 PASS。

| Case / 实际请求 | Expected | Actual / Status | Bug / Fix / Regression |
|---|---|---|---|
| UI 创建项目、四类真实输入上传 | 系统创建并绑定 | PASS，9 样本/24,581 genes/4 输入 | 原数据保留 |
| “以 T 为对照比较 OT，画火山图，并对上调差异基因做 KEGG 富集。”首次 | DEG → {Volcano, KEGG up} | FAIL，DEG 拒绝 3 组输入，下游 skipped | P1-01；修复后原句重测 PASS |
| 同句修复后重测 | 合法分支，正确比较 | PASS；执行 deg,kegg,volcano；up 集合逐 ID 精确一致 | comparison 输入派生，未改 DEG 统计 |
| “画样本PCA，同时比较OT和T的差异表达，T为对照。” | 两个独立 root | PASS；PCA 9 样本，DEG 6 样本，没有 PCA→DEG 边 | 永久独立根回归 |
| “比较OT和T，并对下调差异基因分别做GO和KEGG富集。” | 不猜比较方向 | PASS 安全性；needs_input treatment/control，未执行 | 原句本身不足以确认方向 |
| 澄清“以T为对照比较OT，并对下调差异基因分别做GO和KEGG富集。” | DEG → {GO down, KEGG down} | PASS；两份 query 均精确等于真实 7,387 down genes | 审计 subset_evidence |
| Volcano 分支失败（离线安全模拟） | DEG/KEGG 成功，Volcano failed，partial | PASS，existing executor + UI fixture tests | 未声称是在线真实数据故障 |
| DEG 根失败（离线模拟 + 首次真实失败） | 所有依赖下游 skipped | PASS，失败原因保留，未生成成功解释 | executor/final-response tests |
| “把刚才的火山图上调改成红色，下调改成蓝色，标注Top20，并额外标注foxl2和sox9。” | 受控重绘，不重跑 DEG | PASS；新图红/蓝、Top20，原图保留 | foxl2/sox9 非当前精确 ID，真实警告；P1-02 修复聊天警告可见性 |
| “刚才的KEGG图只显示Top15通路。” | 只重绘已有结果 | PASS，15 terms，新图，未重跑富集统计 | source run/hash 可追溯 |
| “筛选OT vs T中与性别分化相关的上调DEG。” | 真实 annotation filter | LIMITATION，中文关键词字面匹配英文注释，0 命中 | 不是“无相关生物学基因”的结论；见 P1-04 |
| 澄清“在GO注释中使用英文关键词 sex differentiation 进行文字匹配” | 真正注释文字筛选 | PASS，205 个上调 DEG；非功能验证 | 不推断/制造 gene list |
| “把刚才筛选出的基因按照log2FoldChange从高到低排列。”首次 | 保持过滤集合 | FAIL，误绑原 DEG，得到 13,861 而非原 0 | **P0-01**；错误历史记录保留但无效 |
| 同句修复后 | 使用上一过滤结果 | PASS，source 为 205-gene 过滤结果；集合仍 205，降序正确 | 两个永久回归覆盖空/非空，另有缺失来源回归 |
| “以OT为对照比较O，并画火山图。” | 第二个真实比较 | PASS，O treatment / OT control | 两比较并存，未覆盖 OT vs T |
| “把刚才的DEG画成火山图。” | 历史 DEG 正确复用 | FAIL 产品能力；安全返回缺 deg_result_file，不执行 | **P1-03 未修复** |
| “把已有火山图的上调颜色改成红色。” | 多来源时不猜 | PASS，Source result 下拉选择，未执行新定制 | 模糊来源安全；本次不替用户选择 |
| “做差异表达，然后画火山图和KEGG。” | needs_input，不猜方向 | PASS，提示 treatment/control 和 O/OT/T | 无新 workflow |
| “把刚才的火山图改成可交互三维图，并自动编写R代码完成。” | unsupported，不执行代码 | PASS，明确拒绝动态脚本与交互三维图 | 原图保留 |
| “基于现有OT vs T差异结果再做KEGG，不要重新运行差异分析。” | 复用历史 DEG，不重跑 | FAIL 产品能力；needs_input gene_list/mapping/background | **P1-03 未修复**；没有偷偷执行 DEG |
| UI 新建 Real_DATA_PHASE7_ISOLATION，再“帮我画PCA。” | 不借用主项目文件/历史 | PASS，0/4 inputs、空 Results、缺失提示、0 API 调用 | 离线测试另覆盖跨项目 source ID 拒绝 |
| 重启实际 Streamlit、重新选主项目 | 全部记录恢复，不执行 | PASS，5 workflows / 7 customizations / 17 requests；审计字节相同 | 无新增 API usage、run 或 workflow |
| Results 图表/下载 | 有限预览 + 原文件下载 | PASS，DEG/GO/KEGG/filtered 表，PCA/Volcano/custom 图；DEG 默认 100 行 | 下载完整 CSV SHA256 与原输出一致 |
| Workflow 列表、step/source | 正确 DAG、来源关联 | PASS，恢复后可以 Open volcano；deg root，kegg/volcano 都依赖 deg | workflow/customization/source IDs 保留 |
| Streamlit rerun | 不重复 API/计算 | PASS，切换 tab、项目、结果、重启均未增加主项目记录/usage | 单元测试同时覆盖同 request ID 幂等 |
| 幻觉数字/pathway/status（离线） | 拒绝错误回答 | PASS，grounding tests；真实成功工作流 final.grounded=true | failed/skipped 不被描述为成功 |
| provenance/checksum | 原结果不变、上下游可追踪 | PASS 结构完整性；0 hash/subset 审计错误 | 不为已知语义错误历史记录背书 |

## 5. 已修复问题与行为边界

### P0-01：排序错误绑定原 DEG（已修复，真实重测通过）

Router 原来只表达 target_skill=rnaseq.deg，宿主只能从原 DEG 候选中找来源，丢失“已筛选结果”语义。

修复：DeepSeek route 增加有限枚举 source_kind=filtered_deg；本地只选择当前项目的 rnaseq.filter_by_annotation 结果，并将其 filtered_table 作为输入。没有匹配来源就返回 needs_input，不回退原 DEG。排序不改变基因集合。

**不可使用的旧结果**：run `475aa6e0-865c-4e0d-b85d-b15fec772210`，customization `2f4f3aac64484971a7b8cba09c20eee1`。这条记录错用了原始 DEG，不代表“刚才筛选的基因”。为保留真实审计历史没有删除/手工改写；当前 UI 尚无撤销/隔离失效结果标记，使用者必须避开此测试记录。

正确复测：filtered source `569e1124-f8d2-4b84-9f7d-a0e5d815bfef` → sorted run `ef13be92-fba5-40b6-8b5e-89396568213f`，205 genes，逐 ID 集合与排序均校验。

### P1-01：多组项目的两组 DEG 输入（已修复）

旧 DEG 有明确“两组输入”契约。Workflow 已有 treatment/control，但把全部三组直接传给它，导致合法两组比较失败。

修复在 Executor 的输入绑定层生成独立 pairwise count/metadata 输入快照，先完整验证原始输入，再按 sample name 选择两组，最后沿用原 DEG 验证。PCA 仍使用全部样本。记录 selected/excluded samples、原始/派生 SHA256，并给出 PAIRWISE_SAMPLE_SELECTION 警告。

这是显式的输入准备行为变更，不是修改 DESeq2 方法、放宽校验或无声丢弃组别。现有直接 single-skill DEG 入口仍遵循旧两组输入契约，未宣称所有入口都已支持多组项目子集。

### P1-02：定制警告未显示到 Chat（已修复）

定制 Skill 已记录 LABEL_GENES_UNAVAILABLE，但成功聊天只展示参数/图。现在展示真实 warnings 和 details，让未找到 foxl2/sox9 不再表现为“全部标签要求完成”。没有自动别名转换。

### 性能问题：注释能力发现重复扫描大 mapping（已改善）

宽表仅通过表头发现支持 GO/KEGG/Description；实际过滤仍完整验证并读取真实内容。纯排序不加载 annotation。没有减少科学验证要求。

## 6. 未解决问题 / 必须先处理

- **P1-03 历史结果复用桥接缺口**：Workflow DEG 已被 ResultManager 收录，但单 Skill 聊天 Context 的旧 upstream 机制没有一致接入该历史；Planner 也没有完整向模型提供可选历史来源。导致已有数据仍被要求补充。底层 Validator/Executor 的 project_result 精确复用测试通过，不等于 UI 这条链通过。
  - 必须补全显式、项目内、比较匹配的来源选择/确认，再真实重测“已有 DEG → Volcano / KEGG”，断言不执行 DEG。
  - 不应简单取最后一个 DEG 写进缓存；存在 OT vs T 和 O vs OT 时会制造新的 P0 来源错误。
- **P1-04 注释语言与输入语义**：中文“性别分化”直接文字匹配英文 annotations 为 0；当前需用户提供实际注释语言的关键词。不能将 0 宣称为生物学无相关基因。
- 已知无效测试记录仍可浏览；需要在投入常规科研使用前决定一个可审计的失效标记机制，不能手工篡改原始结果以掩盖问题。
- **P2**：侧栏仍显示 One request · One Skill、页头 Phase 6.5.3；与当前 Workflow 能力不符，列入文字修订 backlog。
- **P2**：纯排序不读 annotation 时 Annotated genes=0 容易误解为无注释；实际只是该转换未再扫描注释，不影响保留的 205 基因集合。
- 本轮未验证功能性 gene-symbol 同义词转换；foxl2/sox9 只能按精确 ID 匹配。
- Final Workflow Summary 当前只提供严格限定的真实摘要事实，不发送大表，也未列具体 top pathway/gene 名称。名称可在真实 CSV 查看；不声称摘要已具备名称层生物学解释能力。

## 7. 修改文件及用途

| 文件 | 类型 / 用途 |
|---|---|
| src/bulk_rnaseq/workflow/comparison_inputs.py | 新增；显式两组输入快照与验证、样本选择记录 |
| src/bulk_rnaseq/workflow/executor.py | 小范围兼容改进；DEG 调用前绑定派生输入，保留源哈希/警告 |
| src/bulk_rnaseq/workspace/chat_router.py | bug fix；让真实模型明确区分原 DEG 与已过滤 DEG 来源 |
| src/bulk_rnaseq/workspace/chat_dispatch.py | bug fix；限定候选来源，不对排序加载大注释 |
| src/bulk_rnaseq/customization/engine.py | bug fix；接受被验证的 filtered_table 来源，复用能力发现 |
| src/bulk_rnaseq/customization/filtering.py | 性能改进；宽表按表头发现注释类别，实际过滤仍完整读取 |
| app/views/assistant.py | bug fix；显示定制结果 warnings/details |
| tests/test_comparison_inputs.py | 新增 4 cases，原始输入/不合法 count/比较方向/按名字对齐 |
| tests/test_workflow_executor.py | 新增多组项目 DEG 子集、PCA 全样本、provenance 回归 |
| tests/test_phase7_e2e_regressions.py | 新增空/非空过滤结果再排序、缺来源不回退、大表发现不扫描回归 |
| tests/test_workflow_chat_ui.py | 扩展 AppTest，验证定制缺失标签警告显示 |
| examples/audit_phase7_e2e.py | 新增只读证据审计，无 API、无 Skill 执行 |
| PHASE7_E2E_REPORT.md | 本报告：实际通过、失败及复测范围 |

没有修改 DEG R 统计方法，没有增加 RNA-seq 模块、任意脚本执行、数据库或部署框架。

## 8. 测试结果与运行方式

项目根目录运行（PowerShell，无需激活虚拟环境）：

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest tests/test_workflow_executor.py tests/integration/test_real_customization.py --run-r -q
.venv\Scripts\python.exe examples/audit_phase7_e2e.py
```

- 最终默认全量：**1191 passed, 96 skipped，119.59 秒**。真实 LLM/R 均跳过；另有 Windows symlink 权限用例跳过，junction 防护另测。
- 本轮显式真实 R 子集：**37 passed，38.21 秒**，不调用 DeepSeek。
- 来源/警告最后局部回归：**5 passed，12.76 秒**，已包含于上述全量。
- 离线故障/grounding：test_failed_sibling_does_not_block、test_failed_parent_skips_children_not_independent、test_workflow_chain_persistence_and_branch_status、test_changed_facts_rejected、test_unsupported_claim_rejected、test_skipped_cannot_be_described_as_success 等均随全量通过。
- 真实 UI E2E 为本次显式授权的在线测试入口，API/R 都是真实的。没有为此重复执行全套 `--run-llm`，避免额外成本；现有付费测试仍必须显式该开关。
- 审计为只读，结果位于 outputs，不会重执行历史任务；其结构 PASS 不替代本报告语义判定。

## 9. 持久化、隔离、下载和 provenance

真实重启：停止已核实的本项目 8501 Streamlit 进程，再以相同虚拟环境、项目根和127.0.0.1重新启动。重新选择主项目后全部 4 个输入恢复；17 个聊天请求及原 workflow/customization/run/source IDs 恢复；5 个 Workflow、7 个 Customization、19 个 ResultManager 条目保持。

主项目重启前后及 UI 恢复后审计完全一致，API requests 保持 37。未自动重新执行历史分析。原火山图可以从 Workflow → Open volcano 再预览、下载。

隔离项目由 UI 新建，输入 0/4，历史空，Results 显示 No analysis results yet；PCA 请求只收到缺输入提示，0 API、0 Skill、0 result。跨项目 source ID 的确定性拒绝由永久单元测试覆盖。本次没有为隔离项目另外上传一套不同真实数据并运行统计，不能夸大为双真实数据集全链验证。

完整 DEG CSV 从浏览器下载至 Downloads，SHA256：`3bd1e05a80461ac59eacf08527410c8c5452d3ad724596dabf6dca91d9398c11`，与原始 Skill 输出一致；不是只下载 100 行预览。

示例 provenance：

```text
原始用户请求
→ workflow ca08b1c0728a438283585a5b57cda98e
→ DEG 0d1e522c-ffb8-467f-b239-2106f987c628
→ filtered_csv 的 up 子集 6474 个（逐 ID 验证）
→ KEGG 2b83b078-3193-4953-b571-92840d612d4f

原 Volcano e378d2af-854c-49c9-9f0d-caa774e7f70c
→ customization 908f29b13cb44563af0e391bc35a0ece
→ red/blue + Top20 + 精确标签请求
→ 新 Volcano 207da68f-79fc-4e49-b27d-4081be5ea483
```

## 10. 性能与 API 成本

以下是实际单机观测，不是稳定 benchmark；包括网络/规划/计算/记录耗时的请求总时长：

| 请求 | 总时长 |
|---|---:|
| OT vs T DEG + Volcano + KEGG(up) | 28.60 s |
| PCA + DEG | 29.80 s |
| DEG + GO(down) + KEGG(down) | 79.56 s |
| O vs OT DEG + Volcano | 25.15 s |
| Volcano 定制 | 4.78 s |
| KEGG Top15 重绘 | 3.92 s |
| 中文注释过滤（旧路径） | 63.38 s |
| 英文关键词过滤（改善后，不同输入） | 23.64 s |
| 原错误来源排序（旧路径） | 62.52 s |
| 正确过滤集合排序（改善后） | 4.17 s |

不同输入/来源有差异，不能从上述数值声称严格倍数性能提升。大 GO mapping 仍明显占用时间；本轮未记录精确上传、首次项目加载和每次预览耗时，不填造数。

本次主项目全部 17 个真实请求（包括失败/澄清/复测）的已记录 DeepSeek usage：

- model：`deepseek-flash`。
- request_count：**37**（含 Router、Planner/Tool call、成功后的 Summary，不等于用户消息数）。
- prompt_tokens：**60,727**。
- completion_tokens：**3,040**。
- total_tokens：**63,767**。
- 隔离项目请求：0 API tokens。

来自持久化响应 usage 的合计，不是账单金额；没有读取/输出 API key，也未估算未提供的单价。重启和浏览未增加 usage。

## 11. 最终 verdict

**NOT READY — 不进入 Phase 8。**

已经证实：真实自然语言多 Skill DAG → 真实计算 → grounded summary → 参数定制/真实注释过滤 → 结果浏览与持久化可用；发现的排序来源 P0 已修复并真实复测，原始结果未被覆盖。

完整 Phase 7 验收仍缺：历史 DEG 在聊天中新调用 Volcano/KEGG 的可靠来源桥接与真实无重跑复测。另需明确处理注释语言提示和测试期已知无效记录，防止把字面零匹配/失效历史误用为科研结论。不能用自动取最新结果、重跑 DEG 或手工改文件来绕过这些问题。
