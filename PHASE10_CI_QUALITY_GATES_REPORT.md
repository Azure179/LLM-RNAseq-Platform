# Phase 10.3 — CI & Automated Quality Gates

2026-11-19 · Windows 3.13.2 / Docker Desktop Linux amd64. 本报告分别记录本地机器实际执行的检查和 GitHub 的执行状态。

## 1. Existing Test Inventory

审计了 pyproject 中的 pytest 配置、tests/conftest.py 的显式 opt-in、Phase 8 contract、Phase 9 Compose / smoke、10.1.1 复用保护和 10.2 锁与科学对照。新增门禁后共有 89 个 test_*.py 文件，其中 12 个直接位于 integration、14 个直接位于 llm_integration。标记决定实际执行条件，同一模块可以同时包含默认测试和真实 R 测试。

| 类别 | 现有测试或入口 | 运行条件 |
|---|---|---|
| Unit / UI | 输入检查、模型、注册表、错误处理、Streamlit AppTest、离线 LLM fixture | Fast 默认 pytest |
| API contract | test_api_contract_freeze、test_api_execution、test_api_error_mapping、test_api_security | Fast，进程内 TestClient |
| Workflow | test_workflow_validator、test_workflow_executor、工作流历史与定制 | Fast 覆盖本地校验和调度；真实计算由 Scientific 独立验证 |
| ResultManager / Resolver | test_result_manager、test_result_reuse、test_deg_reuse_guard、test_index_contention | Fast，含同名替换、历史显式复用与父结果 |
| Security | API 校验、路径和项目边界、secret redaction、ResultRef、workspace 锁、isolated worker allowlist | Fast；Docker 层另查实际容器配置 |
| Real R | 现有 --run-r 测试，以及 container/smoke_runtime.py 和 runtime_checks.py | Scientific 使用公开输入；部分历史测试需要私有数据，保留原 opt-in |
| Real DeepSeek | llm 标记、--run-llm | 本阶段 NOT RUN，无付费 API 调用 |
| Docker integration | compose_checks、check_deg_guard_containers、result_snapshot | Docker 独立门禁，需实际 Docker Engine |

## 2–6. Architecture, Fast CI, Scientific CI, Docker CI and Actions

| Job / Workflow | 触发 / 资源 | 强制门禁 |
|---|---|---|
| Fast CI · ci.yml | 每次 push、pull_request；windows-2025，35 min | 新 venv、Windows 精确 wheel 和 build backend、actionlint、发布候选扫描、wheel 元数据及安装导入、全套默认 pytest |
| Scientific · scientific-validation.yml | 手动、默认分支、专用 Linux/amd64 runner，150 min | 从本次源码构建、API 健康、六个真实 Skill、指纹和实际已安装版本、公开数据逐字段对照 |
| Docker · docker-integration.yml | 手动、默认分支、同类专用 runner，150 min | 构建、双服务健康、共享卷与权限、跨容器同步并发复用、重建后校验索引与文件哈希 |

本地和 Actions 使用同一入口 `python scripts/ci/run.py fast|scientific|docker`。两个重型层可以单独运行，各自创建唯一的项目、镜像标签、命名卷、回环端口。R 和 Python 动态安装仅发生在受控构建或临时开发环境。应用运行继续沿用已验证的双服务架构。

