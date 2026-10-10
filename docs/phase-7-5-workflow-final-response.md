# Phase 7.5 — Workflow Final Response

日期：2026-10-08。范围：已有 WorkflowResult → 真实 DeepSeek → Grounded Final Response。

## 修改文件

| 文件 | 作用 |
| --- | --- |
| `src/bulk_rnaseq/workflow/final_response.py`（新增） | Workflow 压缩、记录一致性校验、失败本地模板、单次 DeepSeek 请求、最终输出模型 |
| `src/bulk_rnaseq/llm/final_response.py`（小改） | grounding 接口接受通用 facts 对象及可指定的连接词集合；单 Skill 默认行为不变 |
| `tests/test_workflow_final_response.py`（新增） | 35 个免费单元测试，含失败/跳过、数字交换、非法 JSON、安全与预算边界 |
| `tests/llm_integration/test_workflow_summary_live.py`（新增） | 3 个 `--run-llm` 用例，读取 Phase 7.4 已保存的真实 R 结果，禁止任何分析执行 |
| `README.md`（修改）、本文（新增） | 使用入口和验收报告 |

没有修改 WorkflowPlan / WorkflowResult、Executor、Registry、Skill、R 脚本、统计逻辑、DeepSeek client 上限或 API 配置。没有新增依赖。

## 架构

```text
已保存 WorkflowResult
  → 原 WorkflowResult/Pydantic 再验证
  → step_records 与 SkillResult 身份、状态、run_id、outputs 一致性检查
  → 复用 Phase 6.4 Skill formatter，生成逐步骤事实
  → 全失败：本地模板，零 API
  → 成功/部分成功：一次无 tools 的 DeepSeek JSON 请求
  → 原 grounding 校验（本阶段不允许额外连接词）
  → 本地追加错误原因、warnings
  → WorkflowFinalResponseResult
```

模块不读 CSV/PNG/大表，不调用 Planner、Registry.execute 或 WorkflowExecutor。测试在免费层和真实 API 层都禁止了 Registry/Workflow 执行与 subprocess，防止不小心重跑分析。

### 压缩给 DeepSeek 的内容

- 原始 user_request：来自 WorkflowResult，而不是另造问题替换原意图。
- workflow_id、真实 workflow_status、每步 step_id / skill_name / status。
- 白名单 metrics 与已记录参数，使用现有单 Skill formatter；未知参数明确说未提供，不猜默认值。
- 每步完整事实段落：身份 + 状态 + 数字，防止多个 Skill 的相同数字/字段串错。
- GO/KEGG 的 gene_subset 只从相互一致的 input_bindings / upstream_sources 获取；当前 Workflow 来源还要匹配成功 DEG 的真实 run_id。不从用户请求推断已经执行的方向。
- artifact 仅发送逻辑名称，如 plot / deg_csv；不发送本机路径、文件内容或 stdout/stderr。
- warnings/错误原文可能含本机路径，**不发 API**，只发送本地注意事项数量；最终由本地原样追加。Workflow 已聚合的同一 Skill warning 去重一次，仍保留 count。

本版与 Phase 6.4 一致，**summary-only，没有 top gene/pathway 名称**。模型必须明确不提供名称，不能凭空补写“主要通路”。没有为了取得 top results 再读大表或重新计算。

### Prompt 与 grounding

Prompt 要求 JSON 对象只有 final_response，逐条 facts 原文恰好一次，允许排序和标点，不允许任何新增词句或解释；把 user_request、group label 当数据而非指令。

Grounding 复用已有“移除全部已验证事实，检查剩余字符”的机制。本阶段禁用单 Skill 的“分析已成功完成”等可选连接词，避免部分成功时误导。它不仅检查数字集合，还检查数字与标签/步骤的绑定；即使 8 和 10 都是真实数字，交换上下调仍被拒绝。新增 TP53、map00010、因果断言、失败改成功、跳过改成功、遗漏状态或重复事实都返回 `LLM_RESPONSE_VALIDATION_FAILED`，final_response 留空，不展示不可靠文本。

