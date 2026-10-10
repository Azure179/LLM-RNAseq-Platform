# Phase 6.1：Natural Language → Structured Intent

2026-09-16 验收：最终完整真实 DeepSeek 测试 20/20 通过；普通测试 443 passed、39 skipped。
仅完成意图解析，没有调用任何 RNA-seq executor、Rscript 或 Tool Calling。

## Inspect 后的设计决定

- 复用 Phase 6.0 的 httpx 客户端、dotenv 配置、DeepSeekError 和标准 logging。
- Phase 5 的参数契约实际是 JSON Manifest + JSON Schema，SkillResult 是标准库模型，不是 Pydantic。
  因此只新增一个 Pydantic Intent envelope；参数继续使用现有 Manifest 的 JSON Schema 校验，避免另写六套参数模型。
- Registry 当前可用六项：DEG、PCA、Volcano、Heatmap、GO、KEGG。GSEA 只有未实现描述，不进入意图能力列表。
- DEG 的真实阈值键为 `alpha` / `lfc_threshold`，不是 `padj_threshold` / `log2fc_threshold`。
  Prompt 指导 DeepSeek 使用真实键名；没有改动原 Skill 签名。
- 当前没有全局 `group_column=group` 默认值。没有用户声明或可信上下文就询问，不能假定 metadata 列名。
- LLM 不获得 `timeout` / `rscript_executable` 等主机执行控制参数。

## 数据流与信任边界

```text
Registry Manifest ──→ 动态目录、默认值与参数约束 ──┐
用户自然语言 + 显式提供的上下文 ──────────────────┤
                                               ↓
                                    真实 DeepSeek JSON Output
                                               ↓
                             严格 JSON 解析 → Pydantic envelope
                                               ↓
                       原 Manifest 参数校验 → 缺失依赖/原文证据校验
                                               ↓
                                     IntentResponse 或 DeepSeekError
```

没有本地关键词路由、正则意图提取、替代模型回答或自动纠正结果。
JSON 解析拒绝 Markdown 包裹、重复键、NaN/Infinity；模型漏默认参数时返回错误，不在本地补写。

### StructuredIntent

| 字段 | 含义 |
|---|---|
| skill_name | 六个注册名称之一；unsupported 时为 null |
| parameters | 已知参数与 Manifest 非 null 默认值；保持真实 Skill 参数名 |
| missing_parameters | 所有必需但未提供的参数名 |
| status | ready / needs_input / unsupported |
| message | 面向用户的简短解释/补充问题 |
| gene_subset | up / down / all / null，保留 GO/KEGG/Heatmap 子集意图，不假装已经筛选基因 |
| evidence | 显式非默认参数与 gene_subset 的用户原文引用，供本地追踪校验 |

对支持的任务，missing 非空就必须 needs_input；为空就必须 ready。
unsupported 必须没有选中 Skill、参数、缺失依赖或子集。
Pydantic 禁止多余字段、非法状态与类型；Manifest 校验范围、未知参数及互斥输入。

实际 DeepSeek Case 01 返回的 validated intent：

```json
{
  "skill_name": "rnaseq.deg",
  "parameters": {"alpha": 0.05, "lfc_threshold": 1, "treatment": "OT", "control": "T"},
  "missing_parameters": ["count_matrix_path", "metadata_path", "output_dir"],
  "status": "needs_input",
  "message": "请提供 count matrix、metadata 和 output_dir 路径。",
  "gene_subset": null,
  "evidence": {"treatment": "OT 是实验组", "control": "T 是对照"}
}
```

即使方向明确，没有输入文件依然 needs_input。`ready` 仅代表请求参数齐全，**不代表文件存在、数据通过验证或允许直接执行**。

### Prompt 和六类任务

DeepSeek 接收 Registry 的描述、展开后的 input schema、动态提取的默认值、Pydantic envelope schema，以及显式上下文。
System Prompt 规定禁止计算、伪造结果/路径/分组、执行代码、调用未知能力或服从用户要求绕过约束。

| 请求语义 | 识别 | 依赖与约束 |
|---|---|---|
| 两组差异表达、DESeq2 | rnaseq.deg | raw counts、metadata、treatment/control、output_dir |
| PCA、样本聚类/异常、整体表达关系 | rnaseq.pca | counts、metadata、group_column、output_dir；不要求 treatment/control |
| 火山图、volcano | rnaseq.volcano | 已有 full DEG result；比较名称不是结果文件 |
| Top DEGs 热图 | rnaseq.heatmap | DEG、metadata、group_column，以及 normalized_expression 或 raw_counts |
| GO 富集 | rnaseq.go | gene_list、gene_to_go_mapping，以及 universe 或 analysis_genes |
| KEGG pathway 富集 | rnaseq.kegg | gene_list、gene_to_pathway_mapping，以及 universe 或 analysis_genes |

所有文件依赖都需要由用户或上下文提供，不自动猜测 Real_DATA_TEST 中的路径。
二选一依赖整体缺少时，用一个 missing 项表达，例如 `universe|analysis_genes`，不要求同时提供两者。
GO ontology 采用现有 Manifest 的 ALL 默认值，不另创默认值。

