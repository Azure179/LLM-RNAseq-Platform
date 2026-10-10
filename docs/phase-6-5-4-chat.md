# Phase 6.5.4：项目聊天入口

2026-09-23：实现与自动化验收通过，浏览器手工验收待补齐。未进入 6.5.5。

## 如何使用

管理员从仓库根目录启动一次（不用激活虚拟环境）：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

浏览器打开 http://127.0.0.1:8501。分析用户之后不必操作 PowerShell：

1. 左侧创建或打开项目。
2. Files 上传 Count Matrix / Metadata，明确点击 Import。GO / KEGG 还需相应 mapping。
3. Assistant 输入一项需求，例如“帮我画一下样本 PCA”或“以 T 为对照比较 OT 的差异表达”。组名以当前 metadata 为准。
4. 等待请求理解、Skill 选择、输入验证、分析、总结。最后显示真实 DeepSeek 回复。
5. 回复下点击 View Results 查看关联结果目录。完整图片/表格浏览器不在本阶段实现。

`.env` 沿用 Phase 6 配置，不在聊天中填写 API key。发送请求会调用收费的 DeepSeek；成功分析通常两次 API（选择 + 总结）。不要把密钥写入消息。

**只支持一个请求一个 Skill**。“比较 OT 和 T，然后画火山图再做 KEGG”不应执行。缺参后请重新完整描述，例如“做差异表达，OT 是实验组，T 是对照”；历史消息不作为隐式对话记忆。

## 数据流与职责

```text
st.chat_input
  → ChatController（项目与输入检查，运行锁，请求记录）
  → FileImportService → ExecutionContext（可信本地路径）
  → 已有 SingleRequestRunner
      → DeepSeek Tool Calling → Tool Adapter → 参数二次验证
      → Registry → 真实 Skill → SkillResult
      → 既有 Formatter / grounding → DeepSeek Final Response
  → ChatResult（安全、紧凑投影）→ st.chat_message
```

Controller 不进行自然语言规则路由、统计计算或 LLM 结果改写。成功回复直接使用已有 grounded Final Response。路径永不成为 DeepSeek 自由参数；用户只需表达比较方向、阈值等业务要求。

`ChatResult` 保留 status、request_id、user_message、assistant_message、selected_skill、run_id、backend_request_id、stage、error_code、允许展示的 details、missing、artifacts、summary、warnings、usage、request_count、duration_seconds、final_response_status。没有复制整个大表或原始模型推理；完整 SkillResult 仍在原有 backend 报告中。状态沿用项目 `succeeded`，不另造 `success` 分支。

系统用已导入的版本化项目文件填入 counts、metadata、mapping；后端生成 UUID 输出目录、日志和 run_id，并管理 Rscript 和凭据。进度来自后端真实阶段回调，不是按时间假装推进的动画。

## 文件变更

| 文件 | 作用 |
|---|---|
| `src/bulk_rnaseq/workspace/chat_controller.py` 新增 | 适配项目上下文和既有单 Skill 闭环；安全 ChatResult、错误/依赖提示、上游引用、日志 |
| `src/bulk_rnaseq/workspace/chat_store.py` 新增 | 项目历史、原子 JSON 写入、请求台账、排他锁、可恢复清空 |
| `src/bulk_rnaseq/llm/tool_runner.py` 修改 | 可选真实进度和执行前检查回调；加强现有多步骤拒绝 prompt，无新统计逻辑 |
| `src/bulk_rnaseq/llm/single_request.py` 修改 | 透传回调并在最终总结阶段通知 UI；默认调用方式不变 |
| `app/views/assistant.py` 修改 | 启用 chat_input/chat_message/status、历史恢复、结果入口、确认 Clear Chat |
| `app/state.py` 修改 | 项目聊天状态、pending 请求、防 rerun 状态和刷新 |
| `app/components/sidebar.py` 修改 | 执行中禁止切换项目；说明真实 AI 与费用边界 |
| `app/main.py` 修改 | 聊天执行中锁定当前页面、接入新 Assistant |
| `tests/test_workspace_chat.py` 新增 | 24 项免费聊天、真实 orchestrator + mock HTTP、持久化、防重和 AppTest 测试 |
| `tests/test_workspace_ui.py` 修改 | 更新上一阶段“无聊天框”断言；保留其余原测试 |
| `tests/llm_integration/test_workspace_chat_live.py` 新增 | 显式开启的真实 Streamlit AppTest / DeepSeek / R 测试 |
| `README.md`、本文件 | 当前用法与验收记录 |

