# Phase 7.4 — Workflow Executor

验收日期：2026-10-05。范围：Validated Workflow → 真实多 Skill 串行执行。不包含 Planner 调用、LLM 总结或 UI 工作流。

## 设计及变更

| 文件 | 作用 |
| --- | --- |
| `src/bulk_rnaseq/workflow/executor.py`（新增） | 执行前重新校验、按原拓扑顺序串行调度、输入注入、子集准备、失败隔离、记录持久化 |
| `src/bulk_rnaseq/workflow/history.py`（新增） | 通过 ResultManager 查找当前项目的明确 run_id，检查真实 artifact 及原始校验和 |
| `src/bulk_rnaseq/workflow/models.py`（修改） | WorkflowResult 增加 execution_order、user_request、step_records；保留原字段与状态值 |
| `src/bulk_rnaseq/workflow/__init__.py`（修改） | 导出 WorkflowExecutor / ProjectHistory |
| `tests/test_workflow_executor.py`（新增） | 免费单元回归测试及 `--run-r` 真实集成测试；明确区分模拟结果和真实 R 结果 |
| `README.md`（修改） | 当前阶段入口 |
| 本文（新增） | 调用方法、设计、验收记录与限制 |

没有修改已有 Skill、DESeq2/R 脚本、统计阈值、现有数据或 UI。没有新增依赖。

执行链：

```text
PlanValidationResult(valid=True)
  → 使用当前 ProjectContext 重新验证（不规划、不补步骤）
  → 检查 execution_order / dependencies / bindings / parameters 一致
  → 按 execution_order 串行处理
  → 注入项目文件 / 真实上游输出 / 系统 output_dir
  → Registry.validate_input → Registry.execute → 原 SkillResult
  → 原子保存检查点、provenance、WorkflowResult
```

执行器不接受裸 WorkflowPlan。入口拒绝、文件系统故障使用既有 SkillError；实际步骤失败则进入 WorkflowResult，不因单分支失败无条件终止。

### Input / output binding

- `project_input`：只取 ProjectManager 当前绑定的项目输入；执行开始记录校验和，每步检查输入仍为同一文件与同一内容。
- `workflow_step`：只取成功上游 SkillResult 声明的真实输出；文件必须存在于该步骤目录内，并与记录的校验和一致。
- `project_result`：只接受当前项目明确的 Skill run_id；不猜 latest，不接受 backend request ID 代替。读取历史时检查原始 artifact 校验和。
- `output_dir`、Rscript 配置与 timeout 由本地主机注入，计划无法自由设置。
- 原 Skill 不支持传入 run_id，因此执行器**保留 Skill 真正生成的 run_id**，不另造 ID 冒充。绑定失败且尚未启动 Skill 时 run_id 为 null。

### DEG 子集：只是输入准备，不是第二套分析

`gene_subset` 由 Phase 7.3 绑定语义限定在 `filtered_csv → gene_list`：

- all：上游已筛选差异基因的全部行。
- up：这些行中 log2FoldChange > 0。
- down：这些行中 log2FoldChange < 0。

不会重新应用另一套 padj/效应量默认阈值，也不会向模型索要 gene ID。派生 `bindings/gene_list.csv` 是下游输入，不是假分析结果。分析背景仍绑定完整 DEG `deg_csv → analysis_genes`，由原富集 Skill 按既有规则处理；不会把上调基因当成背景。

子集为空返回 `EMPTY_GENE_SUBSET`，不调用 enrichment。与“实际执行成功、没有显著 term/pathway”的有效空结果不同：后者保留 succeeded。

### DAG 失败传播

| 情况 | DEG | Volcano | KEGG |
| --- | --- | --- | --- |
| 全部成功 | succeeded | succeeded | succeeded |
| DEG 失败 | failed | skipped | skipped |
| Volcano 失败 | succeeded | failed | succeeded |

