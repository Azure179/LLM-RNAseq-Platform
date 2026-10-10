# Phase 5.3：Skill Registry

本阶段实现统一名称查询和同步执行入口，不增加分析能力、LLM、Agent、API、MCP 或 Workflow。
已有六个 Skill 的参数、统计逻辑、文件输出、直接调用接口保持不变。

## 设计与职责

Registry 内部只有一个 `dict[str, RegisteredSkill]`，没有按 Skill 名称分支的巨大 if/elif。
`create_default_registry()` 用明确导入的现有函数构建绑定表，再加载 Phase 5.2 的七份 Manifest。
它不执行 Manifest 的 entry_point 字符串、不扫描第三方插件，也不根据用户输入动态导入代码。

默认注册六个可调用 Skill 和一个不可调用的 GSEA 描述。可用性表示代码是否实现，不代表本机依赖和输入一定满足运行条件。
每次创建都是独立 Registry，无全局单例。建议在程序启动时完成注册，然后查询和执行。

| API | 返回/作用 |
|---|---|
| `SkillRegistry()` | 创建空 Registry，加载包内公共 Schema，不执行 R |
| `register(manifest, executor)` | 注册可信函数和描述；重复名称报错，不替换旧绑定 |
| `get(name)` | 返回 RegisteredSkill，含 name、executor、available、manifest、input_schema |
| `get_manifest(name)` | 返回 Manifest 的 JSON 副本 |
| `get_input_schema(name)` | 返回 input_schema 的 JSON 副本 |
| `get_executor(name)` | 返回原 Python 函数；GSEA 报 SKILL_UNAVAILABLE |
| `list_skills()` | 返回按名称排序的六个可调用 Skill 名称 |
| `list_skills(available_only=False)` | 返回全部七个名称，包括 GSEA |
| `execute(name, validated_input)` | 总是返回规范 SkillResult，包括失败；不向调用方暴露未处理的普通执行异常 |

注册和查询属于程序配置操作，错误继续抛出已有 `SkillError`，可用 `.to_dict()` 获取结构化信息。
`execute()` 是统一分析边界，会捕获 SkillError 并返回失败 SkillResult。
这与直接调用 `run_deg()` 的“返回成功 / 抛出 SkillError”行为不同；旧接口没有改变。

## 调用链

```text
User / LLM（本阶段用 Python 参数字典模拟，尚未接入 LLM）
    ↓ name + input
Skill Registry
    ├─ 查询名称与可调用状态
    ├─ 校验 JSON 参数结构，转换 pathlib.Path
    └─ 取出绑定 executor，以命名参数调用
    ↓
原 Skill
    ├─ 检查文件内容与科学前提
    ├─ 调用原 Rscript / 统计或绘图程序
    └─ 验证产物、记录日志、发布 summary.json
    ↓
Skill Registry：转为统一模型、核对身份与 output_schema
    ↓
SkillResult（succeeded 或 failed）
```

Manifest 是说明书；executor 是实际函数；Registry 把名称与二者关联；SkillResult 是一次执行的结果。
Registry 不理解自然语言，不决定分析计划，也不重算任何统计量。

## 使用示例

在项目环境的 Python 中：

```python
from pathlib import Path
from bulk_rnaseq.builtin_registry import create_default_registry

registry = create_default_registry()
names = registry.list_skills()
entry = registry.get("rnaseq.deg")
manifest = registry.get_manifest("rnaseq.deg")
input_schema = registry.get_input_schema("rnaseq.deg")
executor = registry.get_executor("rnaseq.deg")

result = registry.execute("rnaseq.deg", {
    "count_matrix_path": Path("examples/data/counts.csv"),
    "metadata_path": Path("examples/data/metadata.csv"),
    "treatment": "treatment",
    "control": "control",
    "output_dir": Path("results/registry_demo"),
    # Rscript 不在 PATH 时，在此用 rscript_executable 指定真实路径。
})
payload = result.to_dict()
if result.status == "succeeded":
    deg_csv = Path(result.outputs["deg_csv"])
else:
    error_code, message = result.error_code, result.message
```

这是运行原 DESeq2 的示例，不是返回模拟分析结果；每次需选择新目录或空目录。
结果为 Phase 5.1 的 SkillResult，保留所有 summary 指标；`to_dict()` 给出规范字段，磁盘 summary 仍附带旧兼容字段。
二者按 `SkillResult.from_dict(disk_summary).to_dict()` 转换后应相同。

需要手动注册现有可信函数时：

```python
import json
from importlib.resources import files
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.skills.deg import run_deg

registry = SkillRegistry()
description = json.loads(files("bulk_rnaseq.manifests").joinpath("rnaseq.deg.json").read_text(encoding="utf-8"))
registry.register(description, run_deg)
```

