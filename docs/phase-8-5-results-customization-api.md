# Phase 8.5 — Result & Customization API

日期：2026-10-14。范围：在已有本地后端上增加结果查询、定制规划与定制执行 HTTP 接口。没有新增统计方法、动态代码执行、文件服务器、认证、数据库或后台任务。

## 接口与职责

| Method / path | 职责 |
|---|---|
| GET `/projects/{project_id}/results` | ResultManager 的 runs 及 Workflow / Customization collections |
| GET `/projects/{project_id}/results/{run_id}` | 项目内结果详情、来源关系及安全 artifact 元数据 |
| POST `/customizations/plan` | DeepSeek 分类、ResultResolver 来源选择、已有 CustomizationPlanner 规划；不执行 |
| POST `/customizations/run` | 重新校验已保存计划与来源，调用已有 CustomizationEngine；不调用模型 |

不提供跨项目的 `/results/{run_id}`。历史 run_id 不是全局唯一、安全的查询凭证，project_id 是必要查询边界。四个接口均进入 `/openapi.json` 和 `/docs`。

```text
Results GET → ResultManager → API-safe metadata

Customization plan
  → project-local lock → real ChatRouter
  → shared source helper → existing ResultResolver
  → existing CustomizationPlanner → local schema / whitelist validation
  → stored, project-scoped PreparedCustomization

Customization run
  → project-local lock → exact stored-plan / input-binding check
  → ResultResolver → existing validation + source artifact hash checks
  → CustomizationEngine
      ├─ parameter_override → Registry / fixed rendering implementation
      └─ annotation_filter → existing deterministic literal filter
  → independent customization report → ResultManager → API-safe result
```

HTTP 层不实现统计分析、注释匹配、文件导入或来源排序。Chat 与 API 共用提取出的来源选择、导入注释查询函数，避免维护两套规则。ChatController 原有执行与回执流程保留。

## 结果结构和 lineage

- `results` 是 individual runs；`collections` 是已有 Workflow / Customization 外层记录，包含失败定制的回执，即使没有成功 SkillResult。
- 列表支持 `skill_name`、`status`、`result_type`、`limit`。result_type 为 `Analysis / Workflow / Customization`，状态保留后端 `succeeded / partial / failed` 等值。
- limit 默认 100，上限 200，分别限制两种列表；`truncated` 提示被截断。本阶段没有分页游标或数据库。
- detail 保留 run_id、比较、参数、summary、warnings、创建/完成时间、时间来源、artifacts。
- `source_run_id / parent_run_ids / workflow_id / step_id / customization_id` 来自已保存 provenance；`gene_subset` 从 workflow gene_list binding 或 customization changed_parameters 读取。没有证据时为 null，不由 API 推断。
- ResultManager 仍负责受限目录扫描、记录解析、重复身份检查、路径约束与遗留时间处理；API 不读取另一项目的结果。
- summary / parameters 使用已有紧凑展示投影，可能截断长列表；此接口不是完整结果表下载。

## 来源选择

1. 显式 source_run_id 优先，必须属于本项目并与目标兼容；无效显式来源在付费请求前被拒绝。
2. 否则使用 DeepSeek 解析出的比较方向及 `recent / latest / unspecified`，调用原 ResultResolver。
3. `recent` 沿项目聊天 lineage 查找，不等同于全局最新；`latest` 只在用户明确表达最新时使用已记录时间。
4. 多候选返回 `needs_input / AMBIGUOUS_UPSTREAM_RESULT` 和 source_options；缺来源返回 `needs_input / MISSING_UPSTREAM_DEPENDENCY`。
5. Volcano 支持已有 Volcano 或 DEG。显式选中的 DEG 不会因项目里已有 Volcano 而被排除。没有显式来源时沿用原策略，优先兼容 Volcano，再在不存在成功 Volcano 时考虑 DEG。
6. 规划不验证所有产物内容；执行前现有 Engine 仍会核验实际来源文件及哈希。来源被改动时拒绝，不回退到另一个结果。

独立 plan/run 不写一条新的 Assistant 对话。“刚才”读取现有项目 Chat 历史；连续 API 定制建议显式提交上一回执的 run_id，不假设 plan/run 会自动推进聊天记忆。

## 计划、执行与原始结果保护

plan 返回 `ready / needs_input / unsupported`。只有 ready 有 PreparedCustomization：plan_id、project_id、source_run_id、原请求、已有 CustomizationDecision。业务参数 schema 仍由原 customization catalog / Manifest 决定，没有新建一套参数白名单。

ready 计划及真实 provider response 保存在项目 runtime/customization_plans 下，仅用于本地批准和执行核验，不是数据库。run 必须原样提交该计划：改参数、改来源、改项目或提交未生成 plan_id 都拒绝。需要改变要求时重新规划。

执行前重新检查绑定快照、来源身份、导入注释 provenance、参数和来源产物哈希；启动前写入 running 标记。每个新批准计划产生独立 customization_id，保留 source_run_id 和原结果。相同已完成计划重放返回原回执，不再次执行；中断后仍是 running 的计划返回 409，不自动重试。