方向规则：

- `A vs B`、`A versus B`、`A 相对于 B` 表示 treatment=A、control=B。
- 明确的实验组/对照标签优先于文字排列，例如“OT vs T，以 OT 为 control，T 为实验组”取 T/OT。
- 仅“比较 OT 和 T”没有方向，必须询问。阈值可以保留，但不能猜方向。
- 自相矛盾的两套标签需要澄清，不能任选一套。
- Seurat/单细胞、GSEA、复杂 design、multi-task/workflow 等当前不支持的需求返回 unsupported。

输出使用 `response_format={"type":"json_object"}`、关闭 thinking、temperature=0、最多 768 输出 tokens。
普通 Phase 6.0 chat 默认行为保持不变。低 temperature 不保证完全确定性；JSON Mode 也不保证语义正确。
依据：[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 运行

在项目根目录，继续使用 Python 3.13 和已有 `.env`：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.request_intent --help
```

以下命令各真实调用 DeepSeek 一次，收费，无自动重试：

```powershell
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.request_intent "以 T 作为 control，分析 OT 相对于 T 的变化。"
.\.venv\Scripts\python.exe -m bulk_rnaseq.llm.request_intent "画一下样本 PCA。"
```

每次在 `outputs/deepseek_intents/<UTC时间>/intent.json` 写新记录，不覆盖旧结果。
控制台使用 logging；文件包含 intent、模型、usage、latency、prompt hash；失败保存结构化错误。
有效的 needs_input / unsupported 退出码是 0；API/校验/I/O 错误是 1，无裸 traceback。

可用 `--context-file your_context.json` 提供由用户确认的参数，结构如下（示例路径仅为占位，需自行替换）：

```json
{
  "rnaseq.pca": {
    "count_matrix": "your_data/counts.csv",
    "metadata": "your_data/metadata.csv",
    "group_column": "your_confirmed_column",
    "output_dir": "your_output/pca"
  }
}
```

本阶段不会读取这些分析文件。上下文参数会被发送给 DeepSeek，只提供你同意发送的信息。
不自动扫描目录、读取分组或推断访问权限。后续执行阶段仍须做文件权限和数据验证。

API 配置复用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。
Key 不放进命令行、Prompt 或报告；`.env` 继续被 gitignore 排除。

## 测试和真实审计记录

```powershell
# 免费：包含客户端、Intent、CLI 及旧 Skill 回归；网络被阻断
.\.venv\Scripts\python.exe -m pytest -q

# 仅免费 LLM 单元测试
.\.venv\Scripts\python.exe -m pytest tests/llm -q

# 付费：20 个真实语义用例，不执行分析；不额外跑 Phase 6.0 连接测试
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_intent_live.py --run-llm -q --tb=short

# 付费：仅一个用例
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_intent_live.py --run-llm -q -k 16_pca_context
```

`--run-llm` 必须与 llm marker 同时存在才放开 Python 网络。默认跳过 21 个 live API 测试与 18 个 R 测试。
20 个真实 Intent 测试全部禁止 Registry.execute、get_executor、subprocess.run；不会执行 Skill。
Mock fixture 只验证工程边界，绝不作为真实 DeepSeek 语义测试证据。

每次实际运行创建 `outputs/deepseek_tests/intent_<UTC时间>/llm_intent_test_results.json`。
记录每例原始 DeepSeek 文本、通过校验的 intent、期望与实际 Skill、错误、pass/fail、模型、usage、latency、prompt hash。
拒绝的回答保留 raw_response；parsed_intent 保持 null，不伪装成通过验证。
测试报告可能包含用户文本与路径，默认被 outputs/ 忽略；分享之前应检查隐私。

### 最终完整一轮

模型实际返回 `deepseek-flash`，20/20 通过，pytest 用时 21.02 秒。
完整原始记录：
[最终 20 例报告](../outputs/deepseek_tests/intent_20260916T105210661699Z/llm_intent_test_results.json)。

| Cases | 验证内容 | 结果 |
|---|---|---|
| 01–04 | DEG 显式标签、相对于方向、自定义阈值、缺少分组 | 全通过 |
| 05–06 | PCA、样本聚类异常 | 全通过 |
| 07–10 | Volcano 依赖、Top 30 Heatmap、GO up 子集、KEGG | 全通过 |
| 11 | Seurat unsupported | 通过 |
| 12–15 | OT vs T、T versus OT、无方向、标签优先 | 全通过 |
| 16–17 | 已确认上下文的 PCA / Volcano 返回 ready | 全通过 |
| 18–20 | GSEA unsupported、矛盾标签、整体表达 → PCA | 全通过 |

### 失败历史与修复（没有删除或替换失败记录）

| 报告目录后缀（UTC） | 通过 | 实际输入 tokens | 实际输出 tokens | total tokens |
|---|---:|---:|---:|---:|
| 20260916T104433075245Z | 9/20 | 72,714 | 1,683 | 74,397 |
| 20260916T104949454514Z | 19/20 | 83,814 | 1,764 | 85,578 |
| 20260916T105043281249Z | 19/20 | 84,474 | 1,767 | 86,241 |
| 20260916T105203482528Z | 1/1（定向复测） | 4,304 | 76 | 4,380 |
| 20260916T105210661699Z | 20/20 | 85,534 | 1,718 | 87,252 |
| 本阶段合计 | 81 次真实调用 | 330,840 | 7,008 | 337,848 |

以上为 API 实际 usage，总 input 中有 304,768 cached tokens；未估算费用。
最终一轮 API latency 合计 19.779 秒；完整 pytest 时间还包含本地校验和报告写入。

第一轮失败 Cases：02、04、07、08、09、10、12、13、14、15、19。
主要是漏掉默认值；02/12 把方向只写在 message 而未写入 parameters；15 在缺文件时错误返回 ready。
修复：从同一 Manifest 自动生成显式默认值/必需项 checklist；强调 needs_input 也保留已知参数；JSON mode 使用 temperature=0。

第二、三轮只剩 Case 16：参数完整、missing=[]，却返回 needs_input。
仅加强文字规则不足以解决。最终要求模型先输出 parameters/missing_parameters，随后输出 status，并在 Pydantic schema 中说明状态条件。
定向 1/1 与随后完整 20/20 均通过。这里改的是模型收到的契约/Prompt，不是本地改写其返回值。

## 新增/修改文件

| 文件 | 类型与职责 |
|---|---|
| src/bulk_rnaseq/llm/intent_schema.py | 新增：Pydantic envelope 和跨字段状态约束 |
| src/bulk_rnaseq/llm/intent_catalog.py | 新增：读取 Registry Manifest、展开共享 refs、提取默认值/依赖 |
| src/bulk_rnaseq/llm/intent_prompt.py | 新增：路由边界和动态 schema/default/context Prompt |
| src/bulk_rnaseq/llm/intent.py | 新增：真实请求、严格 JSON、Pydantic + 原 Manifest 校验与审计关联 |
| src/bulk_rnaseq/llm/request_intent.py | 新增：手动单次命令，无 Skill 执行，结构化错误输出 |
| tests/llm/test_intent.py | 新增：解析、schema、依赖、证据、非法阈值和无执行 mock tests |
| tests/llm/test_intent_cli.py | 新增：CLI 成功/异常/I/O 的免费测试 |
| tests/llm_integration/test_intent_live.py | 新增：20 个真实语义用例和逐例报告 |
| src/bulk_rnaseq/llm/client.py | 修改：可选 system prompt、JSON mode 和单次 token 上限；普通 chat 不变 |
| src/bulk_rnaseq/llm/exceptions.py | 修改：可选 typed 响应/Prompt hash 审计上下文，不加入安全 error JSON |
| src/bulk_rnaseq/llm/__init__.py | 修改：阶段职责说明 |
| requirements.txt、pyproject.toml | 修改：加入唯一新依赖 Pydantic，与原 JSON Schema 共存 |
| README.md、本文件 | 更新入口、状态、操作与真实验收说明 |

未修改六个 Skill 的统计逻辑、Manifest、Registry 执行实现、toy data、旧 pytest、Real_DATA_TEST 数据/结果。

## 本阶段新增的工程概念

1. **结构化输出不等于可信输出**：JSON 语法、Pydantic envelope、业务参数与语义是不同层次；每层都可能拒绝模型回答。
2. **单一契约来源**：默认值和参数从现有 Manifest 派生，不维护第二套 RNA-seq Skill 定义。
3. **部分请求是一等状态**：needs_input 不是异常；已知参数保留，同时明确缺失的分析依赖。
4. **可追踪的语义测试**：真实响应与 mock 严格分开；保留失败轮次、Prompt hash 和真实 usage，不用修改答案获得“通过”。

新增错误码：LLM_INTENT_JSON_ERROR、LLM_INTENT_SCHEMA_ERROR、LLM_INTENT_VALIDATION_ERROR、LLM_INTENT_INCOMPLETE。
复用 LLM_INPUT_ERROR 及原认证、超时、HTTP/网络异常。手动报告 I/O 失败记录 LLM_REPORT_IO_ERROR。
没有自动重试、网络失败后的假响应或后备本地意图路由。

## 验收与限制

用户要求的 12 项验收已由最终真实 20 例与免费边界测试覆盖：六类识别、方向/参数、needs_input、unsupported、Pydantic、非法输出保护、显式付费门控、真实模型而非 Codex 意图、不执行 Skill。
普通 pytest：443 passed、39 skipped。LLM mock 子集：84 passed。`pip check` 无损坏依赖，CLI --help 可用。

有限测试通过不是任意自然语言正确性的保证；evidence 是原文引用检查，不是完整语义证明。
模型升级或 Prompt 改动后需要重新验收，temperature=0 也不保证完全可复现。
当前仅单轮、单 Skill；不做上下文自动发现、多轮补参、子集实际筛选、路径授权或文件内容验证。
group_column 不猜；GSEA 不支持；ready 不自动执行；复杂需求明确留到后续授权阶段。

**Phase 6.1 在已测试范围内通过，停止于此，不进入 Phase 6.2。**
