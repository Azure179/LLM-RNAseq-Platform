# Phase 7.2 — DeepSeek Workflow Planner

## 本阶段范围

```text
用户自然语言 + 安全的项目上下文
    → 现有 DeepSeek Client（一次 JSON 请求）
    → PlannerDecision
    → 现有 WorkflowPlan 的 Pydantic 校验
    → 现有 Phase 7.1 DependencyResolver
    → 返回计划或结构化拒绝；不执行
```

DeepSeek 提出步骤、参数、绑定；本地验证器才判断是否符合现有契约。没有第二次总结请求、工具调用、自动纠错或执行。未修改任何 RNA-seq 统计方法。

## 文件

| 文件 | 用途 |
|---|---|
| src/bulk_rnaseq/workflow/planner.py | PlannerDecision 信封、WorkflowPlanner、严格 JSON/Pydantic 解析、已有 DAG 校验、安全错误与日志 |
| src/bulk_rnaseq/workflow/planner_prompt.py | 从 Manifest、ToolAdapter、DependencyContracts 构建最小能力目录与 Prompt |
| tests/test_workflow_planner.py | 免费 MockTransport 测试；覆盖非法 JSON、错误计划、路径/凭据边界、默认值与不执行护栏 |
| tests/llm_integration/test_workflow_planner_live.py | 仅 --run-llm 开启的真实 DeepSeek 语义测试与报告 |
| docs/phase-7-2-workflow-planner.md | 本报告与调用方式 |
| README.md | 当前阶段入口 |

没有修改 Phase 7.1 WorkflowStep/WorkflowPlan/WorkflowResult、Manifest、Registry、现有 Client、Skills、UI 或真实数据。

## Planner 设计

`PlannerDecision` 只是状态信封：

- `status`: ready / needs_input / unsupported
- `steps`: **直接复用 WorkflowStep**，最多 8 步
- `missing_parameters`: 信息不足时的参数/角色列表
- `message`: 简短澄清或不支持说明

ready 必须有步骤且无 missing；needs_input 必须有 missing；needs_input/unsupported 不允许附带部分计划，避免意外执行支持的子任务。

本地注入 project_id、原 user_request，并让原 WorkflowPlan 生成 workflow_id 和 created_at。模型不能指定这些 host 字段。Pydantic 或 DAG 不通过就返回 `DeepSeekError`，不修改模型步骤重试。Schema 默认值、confirmed parameters 的合并由现有 Resolver 处理，不重写原始 DeepSeek 参数。

返回 `PlannerResponse` 包含 decision、plan、validation、原 ChatResponse 和 prompt_sha256。非 ready 时 plan/validation 为 None；ready 仍只表示声明与依赖契约通过，**不是可以跳过执行前数据校验的授权**。

## Prompt 与 Single Source of Truth

- Skill 用途、使用/禁用场景：来自 Manifest。
- 业务参数类型、默认值、范围：来自现有 ToolAdapter 对 Manifest 的安全投影。这里只复用参数 Schema，不向 API 发送 tools。
- 数据角色、合法输出键、允许依赖边、子集适用位置：来自 Phase 7.1 DependencyContracts。
- Step/Binding JSON Schema：来自原 Pydantic 模型自动生成。
- 不传 requirements 中的执行配置、本机路径、文件内容、output_dir、API Key、项目目录或结果大表。

System Prompt 要求只做规划、遵循真实产物名、不得把自然语言排列顺序误当依赖；禁止虚构结果/路径/组名，禁止自动补算未请求的上游分析。PCA 不输出 VST，Heatmap raw_counts 模式由未来实际 Skill 走现有公共 VST 模块。

模型看到的项目上下文只有逻辑输入角色、已确认业务参数、可用组名。含本机绝对路径的输入会在请求前拒绝；这不是复杂 sandbox，也不代替未来文件访问校验。group 名或 metadata 列名必须来自用户原文或确认参数；提供 available_groups 时还检查 comparison 在清单内。

### 对比方向的重要说明

“比较 OT 和 T”不能仅按文字顺序确定处理组/对照组。因此需求中的 Case 2/3/4 使用**明确的测试前提**：上下文已确认 treatment=OT、control=T。该前提也实际发送给 DeepSeek，完整记录在报告中；不是 Codex 替模型生成计划。

没有确认上下文时，同一句 Case 2 文本必须返回 needs_input。本次单独进行了真实测试。可用组名 T/OT/O 本身不构成已确认 comparison。反向比较也进行了真实测试：以 OT 为对照比较 T → treatment=T、control=OT。

### 子集与分支

仍使用 Phase 7.1 绑定：

```json
{
  "gene_list": {
    "source": "workflow_step",
    "step_id": "deg",
    "output": "filtered_csv",
    "gene_subset": "up"
  },
  "analysis_genes": {
    "source": "workflow_step",
    "step_id": "deg",
    "output": "deg_csv"
  }
}
```

gene_subset 不加入 GO/KEGG executor parameters；不虚构 deg_up/deg_down 文件，背景不随 up/down 筛选。所有子集物化留给未来执行器。

## 真实 DeepSeek 测试

本次首轮 **8/8 PASS**，模型为 API 实际返回的 `deepseek-flash`。没有真实 API 失败用例，没有自动重试或 Prompt 修补后隐藏失败。

