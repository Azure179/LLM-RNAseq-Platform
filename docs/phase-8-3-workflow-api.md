# Phase 8.3 — Workflow API

日期：2026-10-14。仅开放规划和同步执行两个入口，不整合最终 LLM 总结，不开放 Assistant、Customization、后台任务或部署功能。

## 设计与阶段边界

```text
POST /workflow/plan
  → Project Context + confirmed input metadata
  → existing DeepSeek WorkflowPlanner
  → existing PlanValidator / DependencyResolver
  → ready + canonical validated WorkflowPlan | needs_input | unsupported

POST /workflow/run
  → submitted WorkflowPlan + current Project Context
  → existing PlanValidator / DependencyResolver
  → import hash gate + exact ResultResolver / ProjectHistory
  → existing WorkflowExecutor (once)
  → Skill Registry → real Skills
  → canonical WorkflowResult (API-safe projection)
```

Router 不遍历步骤执行，没有复制拓扑排序、子集提取、失败传播或生信逻辑。服务只负责准备现有后端的输入和边界检查。保留项目真实状态值 `succeeded / partial / failed`，不另造 `success / partial_success` 映射。

## 文件

| 新增／修改 | 作用 |
|---|---|
| 新增 `workspace/workflow_service.py` | 延迟初始化真实 Planner；分别提供 plan/run；复用项目、校验、历史来源和执行器 |
| 新增 `api/workflows.py` | 两个 Router、HTTP 请求／规划响应信封、WorkflowResult 安全视图 |
| 修改 `api/dependencies.py` | 注入共享 WorkflowService |
| 修改 `api/main.py` | 注册入口、主机侧 env_file、DeepSeek 错误处理；启动时不读取凭据 |
| 修改 `api/execution.py` | 补充 Phase 7 计划错误的 HTTP 映射，继续复用 SkillResult 路径脱敏 |
| 修改 `tests/test_api.py` | 保留已有测试并更新路由目录预期 |
| 新增 `tests/test_api_workflow.py` | HTTP 与真实 parser/validator/executor 的离线测试，模拟 Skill 结果和模型响应 |
| 新增 `tests/llm_integration/test_workflow_api_live.py` | 显式真实规划；可再显式启用真实 R；记录原始真实返回及 usage |
| 修改 `README.md`、新增本报告 | 最小示例、接口边界和验收记录 |

本阶段没有修改原 Planner、Validator、DependencyResolver、Executor、Skill、R 脚本或 Streamlit 页面。工作区中先前阶段尚未提交的修改保持不动。

## 请求和返回

规划请求：

```json
{"project_id":"my_project","request":"以T为对照比较OT，画火山图，并对上调基因做KEGG。"}
```

返回信封包含 `status`、`plan`、`required_fields`、`message`、`execution_order`、`dependencies`、`warnings`、`model`、`usage`、`latency_seconds`。`plan` 直接使用 Phase 7 WorkflowPlan；非 ready 时为 null。规划不创建执行结果，不运行 R。请求文本上限与现有 DeepSeekClient 一致，为 4000 字符。

运行请求：`{"project_id":"my_project","plan":<previous response.plan>}`。只支持 Mode A，不接收自然语言直接规划执行。客户端提交的 plan 不能代替本地校验：运行时重新读取项目并检查文件、绑定、业务参数、DAG、历史结果及哈希。跨项目的 plan 或 source 被拒绝。

成功或业务失败均返回原 WorkflowResult；包含 workflow_id、user_request、execution_order、completed/failed/skipped_steps、step_results、outputs、warnings、step_records。`step_records` 承载 provenance，没有第二套 provenance 数据源。运行结果进入原 ResultManager，来源关系与 run_id 保留。

服务端管理文件路径、Rscript、输出目录及执行环境。HTTP 不接受这些参数。workflow_id 是原计划的逻辑标识，不是文件路径；历史绑定 run_id 是已有结果身份，不控制新 Skill 的 run_id。原执行器的目录占用检查保证重复提交相同计划返回 409、不覆盖旧结果。

