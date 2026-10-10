# Phase 5.1：统一 SkillResult（schema 2.0）

本阶段只统一 Python/JSON 结果契约。没有更改统计计算、筛选阈值、背景定义、R 脚本或 CSV/PNG 文件格式。
没有新增 LLM、Registry、Manifest、API 或分析 Skill。当前仓库没有 GSEA，不能把规划中的功能标记为已实现。

## 检查发现

| Skill / 新标识 | 现有主要输入（本次保留签名） | 原返回对象 / 专属指标 |
|---|---|---|
| DEG / `rnaseq.deg` | `count_matrix_path, metadata_path, treatment, control, output_dir`；`alpha, lfc_threshold` | `DegRunResult`；输入/分析基因数、padj 显著数、DEG、上下调、重复数 |
| PCA / `rnaseq.pca` | `count_matrix, metadata, group_column, output_dir` | `PcaRunResult`；样本/组数、VST/PCA 基因数、PC1/PC2 解释方差 |
| Volcano / `rnaseq.volcano` | `deg_result_file, output_dir`；`padj_threshold, log2fc_threshold, top_n_labels` | `VolcanoRunResult`；Up/Down/NS、实际绘制/排除/标注数 |
| Heatmap / `rnaseq.heatmap` | DEG、metadata、group_column；`normalized_expression` 或 `raw_counts`；`top_n`、阈值 | `HeatmapRunResult`；请求/实际基因数、样本/组数、分组列 |
| GO / `rnaseq.go` | gene_list、gene_to_go_mapping；`universe` 或 `analysis_genes`；ontology、两个概率阈值 | `GoRunResult`；有效前景/背景、映射覆盖、检验/显著条目数 |
| KEGG / `rnaseq.kegg` | gene_list、gene_to_pathway_mapping；`universe` 或 `analysis_genes`；两个概率阈值 | `KeggRunResult`；前景/背景、映射/别名、检验/显著通路数 |
| GSEA | **不存在** | 本阶段不补实现 |

所有已实现函数还保留 `timeout`、`rscript_executable`；GO/KEGG 保留 `mapping_sheet`。
输入参数名字不同是现有公开接口，本次不强行重命名。统一结果中的 `inputs.parameters` 承载各 Skill 已记录的参数；
不是新增通用执行入口或输入参数验证模型，输入仍由各 Skill 的公共验证设施检查。

共同问题与差异：

- 磁盘 `summary.json` 已有完整 `metrics`，原 Python `to_dict()` / CLI 却没有这些指标，也没有完整 inputs/warnings/provenance。
- DEG/PCA/Volcano 有专门的路径属性；Heatmap/GO/KEGG 主要依赖 `output_dir` 和 `artifacts`。原 artifacts 是相对于输出目录的文件名。
- 原 warnings 都有 code/message，但有的附加 count、display_height 等顶层字段，没有统一 details 容器。
- DEG/PCA/Volcano 使用 `process`，Heatmap 使用 `processes`，GO/KEGG 使用返回对象的 stdout/stderr；脚本哈希也有单值和字典之别。
- 异常机制本来已经共用 `SkillError`，但错误码在嵌套 `error.code`；没有统一 skill_name、inputs、summary、metadata。

## 新契约

标准库 `dataclass` 实现 `SkillResult`，显式验证状态、必填错误字段、JSON 类型、warnings 和非有限数值；不引入 Pydantic 依赖。
`type hints` 帮助开发者理解类型，运行时校验才负责阻止错误数据进入 JSON。允许各 Skill 保留专属的 summary/metadata 内容。

| 字段 | 类型与语义 | 主要用途 |
|---|---|---|
| `schema_version` | 固定 `"2.0"`；与 Python package 版本分开 | 机器识别契约版本 |
| `status` | `"succeeded"` / `"failed"` | 机器分支判断 |
| `skill_name` | 上表的 `rnaseq.*` 标识 | 机器识别执行的 Skill |
| `run_id` | 本次运行的 ID；CLI 参数错误等尚未建立运行时可为 null | 日志、产物、错误关联 |
| `inputs` | 原输入文件键 + `parameters` 对象 | 输入和参数追踪 |
| `outputs` | 逻辑名称 → 已发布文件的绝对路径字符串 | 下游读取真实产物 |
| `summary` | 完整的原 `metrics`，不删减或重命名指标 | 机器判断及用户结果展示 |
| `warnings` | 列表，每项 `code, message, details` | code/details 供机器；message 供用户 |
| `metadata` | 参数、方法、时间戳、provenance、运行诊断及原扩展字段 | 审计、排错、复现 |
| `error_code` | 成功为 null；失败为稳定错误码 | 机器分类处理 |
| `message` | 成功为 null；失败为简明描述 | 用户展示，不解析其文字做流程判断 |
| `details` | 成功为空对象；失败为结构化上下文 | 机器处理及可选错误详情展示 |

