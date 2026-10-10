# Phase 3：验证与学习指南

## 数据流与阅读顺序

`cli.py -> run_deg() -> reserve_output() -> 输入快照 -> validate_inputs() -> run_rscript()
-> verify_results() -> 发布 CSV -> 最后发布 summary.json`。

失败沿统一 SkillError 回到 CLI，输出失败 JSON 和非零退出码。详细契约见 [contracts.md](contracts.md)。

| 文件 | 重点理解 |
|---|---|
| src/bulk_rnaseq/exceptions.py | 一个异常类 + 稳定错误码；为 AI/API 提供可分支处理的信息 |
| src/bulk_rnaseq/models.py | dataclass/type hints；将 Python Path 转成 JSON 字符串 |
| src/bulk_rnaseq/validation.py | 逐行读取、逐单元格检查、集合比较和生物学前置条件 |
| src/bulk_rnaseq/run_io.py | 排他锁、空目录保护、原子 JSON 替换、SHA-256 |
| src/bulk_rnaseq/logging_config.py | 每次运行独立的日志、finally 关闭文件句柄 |
| src/bulk_rnaseq/r_runner.py | 无 shell 的 subprocess，检查退出码、捕获输出、超时处理 |
| src/bulk_rnaseq/artifacts.py | 运行成功不等于产物正确；验证数据结构和一致性 |
| src/bulk_rnaseq/skills/deg.py | 调用上述小函数，统一成功和失败流程 |
| src/bulk_rnaseq/cli.py | 命令行边界捕获异常；stdout JSON，stderr logging |
| src/bulk_rnaseq/r_scripts/deseq2_deg.R | 真实统计计算；保留字符串 ID；修正 Phase 2 缺失的双阈值筛选 |
| tests/conftest.py | 复用小数据夹具、明确开启真实 R 集成测试 |
| tests/test_validation.py | 每种坏输入对应明确错误码，并确认 R 未启动 |
| tests/test_execution.py | 模拟进程失败、坏产物、写入失败及目录保护 |
| tests/test_artifacts.py | padj/LFC 阈值边界、合法 NA、空 DEG 表 |
| tests/integration/test_real_deg.py | 真实 DESeq2、metadata 换序不变性、contrast 反向性 |

## 运行命令

在项目根目录的 PowerShell 中运行，不需要激活环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pytest
```

默认会跳过标记为 integration 的 R 测试，这是明确的跳过，不是 R 已测试通过。

运行真实 R 测试（需要 Rscript 在 PATH、DESeq2 已安装）：

```powershell
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests --run-r
```

R 不在当前 PATH 时，在上条命令后添加 `--rscript` 和实际 Rscript.exe 路径。
测试会真的调用三次 DESeq2；验证样本换序和反向比较，不固定依赖版本可能变化的精确 p 值。

演示（每次选择新的输出目录）：

```powershell
.\.venv\Scripts\python.exe main.py deg --counts examples/data/counts.csv --metadata examples/data/metadata.csv --treatment treatment --control control --output-dir outputs/my_phase3_run
```

检查 `summary.json`、完整/筛选 CSV 和日志。一个目录存在旧文件时运行会被明确拒绝。
模拟数据只用于验证程序流程，不能据此得出生物学结论。

## 阶段验收

2026-09-10 已在 Python 3.13.2 / R 4.6.1 / DESeq2 1.52.0 环境完成全部 69 项测试，
其中真实集成测试执行三次 DESeq2。复查命令如上；测试夹具只在临时目录产生。

- 所有要求的输入问题在 R 启动前拒绝，有清楚的错误码和定位信息。
- Python 3.13 下 pytest 通过，默认测试不依赖 R/网络。
- 显式开启的真实 R 回归测试通过，不能用 mock 替代这个结论。
- CLI 错误是可解析 JSON，非零退出，终端没有完整 traceback。
- R 的退出码/stdout/stderr 可追踪，超时有独立错误码。
- 失败不产生成功摘要，旧运行不覆盖，空 DEG 是成功。
- 结果文件列、基因集合、范围、过滤条件与摘要一致。
- 没有新增 RNA-seq 分析模块、LLM、API 或 Docker。

## 对应“确保输出结果稳定性”的岗位能力

| 机制 | 避免的问题 | 可展示的证据 |
|---|---|---|
| 输入契约和前置验证 | 不合法数据进入统计流程 | 参数化坏输入测试，R 未被调用 |
| 样本对齐和显式 contrast | 样本错配、方向混淆 | 真实换序/反向比较回归 |
| exit code、超时和流捕获 | 外部程序失败被报告为成功 | R 失败与超时测试、日志 |
| 输出集合/范围/双阈值校验 | 缺失结果、错误过滤、损坏产物 | 损坏产物与边界测试 |
| 独立目录、锁、summary 最后发布 | 旧结果混入或半成品被当成成功 | 非空目录/锁/写入失败测试 |
| 统一错误 JSON + run_id | API/AI 无法可靠判断失败原因 | failure.json、CLI JSON 测试 |
| 输入快照、版本、SHA-256 | 无法确定一次结果来自什么输入/程序 | summary 的 provenance |
| 单元测试 + 真实集成测试 | 重构导致行为回退 | pytest 和实际 DESeq2 的独立证据 |

Phase 3 完成后停止，由用户确认后才进入下一阶段。