注册要求描述版本和状态正确、executor 可接受命名参数、参数集合及必填项匹配，Schema 可校验且引用可解析。
自定义 executor 必须返回 SkillResult 或现有 ResultEnvelope；不是任意 dict。仅接受同步普通函数契约，不提供异步调度支持。
未实现描述只能绑定 None，input/output_schema 必须为 false。
不支持 **kwargs、位置专用参数或不受限的外部 Schema；局部引用及包内两个共享 Schema 可用。
Manifest 查询返回副本，普通调用方修改副本不会改变已注册的契约。

## 验证与错误边界

虽然参数名是 `validated_input`，Registry 不假设调用者已经正确验证：

- 先复制并检查 JSON 可序列化性，拒绝 NaN/Infinity 等。
- 按 Manifest 检查必填项、未知参数、阈值、互斥输入等结构约束。
- 依据 executor 的 Path 类型注解转换路径，不通过字段名字后缀猜测。
- 默认值仍由原 Python 函数提供；Schema 不填充默认值。
- 文件是否存在、raw counts 是否正确、sample 是否匹配、分组与背景是否合理，仍由原 Skill 检查。
- Schema 中 integer 以数学意义判断，原 Python 验证可能更严格；通过 Schema 不等于数据或执行必定成功。

| error_code | 含义 |
|---|---|
| UNKNOWN_SKILL | 名称未注册 |
| DUPLICATE_SKILL | 重复注册，原绑定保留 |
| SKILL_UNAVAILABLE | 已描述但未实现，例如 GSEA |
| INVALID_MANIFEST | 注册描述、签名、可用性或 Schema 不合法 |
| REGISTRY_CONFIG_ERROR | 包内配置资源无法读取 |
| INVALID_INPUT | Registry 参数结构校验失败，未启动 Skill |
| INVALID_SKILL_RESULT | executor 返回类型、Skill 身份或结果 Schema 不兼容 |
| INTERNAL_ERROR | 非预期执行错误，或错误详情不可序列化 |
| 原 Skill 错误码 | 例如 FILE_NOT_FOUND、SAMPLE_MISMATCH、RSCRIPT_FAILED，保持原错误信息和已有 run_id |

未启动 Skill 时没有分析 run_id，也不创建输出目录或 failure.json；以返回的失败 SkillResult 为准。
运行后的失败沿用 Skill 的日志和磁盘诊断，不重复生成一套运行目录。
Registry 使用 logging 记录注册、开始/结束和简明错误；原 Skill 在 run.log 中记录内部诊断。
自定义 executor 的非预期异常只记录异常类型，不把其原始异常文本/traceback 返回用户。
若 executor 已写文件后返回不兼容结果，Registry 拒绝该结果，但不会删除 executor 已写的文件或替它实现事务回滚。

这是可信本地代码接口，**不是安全沙箱**。`register()` / `get_executor()` 供可信宿主使用。
未来接入不可信 LLM/API 时还需由宿主限制可执行文件、超时、读写目录与资源使用；不能把任意 Rscript 路径直接开放出去。
本阶段没有自动重试、并发注册保证、动态插件加载、持久化 Registry 或全局单例。

## 文件与依赖

- 新增 `src/bulk_rnaseq/registry.py`：注册项、查询、结构验证、路径转换、统一执行结果。
- 新增 `src/bulk_rnaseq/builtin_registry.py`：七个显式绑定及默认 Registry 构建。
- 新增 `tests/test_registry.py`：注册/查询/列表/重复/未知/禁用/执行/错误/真实 R 测试。
- 修改 `requirements.txt`、`requirements-dev.txt`、`pyproject.toml`：将已有 jsonschema 提升为运行时依赖，避免重复声明。
- 修改 README、Phase 5.2 文档，并新增本文。
- 不修改六个 Skill、R 脚本、现有数据、既有测试或 Phase 5.1 SkillResult 模型。

## 验收命令

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/test_registry.py -q
.venv\Scripts\python.exe -m pytest tests --run-r --rscript Rscript -q
```

Rscript 不在 PATH 时替换为实际位置；真实测试需要 DESeq2。
验收标准：重复注册不覆盖；未知或禁用名称不执行；Schema 无效输入不调用 executor；六个原 Skill 可经 Registry 调用；
结果身份/结构正确；失败保持结构化；真实 Registry → DESeq2 → SkillResult 链路通过；原测试无退化。

实际验证：Registry 专项不启用 R 时 38 passed / 1 skipped；全量启用真实 R 时 **377 passed**，
包括新增的 Registry → 真实 DESeq2 集成测试。既有真实数据结果未重跑或改写。
