# DEG Skill 契约（Phase 3 / package 0.2.0）

**Phase 5.1 更新：** 新运行使用 [统一 SkillResult schema 2.0](skill-result-schema.md)。
本文的 schema 1.0 JSON 示例作为历史兼容说明保留；输入与统计约定仍有效。
新增统一字段、完整 Python/CLI 返回和错误顶层字段，原 metrics/artifacts/error 等兼容字段保留。

Phase 4.5（package 0.7.0）增加 `operation=kegg_enrichment`，详细输入输出见 [KEGG 契约](phase-4-5-kegg.md)。
`run_kegg_enrichment(gene_list, gene_to_pathway_mapping, output_dir, *, universe=None, analysis_genes=None,
padj_threshold=0.05, pvalue_threshold=1.0, mapping_sheet=None, timeout=600, rscript_executable="Rscript")`。
必须声明 universe 或 analysis_genes；gene ID 精确匹配。eggNOG 使用 KEGG_Pathway，ko/map 参考编号统一并去重。
新错误包括 PATHWAY_MAPPING_REQUIRED、INVALID_PATHWAY_ID、MIXED_PATHWAY_NAMESPACES、NO_PATHWAY_ASSOCIATIONS；
背景、执行、输出错误沿用已有结构。空富集为成功状态，不伪造 pathway 名称。

Phase 4.1（package 0.3.0）保留本文的 DEG 统计与输出约定；通用错误的 operation 现可指定。
PCA 使用同一信封和目录/日志规则，operation=pca，专属输入与产物见 [PCA 契约](phase-4-1-pca.md)。
Phase 4.2（package 0.4.0）增加 operation=volcano，仅消费已有 DEG 表，不重算统计；
专属字段和零值/缺失值规则见 [Volcano 契约](phase-4-2-volcano.md)。本文 DEG 约定不变。
Phase 4.3（package 0.5.0）增加 operation=heatmap，两种输入路径和输出见 [Heatmap 契约](phase-4-3-heatmap.md)。
PCA 与 Heatmap 的 raw-count 路径共享 VST 模块，DEG 差异检验逻辑未修改。
Phase 4.4（package 0.6.0）增加 operation=go_enrichment；使用用户注释和显式分析背景，详见本文末节及 [GO 契约](phase-4-4-go.md)。

Python 采用标准库 dataclass 和显式验证函数；本阶段无需增加运行时框架。
JSON 信封版本保持 `schema_version=1.0`，包版本升级到 0.2.0，标识 DEG 阈值修正。
CLI 的 JSON 输出与 Python 的 `to_dict()` 是未来 AI/API 的接入边界；当前没有 LLM/API。

## 输入

`run_deg(count_matrix_path, metadata_path, treatment, control, output_dir, *, alpha=0.05,
lfc_threshold=1.0, timeout=600, rscript_executable="Rscript")`。

- 路径参数使用 pathlib.Path；相对路径以调用时工作目录解析，程序不硬编码绝对路径。
- count matrix：UTF-8（可带 BOM）逗号分隔，第一列 `gene_id`，其他列是唯一的样本名称。
- metadata：UTF-8 CSV，包含唯一 `sample_id` 和 `condition`；可有额外列，额外列不进入设计。
- 标题、基因 ID、样本 ID 不得为空/重复/带前后空格；数值样式的 ID 按字符串保留。
- 各行列数必须与表头一致。允许纯空白行；空单元格以及 NA、N/A、NaN、NULL、None 视为缺失（不区分大小写）。额外 metadata 列也检查缺失。
- counts 必须为有限、非负、数学意义上的整数，最大 2147483647；10.0、1e1 等整数表示合法。
- 只能判断数值是否符合要求，不能从矩阵自动证明它是 raw counts；数据提供者必须保证未使用 TPM/FPKM/normalized counts。
- 两个表的样本集合必须完全一致；R 根据 counts 列名显式重排 metadata。
- treatment/control 必须不同、非空、均存在；metadata 只允许这两组。
- 每组至少 2 个样本；程序无法证明这些样本是独立生物学重复，不能用技术重复冒充。
- 拒绝全零矩阵、零文库样本；单个全零基因继续按既有 R 实现排除。
- alpha 必须在 (0,1)，lfc_threshold >= 0，timeout > 0，均要求有限数值。

