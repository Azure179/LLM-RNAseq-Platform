# Phase 8.4 — Assistant / Natural Language API

日期：2026-10-14。范围：已有本地项目的同步自然语言分析入口。不新增统计方法、上传 API、数据库、后台队列或部署功能。

## 设计

```text
POST /assistant/analyze
  → AssistantService
  → existing ChatController / ChatStore (project-local lock and request identity)
  → real DeepSeek ChatRouter
     ├─ single_skill → ToolRunner validation → Phase 8.2 SingleSkillService
     │                 → Registry → SkillResult → Single FinalResponseBuilder
     ├─ workflow → Phase 8.3 WorkflowService.plan
     │              → existing Planner / Validator / DependencyResolver
     │              → WorkflowService.run → Executor → Registry
     │              → WorkflowResult → WorkflowFinalResponseBuilder
     ├─ result_reuse → existing ResultResolver → explicit project_result bindings
     │                 → WorkflowService.run → WorkflowFinalResponseBuilder
     ├─ customization → existing CustomizationPlanner / CustomizationEngine
     │                  → parameter_override or annotation_filter → grounded local receipt
     └─ needs_input / unsupported → no execution
  → existing persisted chat result
  → API-safe response
```

HTTP router 不选择 Skill，不复制 DAG、基因子集准备、统计分析或总结逻辑。SingleRequestRunner / ToolRunner 新增**宿主代码注入**的执行回调，只有经过原工具参数、上下文与完整输入校验后才调用 Phase 8.2 服务；模型和 HTTP 请求不能提供该回调。未注入时保留 Streamlit 原执行方式。

WorkflowService 可接收宿主已有 Planner 实例，避免在同一个请求里建立另一套 client / 配置。API ChatDispatch 通过它规划和执行；Streamlit 未注入此服务时保留原路径。历史复用也重新核验项目、输入、来源文件及绑定，不补跑 DEG。

## 请求、状态与来源

```json
{
  "project_id": "my_project",
  "request": "以T为对照比较OT，画火山图，并对上调基因做KEGG。"
}
```

project_id 指向已经通过现有工作区创建、导入数据的项目。request 为 1–4000 字符。客户端不能提供 output_dir、Rscript、API key、任意文件路径、运行环境或新 run_id。额外字段被拒绝。

可选 request_id 是现有 ChatStore 的 canonical UUID，不是新会话系统。建议请求前生成并保存；同一项目/ID/文本重放返回已有结果，不重新调用模型或 Skill。相同 ID 改换文本返回 REQUEST_ID_CONFLICT，原记录保留。异常中断的 submitted 标记仍阻止重复执行。

来源有歧义时返回 source_options 和 request_id。客户端选择候选 source_run_id，用原 ID 和原文本继续；新的请求不能随意用该字段绕过来源选择。没有 conversation_id，因为后端不存在独立多用户会话。API 和 Streamlit 共享项目聊天历史，“刚才”使用已有聊天来源链，不等于全局最新结果。

响应包括 status、request_type、project_id、request_id、response、required_fields、source_options、artifacts、warnings、summary、steps、error_code、stage、final_response_status、usage、request_count、duration_seconds，以及按需出现的 workflow_id / run_id / customization_id / source_run_id。

这个新入口把宿主 `succeeded / partial` 展示为 `success / partial_success`；没有修改原 SkillResult / WorkflowResult。request_type 额外保留既有 `result_reuse`，便于识别历史来源。步骤状态仍使用原模型。

## Grounding 和错误

- 单 Skill 和 Workflow 分别复用 Phase 6.4、7.5 的 formatter、事实约束与 grounding validator。
- 仅发送有限 summary facts，不发送完整矩阵、DEG 大表或主机路径。
- 总结校验失败返回 LLM_RESPONSE_VALIDATION_FAILED 和本地安全提示；不返回被拒绝文本。已生成 artifacts、run_id、workflow_id 保留，避免误重跑。
- 分支失败仍允许独立分支成功；failed/skipped 不被写为成功。根失败使用已有本地模板，不额外请求生物学解释。
- 0 显著 KEGG 通路是成功空结果，不是失败。
- Customization 延续既有确定性本地回执，不为本阶段新增一次模型总结或任意代码执行。
- API 只返回项目内相对 artifact 引用，并重新检查其位置。自由文本经过安全投影；带主机路径的诊断行隐藏，保留其它已验证段落。