## 历史结果复用

复用当前项目某次已知 DEG 的 KEGG 上调子集，应在现有 WorkflowStep 使用：

```json
{
  "step_id": "kegg",
  "skill_name": "rnaseq.kegg",
  "input_bindings": {
    "gene_list": {
      "source": "project_result", "run_id": "EXISTING-DEG-ID",
      "output": "filtered_csv", "gene_subset": "up"
    },
    "analysis_genes": {
      "source": "project_result", "run_id": "EXISTING-DEG-ID", "output": "deg_csv"
    }
  }
}
```

`gene_subset` 位于现有 binding，而不是另加在 Skill parameters 中。Manifest 决定合法 output/input 对应，ResultResolver / ProjectHistory 检查结果身份、项目归属、实际文件和记录的 hash。执行器从真实 filtered CSV 派生查询，完整 DEG CSV 提供合格背景，不再次运行 DEG。

当前 Planner 的已有历史范围限制保持不变：不会把历史 run 清单交给模型，也不会自动选择“最新”或猜 source_run_id。自然语言要求复用但未提供可用输入时应请求补充；本阶段的可靠历史复用入口是 Mode A 的显式 project_result binding。这里没有复制聊天路由或新增复杂历史选择机制。

## HTTP 与业务失败分开

| 情况 | HTTP / body |
|---|---|
| 合法执行完成 | 200，status 为 succeeded、partial 或 failed |
| 分支失败 | 200 partial；独立分支继续，保留各步真实错误 |
| root 失败 | 200 failed；依赖步骤 skipped；保留完整步骤状态与来源 |
| 未知 Project / Skill | 404 |
| 非法计划、cycle、output binding、参数、跨项目计划 | 422，沿用 Phase 7 错误码或 INVALID_WORKFLOW_PLAN |
| 缺项目输入、缺上游、来源歧义、输入变更、重复 workflow_id | 409 |
| Planner JSON/Schema/依赖校验失败 | 502，未执行任何 Skill |
| Planner 配置/凭据/网络/限流不可用 | 503，无自动重试 |
| Planner 超时 | 504 |
| 未预期系统失败 | 500，固定安全消息 |

校验错误保留 `details.errors` 中的既有 PlanIssue，但敏感文本会被处理。不会把已执行步骤的失败转换成通用 HTTP 500。KEGG 0 显著通路是 succeeded，不是 failed。

## 安全视图

- WorkflowPlan 只有逻辑引用，输入请求中额外系统字段被拒绝；绝对路径、凭据片段不允许进入计划。
- 每个 step_results 继续使用 Phase 8.2 `public_result`，输出文件须位于当前项目 results 内且实际存在，再转换为项目相对路径。
- step_records 只返回白名单参数、依赖、时间、run_id、状态、来源、校验值及安全输出；移除 inputs/output_dir、进程环境、日志内容等内部信息。
- failed 的错误消息不回传 raw stderr 或 traceback。skipped 步骤保留 blocked_by 和原因，不虚构 SkillResult。
- 规划服务延迟加载 `.env`；健康、元数据、单 Skill 和 Workflow run 不读取 DeepSeek 配置。
- OpenAPI 复用 WorkflowPlan / WorkflowResult Pydantic 定义；没有第二套 Workflow schema。

