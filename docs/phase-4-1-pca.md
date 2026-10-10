# Phase 4.1：独立 PCA Skill

## 检查结果与设计原因

Phase 3 已有严格 UTF-8 CSV 校验、SkillError、每次运行日志、独立目录、输入快照、
Rscript 子进程和 pytest。PCA 使用这些组件，保留 schema_version=1.0 的结果信封。

原来的 validation 把公共文件检查与 DEG 的两组重复要求放在同一个函数中。
本次拆出 `validate_expression_inputs()`，原 `validate_inputs()` 仍执行 DEG 的两组检查；
PCA 使用独立 `validate_pca_inputs()`。没有修改 DESeq2 DEG 的模型、检验或阈值。

原 SkillError 将 operation 写死为 differential_expression，现改为可指定，默认值仍兼容 DEG。
PCA 使用 operation=pca。输入快照、流保存与失败清理移到 run_io，两个 Skill 共享同一套运行上下文。

测试发现反复调用 CLI 时旧 logger 可能引用已经关闭的 stderr。已修复为只替换本项目的控制台
handler，重新绑定当前流；不删除用户添加的其他 handler。新增回归测试验证无重复日志、无 logging traceback。

## 方法与数据流

```text
run_pca(count_matrix, metadata, group_column, output_dir)
  -> 占用新/空目录，启动 run.log
  -> 输入快照和公共 CSV 验证
  -> PCA 特有检查：至少 3 个样本、至少 2 个非全零基因
  -> 固定 transform_pca.R
     -> 读取 raw counts，按 counts 样本顺序对齐 metadata
     -> 排除全零基因
     -> DESeqDataSet(design = ~1)
     -> estimateSizeFactors(type = ratio)
     -> varianceStabilizingTransformation(blind = TRUE, fitType = parametric)
     -> 排除 VST 后样本间方差为零的基因
     -> prcomp(t(VST), center = TRUE, scale. = FALSE)
     -> 写 coordinates / variance / PNG
  -> Python 检查 exit code、stdout、stderr 和产物
  -> 发布产物，最后发布 summary.json
```