每步支持 pending / running / succeeded / failed / skipped。沿用 Phase 7.1 已有 succeeded / partial / failed 工作流状态，不另建 success / partial_success 同义状态。独立 PCA 不会因 DEG 失败被跳过；只有其自己的数据/执行异常才失败。

## 持久化及追踪

```text
<project>/results/workflows/<workflow_id>/
  validated_plan.json
  workflow_state.json
  workflow_result.json
  workflow.log
  steps/001/
    provenance.json
    execution_result.json
    bindings/                 # 仅需要派生输入时创建
    skill/                    # 原 Skill 自己的目录结构、summary、日志和真实结果
```

数字步骤目录避免 Windows 保留名称或大小写冲突；逻辑 step_id 保存在所有记录中。workflow_id 另做可移植目录名验证。同一 workflow_id 不能二次执行，也不能覆盖中断后的目录；不会自动重试。

Provenance 包含 workflow/user request、执行顺序、skill/step/run ID、参数、binding、真实输入与输出路径、SHA-256、子集方向和数量、开始/结束时间、错误及状态。skipped 原因保存在 workflow_state / workflow_result。成功 Skill 的原 summary 仍可由 ResultManager 发现；新增 execution_result 文件避免重复注册同一 run。

历史结果缺少原始 artifact 校验和时明确拒绝（`UPSTREAM_PROVENANCE_MISSING`），不偷偷信任或重算历史。校验和用于检测变化，不是密码学身份认证或恶意用户隔离。

## 如何调用

在已导入数据的项目上，由宿主程序调用下面的接口；这不是新增 UI 功能。`checked` 可以是 Phase 7.3 对真实 Planner 输出的验证结果：

```python
from pathlib import Path
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workflow import PlanValidator, ProjectHistory, WorkflowExecutor

manager = ProjectManager(Path("workspace"))
registry = create_default_registry()
project_key = "your_existing_project"
history = ProjectHistory(manager, project_key)

# plan 为 Phase 7.2 生成的 WorkflowPlan；这里不创建/调用 Planner。
checked = PlanValidator(registry).validate_project(
    plan, manager.get_project_context(project_key), history=history,
)
if checked.valid:
    result = WorkflowExecutor(registry, manager, project_key).execute(checked)
    # result 为原 WorkflowResult 模型；调用方展示 result.status / failed_steps。
else:
    # 展示 checked.errors，不执行。
    pass
```

宿主应捕获 SkillError，使用 code/message/details 展示错误。不要把主机路径和 Rscript 配置交给 DeepSeek。

历史 DEG binding 示例（run_id 必须替换为真实项目记录中的 ID）：

```json
{
  "deg_result_file": {
    "source": "project_result",
    "run_id": "actual-skill-run-id",
    "output": "deg_csv"
  }
}
```

### 测试命令（项目根目录）

```powershell
# 默认：不调用 API、不运行 R
.venv\Scripts\python.exe -m pytest -q

# 本阶段免费单元测试
.venv\Scripts\python.exe -m pytest tests/test_workflow_executor.py -q

# 本阶段真实 R 集成测试 + 单元测试；仍不调用 API
.venv\Scripts\python.exe -m pytest tests/test_workflow_executor.py --run-r -q
```

真实测试将独立项目和结果保留于 `outputs/workflow_tests/<unique-id>/`，不覆盖 Real_DATA_TEST、workspace 用户项目或原 examples。

## 测试记录

- 本阶段：25 个默认单元测试；显式真实 R 测试 3 个参数化用例，共 28 passed（39.31 秒）。
- 全项目默认回归：1097 passed、71 skipped（76.79 秒）；跳过包含显式 opt-in 的 R/API 测试与一个需本地权限的 Windows symlink 测试，没有失败。
- 真实测试一：DEG → Volcano，随后另建工作流只执行 Volcano 并复用原 DEG。
- 真实测试二：DEG → KEGG(up)，逐项比对派生 gene_list 和 KEGG 实际 query_used 与原 DEG 上调集合完全一致。
- 真实测试三：DEG 分支到 Volcano / KEGG，同时包含独立 PCA root。
- 单元测试覆盖 up/down/all、分支失败、传递跳过、独立 root、明确历史引用、项目隔离、历史文件篡改、输入中途变化、非法/缺失输出、原校验和不符、重复执行拒绝、非法目录 ID、检查点/结果 round trip、warnings 与 output_alias。

