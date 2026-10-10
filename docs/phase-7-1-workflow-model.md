# Phase 7.1 — Workflow Model + Dependency Graph

## 范围与结论

本阶段实现 **声明式计划 + 确定性依赖契约检查**，没有 Planner、Executor、DeepSeek 请求、R 调用、子集文件生成或 UI 工作流入口。`valid=true` 只表示依赖契约成立，不表示统计分析已成功，也不表示可以跳过执行前 validation。

此前 README 仍记录浏览器完整 E2E 未完成；本阶段不以单元测试补签 Phase 6.5.6 的真实 UI 验收。

## 1. Inspect：实际接口，而非假设

| Skill | 当前输入/数据依赖 | 可复用的实际输出 |
|---|---|---|
| DEG | count_matrix_path、metadata_path、treatment、control | deg_csv、filtered_csv、summary_json |
| PCA | count_matrix、metadata、group_column | coordinates、variance、plot、summary_json；**没有 VST matrix 输出** |
| Volcano | deg_result_file，必须为完整 DEG 表 | plot、plot_data、summary_json；不重跑 DESeq2 |
| Heatmap | deg_result_file、metadata、group_column；normalized_expression/raw_counts 二选一 | normalized_expression 为完整 VST；matrix 为行 Z-score，不能当 VST |
| GO | gene_list、gene_to_go_mapping；universe/analysis_genes 二选一 | enrichment、all_results、query、universe、mapping、图和 summary |
| KEGG | gene_list、gene_to_pathway_mapping；universe/analysis_genes 二选一 | 与 GO 类似，另有 pathway_aliases |

- 当前 Skill 参数的事实来源是 **Manifest JSON Schema**，并非每个 Skill 都有独立 Pydantic 输入模型。Registry 继续核对 schema 与函数签名；参数默认值不复制到 workflow。
- GSEA 有 Manifest 但未实现，仍拒绝调用/计划使用。
- SkillResult 2.0 是现有 dataclass；实际状态为 `succeeded/failed`，保留真实 `run_id`。
- ProjectContext 保存本地路径；FileImportService 的 ExecutionContext 保存已确认 group_column 等参数。工作流层只接收输入角色清单，不把路径交给计划作者。
- ResultManager 从项目已有 summary/result 文件建立索引，真实 Skill run_id 和后端 request_id 不同。
- 当前 ChatController 的最近 DEG 记录有输入快照与文件 hash 检查，但不是通用 workflow history；本阶段不修改它，也不自动选择最新结果。

## 2. 新增/修改文件

| 文件 | 作用 |
|---|---|
| src/bulk_rnaseq/workflow/__init__.py | 小型公共 API；没有执行入口 |
| src/bulk_rnaseq/workflow/models.py | Step、Plan、Result、绑定与 validation result 的 Pydantic 模型 |
| src/bulk_rnaseq/workflow/context.py | host-owned 输入角色快照、ProjectContext 桥接、精确历史 run 查询协议 |
| src/bulk_rnaseq/workflow/contracts.py | 读取 Manifest，检测 workflow metadata 与实际参数/产物是否漂移 |
| src/bulk_rnaseq/workflow/resolver.py | 参数/绑定检查、DAG 构建、拓扑排序、循环检测；无执行 |
| manifests/rnaseq.{deg,pca,volcano,heatmap,go,kegg}.json | 仅增加 dependencies.workflow 元数据；不改变原有 input/output schema 或统计实现 |
| tests/test_workflow.py | 本阶段契约回归，包括禁止 Skill/API/R 执行的护栏 |
| docs/phase-7-1-workflow-model.md | 本报告与调用示例 |
| README.md | 当前阶段入口及范围说明 |

没有修改既有 Skills、R 脚本、Registry 执行链、Phase 6 路由、UI、真实数据或 toy data。

## 3. WorkflowStep

字段：`step_id`、`skill_name`、`parameters`、`depends_on`、`input_bindings`、可选 `output_alias`。

```json
{
  "step_id": "kegg",
  "skill_name": "rnaseq.kegg",
  "parameters": {},
  "depends_on": ["deg"],
  "input_bindings": {
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
}
```

`parameters` 只接受已注册业务参数，例如 DEG 的真实参数名 `alpha`、`lfc_threshold`，而不是再维护一套别名。路径、output_dir、timeout、Rscript/API 配置被拒绝。

`depends_on` 可以省略，因为数据绑定会产生边；显式填写时只能确认已有数据依赖，不允许额外强制 PCA→DEG 等无数据依据的边。`output_alias` 仅为预留显示标签；引用一律使用明确 step_id + actual output key。

## 4. WorkflowPlan

字段：`schema_version="1.0"`、本地生成的 `workflow_id`、`project_id`、`user_request`、`steps`、`status="planned"`、带时区 `created_at`、`warnings`。