raw counts 受文库大小影响，而且表达均值与方差相关。DESeq2 的 size factors 处理文库规模，
VST 降低均值与方差之间的依赖，适合探索样本关系。这里不将基因再次缩放为单位方差，
也不先选显著 DEG，避免把差异分析的选择带入探索性 PCA。
参考 [DESeq2 数据变换说明](https://bioconductor.org/packages/release/bioc/vignettes/DESeq2/inst/doc/DESeq2.html#data-transformations-and-visualization)。

固定使用完整 `varianceStabilizingTransformation()`，不使用带默认 nsub=1000 子抽样的快捷 `vst()`。
小数据无需人为凑到 1000 个基因，但仍必须能可靠拟合离散度；无法拟合会如实返回 R 错误。

`blind=TRUE` 配合 `~1` 适合不使用分组信息的样本质控，group_column 仅用于着色。
当大量基因具有很强的真实组间差异时，盲估计可能增加离散度、压缩组间差别；本版固定方法，
不提供自动切换。VST 不会消除批次效应。

使用所有有非零方差的 VST 基因，无 top-N 参数。PC1/PC2 的解释方差百分比均以
**本次 PCA 的所有主成分方差之和**为分母，并非把前两个主成分强制归一到 100%。
至少三个样本是必要条件；如果数据不能提供两个数值上独立的主成分，仍会明确失败。

## 输入与调用

```python
from pathlib import Path
from bulk_rnaseq.skills import run_pca

result = run_pca(
    count_matrix=Path("examples/data/counts.csv"),
    metadata=Path("examples/data/metadata.csv"),
    group_column="condition",
    output_dir=Path("outputs/my_pca_01"),
)
result.to_dict()
```

- CSV 格式与 DEG 相同：count 第一列 gene_id；metadata 含唯一 sample_id。
- group_column 可以是任意存在的分组注释列，不要求名称为 condition。
- 支持一个或多个组；单组结果会带 SINGLE_GROUP 提示；不要求每组至少两个重复。
- 样本集合必须完全对应，metadata 可以换序。
- 沿用现有缺失值策略：counts 和 metadata（含额外列）都不允许缺失。
- 非负整数、R 整数范围、非零文库、唯一 ID 等检查复用公共实现。
- output_dir 必须新建或为空；已有结果保持不变。超时默认 600 秒。

在项目根目录执行（无需激活虚拟环境）：

```powershell
.\.venv\Scripts\python.exe main.py pca `
  --counts examples/data/counts.csv `
  --metadata examples/data/metadata.csv `
  --group-column condition `
  --output-dir outputs/my_pca_01
```

如果终端没有找到 Rscript，追加 `--rscript` 和实际可执行文件路径。
这个项目的已安装位置可使用：

```powershell
--rscript Rscript
```

这是本机运行命令示例；源码没有硬编码这条路径。

## 输出与错误

| 文件 | 用途 |
|---|---|
| pca_coordinates.csv | 严格四列 sample/PC1/PC2/group，一行一个样本，顺序与 count matrix 一致 |
| pca_variance.json | PC1/PC2 的解释方差百分比，有限数值，范围 (0,100] |
| pca_plot.png | PC1/PC2 散点图，轴标注百分比，颜色和图例区分组；1200×900 像素 |
| summary.json | 统一成功信封、参数、样本/组/基因数量、方差、版本、哈希和进程输出 |
| run.log、r_stdout.log、r_stderr.log | 复用 Phase 3 的日志契约 |
| failure.json | 目录可用时记录失败；失败不发布成功 summary |

沿用现有字段 `status=succeeded`、`operation=pca`、`metrics`、`artifacts`、`warnings`、
`provenance`、`error=null`；不另外建立 status=success/outputs 风格的第二套接口。

metrics 包含 n_samples、n_groups、pc1_variance、pc2_variance、genes_input、n_genes_vst、n_genes_pca。
artifacts 的路径相对于本次 output_dir；Python 返回对象也提供绝对 Path，to_dict() 将其转换为字符串。
输入快照、R 脚本、结果文件均记录 SHA-256，软件版本记录 R、DESeq2 和本项目版本。

产物检查包括样本顺序/组映射、有限坐标、中心化、正交性、解释方差范围/顺序/总和，
以及坐标方差比和报告方差比的一致性。PNG 检查签名、尺寸和结束标记，属于结构校验，
不是完整像素解码；真实示例另作人工视觉检查。

错误仍通过 SkillError(code/message/details) 返回。新增 GROUP_COLUMN_NOT_FOUND、
INSUFFICIENT_SAMPLES、INSUFFICIENT_GENES；其他错误码沿用 Phase 3。
R 内部变换/退化数据失败返回 RSCRIPT_FAILED，具体 PCA_ERROR 说明保存在 r_stderr.log。
输入错误、R 失败、输出目录失败的 operation 都标记为 pca。CLI 不显示未处理 traceback。

## 文件变更与工程概念

| 文件 | 变更与职责 |
|---|---|
| src/bulk_rnaseq/skills/pca.py | 新增独立 run_pca；负责组合现有基础设施、验证产物、生成统一摘要 |
| src/bulk_rnaseq/r_scripts/transform_pca.R | 新增真实 VST、PCA、方差计算与基础 R 绘图 |
| src/bulk_rnaseq/pca_artifacts.py | 新增 PCA 专有产物验证 |
| src/bulk_rnaseq/validation.py | 拆分公共表达矩阵验证与 DEG/PCA 各自的前置条件 |
| src/bulk_rnaseq/models.py | 增加 ExpressionOverview 和 PcaRunResult，保留旧 DEG 结果接口 |
| src/bulk_rnaseq/exceptions.py | operation 可指定；默认值兼容 DEG |
| src/bulk_rnaseq/run_io.py | 提取输入快照、流保存、analysis_run 运行上下文和失败清理 |
| src/bulk_rnaseq/skills/deg.py | 改用公共运行上下文，内部统计参数与结果检验不变 |
| src/bulk_rnaseq/cli.py | 增加 pca 子命令和 PCA 错误归属 |
| src/bulk_rnaseq/logger.py | 修复重复调用后引用已关闭 stderr 的旧问题 |
| src/bulk_rnaseq/skills/__init__.py | 导出 run_pca/PcaRunResult |
| src/bulk_rnaseq/__init__.py、pyproject.toml | 版本升级为 0.3.0；R 脚本继续使用既有 package-data 规则 |
| tests/test_pca.py | PCA 输入/产物/异常/CLI 测试，复用 input_files 与原有公共测试 |
| tests/test_logger.py | 新增 logger 重复调用回归 |
| tests/test_artifacts.py | 原输入读失败测试的 monkeypatch 路径随公共函数迁移更新 |
| tests/integration/test_real_pca.py | 真实 PCA、任意三组注释、标签不变性、退化样本失败 |
| README.md、docs/contracts.md、本文件 | 更新使用入口、接口与本阶段解释 |

本阶段新增工程概念只有两个重点：

1. **分离公共规则与分析规则**：同样的 raw counts 可以服务不同分析，但 PCA 不应该继承 DEG 的对照组约束。
2. **共享运行上下文**：目录、日志、异常清理只维护一处，各 Skill 只编排自己的计算和产物。

统计回归还使用了“不变性测试”：改变分组标签不能改变盲 PCA 结果。PC 轴的正负方向任意，
比较样本间距离比直接断言坐标符号更合理。

## 测试与验收

```powershell
# 单元测试，默认明确跳过 R 集成测试
.\.venv\Scripts\python.exe -m pytest

# 含 DEG/PCA 的真实 R 集成回归
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests --run-r
```

第二条可追加上述 `--rscript` 路径。开启真实 R 测试后，缺少依赖属于失败，不会假装通过。
单元测试中的坐标/图片明确为故障注入夹具，不能视为真实 PCA 结果。

验收要求：

- 所有要求的输入错误在 R 调用前被阻断；组列不存在有独立错误码。
- 支持多组、单组及重排 metadata，不错误套用 DEG 重复数要求。
- 真实 DESeq2 VST + PCA 生成全部四项输出，轴含解释方差，图例可读。
- 解释方差和坐标可互相核对；标注信息不进入计算。
- 默认测试、完整真实集成测试通过，原 DEG 回归通过。
- 旧输出不覆盖，失败不发布成功摘要，日志无未处理 traceback。

### 本机验收记录（2026-09-10）

- Python 3.13.2，R 4.6.1，DESeq2 1.52.0，项目 0.3.0。
- 默认测试：96 passed、3 skipped（仅跳过明确 opt-in 的真实 R 测试）。
- 完整测试：99 passed；包含原 DEG 真实回归和新增 PCA 真实回归。
- 真实程序示例：`outputs/pca_phase41_demo_v2`，6 样本、2 组、30 个 VST/PCA 基因。
- PC1 为 98.4012283956765%，PC2 为 1.11594639943625%；R exit code=0，stderr 为空。
- 已视觉检查 PNG：轴标签、百分比、分组图例完整。首次图例裁切已修复；旧演示目录保留，不覆盖。
- 数据来自项目自带模拟 counts，仅用于跑通验证；这些数值由真实 DESeq2/PCA 计算，不是预填结果。
- 原 `deseq2_deg.R` SHA-256 与 Phase 3 一致，DEG 统计脚本未修改。

本次测试进程临时移除了运行环境注入的 LANG/LC_ALL/LC_CTYPE，再调用明确的本机 Rscript 路径；
未更改系统区域设置，也未在源码中覆盖用户环境变量。

## 限制

仅支持普通 gene-level raw count CSV；不支持 TPM/FPKM、稀疏巨型矩阵或转录本定量导入。
使用 DESeq2 默认 ratio size factors：如果每个基因都至少在一个样本中为零，可能无法估计大小因子，
会返回 R 错误，不自动切换算法。少基因或无独立变化的数据也可能无法拟合或生成二维 PCA。
固定 blind VST、所有变异基因，未提供设计感知变换或 top-N；没有批次校正或交互图。
图适合少量到中等数量分组，极多/极长分组名的布局没有专门优化；图上不逐点标注样本名。
不导出 VST 矩阵，也不做组间显著性检验；点的分离不能单独证明处理效应。

Phase 4.1 完成后停止。