三个 workflow 均使用 contents: read，checkout 关闭凭据持久化。checkout v7.0.1、setup-python v7.1.0、upload-artifact v7.0.2 根据官方 release 核对并固定到完整提交 SHA。分支保护和专用 runner 的注册由仓库管理员管理。[checkout](https://github.com/actions/checkout/releases/tag/v7.0.1) · [setup-python](https://github.com/actions/setup-python/releases/tag/v7.1.0) · [upload-artifact](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)

## 7. Dependency Lock Verification

Fast 按 SHA-256 安装 wheel，执行 pip check，核对 requirements 与 pyproject、当前 Windows 已安装闭包与开发锁、构建后端版本、Linux Python/R 锁与参考环境指纹。现有锁测试继续检查直接依赖范围、仓库分类及归档哈希，新上线的静态测试覆盖 Dockerfile 的锁使用。打包检查在临时目录安装本次构建的 wheel，并检查发布元数据及从安装目录加载的 Registry。

Scientific 从当前 Dockerfile 构建，复用 10.2 verify_runtime 检查实际安装的完整 Python/R 清单，再与参考环境比对。基础环境为 Python 3.13.2、R 4.6.1、Bioconductor 3.23、DESeq2 1.52.0、clusterProfiler 4.20.2、ggplot2 4.0.3。CI 不改写语言锁。本阶段的 Docker 构建允许使用已核验的依赖缓存；干净语言恢复的独立证据见 [Phase 10.2 报告](PHASE10_DEPENDENCY_LOCKING_REPORT.md)。

## 8. Scientific Regression Baseline

冻结的参考来自本机已验收的 Phase 10.2 镜像 `sha256:7c529fa626c7d71cc4ac447994a6231446d0ce6465fcdf7aa560e1c8298f70cc`。在无外网的独立参考容器上，用仓库公开的 30 基因、6 样本 counts/metadata，和两个小型合成映射运行全部 Skill。所有关联均来自公开测试基因编号。独立读取 Phase 9 的公开 demo，确认 comparison、每个 DEG 的 LFC/pvalue/padj、全部样本 PCA 坐标和解释方差一致。富集的 A/B 合成条目专为检验正例和有效空结果加入。私有科研表和旧科研 Workspace 均未复制进参考文件。

| 比较 | 公开基线 |
|---|---|
| DEG | treatment 相对 control，各 3 个重复；上调 9、下调 9。逐 gene ID 对齐 30 行并保留概率及方向。 |
| PCA | 6 个样本；PC1 98.4012283956765%，PC2 1.11594639943625%。两个轴各自允许整体符号翻转。 |
| Volcano / Heatmap | 真实 PNG、Summary 和输出可预览，Heatmap 使用 18 个基因。测试保留正确父 DEG，校验定制前后原 DEG 哈希。 |
| GO / KEGG | 每个 Skill 的 all、up、down 均在 30 基因背景上实跑。各子集分别检测 3 个正重叠条目，显著条目数为 0 / 0 / 1。按 ID 对照 overlap、pvalue、padj，核对输入子集、有效 universe、TERM2NAME，并用 R stats::p.adjust 独立核查 BH。 |

版本化参考包含四个输入的 SHA-256 和安装清单指纹。数值容差 rtol=1e-8；pvalue/padj 的 atol=0，其他浮点数 atol=1e-10。PNG 检查内容格式、读取和来源。基线修改必须记录原因、受影响 Skill、依赖版本、数值差异、方向及背景、审核依据；CI 没有自动更新入口。[更新流程](scripts/ci/README.md)

## 9. Security Gates

检查 Git 已跟踪文件、非忽略的新文件，以及工作区内容与 Git index 不同时的暂存内容。扫描文件角色、路径、大小、常见凭据形式；已知的五个合成 redaction 测试令牌只在指定测试文件内按完整值允许。另查 Docker 构建上下文及 wheel 成员。真实 Workspace、分析产物、日志、密钥和未审核数据表不得作为公开 CI 输入。Docker 测试只传四个已知测试数据文件，另传验证代码和参考值。

安全回归覆盖 API、路径、跨项目隔离、ResultRef、Worker 固定任务、写锁与过期 DEG。运行时检查包括 UID/GID、cap_drop、privileged、卷类型与挂载位置、localhost 端口和 healthcheck。用户的 Docker 网络和科研部署保持独立。

## 10–12. Local Validation, Remote CI and Failure Policy

- **PASS** 表示实际运行成功。**FAIL** 表示检查或数值断言失败并返回非零。**SKIPPED** 需要显式 opt-in 或明确的 Windows 符号链接权限原因。必需套件缺失、意外 skip、测试进程异常退出或缺少科学 fixture 均阻断验收。
- **BLOCKED** 表示解释器、平台或必需的 Docker Engine 不可用，入口返回非零。**NOT RUN** 表示未触发。GitHub-hosted execution 为 **NOT RUN**。未进行 Git commit、push、release 或远端 dispatch。
- 自动上传范围只有 `outputs/ci/<layer>/summary.json`。该文件保存 job、trigger、Python、命令、耗时、阶段结果、pytest 计数和简要错误。原始 pytest XML 仅用于本地解析，安装及构建日志保存在忽略目录。GitHub checkout 或 setup 先于入口失败时，由 Actions 原生 step 状态记录。

## 13–14. Limitations and Changed Files

| 文件 | 用途 |
|---|---|
| .github/workflows/ci.yml、scientific-validation.yml、docker-integration.yml | 三个独立 GitHub Actions 入口 |
| .github/actionlint.yaml | 声明受控 runner 标签 |
| scripts/ci/run.py、gate.py | 创建干净环境、运行门禁、检查 skip 和汇总结果 |
| scripts/ci/security.py、lint_workflows.py | 发布候选与 wheel 检查、固定工具摘要的 YAML 静态验证 |
| scripts/ci/docker_gates.py、scientific.py | 独立 Compose 生命周期、复用探针、数值对照 |
| .dockerignore、container/context_audit.py | 将构建工具生成的 egg-info 从 Docker 上下文排除，并使上下文审计采用相同规则 |
| tests/fixtures/ci_science/{go.tsv,kegg.tsv,reference.json} | 公开输入映射与经验证的参考 |
| tests/test_ci_gates.py、.gitattributes | 敏感文件等负向测试、Docker 锁引用检查、固定测试输入换行 |
| README.md、scripts/ci/README.md、本报告 | 本地调用、人工基线审核、验收证据 |

公开小数据能检测本次定义的统计和工程回归。大数据性能、其他物种与其他平台的完全等价仍需相应数据和独立验收。密钥扫描是一项基本防护，不代替人工数据分类和仓库托管方的 Secret Scanning。


## 15. 实测记录与负向对照

验收使用干净 venv，以及由 Git 可提交源码组成的独立 checkout。该 checkout 不含 .env、私有研究输入或工作区。Fast 进程的 PATH 已确认找不到 Rscript、docker。科学与部署作业从相同源码构建，复用已有 Docker 层。真实 GitHub runner 栈、远端排队和 artifact 上传仍需首轮远端运行验证。

| 检查 | 实测结果 |
|---|---|
| actionlint | 1.7.12 Windows amd64，以官方归档 SHA-256 及解压后程序 SHA-256 核对。三个 workflow 的语法、表达式和 runner 标签检查退出 0。Windows 门禁不包含 shellcheck/pyflakes；实际执行命令由跨平台 Python 入口实现。 |
| API contract、安全、Workflow Validator、ResultResolver、输入版本及历史显式复用、Worker、锁与私有文件测试 | 专项 221 passed、0 failed、0 skipped，167.96 秒，两条已有依赖弃用警告。该数目与默认套件有重叠。 |
| 构建上下文回归 | 发现 setuptools 在 src 下产生的 egg-info 进入原 Docker allowlist；已同步更新 .dockerignore、container/context_audit.py 和既有打包检查。针对打包约定和门禁的检查 16 passed。 |
| 敏感文件 | Git 发布候选、Docker context、实际 wheel 均检查。开发时修复了多行正则把示例中的空变量和值在下一行的另一项配置拼接的误报。允许的 synthetic 测试值有路径与完整值双重限制。 |
| 必需依赖缺失 | 在测试进程中移除 Docker CLI 后，Docker 门禁报告 BLOCKED，退出 1，实际未运行任何容器检查。 |
| 测试故意失败 | 在临时测试目录执行一个明确失败的 pytest 断言，门禁记录 1 failed，退出 1。独立科学负向对照只改变临时期望值，将上调数量从 9 改为 10，真实数值验证以 upregulated_genes 断言和退出 1 拒绝。冻结的参考和产物未改动。 |
| 服务就绪 | 首次独立参考采集在 API 尚未监听时得到 ConnectError。等待服务就绪后重新采集；正式科学与部署入口均使用 Compose --wait 并核验健康状态。 |

部署层复用现有同步探针：一次真实 toy DEG，API 和 Streamlit 容器后端同时请求新 Volcano，要求两个不同子 run、同一 parent、有效输入快照、可读 PNG 和三个完整索引条目。下线重建后两容器的结果快照必须与重建前一致。此用例验证两个入口共享的后端服务；本阶段没有自动化浏览器点击或真实 Chat API 验收。

## 16. Remaining P0 / P1 / P2

- P0/P1：本地质量门禁范围内无已知未关闭项。远端状态单列。
- P2：两项手动重型工作流需要管理员配置受限、一次性的 Linux runner。首次远端执行时间、托管网络和 artifact 上传权限有待验证。符号链接的 Windows 权限用例按平台条件跳过，junction 回归仍保留。Phase 10.2 已记录的 Debian 源和上游归档可用性限制继续适用。

最终验收的可发布源码快照位于本机忽略目录 `outputs/phase10_3/accepted-src`。三个作业通过与 workflow 相同的 `scripts/ci/run.py` 启动。Docker 与 Scientific 各使用自己的卷和镜像标签，结束时已清理。两次并发构建可共享 BuildKit 缓存。阶段运行耗时以下游 gate 计时为准，额外的 venv 初始化和安装时间由调用方承担。

- Scientific：5 项必需检查全部通过，0 失败，0 跳过，253.28 秒。六种真实 Skill 加基线与 lineage 校验用时 106.96 秒。退出 0。
- Docker：9 项必需检查全部通过，0 失败，0 跳过，172.25 秒。两容器并发请求及登记核验用时 13.23 秒。退出 0。
- 证据：快照下的 `outputs/ci/scientific/summary.json`、`outputs/ci/docker/summary.json` 和 `outputs/ci/fast/summary.json` 是安全汇总。独立的负向及专项证据保存在本机 `outputs/phase10_3/`。没有远端运行编号。
- Fast 在全新 Python 3.13.2 环境通过全部 5 项门禁，耗时 556.54 秒。默认 pytest 实际用时 537.16 秒，1636 passed、0 failed、126 skipped、2 warnings。必需套件与跳过原因检查通过，进程退出 0。

## 17. Phase 10.3 Verdict

**LOCAL IMPLEMENTATION PASS / REMOTE CI PENDING**

本地三层门禁均有实际执行证据，科学数值偏离与测试断言失败均已验证会阻止通过。GitHub-hosted execution 与 GitHub 自托管作业均为 **NOT RUN**。本地已具备进入 Phase 10.4 的条件。首次远端运行及对应的 run ID 留待发布阶段核实。
