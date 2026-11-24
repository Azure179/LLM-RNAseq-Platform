# Phase 8.2 — Skill Execution API

本阶段仅增加现有项目内的单 Skill 同步执行，不调用 DeepSeek，不开放 Workflow / Assistant / Customization API。统计程序、Manifest、Registry、ResultResolver 和既有 Streamlit 页面没有重新实现。

## 执行链与文件职责

```text
POST /skills/{skill_name}/run
  → Request validation
  → SingleSkillService
  → ProjectContext + ResultResolver + existing PlanValidator
  → existing executor (exactly one step)
  → Registry.validate_input → Registry.execute
  → persisted canonical SkillResult + provenance + ResultManager
  → API-safe SkillResult
```

- `src/bulk_rnaseq/workspace/skill_execution.py`：无 HTTP/LLM 依赖的单 Skill 服务。沿用 ToolAdapter 的参数白名单、Manifest 依赖、项目导入校验、历史结果解析和执行器。内部构造一个步骤以复用成熟的绑定与持久化，不接受客户端提交计划。
- `src/bulk_rnaseq/api/schemas.py`：请求信封与结构化错误。禁止额外顶层字段。
- `src/bulk_rnaseq/api/routes.py`：同步 POST；每个有效请求最多调用一次 Registry，不直接调用 R。
- `src/bulk_rnaseq/api/dependencies.py`：共享应用生命周期内同一个 Registry 和服务。
- `src/bulk_rnaseq/api/execution.py`：HTTP 错误映射、Canonical SkillResult 的安全视图；响应 Schema 从既有 dataclass 自动生成。
- `src/bulk_rnaseq/api/main.py`：主机配置、生命周期、脱敏错误与从服务参数 Schema 生成的 OpenAPI。
- `tests/test_api.py`：沿用健康检查和元数据回归测试。
- `tests/test_api_execution.py`：离线请求、执行次数、绑定、隔离、安全和失败测试；模拟 Skill，不运行 R。
- `tests/integration/test_real_api.py`：显式 `--run-r` 测试真实 DESeq2、Volcano 和重建应用后的结果复用。
- `README.md`：最小调用示例与当前限制。

Phase 8.1 已加入的依赖、测试网络隔离和原 Phase 7.8.1 修改仍保留；本阶段不增加 Python 依赖、不修改已有统计方法。

## 请求与参数来源

```json
{
  "project_id": "my_project",
  "parameters": {"treatment": "OT", "control": "T"}
}
```

`project_id` 是已创建的工作区项目目录名，不是任意路径。用户先通过现有 Streamlit 导入数据，文件路径由 ProjectContext 注入。默认主机工作区为启动目录下的 `workspace/`，可通过服务环境变量 `RNASEQ_WORKSPACE_ROOT` 配置。Rscript 和超时仅允许主机 `create_app(...)` 配置，不接受 HTTP 提交。

参数约束与默认值来自既有 ToolAdapter / Manifest，不复制统计 Schema。DEG 需要 `treatment/control`，阈值字段为 `alpha/lfc_threshold`；PCA 与 Heatmap 需要 `group_column`。各 Skill 的实际业务 Schema 可在 Swagger 和 OpenAPI 操作的 `x-skill-parameters` 中查看。GSEA 尚不可执行，返回 409。

API 额外支持下游来源选择：

- `parameters.treatment/control`：沿用 DEG 参数定义，筛选相同方向的历史 DEG，不改变历史统计结果。
- 顶层 `source_run_id`：在本项目精确选择一个已有结果；不控制新运行 ID。根 Skill 不接受它。
- `parameters.gene_subset`：复用既有 `GeneSubset` 的 `all/up/down`；仅适用于 Manifest 声明的富集输入。

示例：`POST /skills/rnaseq.kegg/run`，请求：

```json
{
  "project_id": "my_project",
  "source_run_id": "EXISTING-DEG-RUN-ID",
  "parameters": {"treatment": "OT", "control": "T", "gene_subset": "up"}
}
```

已有 ResultResolver 必须找到唯一的成功 DEG，其文件和原始校验摘要均有效。多个候选返回 409 和脱敏候选标识；不隐式挑选最新结果。富集查询来自 `filtered_csv` 的真实方向子集，背景来自完整 `deg_csv`。Heatmap 使用已有公共归一化模块，不直接对 raw counts 绘图。本入口不会自动串联新 DEG。

## 返回与安全

响应沿用 schema_version `2.0`，包含 `status/skill_name/run_id/inputs/outputs/summary/warnings/metadata/error_code/message/details`。成功状态是项目既有的 **`succeeded`**，不是新增的 `success`。

- 内部完整结果、输入路径、进程日志仍保存在本地，API 不修改这些文件。
- HTTP 的 outputs 仅返回经过项目边界与文件存在性检查的相对路径，形如 `results/workflows/<id>/steps/001/skill/deg_results.csv`，不是下载 URL。
- metadata 包含项目、工作流容器、step、运行时间、业务参数、父 run IDs、来源与校验值；不发送完整进程/environment/provenance 对象。
- summary / warnings 保留分析信息，对明显路径/凭据片段脱敏；HTTP 失败不传 raw stderr/stdout/traceback。
- 数据和上游文件继续经过现有哈希校验。测试初期缺少哈希的模拟结果被拒绝，修正的是测试 fixture，没有放宽生产检查。
- 成功结果由原执行器写入现有结果索引，Streamlit Results、ResultResolver 和 Workflow 历史接口可以发现。

