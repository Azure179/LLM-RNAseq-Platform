# Phase 7.3 — Plan Validator + Dependency Resolver

## 范围

只做本地 **Validate + Resolve**。无 Skill 执行、R、DeepSeek 请求、自动修复、自动补分析步骤或 UI 改动。

Phase 7.1 已包含 DAG、绑定及 schema 检查，因此本次不再建立第二套依赖规则：增加统一的 PlanValidator 入口，复用 DependencyResolver，并补上项目文件重查及校验后计划。

## 新增 / 修改文件

| 文件 | 作用 |
|---|---|
| src/bulk_rnaseq/workflow/validator.py（新增） | PlanValidator 公共入口、文件快照刷新、生成独立 validated_plan |
| src/bulk_rnaseq/workflow/resolver.py | 保留图算法；补充上下文安全检查、DEG 跨字段参数检查、严格整数类型、具体缺失业务参数 |
| src/bulk_rnaseq/workflow/context.py | 保存仅供 host 使用的 ProjectContext；检查文件存在、可读、位于项目 input 范围 |
| src/bulk_rnaseq/workflow/models.py | 原 PlanValidationResult 增加 validated_plan 和文件检查 scope，保留旧字段 |
| src/bulk_rnaseq/workflow/planner.py | Phase 7.2 ready 计划统一进入 PlanValidator；host 文件上下文保留但不传给 DeepSeek |
| src/bulk_rnaseq/workflow/__init__.py | 导出 PlanValidator |
| tests/test_workflow_validator.py（新增） | 统一入口、数据绑定、文件变化、类型和历史接口回归 |
| docs/phase-7-3-plan-validator.md、README.md | 本报告、调用方式和当前范围 |

没有修改 Manifest、Skill 函数、R 脚本、Registry 执行器、toy/test data 或真实结果。没有新增依赖。

## Inspect 发现与修复

1. 原 Resolver 已检查 schema，但直接调用它时 `treatment == control` 可以漏过；现复用原有纯 Python DEG `validate_parameters()` 拦截，不再仅依赖 Planner 的检查。
2. JSON Schema 将 `2.0` 视为 integer，但现有 Python Skill 的 top_n 等参数要求 int；现拒绝而不偷偷取整，避免计划 valid、执行时才发现类型不符。
3. WorkflowContext 的输入清单可能在规划后过期；现保留 host ProjectContext，每次验证重查文件，而不是把旧清单视为永久可用。
4. 非法 host confirmed_parameters（例如非对象、NaN）原先可能引发未处理异常；现在返回 INVALID_WORKFLOW_CONTEXT。
5. 缺少业务参数以前只有笼统规则名；现 details.missing 明确列出 treatment/control 等。

这些是输入拒绝与错误处理修复，不改变统计方法、阈值默认值或已有成功分析的结果。

## Validator 与 Resolver 分工

```text
已有 WorkflowPlan（可来自 DeepSeek，或从 JSON 恢复）
    ↓
PlanValidator
    ├─ 模型快照：不修改原始计划
    ├─ 如有真实 ProjectContext，重查已绑定文件
    ↓
原 DependencyResolver
    ├─ Registry 名称 / 可用性
    ├─ Manifest 必需参数、默认值、范围、数据源二选一
    ├─ project_input / workflow_step / project_result 绑定
    ├─ 实际产物键和允许的数据流
    └─ 环检测 / 稳定拓扑排序
    ↓
PlanValidationResult
    ├─ valid=false：空 execution_order、validated_plan=null
    └─ valid=true：已解析计划副本 + 拓扑顺序 + 绑定/参数
```

合法计划中，validated_plan 的每一步包含：已确认参数与原 schema 默认值、已解析根输入绑定、由实际输入引用得到的 depends_on。step_id、workflow_id、用户请求、原步骤列表及业务意图不改变。原始模型响应/计划仍保留，便于审计。

