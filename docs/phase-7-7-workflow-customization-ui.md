# Phase 7.7：Streamlit Workflow + Customization UI

实施与测试：2026-10-08；收尾验收：2026-10-09。已有 Phase 7.1–7.6 后端接入同一个聊天框。没有修改统计方法、增加自主 Agent、动态脚本、Docker、FastAPI 或云部署。

## 普通用户怎么用

1. 更新代码后先关闭旧的应用服务，再双击仓库中的 `Start_RNAseq.cmd`；或在项目根目录运行 `.\.venv\Scripts\python.exe -m streamlit run app/main.py`。仅刷新浏览器不一定能清除 Python 已导入模块。
2. 创建/打开 Project。在 Files 导入 count matrix，填写样本分组；富集需导入对应 mapping，推荐原始 eggNOG annotations。
3. 在 **同一个 Assistant 输入框**提交自然语言，例如：
   - 单项：“帮我画 PCA。”
   - 工作流：“以 T 为对照比较 OT，画火山图，并对上调基因做 KEGG。”
   - 独立任务：“画 PCA，同时以 T 为对照比较 OT。”
   - 修改原图：“把刚才火山图上调改红色、下调改蓝色，标 Top20。”
   - 真实注释过滤：“筛选与性别分化相关的上调 DEG。”
4. 若出现 **Source result**，选择明确的源 run，再点击 **Continue with selected result**。不会默认选第一个；继续操作沿用原请求，不重跑 Router。
5. 点击 **View Results**。可按 Result type 选择 Analysis / Workflow / Customization；Workflow 展示各步和依赖，点击 **Open step** 查看原有图片/表格/下载。
6. 关闭再打开应用后，重新选择项目可恢复聊天和结果。不自动继续中断执行，不重跑历史任务。

请使用真实 metadata 组名并明确 treatment/control；“比较 OT 和 T”若方向不明确，仍需要补充信息。输入不含本机路径，路径通过 Project 注入。一次请求可以是复合 Workflow，但仍是串行执行，不是无限自主循环。

### 费用提示

默认增加一次 DeepSeek Router 分类请求：正常 single Skill 通常 3 次 API（Router + 原 Tool Calling + 原 Final Response），Workflow 通常 3 次（Router + Workflow Planner + Workflow Final Summary），定制通常 2 次（Router + CustomizationPlanner，本地展示实际定制事实）。缺信息/拒绝/全失败可能更少。来源选择暂停后不会再次调用 Router。没有自动重试。

## 架构与职责

```text
st.chat_input（唯一入口）
  → ChatController / ChatStore：项目锁、请求 ID、防重、持久化
  → DeepSeek ChatRouter：只判断请求类型/定制目标类型/是否明确说“刚才”
      single_skill  → 原 SingleRequestRunner → 原 Tool Calling / Registry / Final Response
      workflow      → 原 WorkflowPlanner → 本地 Validator → 原串行 Executor
                    → 真实 WorkflowResult → 原 grounded WorkflowFinalResponseBuilder
      customization → 当前项目来源解析（必要时暂停选择）
                    → 原 CustomizationPlanner → 原 CustomizationEngine → 新派生结果
      needs_input / unsupported → 不执行分析
  → Chat + ResultManager：关联 IDs、步骤、artifacts、来源
```

UI 不自己解析 comparison、选择 Skill 或生成生物学总结。Router 使用真实 DeepSeek JSON/Pydantic，Skill 用途从 Registry 的 Manifest 获取；不维护新的 Skill 参数定义。Router 不返回路径、run ID 或统计结果。下游 Planner/Schema/Validator 仍决定是否合法；错误分类不是绕过 validation 的理由。

`ChatDispatch` 只是宿主桥接器，不复制 DEG、VST、富集或注释过滤逻辑。Workflow 的最终回复直接使用 Phase 7.5 的 grounded 输出。定制完成后的文字来自真实 report 的本地模板，仅说明 source、参数与匹配数量，不再让 UI 推断生物学功能。

## 文件变更