| Case | 测试条件与实际返回 | 结果 |
|---|---|---|
| 1 | 以 T 为对照比较 OT，然后画火山图 → DEG→Volcano，方向 OT/T | PASS |
| 2 | 已确认 OT/T；比较 OT 和 T，并对上调基因做 KEGG → DEG→KEGG(up) | PASS |
| 3 | 已确认 OT/T；比较并画火山图、GO、KEGG → DEG 分出三个下游节点 | PASS |
| 4 | 已确认 OT/T；PCA＋DEG → 两个独立 root | PASS |
| 5 | 无已确认 comparison，组名为 T/OT/O → needs_input，缺 treatment/control | PASS |
| 6 | 单细胞分析 → unsupported，无计划 | PASS |
| 7 | Case 2 原文但移除 comparison 确认 → needs_input，不猜方向 | PASS |
| 8 | 以 OT 为对照比较 T＋Top 30 Heatmap＋下调 GO → 反向 DEG，Heatmap/GO 为同级下游，subset=down | PASS |

五个 ready 响应全部通过 WorkflowPlan 和本地 DAG 校验。其他三个响应无部分计划。测试覆盖全部六种现有 Skill，但不执行任何一种。

测试使用声明式项目输入库存，不是导入真实文件的生物学 E2E；计划中包含 T/OT/O 组名不表示现有两组 DEG 已支持自动样本裁剪。

报告路径：`outputs/deepseek_tests/planner_20260930T093115850256Z/llm_planner_test_results.json`。包含原始模型响应、parsed decision、WorkflowPlan、本地 validation、已确认上下文、model、usage、latency、prompt hash 和 pass/fail。

成本记录：

- 请求数：8
- prompt tokens：32,228
- completion tokens：1,187
- total tokens：33,415
- 单次响应约 1.02–1.57 秒；整组测试 10.87 秒
- 不推算费用金额，因为未查询计费价格/缓存计价细节

## 错误与回归

最终免费测试：新增 Planner 专项 **41 passed**；全量 **1031 passed、68 skipped，60.98 秒**。68 个跳过项包括未开启的真实 R/API 测试及一个 Windows 符号链接权限测试；不是绕过失败的 Planner 用例。

免费测试主动注入并成功拒绝：

- 非 JSON、重复 JSON key、NaN、截断响应、schema 不符；
- 未注册 Skill、非法阈值、output_dir/Rscript 参数；
- 错误 Volcano→KEGG 串链、循环、虚构 deg_up、重复 step ID；
- 错放 gene_subset、缺 mapping、猜测历史 run、编造组名；
- 输入绝对路径、凭据进入上下文、组清单与确认 comparison 矛盾；
- 同组对比、不合法项目标识、429 错误。

这些是故意构造的单元错误 fixture，不是声称真实 DeepSeek 曾返回这些错误。API 原有超时、网络、鉴权、限流处理继续复用；没有无限重试。新增 Planner 错误码区分输入、JSON、Schema、未完成输出、非法绑定/参数和 DAG 拒绝。日志仅记录响应 ID、模型、状态、usage、耗时和错误码，不自动写入原始请求/响应或秘密。

测试显式阻止 Registry.execute/get_executor 及 subprocess。普通 pytest 继续禁止网络；真实测试只有 --run-llm 才运行。

## 运行测试

```powershell
# 免费专项与已有模型回归
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_planner.py tests/test_workflow.py -q

# 免费全量回归
.\.venv\Scripts\python.exe -m pytest -q

# 明确付费：只运行本阶段 8 个真实 Planner 用例，不运行整个 LLM 测试集合
.\.venv\Scripts\python.exe -m pytest tests/llm_integration/test_workflow_planner_live.py --run-llm -q
```

沿用 .env 的 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL；本阶段不更改它们。没有 --run-r。

## Python 调用

```python
from pathlib import Path
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.workflow import WorkflowContext
from bulk_rnaseq.workflow.planner import WorkflowPlanner

# project_context 由已有 ProjectManager 提供；execution_context 由
# FileImportService.build_execution_context 提供（包含实际确认的 group_column）。
context = WorkflowContext.from_project_context(
    project_context,
    confirmed_parameters=execution_context.confirmed_parameters,
)
planner = WorkflowPlanner(
    DeepSeekClient(load_deepseek_config(Path(".env"))),
    create_default_registry(),
)
try:
    result = planner.plan(
        "以 T 为对照比较 OT，然后画火山图。",
        context=context,
        available_groups=["T", "OT"],  # 必须来自实际项目已验证的组名
    )
    decision = result.decision.model_dump()
    plan_json = result.plan.model_dump_json() if result.plan else None
except DeepSeekError as error:
    safe_error = error.to_dict()  # 交给调用层展示/记录，不暴露 traceback
```

这个方法会真实消耗 API token，但不会运行分析。不是 Streamlit Workflow UI；现有 UI 的单 Skill 路径未改变。

## 限制与验收边界

- 仍是一次有限规划请求：最多 8 步、最多 1024 输出 tokens，沿用现有 Client 成本上限。更长计划可能安全拒绝为 INCOMPLETE，不自动重试。
- 当前不向模型提供历史 run 清单，不允许它编造 project_result；历史结果自动选择留给后续明确设计。
- 没有自动对话补全循环。needs_input 后需调用方收集澄清，作为明确文本/确认上下文重新提交。
- 本地契约校验不能证明所有自然语言语义正确。特别是处理组/对照组的语义仍由真实 DeepSeek 提取；本阶段以真实测试验证，不用本地 regex 路由代替模型。
- 不校验真实表内容、不裁剪组别、不计算子集、不生成统计输出。ready 不等于 DESeq2 拟合或 R 环境已验证。
- 无 Phase 7.3 新验证框架、Workflow Executor、Final Summary、UI、Agent 或自动修复。

**Phase 7.2 Verdict：PASS（自然语言 → 声明式 WorkflowPlan 范围）。** 停止，等待确认。
