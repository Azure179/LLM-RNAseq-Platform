# Phase 6.3：Real Single Tool Calling

2026-09-17 验收范围：一个用户请求、一次真实 DeepSeek 请求、最多执行一个已注册 Skill。
DEG 与 PCA 已真实执行；缺参数、unsupported、方向歧义均不执行。没有 Workflow、自动上游计算或最终 LLM 总结。

## Inspect → 最小设计

已复用：DeepSeek client、ToolAdapter 双向映射、Manifest input schemas、Registry、SkillResult、标准 logging、原 R 调用及输出校验。
参数的唯一事实来源仍是 Manifest + 共享 JSON Schema；当前没有六套独立 Pydantic Skill input models。
因此业务参数沿用原 JSON Schema 校验，不另建参数模型；Pydantic 用于 ExecutionContext、NoToolDecision 和 ToolExecutionResult。

原 Registry 只有 execute 内部校验。本次抽出公开 `validate_input()`，既可在派发前验证，也被 execute 再次调用；不复制验证规则，不改变执行器。
Phase 6.1 保留：仅把重复键/非有限数 JSON 检查函数移入公共工具文件，行为不变。生产链不先调用 IntentParser，也不会两次调用模型选择 Tool。

```text
User natural language + 可信上下文的业务值/字段存在信息
 → DeepSeek API (system + user + tools, tool_choice=auto)
 → 0 / 1 / 多个 tool_calls 检查
 → ToolAdapter 名称映射 → Registry lookup
 → 严格 JSON arguments 解析
 → 原 Tool Schema 验证（先拒绝系统参数）
 → 合入用户确认的业务参数，验证完整 Tool 参数
 → 注入本地文件、Rscript、独立 output_dir
 → 原完整 Skill Schema 验证
 → Registry.execute() → 现有 Skill → 实际 R 程序
 → 原 SkillResult 2.0 → ToolExecutionResult
```

多 Tool 返回 `MULTIPLE_TOOL_CALLS_NOT_SUPPORTED`，在任何一个执行前停止。
0 Tool 时只接受 DeepSeek 自己给出的 Pydantic-valid needs_input/unsupported JSON；普通自然语言无法可靠分类时返回 `NO_TOOL_SELECTED`。
没有本地正则分类、Codex 代填 Tool Call、自动修复模型 JSON 或无限重试。

## ExecutionContext

所有路径基于 pathlib。配置中的相对路径以 `project_root` 为基准；示例配置须在项目根目录使用，`project_root="."` 指当前工作目录。

| 字段 | 用途 |
|---|---|
| project_root | 宿主允许读取/写入的项目边界，不发送给模型 |
| output_root | 新运行的父目录，每次追加本地生成 UUID |
| bindings | 按真实 Skill 名 → 真实参数名绑定输入文件，不做别名猜测 |
| confirmed_parameters | 用户明确确认的业务参数，如 PCA 的 group_column；不是模型猜测的默认值 |
| rscript_executable | 可信宿主配置，默认 PATH 中的 Rscript |
| timeout_seconds | 可选宿主覆盖；未设时沿用 Skill 默认值 |

例如 DEG 使用 `count_matrix_path`/`metadata_path`，PCA 使用 `count_matrix`/`metadata`。
配置分别绑定这些真实参数名，是为了不再维护第二套字段别名转换。

- LLM-facing：treatment/control、alpha/lfc_threshold、group_column，以及原 Tool Schema 暴露的绘图/富集参数。
- System-managed：所有文件、mapping、background、output_dir、Rscript、timeout、项目根目录、临时目录和运行 ID。
- `.env`/API Key 不属于 ExecutionContext；继续由原配置加载器使用，不写入 Prompt 或日志。
- 模型只看到 confirmed_parameters 和已经绑定的字段名称；看不到本地绝对路径，也不上传表达矩阵。
- 输入路径 resolve 后必须仍在项目内；拒绝 `.env*`、`.git` 和越界路径，所选输入文件须存在。
- 这是本地可信配置边界，不是多租户 sandbox，不能对抗另一进程并发替换文件/符号链接。

模型 arguments 禁止包含 output_dir/Rscript 等系统字段，不是“接收以后再覆盖”。
合并规则是 confirmed_parameters → 模型业务参数 → 宿主文件与运行配置；模型不能覆盖宿主字段。
标量必须匹配原 Schema；分组/列名/sheet 名还要来自原文或 confirmed_parameters。原文出现检查不是完整语义证明。