这属于确定性补齐**默认参数和根数据引用**，不是自动补分析：不会新增 DEG，不会替换错误的边，也不会把非法计划修成合法。拓扑顺序单独放在 execution_order，不按自然语言或 steps 列表顺序强行串联。

为了兼容已有调用，沿用 `dependencies`（step → parents）与 `resolved_bindings`，没有再维护同义的 resolved_dependencies 字段。

## 项目文件与历史结果

两种 scope 明确区分：

- `dependency_contract_only`：WorkflowContext 是声明式库存；适合 Planner/离线模型测试，不声称检查了实际磁盘文件。
- `project_files_and_dependency_contract`：通过真实 ProjectContext 构建/验证；检查文件存在、是普通文件、可读取，并且路径位于当前项目 input 内。

文件不存在、变成不可读、或越界时从可用清单移除，依赖它的步骤得到 MISSING_PROJECT_INPUT，并在 details.missing 中列出具体角色。文件内容不在这一步重新扫描；这不意味着一个任意 CSV 已通过生物学验证。

每次从 ProjectManager 获取最新 ProjectContext，才能反映用户刚替换的绑定；保存的旧 Context 只能重查它原先绑定的文件，不能发现尚未刷新到 Context 的 project.json 变化。

历史结果沿用 Phase 7.1 `HistoryLookup.lookup(project_id, run_id)`，校验项目、明确 run_id、成功状态和 output key。单元测试覆盖同项目可用结果，以及跨项目、错误 run_id、failed、缺失产物。**本阶段仍保留接口/stub，不实现 ResultManager 的自动选择或 latest/comparison 解析。** 接口实现者必须负责验证真实历史 artifact 存在及受限路径，不能返回预测产物。

同样，依赖本计划上游尚未执行的步骤时，只能验证产物定义，不能宣称文件已生成；实际存在性必须在未来执行器拿到真实 SkillResult 后检查。

## 正确数据依赖

```text
        DEG                 PCA       DEG
       /   \                 ↑         ↑
 Volcano   KEGG            共享项目输入，但无相互依赖
```

- Volcano 绑定 full `deg_csv`，不能使用 `filtered_csv` 推算完整 NS 分类。
- GO/KEGG 的 gene_list 绑定 `filtered_csv`，可携带 all/up/down；analysis_genes 绑定 full DEG，或用户显式提供另一个允许的背景来源。
- gene_subset 仍是绑定上的未来筛选指令，本阶段不读 DEG、不物化子集，不给背景添加 up/down。
- 热图继续要求 DEG、metadata 和 VST/raw_counts 二选一；不把 PCA 坐标或行 Z-score 视为 VST。

## 主要错误码

| code | 含义 |
|---|---|
| INVALID_WORKFLOW_PLAN | Pydantic 模型不符、重复 step ID 等 |
| INVALID_WORKFLOW_CONTEXT | 上下文类型、角色、确认参数或数值不合法 |
| PROJECT_CONTEXT_MISMATCH | 计划与 host 项目不一致 |
| UNKNOWN_SKILL / SKILL_UNAVAILABLE | 未注册 / 尚未实现 |
| INVALID_WORKFLOW_PARAMETERS | 必需业务参数缺失、非法范围、host 参数混入或整数类型错误 |
| IDENTICAL_GROUPS / INVALID_PARAMETER | 原 DEG 参数校验拒绝 |
| MISSING_PROJECT_INPUT | 缺少可用项目输入文件/角色 |
| MISSING_UPSTREAM_DEPENDENCY | 缺少上游步骤结果绑定或可用历史结果 |
| UNKNOWN_WORKFLOW_STEP | 引用了不存在的步骤 |
| INVALID_OUTPUT_BINDING | 输出键未定义，或历史记录没有该输出 |
| INCOMPATIBLE_OUTPUT_BINDING | 产物存在定义，但不适合此下游输入 |
| INVALID_INPUT_BINDING / INCOMPATIBLE_INPUT_BINDING | 输入目标或项目角色错误 |
| INVALID_GENE_SUBSET_BINDING | 来源/目标不支持 DEG 方向筛选 |
| INVALID_EXECUTION_DEPENDENCY | 手工指定的边没有数据绑定依据 |
| WORKFLOW_CYCLE_DETECTED | 环或自环 |
| CONFLICTING_INPUT_BINDINGS / AMBIGUOUS_INPUT_BINDING | 二选一输入冲突或未明确选择 |
| HISTORY_LOOKUP_FAILED | host 历史查询失败；不暴露底层路径/异常细节 |