| 文件（相对仓库根目录） | 作用 |
| --- | --- |
| `src/bulk_rnaseq/workspace/chat_router.py` 新增 | 真实 DeepSeek 五类分流；结构验证，无关键词业务分类器 |
| `src/bulk_rnaseq/workspace/chat_dispatch.py` 新增 | Workflow/Customization 宿主桥接、来源解析、注释输入、usage 汇总 |
| `src/bulk_rnaseq/workspace/chat_controller.py` 修改 | 统一提交入口、扩展 ChatResult、来源续接、失败持久化；原 single 链保留 |
| `src/bulk_rnaseq/workspace/chat_store.py` 修改 | 更新暂停请求的 assistant 消息，不制造重复聊天轮次 |
| `src/bulk_rnaseq/workspace/result_manager.py` 修改 | 发现 Workflow/Customization 集合、关联原 run/step/source，保留已有安全预览/下载 |
| `src/bulk_rnaseq/workflow/executor.py` 小改 | 增加只读 progress callback；不会更改 DAG/执行顺序/失败传播 |
| `app/views/assistant.py` 修改 | 单框输入、阶段进度、步骤状态、来源 selectbox、修改项、定制图预览与 Results 定位 |
| `app/views/results.py` 修改 | 结果类型筛选、集合详情、每步入口、定制 provenance、过滤/重绘摘要卡 |
| `app/state.py` 修改 | 当前选中的集合 ID；随项目切换清空 |
| `tests/test_workflow_chat_ui.py` 新增 | 免费 Chat/服务/AppTest 集成与安全回归 |
| `tests/llm_integration/test_workflow_ui_live.py` 新增 | 显式真实 Router + Streamlit AppTest/真实 API/R 闭环 |
| `tests/test_workspace_chat.py`、原 `test_workspace_chat_live.py` 小改 | 旧 Phase 6 专项显式 `enable_routing=False`，继续验证原单链，不静默改变旧用例成本/语义 |
| `README.md`、本文 | 日常用法、架构、验收、成本与限制 |

生产默认 `enable_routing=True`；不是只在测试里接上新链。`enable_routing=False` 为既有单链兼容入口，不暴露为自然语言参数。

## Workflow UI

- 状态条显示 Routing → Planning workflow → 每步 pending/running/succeeded/failed/skipped → Preparing summary。
- pending 列表来自本地校验过的计划；运行/失败/跳过来自 Executor 的实际事件。最终聊天步骤来自真实 WorkflowResult，不用模拟计时进度。
- Executor 的回调即使因 UI 断开而抛错，也不改变分析结果。无并行、无自动重规划、无自动重试。
- 一个分支失败仍显示 `partial`；failed/skipped 明确列出。全失败使用原 Phase 7.5 本地失败说明，不让 DeepSeek 编造成功总结。
- 如果统计执行成功但 Final Summary 验证失败，结果保留，明确提示到 Results 查看，不重跑分析。
- 缺 comparison 时显示缺少的参数和真实 available groups。缺执行输入/上游依赖时不运行计划。

## Source result 解析

只查询当前 Project 的 `ResultManager` 和 `ProjectHistory`；候选必须是兼容类型的成功记录。不存在 Volcano 时允许用当前项目成功 DEG 作为 Volcano 的真实输入，但不会新跑 DEG。

1. 只有一个兼容结果：可自动使用。
2. DeepSeek 识别用户明确说“刚才/上一张”：从当前项目已持久化 Chat history 逆序找最近一轮相关结果。该轮若有多个同类型 run，则仍要求选择。
3. 其余多个来源：返回 `needs_input / SOURCE_SELECTION_REQUIRED`，列 comparison 与 run_id，暂停。
4. 续接时同时检查原 request_id、原 user text、原候选清单、当前项目/兼容类型和结果仍可用；不允许向别的项目提交该来源选择。
5. “最新文件 mtime”不是授权，也不会跨项目借用结果。明确的多个 comparison 即使名字相似，也不会猜。

Workflow 下游的 comparison 可沿实际唯一 upstream run 继承用于展示；Customization 再沿 source_run_id 继承。只改变显示标签，不改变统计对比。

## Annotation UI

从**当前绑定且通过导入 checksum 检查**的 GO/KEGG import record 取得保留的 original 文件。原始 eggNOG 的 Description、GOs、KEGG_Pathway 可以继续供 Phase 7.6 使用，不把已经丢失的 Description 从 ID 表中猜回来。

eggNOG 导入按 GO/KEGG 两个 role 各保留一份原始副本；桥接器按内容 checksum 去重，避免同一注释被重复记为两条证据。没有自动 ID 转换。用户若只有 ID mapping，没有实际名称/Description，“性别分化”等名称筛选可能没有命中；这是数据限制，不由模型补注释。

聊天显示真实 source、subset、keywords、matched count；Results 可查看/下载 filtered table 和逐条 matched annotations。匹配只是注释文字筛选，不是功能验证。

## Results Browser