Heatmap/Volcano 缺 DEG、GO/KEGG 缺 gene list/mapping/background 时返回 `needs_input + MISSING_DEPENDENCY`。
不会自动执行 DEG、筛选 gene list、查询数据库或猜背景；上调/下调子集没有已确认输入时应由模型请求补充。
原完整 Schema 的 oneOf 等依赖约束继续有效。缺少宿主文件不能靠 Tool Schema 的空 required 绕过。

## 统一执行结果与日志

`ToolExecutionResult` 包含：

- status：succeeded / failed / needs_input / unsupported / validated。
- stage：context_injection、llm_request、tool_selection、registry_lookup、argument_parsing、argument_validation、skill_execution。
- request_id、selected_tool、skill_name。
- arguments：模型实际参数；validated_arguments：合入可信业务上下文后的参数。
- missing_parameters、error_code、message、details。
- skill_result：实际 Registry 返回的 SkillResult 2.0，不重写分析数量或统计值。
- llm_response：真实模型、assistant message/arguments、usage、API latency。
- request_count：client 请求尝试次数（0 或 1）；latency_seconds：执行层总耗时。

`validated` 仅用于 `execute=False` / `--no-execute`：Schema、参数和文件绑定可用，但未验证文件内容、更未分析。
JSON Schema default 是注解；模型省略可选参数时沿用原 Skill 函数默认值，不另外补一套默认值。

输出结构：

```text
output_root/<request_id>/
├── tool_run.log          # 请求、模型、参数、验证、执行、最终状态
├── tool_execution.json   # 原始 LLM 响应 + 执行结果 + token/latency
└── skill/               # 只在执行 Skill 时创建
    ├── summary.json
    ├── run.log
    ├── r_stdout.log
    ├── r_stderr.log
    └── 真实分析产物
```

每次创建新 UUID 目录；不覆盖原 toy/test data、pytest 或 Real_DATA_TEST 历史结果。
API Key 在审计数据中被递归脱敏；不记录 headers/环境变量。包含凭据的执行配置会在付费请求前拒绝。
请求目录尚无法创建时只能返回/记录结构化错误，不能保证生成磁盘报告。
若分析完成后报告写入失败，保留已产生的 Skill 文件，并提醒先检查旧运行，不自动重新分析。
报告含自然语言和 SkillResult 的本地路径，分享前仍应检查隐私；本地路径策略与既有 Skill 日志一致。

## 操作命令

在项目根目录执行，继续使用现有 Python 3.13、.env 和 Rscript，不需新依赖。

```powershell
# 帮助，不调用 API
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_tool --help

# 单次真实 API + 真实 toy DEG（原 toy 的分组名是 treatment/control）
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_tool "以 control 为对照比较 treatment 的差异表达。" --context examples/tool_execution_context.json

# 单次真实 API + 真实 toy PCA
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_tool "画 PCA。" --context examples/tool_execution_context.json

# 单次真实 API + 真实 OT vs T 数据 DEG，写入新的 UUID 目录
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_tool "以 T 为对照比较 OT 的差异表达。" --context Real_DATA_TEST/config/tool_execution_context.json

# 仍会消耗 API Token，但不会调用 Registry.execute/R
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.run_tool "以 T 为对照比较 OT 的差异表达。" --context Real_DATA_TEST/config/tool_execution_context.json --no-execute
```

needs_input/unsupported 是正常协议结果，CLI 返回 0；技术错误返回 1，不输出未处理 traceback。
上下文没有默认 DEG 对照方向，“帮我做差异表达”不能因为数据里恰好只有两组就自动执行。
PCA 的分组列分别由 toy 的 condition / real 的 group 显式确认，不假定用户所有 metadata 都叫 group。

## 分层测试

```powershell
# Layer 1：免费，无 API、无真实 R
.\.venv\Scripts\python.exe -m pytest -q

# 仅本阶段新增离线测试
.\.venv\Scripts\python.exe -m pytest tests/llm/test_tool_runner.py tests/llm/test_run_tool_cli.py -q

# Layer 2：真实 R 集成与 Registry 回归，无 API
.\.venv\Scripts\python.exe -m pytest tests/integration tests/test_registry.py --run-r -q

# Layer 3a：六例真实 API，禁止执行器/R
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_single_tool_live.py --run-llm -q

# Layer 3b：六例真实 API，前三例实际执行 toy DEG/DEG/PCA
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_single_tool_live.py --run-llm --run-r -q

# 独立真实数据 DEG E2E，两种 opt-in 缺一不可
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_real_data_tool_live.py --run-llm --run-r -q
```