参数覆盖只运行已有受控实现。例如 DEG → Volcano 仅调用 plot_volcano.R；不会重新运行 DESeq2。GO/KEGG 参数覆盖只重新绘制已有富集结果，不新增富集计算。annotation_filter 使用已有真实注释与显著 DEG，DeepSeek 只提取关键词/方向，不能提交生成的基因列表或任意代码。

## 安全和错误约定

- 请求不接收主机路径、API key、Rscript、shell command、输出目录。公开 plan 与模型参数再次做本地校验。
- artifacts 返回 artifact_id、filename、type、run_id、size_bytes、error_code 和项目内 relative_ref。使用 ResultManager.validate_result_path 再校验；拒绝越界、路径穿越和不安全链接。
- 输出经过共享 safe_value；内部命令、进程输出、credential 字段、绝对路径不投影到 API。错误响应不返回 traceback。
- 不提供任意下载或 preview endpoint。相对引用不是可直接请求的下载 URL；本地 Streamlit Results Browser 继续负责查看文件。
- HTTP 日志使用路由模板与随机 request_id，不记录请求正文或密钥。规划成本信息在响应和已有规划日志中提供。

| 情况 | HTTP / 业务状态 |
|---|---|
| 项目不存在 | 404 PROJECT_NOT_FOUND |
| 显式结果不在当前项目 / 不存在 | 404 RESULT_NOT_FOUND |
| 重复 result identity | 409 AMBIGUOUS_UPSTREAM_RESULT |
| 来源有歧义 / 缺少来源 | 200 needs_input + error_code / source_options |
| 不支持定制 | 200 unsupported，不生成执行计划 |
| 请求 schema 或路径非法 | 422 INVALID_REQUEST |
| 修改/伪造已批准计划 | 422 INVALID_CUSTOMIZATION_PLAN |
| 来源类型与目标不兼容 | 422 INVALID_PARAMETER |
| 项目绑定改变 | 409 PROJECT_INPUT_CHANGED |
| 计划已提交但尚未完成 | 409 CUSTOMIZATION_ALREADY_SUBMITTED |
| 项目正在处理聊天/定制 | 409 PROJECT_BUSY |
| 模型输出非法 | 502 CUSTOMIZATION_RESPONSE_INVALID / LLM_ROUTE_INVALID |
| 模型网络/配置/额度不可用、超时 | 503 / 504，保留既有 LLM 错误码 |
| Engine 执行失败 | 200 + failed + 实际 error_code / validation_status；检查业务状态 |

## 测试与真实验证

测试文件：`tests/test_api_results_customizations.py`（27 项）、`tests/test_api.py` 的 OpenAPI 验证，以及可显式开启的 `tests/llm_integration/test_customization_api_live.py`（4 项）。默认不调用 DeepSeek 或 R。

新增覆盖：列表/筛选/详情、项目隔离、lineage/up 子集、artifact 路径泄漏/越界、来源不存在、显式来源、歧义、recent 与 latest 区别、比较反向、颜色/Top20/指定 labels、字面注释筛选及合法零命中、unsupported、原文件哈希不变、新计划新 ID、相同计划不重复执行、plan 篡改、输入绑定变更、来源产物变更、危险模型参数拒绝。

全量回归最终结果：**1,348 passed、109 skipped、2 warnings，242.09 秒**。跳过项包括显式收费/R 测试及一个受 Windows 权限限制的符号链接测试（junction 防护另有测试）；两项 warning 为 Starlette/httpx 与 AnyIO 弃用提示。既有 Chat / Results / Streamlit 测试同时通过。语法编译与 whitespace diff 检查通过。

真实集成最终运行：**4 passed，11.40 秒**，模型实际返回 `deepseek-flash`：

| 测试 | 真实执行范围 | 观察 |
|---|---|---|
| Volcano plan | DeepSeek routing + customization planner | parameter_override，red/blue、Top20、foxl2/sox9；没有绘图 |
| Annotation plan/filter | 真实 DeepSeek + 原 Python filter | up、英文关键词，实际命中 1 个 fixture gene；不调用 R |
| R rerender | mock 模型，真实 R | 只执行 plot_volcano.R；PNG 可解析；原 DEG 哈希不变 |
| 完整 closure | 真实 DeepSeek + API plan/run + 真实 R | 同上，结果可通过新 GET detail 查询 |

原有 `tests/integration/test_real_customization.py --run-r` 也通过：**8 passed，4.36 秒**。覆盖 Volcano、Heatmap、GO/KEGG 已有结果重绘及合法空富集结果；没有真实 LLM 请求，没有重跑 DEG 或 enrichment。

来源是明确标注的**合成已有 DEG fixture**（3 个基因，含2个上调、1个下调），不是本阶段重新计算的真实 RNA-seq 科研结果。真实的是 provider 返回、HTTP/backend 调用、确定性过滤与 R 绘图；没有将合成统计量冒充真实 DESeq2 分析。本阶段不重新运行 RNA-seq 统计流程。