失败结果示例结构（不是一次真实分析结果）：

```json
{
  "valid": false,
  "validated_plan": null,
  "execution_order": [],
  "errors": [{
    "code": "MISSING_PROJECT_INPUT",
    "step_id": "deg",
    "message": "Required data is unavailable; provide an explicit binding.",
    "details": {"missing": ["metadata"], "parameter": "metadata_path"}
  }]
}
```

## 如何调用

```python
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.workflow import PlanValidator

validator = PlanValidator(create_default_registry())

# proposal 为已有 WorkflowPlan；project 为最新 ProjectContext。
# confirmed_parameters 复用 FileImportService 的 ExecutionContext，不猜 group_column。
checked = validator.validate_project(
    proposal,
    project,
    confirmed_parameters=execution_context.confirmed_parameters,
)
if checked.valid:
    validated_plan = checked.validated_plan
    execution_order = checked.execution_order
    # 本阶段只查看/保存模型；不执行。
else:
    safe_errors = [error.model_dump() for error in checked.errors]
```

已有 WorkflowContext 可用 `validator.validate(proposal, context)`。如果它由 from_project_context 创建，仍会自动重查文件；纯逻辑库存不假装具备文件检查保证。

Phase 7.2 Planner 已改为调用同一个 PlanValidator。PlannerResponse.plan 仍保留原计划，`PlannerResponse.validation.validated_plan` 提供本地解析后的副本。

## 测试与验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_validator.py tests/test_workflow.py tests/test_workflow_planner.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

本次新增 41 项测试，联合专项 **169 passed**。覆盖用户要求的十类场景，另包括：文件删除/不可读/越界、最新 Context 恢复、参数缺失明细、同组比较、严格整数、上下文损坏、结果副本独立性、JSON 往返、历史来源隔离、Planner 必须进入统一 gate。

最终全量免费回归：**1072 passed、68 skipped，63.72 秒**。跳过项为未开启的真实 R/API 测试及一项 Windows 符号链接权限测试；没有将失败测试改为跳过。

还离线重新校验了 Phase 7.2 保存的 **5 份真实 DeepSeek ready 计划，5/5 通过**（DEG→Volcano、DEG→KEGG(up)、四节点分支、PCA/DEG 独立、反向比较＋Heatmap＋GO(down)）。这是对既有 API 返回的本地回放，不是新 API 请求；未重新生成或手工改写计划。

没有调用 DeepSeek、R 或 Skill，测试设置了明确的禁止调用护栏；没有开启 --run-llm / --run-r。

## 限制

validated_plan 是当前时刻的结构/依赖校验结果，不是永久执行授权，也不是不可伪造的令牌。文件可能随后变化，未来执行器必须再次验证。数据格式、样本对应、组别数量、跨 run 的样本/比较一致性、VST 来源、R 环境和统计可估计性，不能仅靠 DAG 判定。

现有 DEG 仍只接受其原有两组设计；本阶段不自动裁剪样本。历史查询只保留明确接口；无自动补步骤、Workflow 执行器、子集生成、UI 或 Agent。

**Phase 7.3 Verdict：PASS（本地计划与依赖校验范围）。** 本阶段完成后停止，不进入 Phase 7.4。