没有新增依赖、修改统计方法，未删除 toy data 或原有单元测试。自动化产生的 workspace/results/reports 被 Git 忽略。

## 持久化、隔离与防重复

每个项目有独立的：

```text
runtime/chat_history.json          紧凑 user/assistant 消息
runtime/chat_requests/<UUID>.json  submitted/completed 请求台账
runtime/chat.lock                  同项目一次仅一个聊天执行
runtime/chat_upstream.json         最近一次可信聊天 DEG 的结果引用与哈希
runtime/chat_archive/<UUID>.json   清空前的聊天历史归档
logs/chat.log                     项目、请求、运行、状态、耗时、usage
results/<backend-request-id>/      既有后端报告、Skill 输出与运行日志
```

session state 只持有当前项目的显示快照；切换项目和新 session 都重新从磁盘读取，不执行历史消息。

防重不是比较消息文本：同一 request_id 只提交一次。完成后再次收到相同 ID 直接返回已保存结果；不同 ID 的相同文字是新请求，会正常分析。Streamlit 在执行前先把 pending 标成 submitted；磁盘完成记录先于 assistant 历史保存，避免展示写入失败诱发重跑。锁也阻止另一会话同时提交当前项目。它提供 **at-most-once（至多执行一次）**，不宣称崩溃时 exactly-once。

Clear Chat 需确认，只把聊天历史移入 archive 并清空展示；不删除 inputs、results、请求台账、上游记录或原分析日志。可手动恢复归档，但不提供恢复按钮。

进程被强制终止可能留下 chat.lock 或 submitted 记录。**不要直接删锁重试**：先确认没有活跃 Python/R 分析，检查 Results 和日志；需要维护人员处理。没有自动续跑、取消、后台任务队列或 TTL 解锁。

## 后续单项请求如何用已有 DEG

成功 DEG 的 full/filtered CSV 及 SHA-256 写入 `chat_upstream.json`，同时记录当时 count/metadata 版本。

- 单独再问 Volcano / Heatmap：绑定真实 full DEG CSV，绝不偷偷重跑 DEG。
- 单独再问 GO / KEGG：绑定 filtered DEG CSV 为 gene_list，full DEG CSV 为 analysis_genes；现有富集 Skill 继续决定可映射的分析背景。
- 替换/解除 counts 或 metadata 后，不再隐式使用旧 DEG。结果文件缺失或哈希改变也不继续使用。
- Heatmap 继续使用原有公共 VST 路径；不拿 raw counts 直接画热图。本阶段 PCA 不新增 VST 导出。
- 不扫描任意历史目录猜测“适合的 DEG”；自动引用仅限本聊天入口记录的最近一次可信 DEG。
- 未支持新增“提取上调/下调 gene list”功能；该子集若未被上下文明确提供，已有 router 应询问，不应忽略用户子集要求。

## 失败与信息不足

- 未选项目：本地提示先创建/打开；不初始化客户端。
- 空项目：提示导入输入，不发出 API 请求。
- 部分输入缺失：DeepSeek 识别 Skill 后，由既有 schema/context 依赖验证拦截，不进入 Skill。
- 缺比较方向：needs_input，列出缺项与当前真实 groups；要求重新完整描述。
- 单细胞/多步骤：unsupported；多个 tool calls 仍由后端强制拒绝，不部分执行。
- 样本不匹配/非法参数：已有 validator 拒绝。仅允许安全字段进入 Technical details，不展示 traceback、API body 或 CoT。
- API timeout/auth/rate-limit：友好错误与安全错误码，无自动重试。
- Skill 失败：原有本地错误处理；不伪装成成功总结。
- Skill 成功但 final summary 失败：明确“分析文件已生成”，保留 Results 入口，避免重复统计。
- 完成后的辅助日志写入失败：保留已完成分析状态，附 CHAT_AUDIT_IO_ERROR；不把分析变成可自动重跑任务。

## 测试命令

```powershell
# 免费：不调用 DeepSeek / R
.\.venv\Scripts\python.exe -m pytest -q

# 聊天免费测试
.\.venv\Scripts\python.exe -m pytest tests/test_workspace_chat.py -q

# 真实 AI 的无执行分支；PCA/DEG 实测被 --run-r 门禁跳过
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workspace_chat_live.py --run-llm -q

# 收费且运行真实 PCA/DEG；每次运行建立新结果目录
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workspace_chat_live.py --run-llm --run-r -q
```