最终成功一轮的原始回执位于本地 ignored `outputs/deepseek_tests/`：

- `customization_api_7bb8ae2c125b448baf33275ea773d691/report.json`：Volcano plan。
- `customization_api_cda69240fc134a4a8cd2632e5d575b19/report.json`：annotation plan/filter。
- `customization_api_be8aa3483f5e4608a2399f2f77c1d883/report.json`：mock LLM + real R。
- `customization_api_887204f4df8d48ec9a2f2a6d38d1758a/report.json`：real closure。

报告包含原始 provider 返回、plan/run 响应、usage、模型和延迟，不包含 API key。原临时 pytest workspace 可被之后测试清理，报告不声称 PNG 永久保存在版本库中。

### 失败记录与修复

第一轮真实测试：1 passed、3 failed，没有删除或改写记录。

1. 两次 Volcano 请求被模型合理分类为 result_reuse；API 原来只接收 customization。修复为只允许 Volcano 的 render-only result_reuse 进入原定制 planner/engine，并新增回归；新的 GO/KEGG enrichment 请求仍不能走定制接口。
2. 中文“性别分化”被提取为中文关键词，而 fixture 注释为英文，实际合法命中 0。没有修改筛选结果或算法，也没有自动扩大关键词。验证请求补充“已有注释使用英文，请用对应英文关键词匹配”，由真实 DeepSeek 返回英文关键词后命中 1。原中文零命中保留为限制及永久测试。
3. 整理共享来源选择时修正：显式兼容 DEG 不应因为已有 Volcano 而被排除。重复创建新定制的测试覆盖该边界。
4. 补充的测试首次出现两个测试构造错误：mock 返回未在用户文本出现的 comparison，被现有校验正确拒绝；另一个试图直接改写 frozen ArtifactInfo。仅修正 fixture 构造，未放宽生产校验。

### 成本

| 范围 | 请求数 | prompt tokens | completion tokens | total tokens |
|---|---:|---:|---:|---:|
| 最终成功一轮 | 6 | 11,101 | 436 | 11,537 |
| 第一轮（含失败） | 4 | 8,507 | 297 | 8,804 |
| 本阶段上述真实测试合计 | 10 | 19,608 | 733 | 20,341 |

以上为 API 实际 usage 累计，不估算货币价格。mock + R 测试无 API 请求。

## 文件变更

新增：

- `src/bulk_rnaseq/api/results.py`：结果响应模型、项目 scoped list/detail、安全 artifact 投影。
- `src/bulk_rnaseq/api/customizations.py`：plan/run 请求响应、薄 HTTP router。
- `src/bulk_rnaseq/workspace/customization_service.py`：分离规划/执行、批准计划、锁、重放保护与 backend 编排。
- `src/bulk_rnaseq/workspace/customization_sources.py`：Chat/API 共用来源解析、已导入注释文件查询。
- `tests/test_api_results_customizations.py`：离线服务/接口回归。
- `tests/llm_integration/test_customization_api_live.py`：显式真实模型/R 测试及成本记录。
- 本文档。

修改：

- `api/main.py`、`api/dependencies.py`、`api/execution.py`：注册服务/路由、错误映射；不改统计实现。
- `workspace/chat_dispatch.py`：复用提取出的来源/注释 helpers。
- `workspace/chat_router.py`：可选宿主已选择来源的 Skill 类型提示，不传文件路径或模型猜测 ID。
- `workspace/result_manager.py`：投影已存 gene_subset，并补齐 customization parent_run_ids。
- `tests/test_api.py`：新增 OpenAPI 路由断言。
- `README.md`：阶段标识、四类简短 API 示例、测试入口及限制。

项目中其他未提交的 Phase 7/8.1–8.4 改动是之前已有工作，本阶段未提交或推送任何文件。

## 当前限制与验收

- 仅本机、单宿主、同步执行；无认证，不应暴露到公网。
- plan 通常需要两次模型调用（路由和定制规划），不要自动循环重试。
- 注释筛选是字面匹配，无自动语言检测、同义词扩展、gene ID 转换或本体推理。建议明确注释语言并审阅关键词；零命中不是程序失败。
- 计划持久化使用现有项目文件及锁，不是跨节点事务系统。running 中断不自动恢复；检查结果后再作决定。
- 不新增结果下载/预览 API，未做真实数据大规模性能基准或重新进行 Streamlit 全 UI E2E；通过既有 UI/backend 回归检查兼容性。
- source 优先策略仍沿用现有后端；有多个候选或跨来源组合要求时请显式选择，不猜。

Verdict：**PASS（Phase 8.5 范围内）**。四个端点、后端复用、来源选择、隔离、原结果保护、路径安全、OpenAPI 和分层测试满足本阶段要求；仍是带上述限制的本地开发版本，不代表生产部署就绪。停止 Phase 8.5，不进入 Phase 8.6。
