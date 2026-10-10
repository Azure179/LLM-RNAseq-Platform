# Phase 6.2：Manifest → DeepSeek Tool Schema

2026-09-16：六个工具生成成功；新增免费测试 45 passed；完整普通测试 488 passed、40 skipped。
真实 DeepSeek Schema Acceptance Test 1/1 通过，仅 1 次 API 请求，不执行 Skill。

## Inspect 与唯一事实来源

现有输入契约是 `manifests/rnaseq.*.json` 的 `input_schema`，共享类型在
`manifests/schemas/input-types.schema.json`。Registry 返回这些 schema 的副本。
当前没有六套 Pydantic Skill input models；Pydantic 仅用于 Phase 6.1 的 StructuredIntent 外壳。
不能把 Intent schema 当成 DEG/PCA 等函数的输入 schema。

本阶段复用 `intent_catalog.build_catalog()` 的本地 `$ref` 展开和路径类型标记；没有复制第二个 resolver。
原来的 Intent schema/Prompt/解析链保持不变。ToolAdapter 只是另一个只读消费者。

```text
原 Skill Python 签名 ── 既有 drift tests ── Manifest input schema + 共享类型
                                                 │
                                          本地 ref 展开
                                                 │
                                    参数角色筛选 + Manifest 描述
                                                 │
                                   DeepSeek 普通 function tools
                                                 │
                               JSON Schema 校验 / 可选 API 接受测试
```

类型、默认值、上下界、枚举、required 都来自 Manifest；没有在 Adapter 写另一套参数定义。
Adapter 的 `LLM_PARAMETERS` 只是已审核的标量参数名称白名单，不包含类型、默认值或业务算法。
这是一项权限策略：将来新增未知标量参数会失败并要求审核，不能自动把 API key、command 等暴露给模型。
所有 path 类型（包括 nullable path）自动划为系统管理；无需维护第二份路径参数清单。

## ToolAdapter 接口

```python
from pathlib import Path

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.tool_adapter import ToolAdapter
from bulk_rnaseq.run_io import write_json

adapter = ToolAdapter(create_default_registry())
tool = adapter.build_tool_definition("rnaseq.deg")
tools = adapter.build_all_tool_definitions()
assert adapter.skill_name_to_tool_name("rnaseq.deg") == "rnaseq_deg"
assert adapter.tool_name_to_skill_name("rnaseq_deg") == "rnaseq.deg"

destination = Path("outputs/tool_schema_export")
destination.mkdir(parents=True, exist_ok=True)
write_json(destination / "tools.json", {"tools": tools})
```

以上代码只生成/保存 JSON，不访问 API、不读取分析输入文件、不获取 executor。
双向名称映射在 Adapter 初始化时集中建立，其他调用者只能查表，不各自替换字符。
函数名检查 `[A-Za-z0-9_-]{1,64}`，检测重名；未知/未实现 Skill 返回结构化异常。
返回值都是独立副本，外部修改导出 JSON 不会污染 Adapter 或 Registry。

## 六个工具与对模型可见的参数

每个 description 自动组合 Manifest 的 description、use_when、do_not_use_when、raw_count_requirement、上游依赖及系统绑定要求。
以下为简要中文说明，不是另一份实际 Tool description。

| Tool name | 用途与禁用情境 | LLM 可见参数（* 为必需） |
|---|---|---|
| rnaseq_deg | raw integer counts + metadata 的 DESeq2 两组差异分析；不接受 TPM/FPKM | treatment*、control*、alpha、lfc_threshold |
| rnaseq_pca | 基于 raw counts，经 DESeq2 VST 探索样本关系；不是差异检验 | group_column* |
| rnaseq_volcano | 消费已有完整 DEG 结果画火山图；不重跑 DESeq2 | padj_threshold、log2fc_threshold、top_n_labels |
| rnaseq_heatmap | 已有 DEG + VST，或通过公共 VST 处理 raw counts 的热图；不直接画 raw counts | group_column*、top_n、padj_threshold、log2fc_threshold |
| rnaseq_go | selected gene list + 自定义 GO mapping + 明确背景的离线 ORA；不假定物种数据库、不替代 GSEA | pvalue_threshold、padj_threshold、ontology、mapping_sheet |
| rnaseq_kegg | selected gene list + pathway mapping + 明确背景的离线 ORA；不把任意 gene ID 当作 KEGG ID | pvalue_threshold、padj_threshold、mapping_sheet |

真实 Skill 名分别为 `rnaseq.deg` 等，保持不变。GSEA 未实现，不暴露为 Tool。
模型仍不能自由编造 treatment/control/group_column；结构校验不是上下文真实性验证，后续阶段必须验证其来源与数据。

### 系统管理的参数