- Analysis：已有独立 Skill runs。
- Workflow：workflow_id、每步 step_id/skill/run/status/dependencies，以及进入对应 artifacts 的按钮；提前失败而没有 summary 的步骤使用真实 execution_result.json 作回退记录。
- Customization：customization_id、source_run_id、mode、changed parameters、keywords、成功/失败/unsupported；原结果仍在列表中。
- Chat → View Results 使用 workflow_id/customization_id/run_id 定位，不依赖目录名猜测。
- 继续复用原有受限 PNG/CSV/JSON 预览和原文件下载；不把大表装进 session_state，不在 UI 重算分析。

结果读取仍限定当前项目 results；链接、越界、重复/坏记录保持原安全检查。损坏的集合记录显示诊断，不应让其他合法结果消失或引发 traceback。

## 持久化与 rerun protection

每次新聊天先生成唯一 request_id，持有项目聊天锁并落盘 submitted，再做任何 API/Skill 副作用。完成记录先于聊天展示保存。重复相同 request_id 只返回已保存 ChatResult。

等待来源选择时持久化 `awaiting_source`（原 Route、候选、usage、请求）。浏览器刷新或重新打开仍显示选择框。明确选择后把同一记录原子标为 submitted，再规划定制；第二次点击/重刷不会重复执行。assistant 消息按原 request_id 更新，而不是追加重复轮次。

ChatResult 保存 request_type、workflow_id、run_id、customization_id、source_run_id、真实 step statuses 和 usage。项目切换同时清空会话中的 pending/选择状态；磁盘记录按项目独立。

程序真正中断留下 submitted 时不自动重试；需要检查 Results/log。防重是本地 at-most-once 提交保护，不是断点续跑、任务队列或分布式 exactly-once。

## 发现并处理的问题

1. Workflow 本地 DAG 检查侧重文件可读性，不能代替 UI 导入 checksum：现在对计划实际需要的 project inputs 再使用 FileImportService 原导入记录校验，防止被手工改动的输入绕过原 UI validation。
2. 来源选择后如果只是追加聊天消息，会留下旧的选择提示或重复执行：新增 awaiting_source 状态及同 request_id 的 assistant 更新。
3. 原 Results 不识别工作流/定制外层记录：增加集合索引，并关联已有 Skill run，而不是复制统计结果。
4. 实际 E2E 审查发现 eggNOG 两个 import original 产生重复证据行：按原文件 checksum 去重，并加入永久回归；匹配基因数量原本正确，受影响的是 evidence 行数。
5. Workflow 下游图本身没有 comparison 参数：仅沿真实 upstream run 继承比较标签，便于选择，不猜测分析方向。

## 如何运行测试

```powershell
# 免费；不访问 API，也不运行 R
.\.venv\Scripts\python.exe -m pytest -q

# 新聊天/UI 回归（mock API / mock Skill，明确非真实分析证据）
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_chat_ui.py -q

# 实际 DAG/R 回归，不访问 DeepSeek
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_executor.py --run-r -q

# 显式付费：6 个真实分流用例 + 1 个真实 Streamlit AppTest/R 闭环
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workflow_ui_live.py --run-llm --run-r -q
```

只带 `--run-llm` 时，真实 R/UI 完整闭环仍跳过。API key 仍从 `.env` 获取，不写入日志。收费测试创建 UUID 专用 workspace，保留原 Real_DATA_TEST、真实用户 projects 和 toy data。

## 实际验收证据

真实接口模型为 `deepseek-flash`。6 个真实 Router 请求分别识别 Workflow、PCA+DEG Workflow、火山图定制、DEG 注释过滤、单项 PCA、unsupported Seurat，均通过。

真实 E2E 经 **Streamlit AppTest** 提交 3 条自然语言，真实 DeepSeek 决策、真实 R/DESeq2/KEGG 计算、真实 grounded 总结；没有用 Codex 决策或 mock 替代中间环节：

1. treatment vs control → DEG + Volcano + KEGG(up)：30 个合成演示基因，6 个样本。真实计算得到 18 DEG（9 up、9 down），Volcano NS=12；KEGG 输入/映射 9 genes，背景 30，显著 pathway=0，正确作为成功空结果。
2. 修改刚才 Volcano：新建 customization，实际再次调用 Volcano，**DEG 仍只运行 1 次**。请求 Top20，但只有 18 个符合条件标签，实际标签数18，没有补造2个。
3. 按英文关键词 sex differentiation 筛选上调 DEG：真实读取合成注释 fixture，匹配9个。此数仅验证过滤链路，**不是性别分化的生物学发现**。