选择具体文件可避免重新付费跑 Phase 6.0–6.2 的旧 API tests。所有默认 pytest 网络阻断机制保留。
真实单 Tool 测试在 `--run-llm` 单独使用时令 execute=False，并禁止 Registry.execute/subprocess；不是用 mock 分析结果冒充运行。
缺参数/unsupported/歧义三例即使带 --run-r 也禁止执行。
普通测试中明确标注的 HTTP/executor fixtures 只证明本地工程边界，不作为真实模型/分析结果。

### 测试结果

| 测试 | 结果 |
|---|---|
| 本阶段新增离线测试 | 38 passed |
| 普通全项目回归 | 526 passed、47 skipped |
| 真实 R + Registry 回归 | 56 passed、1 skipped，含 18 个真实 R 集成测试；连接测试跳过 |
| 真实 API + toy R（初版 Prompt） | 6/6 passed |
| 真实数据 DEG（修复中文方向规则后） | 1/1 passed |
| 修复后真实 API-only 回归 | 6/6 passed，全部不执行 R |

额外边界覆盖：重复 JSON key、NaN/数值溢出、数组型 arguments、未知 Tool、多 Tool、非法阈值/系统字段、相同分组、无来源分组、缺依赖、文件不存在、越界/秘密文件、互斥背景、截断响应、Skill failed、凭据脱敏、CLI 异常。

## 实际生物信息学结果

### Toy 数据（明确是模拟数据）

真实 API + R 测试复制原 examples 数据到新的审计目录，仅将新副本的 treatment/control 标签改为 OT/T，方便验证用户示例句子。
没有修改旧 examples 数据；没有模拟分析结果。

- 两种中文表达均返回 rnaseq_deg、treatment=OT、control=T。
- 实际 DESeq2：30 个分析基因，6 个样本（3+3），18 个 DEG，其中上调 9、下调 9。
- 实际 PCA：30 个 VST/PCA 基因、6 个样本，PC1=98.4012283956765%，PC2=1.11594639943625%。
- 所有声明的输出文件存在。分析摘要来自实际 SkillResult，不是模型解释。

审计：[6 例真实 API + R](../outputs/deepseek_tests/single_20260917T090008163474Z/single_tool_test_results.json)。
修复后对照：[6 例真实 API-only](../outputs/deepseek_tests/single_20260917T090311229089Z/single_tool_test_results.json)。

### Real_DATA_TEST

真实句子：`以 T 为对照比较 OT 的差异表达。`
DeepSeek 实际返回 rnaseq_deg 和 OT/T；程序注入现有 prepared count/metadata，DESeq2 真正执行。

| 指标 | 实际结果 |
|---|---:|
| 输入基因 | 24,581 |
| 输入样本 | 6：OT 3、T 3 |
| 分析结果行 | 24,310 |
| 有效 pvalue 行 | 24,206 |
| padj NA 行 | 104 |
| 通过 padj<0.05 且 abs(log2FC)>=1 | 13,861 |
| 上调 | 6,474 |
| 下调 | 7,387 |

实际 R 4.6.1 / DESeq2 1.52.0，exit code=0，总调用耗时约 14.35 秒。
完整 DEG CSV 的 SHA-256 与 Phase 4 的直接 Skill 基准完全一致；summary metrics 一致；输入文件前后 SHA-256 一致。
没有重跑旧全流程，没有覆盖旧 results/02_deg。

[真实执行报告](../Real_DATA_TEST/results/phase_6_3/5c3c2685-fccf-424c-855d-d54c99d6249c/tool_execution.json)
包含真实响应、完整 SkillResult 和输出路径。

## 发现和修复的问题