## 执行与输出目录

输出目录必须不存在或为空。程序创建 `.run.lock` 防止同一目录并发运行。
已有文件不会被覆盖，包括失败目录；重试使用新的目录。
参数/输入检查不通过不会启动 R。

输入先复制到本次运行的临时目录，验证和 R 计算使用同一份快照，并记录该快照 SHA-256。
输入原文件不会被修改。临时目录正常结束后清理。
程序使用固定包内 R 脚本、参数列表和 `subprocess.run`，捕获两个输出流并检查退出码，默认超时 600 秒。
stderr 非空可能只是 R 的正常诊断/警告，不单独判定失败；记录 `R_DIAGNOSTICS` 提醒检查。

## 成功结果

Python 返回 `DegRunResult`；可调用 `to_dict()`。CLI 将一个 JSON 对象写入 stdout，退出码 0。

| 文件 | 用途 |
|---|---|
| deg_results.csv | R 的全部非全零基因结果，七列：gene_id/baseMean/log2FoldChange/lfcSE/stat/pvalue/padj |
| deg_filtered.csv | R 输出满足 padj < alpha 且 abs(log2FoldChange) >= lfc_threshold 的子集；可只有表头 |
| summary.json | 成功信封、参数、计数、版本、输入和脚本及结果 SHA-256、进程信息 |
| run.log | run_id、输入路径、参数、验证与执行阶段、内部异常细节 |
| r_stdout.log | Rscript 的完整 stdout（UTF-8 解码，非法字节替换） |
| r_stderr.log | Rscript 的完整 stderr |

Python 在写成功摘要前检查两份 CSV：文件存在/非空、列完整、基因 ID 唯一且与预期集合一致、
概率在 [0,1]、数值有限、baseMean/lfcSE 非负、筛选表与完整表及阈值完全一致、软件版本存在。
DESeq2 合法的 pvalue/padj 缺失保留并计数，不改成 0，不算显著。

summary 中 `significant_genes` 在 0.2.0 起指同时通过两个阈值的 DEG 数量；
`padj_significant_genes` 单独保留只按 padj 筛选的计数。正 log2FoldChange 表示 treatment 相对 control 上调。
没有 DEG 是合法成功，并非软件错误。

两个 CSV 验证通过后移到最终位置，`summary.json` 通过临时文件替换最后发布。
这不是多个文件的事务；消费者必须以 `summary.json` 的成功状态为准，不能仅凭 CSV 存在判断成功。

## 失败结果

Python 统一抛出 `SkillError`（`DegRunError` 为兼容别名），属性包含 code/details/run_id。
预期失败不会把 traceback 放进 JSON；内部异常 traceback 只写到 run.log。
CLI 捕获异常，stdout 返回下列 JSON，stderr 通过 logging 输出简明错误：

```json
{
  "schema_version": "1.0",
  "operation": "differential_expression",
  "run_id": "本次运行ID",
  "status": "failed",
  "artifacts": {},
  "error": {
    "code": "SAMPLE_MISMATCH",
    "message": "Count and metadata sample sets differ.",
    "details": {"missing_in_metadata": ["sample_x"], "missing_in_counts": []}
  }
}
```

已占用输出目录时保存相同 `failure.json`，清除本次尚未完成发布的 CSV/summary。
若目录根本不可写、非空、已占用，或磁盘失败导致诊断也无法写入，CLI/Python 返回值是有效依据。
输入失败没有 R 输出流；R 启动失败/超时/非零退出使用具体错误码，已有的进程输出尽量保存。
失败不自动重试；用户修复后使用新目录运行。

CLI 退出码：0 成功；1 DEG 输入/执行/产物失败；2 CLI 参数或配置错误。
`--help`/`--version` 使用常规文本输出。