真实程序使用既有 demo：30 基因、6 样本、两组各 3 个重复；KEGG 注释是明确标注的**测试用合成关联**，不是黑鲷生物学验证，也不作真实通路解释。输入由测试声明，不冒充 DeepSeek 计划。

这轮保留的证据：

| 用例 | 报告路径 |
| --- | --- |
| DEG → Volcano / 历史复用 | `outputs/workflow_tests/45913d03fcc14ecdbdb5510668ef3d77/integration_report.json`；历史复用的第二份 WorkflowResult 在同项目 results/workflows 下 |
| DEG → KEGG(up) | `outputs/workflow_tests/60bfa8df864e432fbbfd801e3797b3f7/integration_report.json` |
| 分支 + 独立 PCA | `outputs/workflow_tests/6d45dde93b734018b97c17dca46ab3d0/integration_report.json` |

从真实 SkillResult 读取：DEG 检验 30 基因，显著 18（上调 9、下调 9）；KEGG 实际输入/映射均为 9，背景 30，显著通路 0，状态成功。PCA PC1=98.4012283956765%、PC2=1.11594639943625%。分支测试约 DEG 7.74 秒、Volcano 0.41 秒、KEGG 0.54 秒、PCA 7.52 秒；仅为小数据烟雾测试，不是性能基准。

API requests=0，API tokens=0。本阶段未调用 Planner 或 DeepSeek。

## 错误码与限制

主要新增错误：WORKFLOW_NOT_VALIDATED、WORKFLOW_REVALIDATION_FAILED、WORKFLOW_VALIDATION_CHANGED、INVALID_WORKFLOW_ID、WORKFLOW_ALREADY_EXISTS、PROJECT_INPUT_CHANGED、WORKFLOW_INPUT_CHANGED、UPSTREAM_ARTIFACT_CHANGED、UPSTREAM_PROVENANCE_MISSING、INVALID_SKILL_OUTPUT、SKILL_RESULT_MISMATCH、INVALID_UPSTREAM_DEG、EMPTY_GENE_SUBSET、WORKFLOW_STEP_FAILED、WORKFLOW_IO_ERROR；原 Registry/Skill 错误保持原码和详情。

限制：

- 仅串行，无自动恢复、重试、并行、计划修复。中断后检查点可诊断，但不能续跑同 ID。
- 历史结果必须显式 run_id 并具有已有校验和；不做“最新比较”的自动推断。
- 仍复用各 Skill 自身默认值；不会为了统一图形阈值自动改下游参数，非默认阈值需要在计划中明确指定。
- 文件检查不是完整 sandbox，也不防止恶意并发修改后恢复原文件内容。
- 未新增 Workflow UI、跨步骤 LLM 总结或真实大规模数据 benchmark；不能以这些后端测试替代 UI E2E。
- 完整 SkillResult 保存在本地，可能含本地路径/R 诊断；不是直接发送给模型的 prompt。

## Phase 7.4 验收结论：PASS

已验证拓扑顺序、Registry 唯一执行入口、真实上游输出传递、up/down/all、分支失败隔离、传递 skip、历史结果复用、WorkflowResult 与 provenance；默认回归和本阶段真实 R 测试均通过。执行前重验、文件变化检测及空子集错误是新增 Executor 的行为约束，不改变已有 Skill 的统计逻辑。未修改已有阶段的稳定执行链，未调用 Planner/DeepSeek。停止于 Phase 7.4，不进入 Phase 7.5。
