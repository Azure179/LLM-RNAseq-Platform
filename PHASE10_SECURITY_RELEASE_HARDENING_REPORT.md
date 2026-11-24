# Phase 10.4 · Local release hardening

验收范围是 Windows Docker Desktop 上由同一可信用户管理的本地工作区。证据日期为 2026-11-19。科研输入使用公开 fixture，Worker 复验使用先前隔离测试项目的独立副本。

## 1. Configuration audit

| 入口 | 实际配置来源与优先级 |
|---|---|
| API 与本地服务 | `bulk_rnaseq.config.load_runtime_config` 读取进程变量，然后使用内置默认。API 工厂的显式 workspace/Rscript 参数优先。默认根目录为当前目录下的 workspace，日志 INFO，服务配置端口 8000/8501。 |
| CLI | `--config` 显式覆盖默认。源码 main.py 指向仓库 config/default.toml；安装后 console script 使用包内 default.toml。该 TOML 配置启动名称、环境标签和日志级别。直接运行分析子命令时由 `--rscript` 指定解释器，默认从 PATH 找 Rscript。 |
| DeepSeek | 指定的 dotenv 文件或当前目录 .env，再以进程同名变量覆盖，包括显式空值。服务设置不通过 Python dotenv 自动导出。仅启用 LLM 时加载密钥。 |
| Compose | Compose 负责 .env 插值；宿主发布端口由 RNASEQ_API_PUBLISHED_PORT、RNASEQ_UI_PUBLISHED_PORT 控制，两个服务的 /workspace 指向同一卷。容器内部监听参数由 command 明确指定。 |
| 项目数据 | project.json 记录绑定和普通项目元数据；ProjectContext 注入文件，系统管理 run_id、输出位置和解析路径。业务参数来自各 Skill schema。 |
| CI | workflows 调用 scripts/ci/run.py，清理应用环境变量，在临时环境使用锁文件。重型 CI 使用空环境文件和独立卷。 |

## 2. Configuration validation

端口范围 1–65535，空配置、控制字符、无效日志级别由 config.py 拒绝。ProjectManager 将根目录创建错误映射为 PROJECT_IO_ERROR。API /ready 实际探测 Rscript 和 Workspace 的创建、写入及同步，失败返回 503 和布尔检查。/health 仅表示进程存活。项目结构校验与损坏项目读取分别见 workspace/manager.py 和 workspace/models.py。宿主 bind 覆盖的检查要求关闭 create_host_path，命名卷通过运行时挂载检查确认。离线 Skills 可在空密钥的科学门禁中独立执行。LLM 缺 key 返回 LLM_KEY_MISSING。

## 3. Secret management

对当前发布候选、暂存差异、Docker allowlist 和 wheel 执行 scripts/ci/security.py 检查。可达 Git 历史的两个提交、257 个去重的路径/blob 组合按同一文件名和内容规则检查，零命中。Git 中只有 .env.example 作为 dotenv 示例。对 537 个本地 Workspace 元数据、日志及测试诊断文本进行当前配置密钥的精确字节比对，零命中。原始文件和密钥未进入报告。扫描覆盖已知格式与当前密钥，不构成对未知凭据的穷尽证明。

DeepSeek 仅接受官方 HTTPS 域名，拒绝重定向和环境代理。客户端校验服务证书、主机名并排除受影响的 CA，忽略 TLS 会话密钥日志环境变量。与 api.deepseek.com 的无凭据握手成功协商 TLS 1.3。本次实际 LLM 请求数为零。容器运行时环境中的凭据对有 Docker 管理权限的操作者可见，分享诊断时勿直接导出 compose config 或 docker inspect。

## 4. FastAPI

api/main.py、api/security.py、api/errors.py 实现类型校验、统一错误、日志及请求 ID。默认 debug 关闭，未安装宽松 CORS 中间件。本地 /docs、/redoc、/openapi.json 为预期接口。已在独立容器复现原始 Uvicorn 日志带出查询参数，修复为禁用 uvicorn.access，保留应用日志中的 method、路由模板、状态、时长与 request_id。真实探针同时验证日志中存在请求 ID、查询标记没有出现。信任边界为操作系统本地用户和 Docker 操作者。

## 5. Streamlit

项目切换与下载使用项目作用域，上传交给 FileImportService 校验，结果通过 ResultManager 重新解析并验证。浏览器展示限制表格行数和图片尺寸。图片解码入口仅启用 PNG/JPEG；UI 顶层异常由统一错误展示器记录异常类型，避免重复记录包含原始异常消息的 traceback。容器现显式设置 STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none，与本机 .streamlit/config.toml 的错误详情策略一致。既有 XSRF/CORS 保护保持默认。UI 功能由现有 Streamlit/Workspace 测试覆盖，浏览器人工全流程和真实 Chat 本轮 NOT RUN。

