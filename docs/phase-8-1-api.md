# Phase 8.1 — FastAPI Skeleton + Metadata API

日期：2026-10-13。范围仅限 HTTP 只读入口；不执行分析、不调用 DeepSeek、不读取用户数据。前一阶段未提交的热修复改动保持原样，本阶段不修改 Skill / Workflow / ResultResolver / Streamlit 业务代码。

## 设计

```text
FastAPI → metadata router → get_skill_registry → 原 SkillRegistry / Manifest
```

服务使用 `lifespan` 在每个进程启动时创建一次 `create_default_registry()`，放入应用状态。各请求通过同一依赖读取 Registry，不各自初始化，不使用另一套 Skill 清单。此生命周期方式遵循 [FastAPI 官方文档](https://fastapi.tiangolo.com/advanced/events/)。

当前真实 Registry 为 6 个可执行 Skill 与不可执行的 GSEA 描述。`/skills` 返回全部注册描述，同时保留 `availability`；GSEA 的输入/输出 Schema 是 `false`。注释过滤目前属于受控定制模块，不在此 Registry，API 不单独编造一个 filtering Skill。

API 的 `version` 直接使用 `bulk_rnaseq.__version__`（当前 0.7.0），开发阶段编号不是发行版本。没有修改已发布 tag 或 release。

## 文件

| 文件 | 用途 |
|---|---|
| `src/bulk_rnaseq/api/__init__.py` | 独立 API 包，不与 Streamlit 的 app/ 冲突 |
| `src/bulk_rnaseq/api/main.py` | app factory、lifespan、错误转换、请求日志、router 注册 |
| `src/bulk_rnaseq/api/dependencies.py` | 获取当前应用唯一 Registry |
| `src/bulk_rnaseq/api/routes.py` | health / skills list / skill detail 三个只读路由 |
| `src/bulk_rnaseq/api/schemas.py` | Pydantic HTTP 响应薄层，不重复定义 Skill 参数 |
| `tests/test_api.py` | API、保密、只读边界与网络保护回归 |
| `tests/conftest.py` | Windows asyncio 内部 socketpair 的窄范围兼容；普通网络仍阻断 |
| `requirements.txt`, `pyproject.toml` | FastAPI / Uvicorn 的一致依赖声明 |
| `README.md` | 启动方式、HTTP 边界，并纠正过期的修复前状态描述 |
| `docs/phase-8-1-api.md` | 本阶段设计、验证和限制 |

安装版本：FastAPI 0.143.0、Uvicorn 0.53.0；没有降级 Streamlit、Pydantic 或现有 HTTP 客户端。`pip check` 无依赖冲突。

## HTTP 契约

| Method / path | 作用 |
|---|---|
| `GET /health` | 进程存活；status/service/version；不代表 R/DeepSeek/数据就绪 |
| `GET /skills` | `skills` 数组；name/version/description/category/availability/dependencies |
| `GET /skills/{skill_name}` | 上述字段与 input_schema/output_schema/limitations/use_when/do_not_use_when/raw_count_requirement |
| `GET /openapi.json` | 自动生成的服务接口文档 |
| `GET /docs` | Swagger UI HTML |

详情额外提供 `schema_documents`：以原相对引用名为键读取包内 `input-types.schema.json` 与 `skill-result.schema.json`。Skill input/output Schema 原样来自 Manifest，消费者可用返回的文档解析引用，不必访问服务器文件或远程地址。它们是参数定义，不是本机运行配置或真实输入/输出实例。

只投影公开字段，不返回 executor、entry_point、project_root、实际 Rscript 路径、环境变量或任何项目实例。没有读取 `.env` 或 workspace。`output_dir` 等在 Schema 中作为参数名称出现，不包含实际本机路径，也不代表存在可执行 API。

错误统一为 `{"error":{"code":"...","message":"..."}}`：

- 未知 Skill：404 / `SKILL_NOT_FOUND`。
- 未知 URL：404 / `NOT_FOUND`。
- 不支持的方法：405 / `METHOD_NOT_ALLOWED`。
- 请求校验失败：422 / `INVALID_REQUEST`。
- 内部错误：500 / `INTERNAL_ERROR`；不回传异常正文、详情或 traceback。

日志复用已有 `create_logger`，记录 method、路由模板、status、服务端生成的 request ID；响应头带 `X-Request-ID`。不记录用户路径参数、query、headers、body 或大表。建议启动时关闭 Uvicorn 通用 access log，避免它另外记录完整 URL。无 CORS 中间件，不默认允许任意跨域来源。

## 启动与测试

项目根目录，PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn bulk_rnaseq.api.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

使用 [FastAPI TestClient](https://fastapi.tiangolo.com/tutorial/testing/) 执行进程内测试；不需要运行服务器、真实矩阵、R 或 API key。测试显式阻断 Registry.execute/get_executor、DeepSeek 请求、R 入口、子进程及 dotenv 加载。

### 发现与修复的测试问题

1. Windows 标准库通过本机回环实现 socketpair，asyncio 的内部唤醒管道因此被原“禁止所有 connect”测试保护误拦。现在仅在标准库 socketpair 操作的 ContextVar 范围内允许其回环连接；普通 loopback、外网 connect/connect_ex 及 create_connection 仍被阻断。永久测试验证例外结束后仍无法直接联网。
2. API logger 不向 root 传播；旧测试在 handler 创建之后捕获 stderr，读不到该 handler 的日志。改为显式挂载测试日志 handler 并在 finally 中移除，不修改应用日志行为。

### 实际 HTTP 检查

Uvicorn 在 127.0.0.1:8000 完成启动。真实 HTTP 请求确认：health 200、skills 200、DEG detail 200、unknown Skill 404/SKILL_NOT_FOUND、OpenAPI 200、docs 200 且含 SwaggerUIBundle。OpenAPI 只有三个业务 GET 路由，没有 POST 分析端点。Streamlit 的 8501 健康接口仍为 200/ok；没有重启或改变其业务状态。

## 验收结果

**Phase 8.1 Verdict：PASS。停止，不进入 Phase 8.2。**

- 新增 API 测试：15 passed，1.43 秒。
- 默认完整回归：1219 passed、100 skipped、2 warnings，154.77 秒。包含现有 Streamlit AppTest / Chat / Workflow / ResultResolver 回归；没有启用真实 API 或 R 测试。
- 100 个跳过为现有显式 LLM/R 开关及 Windows symlink 权限限制，未通过跳过新增 API 测试来获得 PASS。
- 依赖完整性检查通过；实际 Uvicorn / HTTP / OpenAPI 检查通过。
- 没有新增业务清单、分析执行、统计变更或复制 Manifest 参数定义；公开元数据来自已有 Registry。
- 保密响应、404、500、请求日志、网络拦截、不可执行 GSEA 标识和禁止 POST 执行均有自动测试。

## 当前限制

- 本机开发只读服务，不支持上传、Skill 执行、Workflow、聊天或定制 HTTP 调用。
- 不添加后台任务、重试、认证、数据库、Docker 或云部署。
- `/docs` 使用 FastAPI 默认 CDN 静态资源；已验证服务 HTML 与 OpenAPI 返回正常，不保证完全离线的浏览器能加载 Swagger 样式和脚本。
- TestClient 当前给出两条上游弃用提示（httpx 后续迁移与 anyio BlockingPortal alias），不影响通过；未为消除提示引入第二套 HTTP 客户端或改动现有 DeepSeek client。
- 未提交或推送 GitHub；修复前公开 Preview 保持不变。