离线测试仍通过全局网络阻断；AppTest 的 Windows 本地 socketpair 仅用于自身 IPC，不能连接 DeepSeek。R 不在免费聊天测试中启动。

## 真实验收记录

数据项目 `workspace/Real_DATA_UI_TEST`：24,581 input genes、6 samples，OT/T 各 3 个生物学重复。用户输入不包含路径。

| 测试 | 实测结果 |
|---|---|
| “帮我画一下样本PCA。” | DeepSeek 选 rnaseq.pca；真实 R 返回 0；PC1 85.9088594454781%，PC2 4.76667160361138%；真实 grounded 总结显示在 chat_message |
| “以T为对照，比较OT的差异表达。” | rnaseq.deg；treatment OT/control T；R 返回 0；分析 24,310 基因，有 pvalue 24,206；显著 13,861，其中上调 6,474、下调 7,387；真实总结显示在聊天 |
| “帮我做差异表达。” | needs_input，缺 treatment/control，未执行 |
| “做单细胞Seurat聚类。” | unsupported，未执行 |
| “以T为对照比较OT，然后画火山图，再做KEGG。” | unsupported，未执行任何一步 |
| 无 GO mapping 项目：“做GO富集。” | needs_input；缺 gene_list、gene_to_go_mapping、universe/analysis_genes；未执行 |

所有输入前后 SHA-256 一致；输出存在检查、真实工具消息、真实 R exit code、总结数字、聊天展示及普通 rerun 不新增请求均有断言。分析成功后 Results 列表自动刷新。

最后通过的 PCA backend request：`c11df591-9696-40a9-8395-c08f407e3847`；DEG：`6041b0e5-6161-463e-8fe5-a954bd7279fd`。输出在项目 results 对应目录的 skill 子目录。

R stderr 出现 Windows locale `LC_*=C.UTF-8` 设置警告；R 返回 0、输出完整。沿用现有 R_DIAGNOSTICS warning 并保留到最终回复，没有屏蔽或改统计方法。

### 测试结果与费用

- 全量免费回归：804 passed、60 skipped（包含真实 API/R 门禁和 Windows symlink 权限跳过）。
- 新聊天测试：24 passed。
- 真实测试：四个无执行分支通过；修复测试脚本后，两个真实分析测试通过。
- 测试脚本曾有两类错误：首轮报告目录未创建；第二轮 PCA/DEG 断言错误读取 `llm_response.tool_calls`，实际结构是 `llm_response.message.tool_calls`。它们发生在真实分析成功之后，未伪造结果或放宽验收。
- 因以上测试脚本修复而产生的所有真实请求已计入：**20 次 API；prompt 43,240，completion 1,045，total 44,285 tokens**。模型为 API 返回的 `deepseek-flash`。未按猜测价格换算金额。
- 机器可读总记录：`outputs/deepseek_tests/phase_6_5_4/acceptance_report.json`（包括失败测试运行的实际消耗）。分次报告也在同目录下。
- 两个独立 `Chat_Missing_GO_*` 项目是缺依赖测试产生的真实数据副本，保留用于审计；没有删除已有数据。

## 当前限制与最终结论

这是本机单用户、同步单 Skill UI；并非 Agent、Workflow、数据库服务或部署平台。没有流式 token、取消、自动重试、复杂聊天记忆、完整图片/表格预览、跨项目结果引用。历史约 7 MiB 有上限，异常中断需人工检查。

多步骤语义识别由真实 DeepSeek 承担，本地同时强制拒绝多个 tool calls；不宣称 prompt 能保证所有未知表达永不误判。当前明确案例通过。

**实现、离线测试与真实 Streamlit AppTest 闭环通过；完整浏览器手工验收尚未完成。** 本次尝试 browser inventory 失败，Windows computer-use 又因无法可靠确认当前浏览器 URL 停止；遵守停止操作，没有绕过限制。AppTest 是运行真实 UI 组件代码，不是浏览器点击或视觉检查。computer-use 技能因此影响了验收范围，而不是分析实现。

手工补验：打开项目 → 查看聊天历史；输入一条 PCA 请求并看实时状态/回复；点 View Results；切项目确认隔离；重启后恢复；确认 Clear Chat 仅影响聊天。运行 PCA 会产生新收费 API 和真实分析，勿为查看已有结果重复提交。

本阶段停止。未实现 Phase 6.5.5，也未进入 Phase 7。