Registry 实际调用顺序：DEG、KEGG、Volcano、Volcano。KEGG 和 Volcano 是 DEG 的两个独立分支；当前合法拓扑排序将 KEGG 先执行，不表示 KEGG 依赖 Volcano。注释过滤通过已有固定本地引擎执行，不重跑统计。

已检查聊天刷新无新增 API/Skill、重新创建 AppTest 恢复6条聊天消息、Results 定位及集合持久化。实际窗口鼠标/浏览器手动验收尚未进行，不把 AppTest 冒充手动浏览器测试。

详细真实记录保存在 `outputs/deepseek_tests/phase_7_7/*/route_test.json` 与 `ui_e2e_test.json`；它们包含真实 API 返回、usage、ChatResult 和执行 Skill 名称。

### 最终测试记录

| 范围 | 实际结果 |
| --- | --- |
| 全量默认 `pytest` | **1182 passed、96 skipped**，100.43 秒；无 API/R |
| 新 Chat/Workflow/Customization 产品回归 | **19 passed**；含真实 Streamlit AppTest 控件、mock API/Skill |
| Workflow Executor `--run-r` 专项 | **28 passed**，37.23 秒；包含 3 个真实 R 集成用例，其余为免费执行器测试 |
| 新 `--run-llm --run-r` 产品专项 | **7 passed**，27.45 秒；6 个真实 Router + 1 个真实 AppTest 完整链 |
| `pip check` | No broken requirements found |

默认跳过的是显式 API/R 集成用例及一个需要 Windows 符号链接权限的用例，不能把 skip 当作已执行通过。新增案例覆盖分支成功/失败/跳过、PCA+DEG 独立 root、缺 metadata/对比参数、项目隔离、来源歧义与续接、防重复、API错误/非法路由、注释筛选、unsupported、输入被改动拒绝、UI回调异常不影响计算，以及聊天恢复/Results 定位。

真实 E2E 首轮通过后审查发现重复 eggNOG 原文件导致证据行重复，已修复并通过最终免费回归的 `n_evidence_rows == 1` 专项断言。原付费记录完整保留，未修改源结果来冒充“修复前就没有问题”；没有为这一纯本地去重补丁重复消耗 API。匹配基因数及统计结果不受该补丁影响。

### 本阶段 API 成本

| 范围 | 请求数 | prompt_tokens | completion_tokens | total_tokens |
| --- | ---: | ---: | ---: | ---: |
| 真实 Router 6 类请求 | 6 | 3975 | 141 | 4116 |
| 真实 UI Workflow + 两次定制 | 7 | 9440 | 693 | 10133 |
| 累计 | **13** | **13415** | **834** | **14249** |

数字直接来自实际 response.usage，没有估算价格。模型为实际返回的 `deepseek-flash`，不是硬编码的模型展示值。本阶段只保留上述收费测试，不因应用 rerun、历史恢复或收尾检查再次请求 API。

## 当前限制

- 无后台队列/取消/自动恢复：本地 Streamlit 同步串行执行，分析期间应保持应用运行。
- 分流增加一次 API 成本；未自动合并 Router 与后续 Planner，避免在本阶段重写已稳定链。
- 缺参数仍需重新完整描述；只有“来源选择”有专门续接机制，没有通用对话记忆或多轮参数补全。
- 对明确 comparison 的复杂历史来源查询，本版宁可让用户选择，不做复杂语义检索。
- 不支持把 workflow 与“改某张历史图”的任意混合要求一次无限执行；没有多 Agent、自动写代码或动态脚本。
- Customization 的能力、ID 对齐、注释名称可用性和固定绘图布局限制沿用 Phase 7.6。
- 未新增 Dynamic Script Execution、Docker、FastAPI、云部署或 Phase 7.8。

## Phase 7.7 Verdict

**PASS：本地 Streamlit Workflow + Controlled Customization 产品接入及自动化验收通过。** 同一个 Chat 入口可使用真实模型规划与真实 Skill；状态、来源、文件与历史均由现有后端证据驱动。原始结果保留，未知参数/跨项目来源/重复提交被拒绝。

这不是“所有浏览器、所有真实物种数据均已验收”的声明。本阶段真实计算使用明确标注的演示 count 和合成注释，并经 AppTest 运行实际 Streamlit 代码；图形浏览器鼠标验收及新的真实数据大规模复测未进行。

停止在 Phase 7.7，不进入 Phase 7.8。
