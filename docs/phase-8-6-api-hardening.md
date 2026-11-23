# Phase 8.6 — Unified Error Model + API Validation & Security Hardening

日期：2026-10-15。只调整 HTTP 边界、错误契约、校验和日志脱敏。不修改 RNA-seq 统计方法，不增加部署、认证、任意代码执行或新分析能力。

## Inspect 与设计原因

Phase 8.1–8.5 已有严格的部分 Pydantic 请求、Manifest 参数白名单、统一的部分错误映射、项目文件校验及安全响应投影，但存在以下不一致：

- 普通异常使用 error envelope，Skill 的非 2xx 返回 SkillResult，Assistant 的非 2xx 返回 AssistantResponse。
- HTTP request_id 已生成在响应头和日志，却未进入错误 body；还缺少耗时。
- DeepSeek 的 HTTP 状态判断分散在两个入口。
- 未知业务参数与系统管理参数都笼统返回 INVALID_PARAMETER。
- Results query 未禁止多余字段；非有限数值、空白请求、数值类型的报错缺少统一边界。
- 脱敏函数主要处理常见路径与 sk- 字符串，缺少 Authorization、.env/runtime、编码路径、已知 opaque secret 和请求内后端日志防护。

采用三个小模块：errors 管理 HTTP 错误，validation 管理传输边界，security 管理脱敏与请求上下文。业务 schema 和统计校验继续使用原有定义，不创建第二套 Skill 参数默认值。

## 统一错误模型

```json
{
  "error": {
    "code": "PROJECT_NOT_FOUND",
    "message": "The requested project was not found.",
    "details": null,
    "request_id": "HTTP-request-UUID"
  }
}
```

ApiError 的 code/message/request_id 必需；details 为 null、受限对象或安全字段提示列表。ApiErrorResponse 是唯一非 2xx OpenAPI 错误模型。原 ErrorDetail / ErrorResponse Python 名称作为 alias 保留，不维护第二套模型。

固定错误消息不直接采用 exception message。Pydantic 422 只公开 field 和受控 message，最多 30 条，不返回 input、ctx 中的异常对象、URL 或原始提交值。未知错误统一 INTERNAL_SERVER_ERROR。

## 状态映射与业务状态

单一 `api/errors.py` 负责 domain / LLM / Assistant transport 错误策略：

| 情况 | HTTP / code |
|---|---|
| Project / Skill / Result / Artifact 不存在 | 404，对应 *_NOT_FOUND |
| 非法结构、类型、参数 | 422 INVALID_REQUEST / INVALID_PARAMETER |
| 多余参数 | 422 UNKNOWN_PARAMETER |
| 系统管理参数 | 422 FORBIDDEN_PARAMETER |
| 相同 treatment/control、非法 group | 422 INVALID_COMPARISON |
| 输入内容非法 | 422 INPUT_VALIDATION_FAILED |
| 非法 Workflow / Customization | 422 INVALID_WORKFLOW_PLAN / INVALID_CUSTOMIZATION_PLAN |
| cycle、无效 output binding | **422** WORKFLOW_CYCLE_DETECTED / INVALID_OUTPUT_BINDING |
| 缺项目输入、缺上游、来源歧义 | 409，对应既有错误码 |
| 输入绑定变化、重复提交、项目忙 | 409，保留对应错误码 |
| R 执行失败 / 模型输出非法或 grounding 失败的服务错误 | 502，保留已知错误码 |
| DeepSeek 网络/配置/鉴权/余额/限额不可用 | 503，保留既有 LLM 错误码 |
| DeepSeek / R 超时 | 504 |
| 未知内部异常 | 500 INTERNAL_SERVER_ERROR |

Cycle / invalid binding 保持 422：这是提交计划本身无效，不是服务器状态冲突。未按提示词机械改为 409。

HTTP 200 的 Workflow / Assistant / Customization 业务结果不因 failed / partial / needs_input / unsupported 自动变成 500。已生成结果但总结被拒绝时保留真实结果，不能据此自动重跑。无 artifact 且属于 LLM 服务错误的 Assistant 请求，按已有边界返回非 2xx，但 body 现在统一为 error。

### 明确的兼容变化