| 情况 | HTTP / 业务结果 |
|---|---|
| 项目不存在 | 404 / PROJECT_NOT_FOUND |
| 请求字段、路径、request_id 非法 | 422 / INVALID_REQUEST |
| 未知模型工具 | failed / SKILL_NOT_FOUND，未执行 |
| 非法工具参数 | failed / INVALID_PARAMETER，未执行 |
| 缺项目数据 | needs_input / MISSING_PROJECT_INPUT + required_fields |
| 缺兼容上游 | needs_input / MISSING_UPSTREAM_DEPENDENCY |
| 多个兼容来源 | needs_input / AMBIGUOUS_UPSTREAM_RESULT + source_options |
| 非法计划 | failed / WORKFLOW_VALIDATION_FAILED，未执行 |
| 请求 ID 冲突、来源选择冲突、项目 Chat 忙 | 409，保留错误码 |
| LLM 超时 / 网络或限流 / 非法响应 | 504 / 503 / 502（尚无分析 artifacts 时） |
| 已完成分析的部分成功、失败、总结失败 | 200 + 明确业务状态、步骤及错误码 |
| 无法预期的服务异常 | 500，通用错误信封，不泄漏 traceback |

不能只检查 HTTP 200。特别是总结失败时，不应把已成功分析描述为“从未执行”，也不应自动重试。

## 新增与修改文件

| 文件 | 本阶段作用 |
|---|---|
| 新增 `workspace/assistant_service.py` | 复用 ChatController，注入 Phase 8.2/8.3 服务；延迟创建客户端 |
| 新增 `api/assistant.py` | 请求与响应 schema、薄 router、安全投影与 HTTP 状态 |
| `api/main.py`、`api/dependencies.py` | 生命周期注册、依赖注入、OpenAPI |
| `llm/single_request.py`、`llm/tool_runner.py` | 可选宿主执行回调，原验证边界不变 |
| `workspace/workflow_service.py` | 可选已有 Planner 参数，复用同一 client |
| `workspace/chat_controller.py` | 可选共享服务、主机配置注入、缺失字段传递、请求内容冲突保护 |
| `workspace/chat_dispatch.py` | API 使用共享 WorkflowService；保留最终失败错误码 |
| `workflow/planner.py` | 保留本地 validator 的结构化 issue，避免 API 丢失具体缺失输入；不改变规划或校验规则 |
| 新增 `tests/test_api_assistant.py` | 25 项离线 HTTP 回归用例 |
| 新增 `tests/llm_integration/test_assistant_api_live.py` | 显式真实分类/规划和完整 LLM + R E2E |
| `tests/test_api.py`、`tests/test_workspace_chat.py` | 新路由目录及请求内容冲突回归 |
| `README.md`、本文件 | 调用方法、边界、证据和阶段报告 |

没有修改 Skill 核心统计逻辑或 R 脚本，没有改动 Streamlit 页面。工作区中前几阶段未提交的修改保留，不属于本阶段新增工作。

## 测试记录

离线 Assistant 定向测试：**25 passed**。模拟 provider 响应和统计执行，实际运行解析、Schema、服务、Workflow executor、历史来源、注释筛选和 grounding。

覆盖单 PCA、分支 Workflow、绘图参数定制、性别分化注释筛选、缺比较方向、unsupported、历史 DEG 复用、来源歧义及继续选择、跨项目隔离、缺 metadata、非法参数/宿主路径/未知工具、模型编造数字、根失败/分支失败、LLM timeout/network/rate limit、重复提交、OpenAPI 和安全响应。

全量离线回归：**1321 passed、105 skipped、2 warnings，216.06 秒**。跳过项包括未显式开启的 API/R 集成和一个 Windows 符号链接权限测试；junction 防护另有测试。现有 Streamlit AppTest、Chat、Result Reuse、Schema、Workflow 和 Customization 回归全部通过。`git diff --check` 通过。

真实 API 测试使用项目已有**公开模拟 demo**（30 genes、6 samples、T/OT 各 3），以及明确标记的 synthetic mapping；不是未公开科研数据，不据此宣称生物学发现。统计结果由真实程序计算，不是预先填写或用 mock 替代。

| 实际测试 | 结果 | 请求数 | prompt / completion / total tokens |
|---|---|---:|---|
| 真实分类 + 真实规划；测试保护在执行前停止 | PASS | 2 | 7018 / 273 / 7291 |
| 首次完整 E2E | 分析/总结成功，但测试来源字段断言错误 | 3 | 7790 / 535 / 8325 |
| 修正断言后完整 E2E | PASS | 3 | 7797 / 559 / 8356 |