全部文件路径不暴露给 LLM：counts、metadata、DEG result、VST、gene list、GO/KEGG mapping、universe/analysis_genes、pathway 名称映射等。
另外隐藏 output_dir、timeout、rscript_executable；run_id、临时路径、API Key 不属于 Tool input。
可以调用 `adapter.system_managed_parameters("rnaseq.go")` 获取该 Skill 的完整隐藏参数清单。

未来由可信宿主绑定用户已授权的数据与输出目录，**不是让模型输出任意系统路径，也不是本阶段发明文件 ID**。
本阶段没有文件绑定器、路径 sandbox 或自动发现数据；这些是未实现的执行前条件。

### required / 默认值 / 条件依赖

- Tool.required = 原 required 与 LLM 可见字段的交集。
- DEG required 是 treatment/control；PCA 和 Heatmap 是 group_column；另外三种 Tool 的 required 是空数组。
- 默认值不导致字段被强制必填；保留原 schema 的 nullable optional，例如 mapping_sheet=null。
- DEG：alpha=0.05、lfc_threshold=1；Volcano：padj=0.05、log2fc=1、top_n_labels=10。
- Heatmap：top_n=50、padj=0.05、log2fc=1；GO：pvalue=0.05、padj=0.05、ontology=ALL。
- KEGG 的真实 pvalue_threshold 默认是 **1.0**，padj_threshold=0.05；没有错误统一为 GO 的 0.05。

上述数值是检查现有 schema 后的说明，不是 Adapter 新维护的默认定义。
JSON Schema 的 default 是注解，不会自动填入参数；模型省略可选值时，未来应沿用原函数默认值，不能改成新的默认。

Heatmap 的 normalized_expression/raw_counts 二选一、GO/KEGG 的 universe/analysis_genes 二选一，全是隐藏路径约束。
它们不放在只含标量的 Tool parameters 中，但仍原样存在完整 Manifest 内，并在 description 中说明宿主输入要求。
若将来出现涉及 LLM 字段的混合 oneOf 或未知对象级约束，Adapter 会拒绝，而不是静默删除。

**Tool Schema 校验通过 ≠ 完整 Skill 输入校验通过。**
GO/KEGG 的 `{}` 可以是合法的 Tool 标量参数，但绝不意味着可以没有 gene list/mapping/background 就执行。
未来合并可信系统参数后，仍必须由原 Registry/Skill 完整验证；当前没有执行这一步。

## DeepSeek 协议与兼容性

格式是普通非 strict function：

```json
{
  "type": "function",
  "function": {
    "name": "rnaseq_deg",
    "description": "由 Manifest 动态生成；此处仅示意",
    "parameters": {
      "type": "object",
      "properties": {},
      "required": [],
      "additionalProperties": false
    }
  }
}
```

上面仅展示外壳，不是实际 DEG 参数定义；真实导出包含 treatment/control 和两个阈值及原有约束。
本地检查工具数、名称唯一性、非空描述、合法 schema、required 存在、default 合法、严格 JSON 可序列化。
展开本地 `$ref`，去掉 `$schema`/内部 x-path 标记，不需要 DeepSeek 去访问本地 schema 文件。

没有启用 beta strict。其所有字段必填规则与本项目可选默认参数不一致，并且有单独的 JSON Schema 子集限制。
本次真实 API 接受了生成的普通 function schemas，包括当前使用的 nullable 类型、enum、default、范围和 pattern/minLength。
这仅证明 API 接受本次 tools 数组，不证明模型始终遵守这些约束，也不证明 strict 模式或任意 JSON Schema 都兼容。