成功继续使用原来的 **`succeeded`，而非 `success`**，避免已有 runner 的状态判断失效。
GO/KEGG 没有显著结果仍是 succeeded，summary 记录 0，warnings 说明空结果；不能把空结果伪装成软件失败。
失败 outputs 和 summary 必须为空，不宣称存在有效分析结果。成功的 run_id 不能为 null。

成功 inputs 使用已解析的路径和 Skill 原来记录的有效参数。
失败时记录调用方传入的路径（可能是相对路径，未必通过验证）；`parameters` 暂为空对象，
不把未经验证的参数误称为有效分析配置。建立运行之前的 CLI 参数错误 inputs 也可能只有空 parameters。
错误具体信息继续由 error_code/message/details 表达；这不是完整的请求重放日志。

`outputs` 沿用原 artifacts 的逻辑键，比如 DEG 的 `deg_csv`/`filtered_csv`、Volcano 的 `plot`/`plot_data`、
GO/KEGG 的 `enrichment`/`all_results`/`query`/`universe`/`mapping`，各 Skill 都包括 `summary_json`。
路径由 pathlib 解析，没有硬编码本机目录；这里的绝对路径是运行产物定位信息，不是可跨机器搬运的 URI。

`metadata.processes` 始终是列表，记录 return_code/stdout/stderr；输入失败时 return_code 可为 null，不能据此认为执行过 R。
`metadata.provenance.r_scripts_sha256` 统一为“脚本名 → 哈希”的字典，同时保留旧单值哈希。
原 methods、sample_groups、timestamps、未知扩展字段均保留在 metadata。内部 traceback 仍只进入日志。

## Python 和 JSON 使用

已有调用不变；以下代码展示接口用法，不提供或编造分析数值：

```python
from pathlib import Path
from bulk_rnaseq import SkillResult
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.deg import run_deg

try:
    legacy_compatible = run_deg(
        Path("examples/data/counts.csv"), Path("examples/data/metadata.csv"),
        "treatment", "control", Path("results/new_schema_run"),
    )
    result: SkillResult = legacy_compatible.to_skill_result()
    payload = result.to_dict()       # 只有统一契约字段
    deg_file = Path(result.outputs["deg_csv"])
except SkillError as error:
    payload = error.to_skill_result().to_dict()
    # 调用方按 payload["error_code"] 决定展示或停止下游，不解析 traceback。
```

文件名需换成实际输入；各 Skill 的原运行命令见已有阶段文档。输出目录仍须是新目录或空目录。
Python 函数继续“返回成功对象 / 抛出 SkillError”，没有悄悄改成总是返回 dict。
统一模型可由 `SkillResult.from_dict(payload)` 重新校验和读取；不支持的 schema version 明确拒绝。
`to_dict()` 返回 JSON 数据副本，修改它不会改变本次已发布的结果快照，也不必重新读取磁盘文件。

## 兼容边界与发布一致性

- `DegRunResult` 等六个原类型、构造参数、Path 属性和 `to_dict()` 保留；它们现在是同一 SkillResult 的兼容外层。
- 原对象新增 `skill_name / inputs / outputs / summary / warnings / metadata` 属性。
- `legacy_compatible.to_dict()`、CLI JSON、新的 `summary.json` 内容完全一致，包含规范字段及旧字段。
- 旧 `operation, metrics, artifacts, parameters, provenance, process/processes, error` 等字段保留；旧 warning 附加字段也保留为兼容别名。
- 新调用方推荐 `to_skill_result().to_dict()`，不依赖旧别名。summary 是原 metrics 的完整副本，不是重新统计。
- schema_version 从 1.0 升为 2.0。严格只接受 1.0 的外部消费者须更新版本检查；不能声称完全零破坏。
- 历史 `Real_DATA_TEST` 结果不自动改写。需要读取旧 summary 时，用 `SkillResult.from_legacy_summary(old_payload)` 在内存中转换。
- 公共 `publish_result()` 先构建并校验统一快照，再原子写入 summary，返回同一快照的兼容对象。
- 原有“验证产物、summary 最后发布、失败清理本次成功产物、旧输出目录不覆盖”机制不变。
- `write_json()` 仍是普通 JSON 写入工具，不会意外改变 Real_DATA_TEST 总报告等其他 JSON 文档。