- 非 2xx Skill Run 不再直接返回 SkillResult；已保存失败 run 的 run_id / workflow_id / project_id 放入 error.details（500 不公开内部 details）。内部 SkillResult 和 Results 记录不改变。
- Assistant 409/502/503/504 不再返回旧 AssistantResponse；可用 error.details.assistant_request_id 关联原防重复提交记录。
- 新增 error.request_id 和显式 details:null；旧 INTERNAL_ERROR 改为 INTERNAL_SERVER_ERROR。
- 细分 UNKNOWN_PARAMETER / FORBIDDEN_PARAMETER；更新原测试对这些契约的断言，保留其执行次数、来源、安全及持久化断言。
- 成功响应、Skill / Workflow / Customization 内部业务状态、数据及统计计算不变。

## 请求校验

SkillRunRequest、WorkflowPlanRequest / WorkflowRunRequest、AssistantRequest、CustomizationPlanRequest / CustomizationRunRequest 通过 ApiRequest 共享 strict=True、extra=forbid、有限数值、递归路径/凭据/系统字段拒绝。

Skill 业务参数仍读取现有 ToolAdapter / Manifest schema；边界检查未知字段、空白字符串、范围、类型和实际 int，不把 `2.0` 或 `"2"` 当成 Top N。执行前 SingleSkillService / PlanValidator 再次校验。Workflow 参数名称来自原 DependencyContracts，绑定、DAG、参数值与输入继续由 Phase 7 Validator 决定。Customization 仍使用原 catalog / Pydantic 及不可改写的已批准计划。

Results query 使用禁止 extra 的 Pydantic model，限制 result_type/status、Skill 名称及 limit 1–200。HTTP query 本来是字符串，limit 仅允许十进制整数字符串转换，不接受 1.0 / 1.5 / true。JSON body 不做类似宽松类型转换。

系统字段清单在 `api/validation.py`：project_root、output_dir、runtime_dir、runtime、Rscript/rscript_executable/rscript_path、python_executable、executable、api_key/deepseek_api_key、env/environment/env_file、shell/shell_command/command、run_id、workflow_root、temp_dir/temp_directory、timeout/timeout_seconds、count_matrix_path/metadata_path、authorization/password/token/credential/credentials。大小写不敏感。

两个必要例外：

1. 正式 `input_bindings` 的目标字段名可以是 count_matrix_path 等，但值必须是原有 binding 对象，不能是本地路径。
2. `source=project_result` 的结构化 binding 内 run_id 表示历史来源，不是指定新的运行目录。保留该 ID，并继续核验归属和产物。

源 source_run_id、已批准 plan_id、WorkflowPlan 原有 workflow_id 同样是受校验身份，不等于开放路径或任意运行配置。没有 blanket 删除这些必要的历史复用能力。

## Path / Secret / Traceback 防护

- 共用 safe_value 递归处理 API 动态结果、错误 details、warnings 和诊断文本，移除敏感字段。
- 检测 Windows/Unix 绝对路径、反斜杠、traversal、最多三层百分号编码、.env、runtime 引用、常见 token 前缀及 Authorization/密码赋值等内容。
- HTTP 上下文补充敏感环境变量的已知非短值及 Authorization header 值的精确匹配；不输出这些值，也不读取 .env 文件内容建立日志。
- artifact 相对引用继续交给 ProjectManager / ResultManager 的 containment、链接防护及项目隔离校验。不能靠替换字符串把越界文件变成合法引用。
- 不新增下载/preview 接口，因此没有客户端任意文件路径读取入口。
- 未知异常及 ResponseValidationError 通过最外层安全错误边界返回 500；不返回 traceback、exception 原文、stdout/stderr 或异常附带敏感 header。

该脱敏是受控 disclosure policy，不是对任意文字的秘密识别保证。未知且未标记的 opaque secret、数据内隐私、生物学敏感性不能靠正则完全识别；客户端不应提交凭据或未获授权的研究信息。

## Request ID 和日志

- 每次 HTTP 请求由服务生成 UUID，忽略客户端 X-Request-ID；body error.request_id 与响应头一致。
- 与分析 run_id、workflow_id、customization_id 和 Assistant 幂等 request_id 明确区分。
- API access event 仅记录 method、路由模板、status、request_id、duration_seconds，不记录真实 URL 参数、Authorization、body 或矩阵。
- 使用 ContextVar 将安全上下文传递给请求内的 worker 线程。一次安装的 LogRecord factory 仅在 API 请求期间脱敏后端日志、屏蔽完整 user/arguments 文本及 traceback，限制单条文本长度并附 HTTP request_id。
- 请求结束恢复上下文；Streamlit / CLI 在 API 请求外的日志行为不变，已有本地文件、业务记录和 provenance 不被清洗或覆盖。
- Uvicorn 自带 access log 位于应用上下文之外，启动命令继续使用 `--no-access-log`。部署者启用额外代理/SDK/debug 日志会形成额外安全边界。