| 错误码 | 含义 |
|---|---|
| FILE_NOT_FOUND / INPUT_NOT_FILE / INPUT_READ_ERROR | 输入不存在、不是文件、无法读取/复制 |
| COUNT_FORMAT_ERROR / METADATA_FORMAT_ERROR | 编码、CSV、列名、行宽或空表不符合契约 |
| MISSING_VALUE / DUPLICATE_ID / INVALID_IDENTIFIER | 缺失、重复或不合法标识符 |
| SAMPLE_MISMATCH | 样本集合不一致；details 给出差集 |
| GROUP_NOT_FOUND / IDENTICAL_GROUPS / UNSUPPORTED_GROUPS | 组不存在、两组相同或包含第三组 |
| INSUFFICIENT_REPLICATES | 每组少于两个样本 |
| COUNT_NOT_NUMERIC / COUNT_NOT_FINITE | 非数字或无限值 |
| NON_INTEGER_COUNT / NEGATIVE_COUNT / COUNT_OUT_OF_RANGE | 小数、负数或超出 R 整数范围 |
| NO_ANALYZABLE_GENES / ZERO_LIBRARY | 全零矩阵或零文库样本 |
| INVALID_PARAMETER | 数值参数/分组名不符合要求 |
| OUTPUT_NOT_DIRECTORY / OUTPUT_NOT_WRITABLE | 输出不是目录或不可创建/写入 |
| OUTPUT_NOT_EMPTY / OUTPUT_BUSY | 旧内容保护/并发占用 |
| RSCRIPT_NOT_FOUND / RSCRIPT_START_FAILED | 可执行程序不存在或无法启动 |
| RSCRIPT_FAILED / RSCRIPT_TIMEOUT | 非零退出或超时 |
| OUTPUT_MISSING / OUTPUT_INVALID | 产物缺失或内容/版本校验失败 |
| IO_ERROR / INTERNAL_ERROR | 文件系统或未预期内部错误 |
| INVALID_ARGUMENT / CONFIG_ERROR | CLI 参数错误或应用配置错误 |

## 稳定性的范围

以上机制提高可重复性、失败可诊断性和结果可核查性，不保证研究设计合理或生物学结论正确。
DESeq2 保持默认 Wald 检验、独立过滤及离群值处理。LFC 阈值是后置筛选，无 shrinkage。
进程被强制终止/机器断电可能留下临时目录或锁；不会自动清除疑似其他进程持有的锁。
人工确认任务已停止后可另选输出目录；完整任务恢复不属于 Phase 3。
版本与哈希是运行溯源，不等于锁定所有 R 依赖；本阶段没有虚构 renv.lock。
# GO enrichment (Phase 4.4)

`run_go_enrichment(gene_list, gene_to_go_mapping, output_dir, *, universe=None, analysis_genes=None,
pvalue_threshold=0.05, padj_threshold=0.05, ontology="ALL", mapping_sheet=None, timeout=600, rscript_executable="Rscript")`

Exactly one background source is required. `analysis_genes` full DEG CSV excludes NA padj/log2FC;
plain lists declare eligibility. Effective universe intersects this background with user GO annotation.
Foreground must be a subset of declared background. Never use all annotation genes as a fallback.

Result follows schema_version 1.0, operation `go_enrichment`, status `succeeded`, `artifacts`, `error: null`.
Summary adds `methods`, `metrics` (including n_query_mapped/n_universe/n_terms_tested/n_enriched),
`parameters`, `inputs`, `warnings` and hash/version `provenance`.
Errors use existing SkillError failed contract, including BACKGROUND_REQUIRED, GENES_OUTSIDE_UNIVERSE,
NO_MAPPED_GENES, MAPPING_FORMAT_ERROR, INVALID_GO_ID, ONTOLOGY_INFORMATION_REQUIRED, RSCRIPT_FAILED,
OUTPUT_MISSING and OUTPUT_INVALID. Empty enrichment is successful with NO_ENRICHED_TERMS warning.

Details and artifact columns: [GO contract and usage](phase-4-4-go.md).