## 6. Workspace / result safety

专项用例覆盖输入替换、样本/文件身份变化、自动与显式历史复用、跨项目与路径隔离。Docker 探针对同一 DEG 同步发起两次 Volcano，检查两个不同子结果、相同父记录和原子索引，重建后校验相同的文件与 lineage。失败的索引、历史来源或文件绑定继续走原有结构化错误路径。

## 7. Worker

host-only launcher 的严格 schema 只接受固定任务和资源 profile。输入只读，输出只能是规定格式和大小的 receipt。实例无网络、无 capabilities、无 Docker Socket、无 DeepSeek Key，使用非 root、只读根、32 PID、128 MiB 与 0.5 CPU。真实 validate_runtime 在测试副本执行七次，2 次成功、3 次预期失败、1 次超时、1 次取消，均完成清理，未登记为分析结果。输出 bind 的总容量尚无硬配额。该设计仅适用于可信任务。

## 8. Docker deployment

Compose 固定回环地址发布端口，两个服务以 10001:10001 运行，使用 cap_drop=ALL、no-new-privileges、unless-stopped 和持久卷。主应用没有特权和 Docker Socket。构建目录使用 deny-by-default allowlist；下载的 R 源码、R 包及 Python wheels 经哈希验证。独立测试栈在重建验证后清理，常用项目及卷保留。

## 9. Dependency and supply chain