限制：step ID 唯一、非空计划、最多 200 步、禁止额外字段；project_id 复用现有项目命名规则，支持中文和空格。工作流 ID **不是**未来某个 Skill 的 run_id；本阶段不预造 Skill run_id。

所有模型均可 `model_json_schema()`、`model_dump_json()` 和 `model_validate_json()`。不另存一份手工维护的 JSON Schema。

## 5. WorkflowResult

字段：`schema_version`、`workflow_id`、`status`、`completed_steps`、`failed_steps`、`skipped_steps`、`step_results`、`outputs`、`warnings`。

- 状态使用 `succeeded/failed/partial`，与 SkillResult 的命名保持一致。
- `step_results[step_id]` 使用原有 **SkillResult.from_dict** 校验，不建立第二套统计结果定义。
- 成功/失败/跳过集合互斥；成功和失败步骤必须有对应真实 SkillResult；跳过步骤不伪造结果。
- `outputs` 是指向成功步骤真实输出 key 的引用；不能把尚未生成的子集包装成已存在的结果。
- Schema 测试的空指标 fixture 只用于验证模型，不作为真实分析报告。

## 6. Manifest 是依赖事实来源

保留现有 `dependencies.upstream/downstream`：由它们推导允许的数据传递关系。保留 `output_schema` 作为真实产物键的唯一列表，不再复制一份 `produces`。

新增 `dependencies.workflow`：

```json
{
  "project_inputs": {"count_matrix_path": "count_matrix", "metadata_path": "metadata"},
  "subset_source_outputs": ["filtered_csv"],
  "subset_target_inputs": []
}
```

- `project_inputs`：Skill 参数名 → 系统输入角色；必需性仍读取原 input_schema.required/oneOf。
- `subset_source_outputs`：哪些实际产物允许执行阶段进行 DEG 方向筛选。
- `subset_target_inputs`：哪些输入允许接收筛选后的查询；GO/KEGG 为 gene_list，**背景不允许方向筛选**。
- Resolver 初始化时核对所有输入角色、输出键与边；漂移返回 `INVALID_WORKFLOW_CONTRACT`。
- 它是 Manifest 的只读投影，不是另一个手写 dependencies.py。

## 7. 三种 binding，全部不含文件路径

| source | 必需内容 | 处理 |
|---|---|---|
| project_input | key，如 count_matrix | 检查角色类型及 host inventory 是否可用 |
| workflow_step | step_id + output | 检查真实输出定义及 Manifest 兼容边；产生 DAG 边 |
| project_result | run_id + output | 精确历史查询；检查项目、run ID、成功状态和实际输出 key；不产生本计划内部边 |

例如历史引用：`{"source":"project_result","run_id":"actual-run-id","output":"deg_csv"}`。

`HistoryLookup.lookup(project_id, run_id)` 目前是 **接口，单元测试使用 stub**，没有上线 ResultManager 自动检索适配器。未来 host 实现必须通过 ResultManager 核验文件存在、项目内路径和 provenance，不能只返回预测输出。未提供 provider 时拒绝历史依赖；没有隐式 latest、comparison 猜测或跨项目 fallback。

## 8. Project input 检查

`WorkflowContext.from_project_context(project, confirmed_parameters=...)` 检查四种已有绑定是否为 input/ 内存在的文件，不读取表达矩阵，不重复生物学 validation。不存在或越界文件不进入 inventory。

`confirmed_parameters` 应来自现有 `FileImportService.build_execution_context()`，因此 PCA/Heatmap 使用实际确认的 group_column；如果没有确认值也未在计划中提供，返回参数错误，不猜 `group/condition`。

逻辑角色还可表达外部已验证 DEG、VST、gene_list、universe、analysis_genes，但现有 UI 并未因此新增上传类型。这些角色只能由可信 host adapter 提供。

必需根数据自动绑定到匹配角色；不自动绑定某个 DEG 步骤，也不自动补全 DEG。Heatmap / 富集二选一的数据源直接来自原 oneOf；若两个 host 候选都可用而计划未选择，返回歧义错误。

## 9. DEG gene_subset

`gene_subset` 是 **binding 上的 workflow 指令**，不是 GO/KEGG executor 的新参数。

- `all`：原始 significant DEG 输出中的全部记录。
- `up`：其中 log2FoldChange > 0 的记录。
- `down`：其中 log2FoldChange < 0 的记录。
- 选择先受 DEG 原阈值筛选，再按方向，不更改统计检验或显著性阈值。省略表示直接使用原 significant 表的全部记录。
- 背景显式绑定 full DEG `deg_csv` → `analysis_genes`，由现有富集输入逻辑确定 eligible genes 并与 mapping 取交集；不能把上调 DEG 当背景。
- 本阶段只保存和验证指令，**不读取 DEG 表、不生成子集文件**。未来 Executor 实现后才可物化，且必须记录来源 run、输出和筛选方向。
- 不添加虚假的 `deg_up/deg_down/normalized_expression` DEG 产物。

## 10. DAG 与错误