## 实际测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_workflow.py -q
.\.venv\Scripts\python.exe -m pytest -q
# 明确允许费用：仅真实规划
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workflow_api_live.py --run-llm -q
# 明确允许费用和 R：规划 + 真实多 Skill
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workflow_api_live.py --run-llm --run-r -q
```

离线测试覆盖分支 DAG、PCA + DEG 双 root、needs_input、unsupported、Executor 一次调用、branch/root failure、历史上调 DEG 复用、项目隔离、源文件篡改、cycle、未知 Skill、缺 metadata、缺上游、非法 output、额外依赖、路径参数、重复请求、Planner 错误、无效模型计划、规划期间输入变更和完整 plan/run 往返。

真实测试 **2 passed**：

- 模型：`deepseek-flash`。两次均由 DeepSeek 真正返回 DEG → Volcano + KEGG(up)，没有手工替代模型决策。
- plan-only：Registry 执行 0 次、Executor 执行 0 次。
- 完整运行：Registry 执行 DEG、KEGG、Volcano 各 1 次；两个下游均绑定本次 DEG，互不依赖。运行阶段没有 LLM 调用。
- 数据：公开合成演示矩阵 30 genes、6 samples，每组 3；测试专用 metadata 使用 OT/T 标签，合成 mapping 把 30 个基因关联 map01100。不是未公开科研数据，也不是生物学证据。
- DEG：18 significant = 9 up + 9 down；Volcano：30 plotted，12 NS；KEGG：query 9、mapped query 9、background 30、tested pathways 1、significant pathways 0；Workflow 为 succeeded。
- 执行约 10.23 秒（不含规划）；文件全部实际存在，来源 run_id 一致。
- 费用记录：2 次请求，prompt 9,010 + completion 432 = **9,442 tokens**；两次各 4,721 tokens。未推算货币费用。

本地报告（均在被忽略的 outputs 中）：

- `outputs/deepseek_tests/workflow_api_f02a78fa2a324063a5b1b829b24ea5c8/report.json`：仅规划。
- `outputs/deepseek_tests/workflow_api_e96b4f63920e4858ad6e861a5a62a908/report.json`：真实完整流程。

首次测试在准备演示 metadata 时遇到空白行，尚未调用 API 即失败；测试准备代码改用标准 csv.DictReader 后通过。没有改变生产输入校验或结果文件。

最终回归：

| 检查 | 结果 |
|---|---|
| 全量默认 pytest | **1296 passed, 103 skipped, 0 failed**，260.72 秒；默认不调用真实 API/R |
| 最后一次 API 定向回归 | **92 passed**，45.40 秒；包括新增 Workflow API 29 项 |
| 分支失败回归 | 强制 Volcano 先失败，再执行 KEGG；最终 partial，未阻断独立分支 |
| 真实 DeepSeek／R | **2 passed**，15.47 秒；详见上面的 usage 与实际结果 |
| 真实 Uvicorn HTTP | docs 200；两个新接口出现在 OpenAPI；未知项目规划请求 404；已执行 workflow 重复提交 409，未重跑分析 |
| 临时服务 | 仅本机端口 8000；检查后关闭，不影响原 Streamlit 服务 |

2 条依赖弃用警告与上一阶段一致：Starlette TestClient 的 httpx 兼容提示、anyio BlockingPortal 别名。跳过项包括显式 R/LLM 测试及 Windows symlink 权限测试，未降低测试要求来消除跳过或警告。

**Phase 8.3 Verdict: PASS（本地阶段验收，不是稳定生产版本）。** 原 Streamlit/Chat/Workflow 自动回归通过。未进入 Phase 8.4，未提交或推送 GitHub。

## 当前限制

- 本机同步服务，没有身份认证、并发作业管理、取消、断线恢复或后台执行；只绑定 127.0.0.1，不应公开到网络。
- 规划与运行之间允许用户检查/修改计划；运行仍必须重新校验。HTTP 200 不等于所有分析成功，要检查 WorkflowResult.status。
- 原始完整 provenance 仍保存在本地，HTTP 是脱敏视图；产物引用不是下载接口。
- 无最终 Workflow LLM 总结、Assistant API、Customization API、Agent、Docker、数据库或部署功能。
- 本阶段未重做 Streamlit 浏览器真实 E2E，使用现有回归测试检查兼容性。Phase 8.4 未开始。