对 Windows、Linux 和构建锁的 72 个不同 Python 包/版本查询 PyPI release-specific 公告，全部得到响应；4 个组件有告警，公告 ID 包含重复别名。OSV 批量比对 110 个 CRAN 包/版本，无匹配。其余 55 项 R 锁记录不计入 CRAN 漏洞查询覆盖范围，完整 165 项的版本/来源按原 R 门禁校验。以下结论限于查询当天与已审计的使用路径。[PyPI 版本接口](https://docs.pypi.org/api/json/) · [OSV 查询协议](https://google.github.io/osv.dev/post-v1-querybatch/)

| 组件 | 当前可达性、采取的措施和发布后续 |
|---|---|
| certifi 2026.7.22 | 确认锁中含受影响的 emSign ECC Root CA - G3。滥用需要受损中间密钥及网络中间人条件。项目会向外部 LLM 发送凭据，已在实际 HTTPX 客户端信任库中按 DER SHA-256 排除该根。其他使用此锁的工具仍应评审迁移至公告建议的 2026.10.19 或更新版本，配套重跑锁与科学验证。[公告](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) |
| Pillow 12.3.0 | 58 条公告记录含别名，涉及 TIFF、PDF、PNG、JPEG 等不同路径。应用预览受限于项目已有图像，限制类型、尺寸，未提供任意图片上传解码服务。PNG/JPEG 本身也有公告；来源不明的工作区/图像不属于可信输入。评审支持的修复版本及 Streamlit 兼容性，再验证绘图基线。[PNG 公告](https://github.com/advisories/GHSA-g4qv-2jxx-769h) |
| opentelemetry-api 1.45.1 | traceparent 解析存在拒绝服务告警。项目无公网部署，未发现应用显式注册该 propagator；传递依赖的全部间接路径未经动态穷举。建议评审 1.45.2 或更新版本。[公告](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg) |
| setuptools 80.9.0 | 涉及特定文件系统上的 sdist 排除规则及 Windows 旧式 wheel 安装路径。实际部署使用审查过的源码、固定构建后端和哈希限定的 wheels，经 pip 安装。评审 84.1.0 或更新版本并重验源包与 wheel。[sdist 公告](https://github.com/advisories/GHSA-h35f-9h28-mq5c) · [Windows 公告](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) |

应用基础镜像按 SHA-256 固定，Actions 按完整提交 SHA 固定；CI 下载的 actionlint 二进制有摘要校验。Worker 的历史构建使用本地 base tag，可信 launcher 执行时要求不可变镜像 ID。Debian apt 快照、全量 OS 层和 Bioconductor 公告扫描仍有覆盖缺口。供应链审核与已运行的科学回归都不等价于全栈无漏洞认证。

## 10. Python package

修复 console script 在无源码目录时无法启动的问题。wheel 门禁离线构建、核对名称/版本/依赖、检查成员及凭据，在隔离路径安装后实际启动 CLI、加载 Registry 并逐项检查源码中的 R 脚本。Manifest 与 schema 由 Registry 加载。Streamlit UI 随源码/Compose 分发。wheel 实测含 144 个成员、10 个 R 脚本与 bulk-rnaseq 控制台入口。单独使用锁定的 setuptools 80.9.0 构建 sdist 并扫描 205 个常规文件。未发布包。

## 11. Release readiness checklist

| 检查 | 验收依据 |
|---|---|
| Dependencies locked、scientific regression | Fast 锁检查、Scientific 环境及统计基线 |
| API contracts、Streamlit 功能、Input-Version Guard | 默认与专项 pytest |
| Compose healthy、result lineage、共享卷、并发 | Docker 门禁及重建验证 |
| Secret scan、package build、security regression | 发布候选与制品扫描、隔离安装、API 日志探针、Worker harness |
| CI 配置与开发文档 | actionlint、本报告与本地运行指南 |
| 远端 GitHub Actions | NOT RUN，留待 Phase 10.6 |
| 正式版本、tag、Release、上传制品 | PENDING，独立发布决策 |

## 12. Tests and evidence

在只包含发布候选文件的独立源码副本运行 `py -3.13 scripts/ci/run.py fast`、`scientific`、`docker`。没有 .env、科研数据及旧 Workspace。第一次 Fast bootstrap 因 PyPI 对 tzdata 的索引响应类型异常而退出，保留失败记录，随后使用相同哈希锁重试。证据目录为本地忽略的 `outputs/phase10_4/`。最终三个作业的汇总在 `accepted/outputs/ci/<job>/summary.json`。默认跳过项包含显式付费与真实 R 用例，独立真实科学门禁另计。Worker runtime_report 在 worker-stage 下，专项运行见 focused.log。

各层单独计数，专项测试与默认测试有重叠。

| 作业 | 实测结果 | 时间 |
|---|---|---|
| Fast CI | PASS，5/5 项。默认 pytest 1643 passed、0 failed、126 skipped、2 warnings。必需套件与跳过原因均通过验证。 | 门禁 518.35 秒，pytest 499.21 秒 |
| Scientific CI | PASS，5/5 项，六个真实 Skills 与已冻结的数值和 lineage 基线一致。 | 244.34 秒 |
| Docker CI | PASS，10/10 项，含原始 URL 探针、共享卷、同步写入以及重建。 | 169.47 秒 |
| 专项回归 | 363 passed、2 warnings；另运行最终新增测试文件，7 passed。 | 159.01 秒、5.59 秒 |
| Worker | PASS，七种执行记录均达到预期状态，无遗留测试容器。 | 单独记录在 runtime_report.json |

## 13. Changed files

| 文件 | 用途 |
|---|---|
| src/bulk_rnaseq/default.toml、pyproject.toml、src/bulk_rnaseq/cli.py | 安装包的本地默认值、随 wheel 分发、CLI 默认选择及配置错误脱敏 |
| compose.yaml、app/main.py | 容器 UI 隐藏内部错误详情，删除顶层重复 traceback 日志 |
| src/bulk_rnaseq/api/security.py、scripts/ci/docker_gates.py | 关闭重复原始 URL 日志并用真实请求验证安全访问日志 |
| src/bulk_rnaseq/llm/tls.py、llm/client.py | DeepSeek 出站证书信任与配置失败处理 |
| src/bulk_rnaseq/workspace/result_preview.py | 限制 Pillow 解码器 |
| scripts/ci/gate.py、tests/test_release_hardening.py | 离线安装包资源与运行检查、七个新增 pytest 回归 |
| scripts/ci/README.md、本报告 | 开发与本地部署配置边界、发布检查与实测证据 |

## 14. P0 / P1 / P2

原始查询日志泄漏及 DeepSeek 使用旧 CA 信任库的路径均已修复。P2 跟进包括审查依赖补丁、补充 OS/Bioconductor 公告覆盖，并在授权发布时执行远端门禁。UI 图片只接受可信项目的生成物。用户数据备份和主机权限由本地操作者负责。

## 15. Deployment limitations

开发时可使用测试 fixture 与调试日志。建议本地部署使用 INFO、回环发布、独立持久卷和运行时凭据。公网多用户仍缺正式认证授权、租户隔离、HTTPS/反向代理加固和运维机制。动态不可信代码执行需独立安全设计与审查。Docker 管理权限属于主机信任边界。

## 16. Phase 10.4 verdict

**Phase 10.4：PASS（本地可信用户部署）。** 本范围内未发现尚待修复的 P0/P1。已具备进入 Phase 10.5 的本地条件。GitHub-hosted 与自托管远端 CI 均为 NOT RUN，正式发布仍为 PENDING。