这是保守的受约束文字生成，不是通用 NLP 真伪判别器；它不能证明上游统计结果本身真实，也不允许自由改写科研解释。已保存的实际 SkillResult 是证据来源。

## 状态与错误

沿用既有工作流状态 `succeeded / partial / failed`，不再增加 partial_success 同义值。

| 场景 | 输出行为 |
| --- | --- |
| 全成功 | DeepSeek 总结每步实际指标 |
| Volcano 失败，DEG/KEGG 成功 | 显式“工作流部分成功，未全部完成”，列成功与失败步骤；本地追加失败原因 |
| DEG 失败，下游 skipped，且无其他成功步骤 | 不调用 DeepSeek；本地列失败步骤、原因、跳过步骤及 blocked_by |
| 一个 root 失败但独立 PCA 成功 | partial，总结 PCA 并明确其他失败/跳过，不伪装全部成功 |
| KEGG 成功但 n_enriched=0 | 保留成功；说明当前阈值下无显著富集结果 |
| API/grounding 失败 | 只让总结失败，不修改、重跑或丢弃原 WorkflowResult |

`WorkflowFinalResponseResult` 继承 Phase 6.4 FinalResponseResult，增加 workflow_id / workflow_status：

```json
{
  "status": "succeeded",
  "workflow_status": "partial",
  "workflow_id": "existing-workflow-id",
  "final_response": "工作流部分成功……",
  "grounded": true,
  "request_count": 1
}
```

其中 status 是**总结生成状态**，workflow_status 是**分析执行状态**，不能混用。完整模型还包含 request_id/model/usage/latency/error_code/verified_context 等现有字段。全失败模板 status=failed、workflow_status=failed、error_code=WORKFLOW_FAILED、request_count=0、model/usage=null；grounded=true 表示本地证据模板，不是模型成功。

格式错误、记录矛盾、缺关键指标在请求前拒绝；错误码包括 INVALID_WORKFLOW_RESULT / 既有 INVALID_SKILL_RESULT。上下文超限返回 WORKFLOW_SUMMARY_TOO_LARGE，不静默删步骤或 warning。

## 如何调用

从项目根目录的宿主代码调用；这里只读取已有结果：

```python
import json
from pathlib import Path
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.workflow.final_response import WorkflowFinalResponseBuilder

saved = Path("workspace") / project_key / "results" / "workflows" / workflow_id / "workflow_result.json"
workflow_result = json.loads(saved.read_text(encoding="utf-8"))
client = DeepSeekClient(load_deepseek_config(Path(".env")))
response = WorkflowFinalResponseBuilder(client).build(workflow_result)
# 宿主展示 response.final_response；若为空，展示 error_code/message。
# 不应因总结失败再次调用 WorkflowExecutor。
```

API Key 仍由原 .env/环境变量读取，不进入 Prompt 或审计记录。模块记录 request_id/workflow_id、执行状态、总结状态、model、usage、latency；原始用户请求只送 API，不额外写入模块日志，调用方本来就持有保存的 WorkflowResult。

## 测试命令

```powershell
# 默认免费，无 API、无 R
.venv\Scripts\python.exe -m pytest -q

# 本阶段及原单 Skill 总结回归
.venv\Scripts\python.exe -m pytest tests/test_workflow_final_response.py tests/llm/test_final_response.py -q

# 仅本阶段的真实 DeepSeek 总结测试：3 次请求，不重跑 Workflow
.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workflow_summary_live.py --run-llm -q
```

真实测试需要已有 `outputs/workflow_tests/*/integration_report.json`。缺少时明确 skip，不偷偷运行 R 补结果。它选择对应 Phase 7.4 case 的最新成功测试报告；这只是测试 fixture 查找，不是产品中的历史分析自动选择。