官方依据：[Tool Calls / strict 限制](https://api-docs.deepseek.com/guides/tool_calls/)、[Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)。

## 客户端改动与异常

`DeepSeekClient.probe_tool_schema(user_message, tools)` 仅发送一次带 tools 的请求，tool_choice=auto，max_tokens=256。
请求仍使用原 .env、HTTPS 官方地址、超时和安全错误机制，不重试。
HTTP/响应检查抽到共享内部方法；Phase 6.0 chat 与 Phase 6.1 JSON chat 仍不接受 Tool Call 响应。
仅新 probe 返回 `ToolSchemaResponse`，保存 assistant message 及原始 arguments 字符串。
**不解析 arguments、不绑定路径、不调用 Registry.execute、不把 tool result 回传模型。**

API Key 在请求内容与响应审计字段中被检查，不写入日志/报告。
HTTP 错误仍使用原有安全 error code；Tool Adapter 新增：

- LLM_TOOL_SCHEMA_ERROR：schema/required/default/名称冲突等异常。
- LLM_TOOL_POLICY_ERROR：出现未审核的标量参数。
- LLM_TOOL_UNKNOWN_SKILL：未知或不可用 Skill。
- LLM_TOOL_UNKNOWN_NAME：未知 Tool name。

这些沿用 `DeepSeekError.to_dict()`，可输出 status/error_code/message/details；不向用户暴露原始异常堆栈或 API 错误正文。

## 测试命令

项目根目录执行，无需重新配置 API Key或安装新依赖：

```powershell
# 默认免费，不访问 DeepSeek
.\.venv\Scripts\python.exe -m pytest -q

# 本阶段 45 个免费测试
.\.venv\Scripts\python.exe -m pytest tests/llm/test_tool_adapter.py tests/llm/test_tool_probe.py -q

# 显式付费：仅一次 Schema Acceptance 请求，不重跑 Phase 6.1 的 20 例
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_tool_schema_live.py --run-llm -q --tb=short
```

原 pytest 双重门控（llm marker + --run-llm）不变；默认跳过 22 个 live API tests 和 18 个真实 R tests。
本阶段测试禁止访问 executor/执行 Skill；全项目回归仍包含原先已有的 mocked Skill 单元测试，不做真实 RNA-seq 分析。

### Schema drift 结果

1. 既有六个函数的签名/required/default 与 Manifest 测试继续通过。
2. 新测试逐字段比较 Tool 与展开后的原 Manifest，包括类型、数值界限、默认值，均一致。
3. LLM 字段与隐藏系统字段的并集等于原函数参数集合，且无交集。
4. Registry 可用 Skill 集合与 Tool 集合一致，GSEA 不被误暴露。
5. 唯一有意差异是安全的参数角色投影：隐藏路径/执行配置及其宿主依赖约束，并非统计契约漂移。

没有发现当前六个 Skill 的 schema/default drift。现有统计参数与默认值未改动。

### 真实 API 结果

报告目录：[本次真实验收](../outputs/deepseek_tests/tools_20260916T151126814535Z/)。

- [tool_definitions.json](../outputs/deepseek_tests/tools_20260916T151126814535Z/tool_definitions.json)：实际发送的全部六个定义。
- [tool_schema_acceptance.json](../outputs/deepseek_tests/tools_20260916T151126814535Z/tool_schema_acceptance.json)：真实响应、schema hash、模型、usage、latency 和未执行标记。

模拟用户句子：`Compare OT and T differential expression. OT is treatment and T is control.`
实际模型：`deepseek-flash`；API 请求成功，finish_reason=`tool_calls`。
DeepSeek 实际返回 `rnaseq_deg`，arguments 原文为 `{"treatment": "OT", "control": "T"}`。
这个返回值来自 API，不是本地测试 fixture 或 Codex 代填。

模型还返回了将要执行分析的说明文字；它不代表实际执行。本地已停止在记录响应，skill_execution_status=`not_executed`。
接受测试不要求特定 Tool 被选中，也不声称完成 Phase 6.3 的选择/参数绑定验收。

仅请求 1 次，无失败重试；输入 2,221、输出 72、合计 **2,293 tokens**，API latency **1.082748 秒**。
真实测试 1 passed in 1.31s；免费完整回归 488 passed、40 skipped。

## 文件变更

| 文件 | 变更及职责 |
|---|---|
| src/bulk_rnaseq/llm/tool_adapter.py | 新增：schema 投影、安全角色政策、名称映射、本地校验 |
| src/bulk_rnaseq/llm/client.py | 修改：共享请求代码、单次 schema probe 和未执行响应模型 |
| tests/llm/test_tool_adapter.py | 新增：六类生成、required/default/description、映射、drift、安全和错误测试 |
| tests/llm/test_tool_probe.py | 新增：免费 HTTP MockTransport、原始调用记录、安全错误与请求校验 |
| tests/llm_integration/test_tool_schema_live.py | 新增：显式付费的单次 API schema 接受验证与报告 |
| README.md、本文件 | 修改/新增：入口、设计、验证和限制说明 |

无新依赖；未改 Manifest、Registry、原 Skill、Intent Prompt、.env、toy data 或 Real_DATA_TEST。

## 本阶段要理解的工程概念与结论

- **契约投影**：Tool Schema 是完整 Skill schema 的模型可见视图，不是另一套业务定义。
- **权限与参数分离**：LLM 可提出分析参数，不能因此获得文件路径、程序或输出目录控制权。
- **协议接受与执行正确性分离**：API 接受 tools、甚至返回 tool call，都不意味着输入已经安全或 Skill 已经运行。
- **防漂移测试**：直接比较派生字段，而不只是检查 JSON 能否解析。

Phase 6.2 在当前六个 Skill 与普通 function 模式下通过验收；停止，不进入 Phase 6.3。
