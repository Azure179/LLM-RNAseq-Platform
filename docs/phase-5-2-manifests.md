# Phase 5.2：Skill Manifest

后续更新：Phase 5.3 已增加 [Skill Registry](phase-5-3-registry.md)，并将 jsonschema 从测试依赖提升为运行时依赖。
以下“本阶段”描述保留 Phase 5.2 的历史边界；七份静态 Manifest 内容不变。

Manifest 是能力说明书，不是一次运行的结果、调度器，也不是 Codex SKILL.md。
本阶段只增加静态描述、共享 JSON Schema、测试和打包配置，不改变任何分析程序。

## 为什么选择 JSON

- 标准库 `json` 即可读取，安装后的包也可通过 `importlib.resources` 获取。
- 参数与结果采用 JSON Schema Draft 2020-12，能被其他语言工具读取，不绑定 Python/Pydantic。
- 输入公共类型和 SkillResult 2.0 使用本地 `$ref` 共享定义，不复制验证函数或统计代码。
- 新增 `jsonschema>=4.23,<5` **仅作为开发测试依赖**，不增加运行时依赖。

Manifest 不是现成的 OpenAI/MCP 工具配置。未来适配时还需要按目标协议处理名称、Schema 方言/引用、可调用性和权限。
本阶段不做这些适配，不从 Manifest 动态导入或执行任意函数。

## 文件布局

```text
src/bulk_rnaseq/manifests/
├── __init__.py
├── rnaseq.deg.json
├── rnaseq.pca.json
├── rnaseq.volcano.json
├── rnaseq.heatmap.json
├── rnaseq.go.json
├── rnaseq.kegg.json
├── rnaseq.gsea.json
└── schemas/
    ├── input-types.schema.json
    └── skill-result.schema.json
```

| 文件 | 作用 |
|---|---|
| `rnaseq.deg.json` | 两组 raw-count DESeq2 的使用条件、真实参数和输出衔接 |
| `rnaseq.pca.json` | raw counts → blind VST → PCA；明确不导出 VST 矩阵 |
| `rnaseq.volcano.json` | 消费已有完整 DEG 表，不重新统计 |
| `rnaseq.heatmap.json` | VST/raw-count 两种互斥输入；Top DEG 与行 Z-score 的边界 |
| `rnaseq.go.json` | 自定义/eggNOG GO 注释、ontology 要求、有效背景与 Fisher/BH |
| `rnaseq.kegg.json` | pathway 映射、KEGG_Pathway 与 KO 的区别、有效背景与 Fisher/BH |
| `rnaseq.gsea.json` | 未实现能力的禁用占位，不承诺可执行参数或结果 |
| `schemas/input-types.schema.json` | 路径、计数/表格说明、概率阈值、超时等公共类型 |
| `schemas/skill-result.schema.json` | Phase 5.1 规范结果字段及成功/失败条件，兼容旧字段 |
| `__init__.py` | 将描述目录作为 Python 包资源；没有加载器或调度逻辑 |
| `tests/test_manifests.py` | 目录完整性、Schema 校验、签名/默认值、上下游引用和实际返回契约测试 |
| `requirements-dev.txt` | 增加仅用于测试的 JSON Schema 验证器 |
| `pyproject.toml` | 将 JSON 和 schemas 子目录包含在发行包中 |
| `README.md`、本文 | 当前阶段与使用说明 |

## 描述字段

每份描述都包含 name、version、description、category、input_schema、output_schema、requirements、dependencies、limitations，另外包括：

- `manifest_schema_version`：描述文件布局版本，目前 1.0。
- `version`：该能力描述的版本；六个已实现描述为 1.0.0，GSEA 占位为 0.0.0，不等于包版本或结果 schema 版本。
- `availability.status / callable`：是否已实现、是否可提供执行能力。implemented 不保证本机 R/DESeq2 已安装。
- `entry_point`：现有 Python 函数的定位信息，不是执行授权。GSEA 为 null。
- `use_when / do_not_use_when`：该调用和不该调用的情境。
- `raw_count_requirement`：是否要求或禁止 raw counts。
- `requirements`：Python、Rscript、R/Python 包及条件依赖。
- `dependencies.upstream / downstream`：真实 output 键与目标 input 参数的映射，以及额外所需数据。

上游引用表示兼容数据来源，并不强制先执行那个 Skill；等价的外部输入也可以使用。
Heatmap 的 normalized_expression 可在另一次 Heatmap 中复用，不是必须循环执行自己的依赖。
空 downstream 表示当前没有声明可直接衔接的下游 Skill，不表示图或表没有使用价值。

## 核对现有实现得到的重要边界