1. 校验模型、项目一致性、Registry Skill 可用性。
2. 收集 workflow_step 绑定产生的父节点。
3. 检查引用存在、output key 存在、数据传递符合 Manifest。
4. 校验原 Skill 标量参数/默认值，以及根输入与替代数据源。
5. Kahn 拓扑算法检测循环；同层节点以 step_id 字典序稳定排序。

结果字段：`valid`、`execution_order`、`dependencies`（child → parents）、`resolved_bindings`、`resolved_parameters`、`errors`、`warnings`、`scope="dependency_contract_only"`。

关键错误码：`INVALID_WORKFLOW_PLAN`、`UNKNOWN_SKILL`、`SKILL_UNAVAILABLE`、`UNKNOWN_WORKFLOW_STEP`、`WORKFLOW_CYCLE_DETECTED`、`MISSING_PROJECT_INPUT`、`MISSING_UPSTREAM_DEPENDENCY`、`INVALID_OUTPUT_BINDING`、`INCOMPATIBLE_OUTPUT_BINDING`、`INVALID_WORKFLOW_PARAMETERS`、`PROJECT_CONTEXT_MISMATCH`。非法计划的 execution_order 一律为空，不能误当成可执行计划。

## 11. 最小调用示例（只校验）

```python
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.workflow import DependencyResolver, WorkflowContext, WorkflowPlan

# 示例库存，不代表实际文件导入；产品中由 ProjectContext 桥接产生。
context = WorkflowContext("demo", frozenset({"count_matrix", "metadata"}))
plan = WorkflowPlan.model_validate({
    "project_id": "demo",
    "user_request": "以 T 为对照比较 OT，并画火山图",
    "steps": [
        {"step_id": "deg", "skill_name": "rnaseq.deg",
         "parameters": {"treatment": "OT", "control": "T"}},
        {"step_id": "volcano", "skill_name": "rnaseq.volcano",
         "input_bindings": {"deg_result_file": {
             "source": "workflow_step", "step_id": "deg", "output": "deg_csv"}}},
    ],
})
check = DependencyResolver(create_default_registry()).validate(plan, context)
assert check.valid
assert check.execution_order == ["deg", "volcano"]
```

这里的计划是模型/图的开发示例，不是宣称来自 DeepSeek 的运行结果；本阶段没有自然语言解析。

## 12. 验证

本次实测：新增专项 **87 passed**；最终完整免费回归 **990 passed, 60 skipped，59.15 秒**；`pip check` 无依赖冲突。跳过项为显式 opt-in 的 R/API 测试及一个 Windows 符号链接权限测试。未消耗 DeepSeek token，没有运行真实 Skill。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

专项覆盖：独立 PCA/DEG、DEG→Volcano、DEG→GO/KEGG all/up/down、分支而非串链、两种 Heatmap expression 来源、历史接口、缺失输入/背景、错误输出、环、自环、未知/GSEA Skill、项目隔离、默认值、系统参数注入拒绝、schema 漂移、结果状态一致性、JSON 往返。

测试护栏阻止 Registry.execute/get_executor、DeepSeek 请求和 subprocess 执行；默认网络防护继续有效。真实 R/API 测试不启用。

## 13. 示例 DAG

```text
A. DEG ──full DEG──> Volcano

B. DEG ──significant subset + eligible background──> KEGG
                                                   ↑
                                             Project KEGG mapping

C.             DEG
              /   \
        Volcano   KEGG

D. PCA           DEG
     ↑             ↑
   Project raw counts + metadata（共享输入，不是分析依赖边）
```

C 中 Volcano 没有产生 KEGG 的查询/背景，所以不建 Volcano→KEGG。当前拓扑排序可能为 DEG、KEGG、Volcano，或使用不同 step_id 得到 DEG、Volcano、KEGG；两者都满足同一分支 DAG，不代表并行执行。

## 14. 限制与阶段边界

- 无 Planner/Executor/UI workflow；当前产品聊天仍是 single-skill。
- 图 valid 不等于文件内容正确、对比方向已由用户确认、样本/基因/来源相匹配、R 依赖已安装或统计可估计。完整业务预检与执行前再检查留给 Phase 7.3/7.4。
- 现有 DEG 仍只支持恰好两组，不能借此把三组输入自动变成所需子集。无 batch/复杂 design 改动。
- PCA 没有可复用 VST 输出；Heatmap raw-count 模式仍走已有公共 normalization，绝不直接画 raw counts。
- 历史结果只有显式查询协议/stub 测试，未实现自动选择或当前输入版本匹配策略。
- 无子集物化、执行日志汇总、重试、失败传播执行器或 workflow 文件持久化；仅定义未来结果模型。
- 没有新增依赖、数据迁移、临时“分析结果”或真实数据覆写。

**Phase 7.1 Verdict：PASS。** 仅适用于以上数据模型与依赖契约范围；不等同于 Phase 7 多 Skill 实际运行已完成。停在此阶段，等待确认。