这里的 SkillResult 是**结果信封**，不是 Skill Registry，也不执行或选择任何分析。

## 为什么对 Tool Calling 重要

未来调用层只需一次判断 status，就能用 error_code 分支、用 outputs 找文件、用 summary 展示真实指标，
不必为六个 Skill 分别猜测字段或解析日志。run_id 与 metadata 保留可追溯证据，schema_version 明确兼容边界。
模型不能根据工具“似乎成功”的文字自行推断成功；必须依赖经过验证的结构化结果。

主要给机器的字段：schema_version、status、skill_name、run_id、outputs、error_code、details。
主要给用户展示：summary 的结果指标、message、warnings.message；metadata 用于展开查看方法和复现信息。
summary 同样可供程序消费。未来不应把完整 stdout/stderr、所有 metadata 或内部路径无筛选地发送给 LLM。

## 验证与验收

在项目根目录运行，无需激活 PowerShell 虚拟环境：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_result_schema.py -q
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m pytest tests --run-r --rscript Rscript -q
.venv\Scripts\python.exe Real_DATA_TEST/self_check.py
```

真实 R 测试需要 Rscript 可调用、DESeq2 已安装；不在 PATH 时，将 `--rscript` 值替换为实际可执行文件路径。
pytest 使用原有明确标注的合成 fixtures 测试代码，不把它们当作真实生物学结论。

验收标准：六个实际 Skill 返回统一成功/失败契约；Python/CLI/summary 一致；所有原专属指标和路径属性保留；
无效状态、NaN/Infinity、格式错误 warning 被拒绝；合法空富集保持成功；输出发布失败不能留下成功标记；
既有单元测试、真实 R 集成测试、独立真实数据 runner 自检通过，R 脚本哈希不变。

本阶段实际验证：2026-09-15，全量 pytest（包含真实 R）**291 passed**；不启用 R 时 **274 passed / 17 skipped**；
`Real_DATA_TEST/self_check.py` **15 项通过**。9 个 R 脚本 SHA-256 与重构前一致。
本次没有重跑或改写历史真实数据分析结果，也没有删除/替换原有 fixtures 和测试。

## 文件变更

| 新增/修改 | 文件 | 作用 |
|---|---|---|
| 新增 | `src/bulk_rnaseq/result_schema.py` | SkillResult、JSON 校验、副本、旧 summary 转换 |
| 修改 | `src/bulk_rnaseq/models.py` | 六个原结果类共用兼容层，保留属性与构造方法 |
| 修改 | `src/bulk_rnaseq/exceptions.py` | SkillError 转为相同的失败结果模型 |
| 修改 | `src/bulk_rnaseq/run_io.py` | 公共发布入口；失败记录输入路径 |
| 修改 | `src/bulk_rnaseq/__init__.py` | 导出 SkillResult |
| 修改 | `src/bulk_rnaseq/skills/deg.py` | DEG 接入统一发布和失败输入记录 |
| 修改 | `src/bulk_rnaseq/skills/pca.py` | PCA 接入统一发布和失败输入记录 |
| 修改 | `src/bulk_rnaseq/skills/volcano.py` | Volcano 接入统一发布和失败输入记录 |
| 修改 | `src/bulk_rnaseq/skills/heatmap.py` | Heatmap 接入统一发布和失败输入记录 |
| 修改 | `src/bulk_rnaseq/skills/go_enrichment.py` | GO 接入统一发布和失败输入记录 |
| 修改 | `src/bulk_rnaseq/skills/kegg_enrichment.py` | KEGG 接入统一发布和失败输入记录 |
| 新增 | `tests/test_result_schema.py` | 39 项模型、序列化、失败/CLI、历史兼容测试 |
| 新增 | `tests/result_helpers.py` | 六个 Skill 复用同一组成功契约断言 |
| 修改 | `tests/test_execution.py` | DEG 成功契约、发布失败注入位置迁移 |
| 修改 | `tests/test_pca.py / test_volcano.py / test_heatmap.py / test_go.py / test_kegg.py` | 原成功/空结果测试加入公共契约断言，不替换原统计断言 |
| 新增 | `docs/skill-result-schema.md` | 检查结论、设计、兼容边界、运行与验收 |
| 修改 | `README.md / docs/contracts.md` | 当前阶段说明与旧契约指向新版本 |