## 验收记录

- 新增免费测试 35 个；与原单 Skill 总结测试合计 **81 passed**。
- 最终全项目默认回归：**1132 passed，74 skipped，68.76秒**，无失败；跳过包括显式 opt-in 的真实 R/API 测试及一个需要本地权限的 Windows symlink 测试。
- 覆盖用户要求的全部 6 类：全成功、部分失败、根失败/下游跳过、成功空 enrichment、独立根、伪造数字拒绝；另覆盖错误状态、具体基因/通路凭空出现、JSON 错误、API 失败、不重试、证据矛盾、warning 去重、安全与容量边界。
- 真实 DeepSeek **3/3 通过**；warning 去重后复测 **3/3 通过**，模型均为 API 返回的 deepseek-flash。
- 部分失败/全失败场景是明确标注的单元 fixture；没有修改真实 WorkflowResult 制造一次“真实失败”或伪造分析结果。
- 本阶段 **R/Skill/Workflow 执行次数为 0**。

真实测试读取 Phase 7.4 demo 的真实程序结果（30 genes，6 samples，合成 KEGG 测试关联），不是本阶段新增真实数据实验。DeepSeek 保留了：DEG 18（上调9、下调9），KEGG input/mapped 9、background30、显著通路0，PCA PC1 98.40%、PC2 1.12%。实际组名是 treatment / control，不冒充 OT/T。

最新真实报告：

| Case | 结果 | Prompt | Completion | Total | 报告目录（outputs/deepseek_tests/phase_7_5 下） |
| --- | --- | ---: | ---: | ---: | --- |
| DEG + Volcano | PASS | 600 | 173 | 773 | `971d40b369af438896c7ededf8314be3` |
| DEG + KEGG(up)，空富集 | PASS | 649 | 197 | 846 | `95f4892987c14583a62a86dc5e43f732` |
| DEG 分支 + 独立 PCA | PASS | 861 | 307 | 1168 | `8a04b9d397c140eb9f916bae8f53abff` |

每个目录含 summary_test.json，记录原始结果报告路径/校验和、最终模型回答、verified_context、usage、latency、pass/fail。最新3次共 2787 tokens；本阶段两轮 **6次真实请求合计 prompt4220 + completion1355 = 5575 tokens**，无自动重试。未按价格猜测金额。

真实返回内容节选（没有由开发助手替代生成）：

> 工作流全部步骤执行成功。步骤deg（rnaseq.deg）执行成功；分析基因数为30；有有效pvalue的基因数为30；差异基因数为18；上调基因数为9；下调基因数为9

完整回答及本地追加 warnings 在上述报告内。

## 当前限制

- 摘要文字受严格事实契约约束，不提供自由生物学解释或 top gene/pathway 名称。
- 继承 client 的 1024 输出 token 上限；facts 总长最多1800字符、context JSON最多14000字符，本地 notices最多16000字符。大 Workflow 不静默省略；超限拒绝，输出被截断也拒绝，无重试。
- 原始 user_request 最多4000字符；老 WorkflowResult 没有原请求时不臆造请求。
- 只报告运行记录声明的 artifact，不重新检查今天文件是否仍存在。文件访问由 Results/ResultManager 负责。
- 没有把 Planner/Executor/Summary 自动接成新的 UI 按钮，没有 Streamlit Workflow UI、Agent、脚本修改或自动分析。

## 验收结论：Phase 7.5 PASS

多步骤统一总结、真实证据约束、部分成功/失败/跳过状态、成功空富集、容量限制、grounding、usage 与免费默认测试均已验证。实际观察到的重复 warning 展示已通过精确匹配去重并加入回归测试；保留原文和 count，没有改变分析行为。真实 API 测试没有失败案例，没有靠放松 grounding 通过测试。

这里的 PASS 限于本阶段后端总结接口，不代表 Workflow UI 已实现。没有进入 Phase 7.6。