| 能力 | 实际边界 |
|---|---|
| DEG | metadata 必须是 `sample_id,condition`，只包含 treatment/control；每组至少两个独立生物学重复 |
| PCA | 至少三样本；输出坐标/方差/图，**没有可传给 Heatmap 的 VST 矩阵** |
| Volcano | 使用完整 DEG 表才能统计全部 NS；不调用 DESeq2 |
| Heatmap | 只有 `normalized_expression` 或 `raw_counts` 二选一；raw-count 路径复用公共 VST R 模块；输出 matrix 是 Z-score，不是 VST |
| GO/KEGG | 实际为离线基础 R `stats::fisher.test` + BH，不要求 clusterProfiler 或 org.Hs.eg.db |
| GO | 普通 eggNOG GOs 没有 ontology，默认 ALL；BP/MF/CC 需要为全部 term 提供 ontology |
| KEGG | 要 KEGG_Pathway，而非单独 KEGG_ko；缺名称保留 ID |
| GSEA | 当前没有实现；不能用 GO/KEGG ORA 冒充 GSEA |

GO/KEGG 使用 `gene_list` 时不会自动把完整 DEG CSV 筛成显著基因：应传筛选表或明确选定的 gene list。
`analysis_genes` 才是完整 DEG CSV 的背景入口，会排除 padj/log2FC 缺失行。
背景必须显式提供 universe 或 analysis_genes（二选一），有效背景再与所选注释取交集。
gene list 中全部基因必须在声明背景内；无映射是错误，无显著条目是合法空结果。

通用 Skill 不自动转换 eggNOG transcript ID 和 gene ID。
真实数据测试目录已有的特定物种 ID crosswalk 属于独立预处理，不能把它描述为通用 Skill 的自动能力。
自定义 GO/KEGG mapping 可提供 term/pathway 名称；eggNOG 的蛋白 Description 不是 term 名称。

## GSEA 占位如何防止误调用

```json
{
  "name": "rnaseq.gsea",
  "availability": {"status": "not_implemented", "callable": false},
  "entry_point": null,
  "input_schema": false,
  "output_schema": false
}
```

这是完整 Manifest 的节选。JSON Schema 的布尔 false 表示拒绝任何实例，不是“接受任意参数”。
描述中只记录未来需要有方向的全基因排序和匹配的 gene sets；不杜撰算法、参数签名、NES 或结果文件。
未来调用方必须先检查 availability，只发布实际可调用能力。

## 读取和验证

从安装了当前包的 Python 环境读取，不要求工作目录位于源码根目录：

```python
import json
from importlib.resources import files

description = json.loads(
    files("bulk_rnaseq.manifests").joinpath("rnaseq.deg.json").read_text(encoding="utf-8")
)
name = description["name"]
callable_in_package = description["availability"]["callable"]
```

注意：读取 JSON 只是读取文档，没有执行 Skill。参数中的路径是 JSON 字符串，交给 Python 函数时应转换为 pathlib.Path。
`input_schema` 描述真实 Python 函数参数，不是 CLI 的短选项，也不是 SkillResult.inputs 的运行记录结构。
`default` 是描述信息，JSON Schema 验证本身不会填入默认值；现有 Python 函数负责自己的默认参数。

`$ref` 相对于各 Manifest 文件所在目录解析，因此分发时必须保留 schemas 文件夹。
测试把共享 Schema 注册为本地资源，**不联网下载 Schema，也不创建 Skill Registry**。
不应只复制一份 Manifest 后忽略它的引用文件，或不加解析直接提交给未来工具协议。

输出使用 SkillResult 2.0：status 仍为 `succeeded/failed`，不是 success；各 Skill 输出逻辑键与实际文件一致。
summary/metadata 保留扩展空间，不为各指标复制一套业务模型。统一结果有修改时，需同步公共结果 Schema 并运行契约测试。

JSON Schema 不打开输入文件，不能检查 sample 对应、基因交集、生物学重复、VST 来源、treatment != control 等内容或跨字段语义。
它也不能替代 Python 对有限浮点数和整数表示的严格检查；例如 JSON Schema integer 按数学整数判断，Python 的 top_n 要求 int。
原有验证与异常机制仍是实际执行时的权威。不能因为 Schema 验证通过就认为分析一定能成功。

`rscript_executable` 和 timeout 为准确描述当前函数而保留；未来对 LLM 开放工具时，应由宿主控制可执行程序路径、超时和读写目录权限。

在项目根目录验证：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/test_manifests.py -q
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m pytest tests --run-r --rscript Rscript -q
```

不在 PATH 的 Rscript 需指定实际位置；真实 R 测试还需要 DESeq2。
Manifest 专项测试复用既有测试 fixtures，不把模拟结果当作生物学结论。

验收：七份描述齐全；六份签名/默认值匹配实际函数；GSEA 不可调用；本地引用可解析；真实 Python Skill 返回符合 output_schema；
成功/失败规则、互斥输入、参数边界和上下游输出键均通过测试；已有分析测试不退化。
完成后停止，不接入 LLM、API、MCP 或 Workflow。

## 本阶段验证记录（2026-09-16）

- Manifest 专项：47 passed。
- 全部 pytest，包含真实 R 集成测试：338 passed。
- 标准隔离构建成功，wheel 内包含七份 Manifest、两份共享 Schema 和资源包入口。
- 初次禁用构建隔离时因虚拟环境未安装 setuptools 而失败；使用 pyproject 已声明的隔离构建依赖后通过，没有修改业务代码。
- 未修改统计脚本、已有 Skill 函数或 Phase 5.1 结果模型；未改写真实数据分析结果。