## 测试

新增 `tests/test_api_security.py` 和 `tests/test_api_error_mapping.py`：

- 所有主要端点的统一 error envelope、request_id/header 关联、OpenAPI schema。
- 禁止系统字段、unknown 字段、嵌套字段、类型/范围/blank/limit 校验。
- Windows/Unix/编码路径、.env、runtime 引用、跨项目读取拒绝。
- 真实本地 Workflow Validator 拒绝 cycle / 无效 binding / 模型注入的 host settings。
- Customization 未知 mode 和 command 注入拒绝，原计划不可修改。
- 模拟 domain / HTTP / internal exception，确认 secret、路径、traceback 不进入 HTTP 或请求日志。
- 成功 warnings 的已知 opaque secret 脱敏；请求结束不污染非 API 日志。
- 语义与状态 mapping 参数化测试，默认网络/R guard 保持启用。

本阶段不调用真实 DeepSeek、不运行真实 R：API token 使用为 0。真实 LLM/R 开关仍沿用 --run-llm / --run-r。

测试过程记录：原 144 项 API 回归通过。首轮旧断言失败主要来自明确的 wire-contract 变化；另发现新通用字段 guard 误拒绝合法 count_matrix_path binding，已限定为“逻辑绑定对象”并通过原 plan/run round-trip 回归。首批新增安全/映射测试 110 项通过，随后补充 query integer、恶意模型 Workflow 与字段提示测试。

最终全量测试：**1,465 passed、109 skipped、2 warnings，226.58 秒**。默认网络/R guard 保持启用。109 个跳过项主要为 opt-in LLM/R 测试，另有一个 Windows 符号链接权限测试；junction 防护另有已通过测试。两项 warning 为 Starlette/httpx 和 AnyIO 弃用提示，未为此升级依赖。

最终专项安全/映射测试：**117 passed，31.77 秒**。原 Phase 8.1–8.5 API 回归 **144 passed**；全量包含原 Streamlit/Chat/Results 测试。语法编译及 diff whitespace 检查通过。本阶段未重跑真实 API/R 或完整手工 UI E2E，不据此宣称真实数据分析重新验收。

## 文件变更

新增：

- `api/errors.py`：从 execution.py 提取原 mapping，合并 LLM/Assistant 策略、统一错误响应和字段提示。
- `api/security.py`：递归脱敏、请求级安全上下文、请求内日志防护。
- `api/validation.py`：共享严格请求基类、禁止字段、生成 schema 的参数边界检查。
- `tests/test_api_security.py`、`tests/test_api_error_mapping.py`。
- 本文档。

修改：

- `api/schemas.py`：ApiError / ApiErrorResponse，兼容旧 Python 导入名称。
- `api/main.py`：统一异常 handlers、request context、duration 和 OpenAPI。
- `api/execution.py`：复用新的 mapper/sanitizer，保留结果投影，不复制策略。
- `api/routes.py`、`api/assistant.py`：非 2xx 改为统一 error，保留历史结果定位信息。
- `api/workflows.py`：复用请求基类、参数名边界检查；仍强制原 Validator。
- `api/results.py`：严格 query model。
- 原 API 四个测试文件：更新有意改变的错误契约断言。
- README：阶段标识、简短 Error Format / Security Notes 与迁移说明。

没有修改 Skill、统计脚本、Workflow 执行算法或现有项目数据。之前阶段的未提交文件保持原样，没有 commit / push。

## 仍未覆盖的安全边界

- local / trusted deployment；无用户身份、跨用户授权、OAuth/JWT、限流、body size 配额或公网攻防验证。
- 不防御有本机文件写权限的恶意用户；本地文件系统不是不可篡改数据库，执行路径仍有正常文件系统竞争边界。
- 本阶段未提供任意代码执行或新增下载服务，也未实现 OS sandbox / 网络隔离。
- 未重新执行完整 Streamlit UI 手工 E2E；通过全量既有后端/UI测试确认兼容，不宣称互联网生产安全认证。
- HTTP query 的重复同名参数、ASGI 服务器自身的连接/协议限制、反向代理配置属于后续部署审查，不作为已完成的严格安全保证。

Verdict：**PASS（Phase 8.6 范围内）**。统一非 2xx 模型、单一状态映射、严格请求与来源校验、响应/日志防泄漏、OpenAPI 和回归满足本阶段要求。当前仍是 local / trusted 环境开发版本，不是互联网生产安全认证。停止 Phase 8.6，不进入 Phase 8.7。