## HTTP 错误

| HTTP | 情况 |
|---|---|
| 404 | `SKILL_NOT_FOUND`、`PROJECT_NOT_FOUND`、`RESULT_NOT_FOUND` |
| 422 | `INVALID_REQUEST`、`INVALID_PARAMETER`、`INVALID_COMPARISON`、`INPUT_VALIDATION_FAILED` |
| 409 | 缺项目输入、缺上游、上游歧义、不可执行 Skill、输入变更等冲突 |
| 502 | `RSCRIPT_FAILED` |
| 503 | Rscript 不存在或无法启动 |
| 504 | R 超时 |
| 500 | 未预期内部异常或无法安全返回的内部输出 |

执行前错误返回 `{"error":{"code":"...","message":"...","details":{...}}}`；已进入执行器的失败返回 canonical failed SkillResult，带对应非 200 状态。OpenAPI 记录两种情况。错误不原样反射提交正文，日志仅记方法、路由模板、状态和 request ID。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_api_execution.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/test_real_api.py --run-r -q
.\.venv\Scripts\python.exe -m pytest -q
```

真实测试产物位于被忽略的 `outputs/api_tests/<id>/`，不会写入用户已有项目。输入为仓库公开合成演示数据，不代表真实科研发现；没有发送任何 DeepSeek 请求。

2026-10-13 本地验证结果：

| 检查 | 结果 |
|---|---|
| 全量默认 pytest | **1267 passed, 101 skipped**，177.58 秒；无真实 API/R 调用 |
| API 测试 | 63 项纳入上述通过结果；15 项元数据 + 48 项执行/安全测试 |
| 显式真实 R API 测试 | **1 passed**，同批 38 项当时的离线执行测试也通过 |
| 真实 DESeq2 | 30 genes、6 samples、每组 3；18 DEG = 9 up + 9 down；约 10.21 秒 |
| 真实 Volcano | 复用该 DEG，30 genes = 9 Up + 9 Down + 12 NS；约 0.79 秒；PNG 存在 |
| Registry 调用次数 | `rnaseq.deg` 一次、`rnaseq.volcano` 一次；Volcano 请求未再次执行 DEG |
| 结果与重建应用 | ResultManager 能查询；重新创建应用后，原 DEG 校验通过且可由 ResultResolver 解析 |
| 安全 | 禁止路径/host 参数、NaN/Infinity、跨项目来源、失效哈希；错误和成功警告的敏感片段不外泄 |
| OpenAPI | POST 请求、canonical 响应、错误响应、六类生成的业务参数 Schema 和 `/docs` 测试通过 |
| 真实 Uvicorn HTTP | 回环端口 8000：health ok、docs 200、OpenAPI 含六类参数、不存在的项目返回 404；临时验证服务随后关闭 |
| DeepSeek | **0 次请求，0 token 消耗** |

真实 R 记录：`outputs/api_tests/a6cc9c01fba44dd483578ff45d3f04c5/report.json`（本地忽略目录）。DEG run_id `bfaacfd7-92b4-42e5-b9f5-8e737a7eb0b5`，Volcano run_id `b2a5a49a-e4ad-4513-aaa6-fe9d5ed4af11`。这些数字仅证明公开演示输入的真实程序运行，不代表此次重新验证了大型科研数据集或全部 R Skill。

测试中处理的问题：

1. 初版 mock 没有生成 artifact hashes，历史复用测试因此失败；补齐 fixture 的真实文件哈希，生产校验未放宽。
2. PCA/Heatmap 的初版测试漏填已有必需 `group_column`；修正测试请求，保留原 Schema，不新增隐式默认规则。
3. HTTP 错误映射补齐无映射、无显著可绘制基因、缺 GO ontology 等已知输入错误，避免误报内部 500。

现有测试依赖产生 2 条弃用警告（Starlette 的 httpx TestClient、anyio BlockingPortal 别名）。另有 1 项 Windows symlink 测试因本机权限跳过；其余跳过项是显式 R/LLM 测试。未为消除警告更换 HTTP 依赖。

**Phase 8.2 Verdict: PASS（本地阶段验收，不是稳定生产版本）。** 已有 Streamlit/Chat/Workflow 回归测试通过，本次没有重做浏览器人工 E2E。没有进入 Phase 8.3，没有提交或推送 GitHub。

## 限制

- 只同步执行一个 Skill；内部复用单步骤 Workflow 容器，故结果目录具有 workflow_id，但没有开放多步骤 API。
- 不提供上传、项目创建、下载、LLM、Workflow、定制或任务队列接口，仍用现有 UI 管理项目及查看产物。
- 未实现幂等 key、自动重试、取消、断线恢复、多用户和认证；重复 POST 是新的独立运行。保持 `127.0.0.1`，不要暴露到公网。
- HTTP 客户端应等待分析完成；客户端断开不代表 R 已停止。运行期间避免 `--reload` 和同时替换输入。
- 本阶段验证不等于生产部署认证。Phase 8.3 未开始。