模型：`deepseek-flash`。包括首次失败测试的实际费用记录：**8 次调用，22605 prompt、1367 completion、23972 total tokens**。没有换算货币成本。最后一次完整请求耗时约 **14.428 秒**；仅作为 demo 观测值，不是性能承诺。

完整 E2E 真实执行 DEG、KEGG、Volcano 各一次，兄弟步骤以现有拓扑顺序串行执行：

- DEG：30 个有效检验基因，18 个显著，9 上调 / 9 下调。
- Volcano：30 个绘图基因，9 Up / 9 Down / 12 NS。
- KEGG：使用上述 DEG 的 9 个真实上调基因；9 个映射；背景 30；检验 1 个 synthetic pathway 分组；0 显著通路。
- DeepSeek 最终回复正确保留空结果、上调子集和真实数值；grounding 通过；输出文件均存在。
- 原始模型返回、计划、WorkflowResult、响应与 usage 保存在本机忽略目录，不进入公开仓库：
  - `outputs/deepseek_tests/assistant_api_87191ddfd5b74a98998619baf6f11e7e/report.json`
  - `outputs/deepseek_tests/assistant_api_bfe2a30e4efc478baf1734034689c683/report.json`（保留首次失败测试）
  - `outputs/deepseek_tests/assistant_api_9639345f564147449aacaf688de3ded6/report.json`（完整 PASS）

## 发现的问题与修复

1. 重放旧 request_id 但修改请求文本，原来会静默返回旧答案。现在拒绝冲突，保留原答案和不重复执行保障；永久回归测试已更新。
2. Planner 原先只保留错误码，API 无法指出具体缺 metadata。现在向 Controller 传递 validator 已有 issues，不重新验证或猜测字段。
3. Workflow 的安全本地失败文本非空时，原来 ChatResult 可能丢失 final error_code。现在保留，便于 API 正确展示失败。
4. 接入期间给不可变上下文赋值导致离线失败：改为通过原 Pydantic model 创建验证后的新上下文。没有降低 validation。
5. 测试 fixture 直接替换不可变 SkillResult.summary、用错 ProjectContext 参数名、沿用旧 OpenAPI 路由列表：修正测试接线。
6. 首次真实 E2E 断言读错来源层级：raw WorkflowResult 的来源在 `upstream_sources.gene_list.source_run_id`，不是 API projection 的顶层字段。修正断言后重新执行，未手改结果文件。

## 运行与限制

```powershell
.\.venv\Scripts\python.exe -m uvicorn bulk_rnaseq.api.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m pytest tests/test_api_assistant.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_assistant_api_live.py --run-llm -q
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_assistant_api_live.py --run-llm --run-r -q
```

`/docs` 包含 request/response/error schemas。服务端使用已有 `.env`；启动和元数据查询不调用 DeepSeek。默认 pytest 不访问付费 API 或 R。仅 `--run-llm` 的测试在执行边界用显式测试保护停止，不是生产 API 的 dry-run 功能。

- 本地同步服务，无身份认证；只能绑定回环地址，不可直接暴露公网。
- 输入导入、结果浏览/下载继续使用现有本地工作区；本阶段没有 HTTP 文件上传/下载接口。
- project_id 仍需要客户端知道。聊天历史按项目共享，不是多用户隔离的会话服务。
- 模型失败不自动重试，不执行任意代码，不修改生产脚本；新请求 ID 意味着新的请求，不保证语义重复去重。
- request_count/usage 沿用既有后端记录；provider 没有返回 usage 或规划校验抛出前未汇入统计时，不能把这些值当成完整账单。真实测试额外在 client 返回边界记录费用证据。
- 本阶段真实 E2E 覆盖复合 DEG/Volcano/KEGG。PCA、定制、复用等接口分支本阶段为离线回归；没有声称全部自然语言变体都经过此次真实 API 测试。
- 依赖库存在既有 Starlette/httpx 与 anyio deprecation warnings；未为消除警告更换依赖。

## Verdict

**Phase 8.4：PASS（本地同步 MVP 范围）。** 默认离线全量回归、显式真实分类/规划和完整 DeepSeek + R + grounded final response 均通过。不是生产部署认证，也不代表所有自然语言请求都可无误理解。

停在 Phase 8.4，没有进入 Phase 8.5；没有 commit、push 或修改 GitHub Release。