1. **执行前没有公开完整输入校验入口**：抽出 Registry.validate_input，执行前检查，execute 内仍防御性复查。
2. **重复 JSON 检查代码**：共用 json_utils，Phase 6.1 路由不变；新增溢出数值检查，避免 1e400 被读成 infinity。
3. **中文明确对照语义被模型误判**：第一次 real-data 请求返回 needs_input，声称“以 T 为对照比较 OT”含糊，因此未执行。
   补充“以 B 为对照比较 A”与 control/treatment 的通用中文对应关系。原句重测成功；没有将 OT/T 硬编码进 Orchestrator，也没有改写模型回答。
   [原失败记录](../Real_DATA_TEST/results/phase_6_3/e278d401-7c5e-4901-8905-def209871315/tool_execution.json)保留。
4. **CLI 在错误路径被拒绝后可能再次解析同一路径**：日志改为报告配置目录/请求 ID 的定位方式，避免二次异常；增加回归测试。
5. **凭据审计边界**：使用递归字符串脱敏，不依赖序列化后裸字符串替换；执行配置含凭据时拒绝，不让它进入 Skill 日志。
6. **测试假设修正**：原 Registry 会把 Path 联合类型的 Rscript 参数转为 Path；互斥背景在付费请求前就被 context 拒绝。测试已按实际契约断言，未修改业务行为迎合测试。

仍有原环境 R locale 启动警告，已保留 stderr / R_DIAGNOSTICS，exit=0 且产物与旧基准一致。本阶段未修改系统 locale 或统计方法。

## API 成本

实际返回模型均为 deepseek-flash；无自动重试。失败修复后由开发者显式重新发起新的测试请求。

| 批次 | 请求数 | prompt tokens | completion tokens | total tokens |
|---|---:|---:|---:|---:|
| 六例 API + toy R | 6 | 16,238 | 256 | 16,494 |
| 首次 real-data：误判 needs_input，未执行 | 1 | 2,678 | 62 | 2,740 |
| real-data 修复复测 | 1 | 2,738 | 56 | 2,794 |
| 修复后六例 API-only | 6 | 16,598 | 258 | 16,856 |
| 合计 | 14 | 38,252 | 632 | 38,884 |

数字来自实际 API usage。没有把缺失 usage 当作已知计费数，没有估算人民币费用。
toy 测试目录的 single_tool_test_results.json 已汇总 usage，real-data 每个 tool_execution.json 保留单次 usage；不往 pytest 源码目录写运行记录。

## 文件变更

新增：

- src/bulk_rnaseq/llm/execution_context.py：可信配置、角色检查、路径边界、输入注入。
- src/bulk_rnaseq/llm/tool_runner.py：单次决策/解析/验证/执行编排及结果模型。
- src/bulk_rnaseq/llm/run_tool.py：手动单次 CLI，支持付费但不执行模式。
- src/bulk_rnaseq/llm/json_utils.py：共用严格 JSON 原语。
- tests/llm/test_tool_runner.py、test_run_tool_cli.py：38 个免费边界测试。
- tests/llm_integration/test_single_tool_live.py：六例真实模型测试，R 单独 opt-in。
- tests/llm_integration/test_real_data_tool_live.py：独立真实数据 DEG 验收与基准/输入 hash 检查。
- examples/tool_execution_context.json、Real_DATA_TEST/config/tool_execution_context.json：不含凭据、无硬编码绝对路径的示例配置。
- 本文档。

修改：client.py（真实 Tool 请求与审计脱敏）、registry.py（抽取 validate_input）、intent.py（复用 JSON 原语）、README.md。
未修改 Skill/R 统计代码、Manifest/Tool Schema、旧 toy 数据或 Real_DATA_TEST 输入/旧结果；没有新增依赖。

## 验收与限制

用户列出的 16 项最低验收已覆盖：真实模型选择 → 本地二次验证 → 可信注入 → 实际 Registry/DESeq2/PCA → SkillResult；拒绝未知/非法/多 Tool/信息不足；测试成本门控保留。
本次真实执行重点是 DEG/PCA；另外四类只验证了通用映射、Schema 和缺依赖拦截，不宣称本阶段已逐一完成真实 LLM→R E2E。
任何合法但语义错误的模型参数仍可能通过格式校验，有限自然语言测试与原文出现检查不能替代人工科学审核。
不做多轮补参、跨请求状态、同一请求自动重试/幂等重放、输入数据自动发现或多租户安全隔离。
没有上传矩阵供 LLM 计算、没有最终 LLM summary、没有 DEG→Volcano/GO/KEGG 串联。

**Phase 6.3 在上述范围内通过，停止，不进入 Phase 6.4 或 Phase 7。**
