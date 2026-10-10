# Phase 6.0：DeepSeek API Client

范围只有 Python → 真实 DeepSeek Chat API → Response。
没有意图识别、Structured Intent、Tool Schema、Tool Calling、Skill 调度或生物学结果总结。
DeepSeek 是后续真实 LLM 决策层；mock 返回只用于测试，不得当作 DeepSeek 的实际输出。

## 本阶段设计

- `httpx`：直接发送兼容 Chat Completions 格式的 HTTPS POST，不依赖 OpenAI SDK或代理框架。
- `python-dotenv`：明确读取选择的 `.env` 文件，不向上搜索其他项目、不修改全局环境、不插值变量。
- 配置优先级：进程环境变量 > 指定 `.env` > 默认值。显式空环境值也覆盖文件，空 Key 会失败。
- 默认 endpoint 为 `https://api.deepseek.com/chat/completions`，只允许官方 HTTPS 主机，base URL 可为根路径或 `/v1`。
- 禁止重定向；TLS 验证开启；不自动读取系统 HTTP_PROXY/HTTPS_PROXY（`trust_env=False`），不支持自定义代理/网关。
- 单次非流式请求，自动重试为 0，默认关闭 thinking，默认 max_tokens=64。连接测试没有 tools 或 response_format。
- 超时默认 30 秒，作用于 HTTP 各阶段/读写等待，并非整个请求的绝对总时限；可设置为 (0,120]。
- max_tokens 限制为 [1,1024]，用户提示最多 4000 字符。这是本阶段应用成本限制，不是服务商最大能力。
- 模型名通过配置传入。`.env.example` 使用核对官方文档时的 `deepseek-flash`，不承诺模型名永久不变。

参考：[DeepSeek 首次调用](https://api-docs.deepseek.com/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)、[HTTP 错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。
这些文档用于实现协议；文档可访问不代表你的 API 认证或连接已成功。

## 新增/修改文件

| 文件 | 用途 |
|---|---|
| `src/bulk_rnaseq/llm/__init__.py` | LLM 客户端包边界 |
| `src/bulk_rnaseq/llm/config.py` | 环境配置、Key/URL/模型/成本参数检查；配置 repr 隐藏 Key |
| `src/bulk_rnaseq/llm/client.py` | 单次请求、HTTP 错误映射、文本响应解析、真实 model/usage/latency |
| `src/bulk_rnaseq/llm/exceptions.py` | 结构化 DeepSeekError：error_code/message/details |
| `src/bulk_rnaseq/llm/check_connection.py` | 显式手动连接命令；一次短提示，写安全 JSON 记录 |
| `.env.example` | 可提交的空 Key 配置示例 |
| `.env` | 私有本机填写入口，初始 Key 为空；已被现有 .gitignore 忽略 |
| `tests/llm/test_client.py` | 免费、明确标注的 HTTP mock fixtures |
| `tests/integration/test_deepseek_connection.py` | 必须 --run-llm 才允许的真实连接测试 |
| `tests/conftest.py` | 新增 LLM 开关、报告目录、默认网络阻断；使用显式 marker 而非目录 keywords |
| `requirements.txt`、`pyproject.toml` | 声明 httpx/python-dotenv 运行时依赖与 llm 测试 marker |
| README、本文 | 配置入口、执行方式、验收状态 |

没有新增第二套 RNA-seq Skill 定义；Manifest、Registry、SkillResult 和统计代码均保持原样。
本阶段不调用 Registry；后续 6.2/6.3 才接入现有定义。`ChatResponse` 是 API 传输结果，不是新的分析 SkillResult。
DeepSeekError 复用已有项目的结构化错误字段约定，但独立于 SkillError：网络失败不能冒充 RNA-seq 分析失败。
报告写入复用现有 `run_io.write_json()` 的原子写入机制。

## API Key 填写入口

项目根目录 `.env` 已创建，请用编辑器在本机填写：

```dotenv
DEEPSEEK_API_KEY=在本机填写你自己的真实Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT_SECONDS=30
DEEPSEEK_MAX_TOKENS=64
```

上面 Key 是说明文字，不是可用凭据。不要把真实 Key 发到聊天、截图、源码或命令行参数中。
其他用户复制 `.env.example` 为自己的 `.env` 并填写，不能共享你的 Key。
也可由宿主安全注入同名环境变量。服务商 Key 与聊天订阅是否关联，应以服务商账户配置为准。

从项目根目录执行所有命令。若从其他位置启动，使用 `--env-file` 明确指定 `.env`。
`.gitignore` 已有 `.env`、`.env.*`、`!.env.example`，无需再改一遍；忽略规则不保护过去已经提交的秘密。

## 运行方式与费用边界

安装依赖（不请求 DeepSeek）：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

免费 mock 和默认测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/llm -q
.venv\Scripts\python.exe -m pytest -q
```

默认 pytest 跳过 llm marker；普通测试通过 autouse fixture 阻断 Python socket 连接，即便本机有真实 Key 也不允许它们联网。
httpx.MockTransport 在内存中返回明确的测试数据，不消耗 API Token。
该防线覆盖当前 Python 测试请求路径，不是操作系统沙箱，不能约束未来人为新增的联网子进程；本阶段没有此类子进程。

**下面两种都是付费请求入口，任选一种，不必连续执行两次：**

```powershell
# 最小连接检查：一次 API 请求，无重试；缺 Key 时请求前失败。
.venv\Scripts\python.exe -m bulk_rnaseq.llm.check_connection

# 真实集成测试：只有 --run-llm + llm marker 同时成立才放开网络。
.venv\Scripts\python.exe -m pytest tests/integration/test_deepseek_connection.py --run-llm -q --tb=short
```

`pytest --run-llm` 也可使用，但会同时运行普通测试；普通测试依然阻断网络。
本阶段只有一项真实 LLM 测试，不执行 R 或 Skill，不需要 `--run-r`。
即使 Key 缺失，显式真实测试也会失败并记录原因，不会偷偷跳过、自动生成回复或改用其他模型提供商。

连接文本固定为：`连接测试：请只回复 OK，不做任何分析。`
客户端保留实际文本，不把返回内容替换为预期答案。验收要求收到非空 assistant 文本且 finish_reason=stop；不以逐字匹配 OK 判断服务商格式是否正常。

## 返回与记录

`DeepSeekClient(load_deepseek_config()).chat("连接测试")` 返回 ChatResponse：

- response_id：API 实际 id。
- model：API 实际返回的模型名，可能与配置别名不同。
- content：API 实际文本。
- finish_reason：API 实际结束原因；length 等不会被连接测试记为完整通过。
- usage：API 实际 usage（保留扩展字段），未提供则为 null，不估算 token 或费用。
- latency_seconds：本地测量的 HTTP 请求耗时。

手动命令保存 `outputs/deepseek_connection/<UTC时间>/connection.json`；真实 pytest 保存 `outputs/deepseek_tests/<UTC时间>/connection.json`。
每次单独目录，不覆盖之前记录。报告含测试名、固定用户文本、请求/返回模型、状态、usage、耗时与安全错误信息。
未收到响应时 model/usage 为 null；缺 Key 时没有发送 HTTP。收到 HTTP 错误并不代表连接验收成功。
没有 selected_skill 或 extracted_parameters，因为 Phase 6.0 尚未实现这些功能，不伪造占位分析结果。

默认日志不记录请求正文、认证头、完整响应体或服务商错误正文；报告只保留连接测试的安全字段。
完整 Key 在配置 repr 中隐藏；禁止把 Key 放入 prompt；若 API 在待公开字段中回显当前 Key，拒绝响应，不落盘输出。
不要开启第三方 HTTP 库的敏感 wire/debug 日志或在开发时 dump 配置/请求对象；.env 仍是本地明文文件，应自行保护。

## 异常处理

| 错误码 | 含义 |
|---|---|
| LLM_KEY_MISSING / LLM_CONFIG_ERROR | 配置缺失/非法，未发请求 |
| LLM_INPUT_ERROR | 提示为空、过长或含凭据，未发请求 |
| LLM_AUTH_ERROR / LLM_FORBIDDEN | 401/403，凭据或访问权限问题 |
| LLM_INSUFFICIENT_BALANCE | 402，余额不足 |
| LLM_BAD_REQUEST / LLM_NOT_FOUND | 400/422/404，请检查配置/请求 |
| LLM_RATE_LIMIT | 429，无自动重试 |
| LLM_SERVICE_ERROR / LLM_HTTP_ERROR | 服务异常或其他 HTTP 状态；重定向不跟随 |
| LLM_TIMEOUT / LLM_NETWORK_ERROR | 超时或网络问题，无自动重试 |
| LLM_RESPONSE_INVALID | 响应 JSON/字段/usage 非法，工具响应或凭据回显 |
| LLM_INCOMPLETE_RESPONSE | 连接检查收到非 stop 的结束原因 |

手动命令捕获 DeepSeekError，使用 logging 输出简明信息，退出码 1；成功退出码 0。
Python API 调用方应捕获 DeepSeekError 并读取 `.to_dict()`，不直接展示底层 HTTP traceback。
超时不保证服务商没有处理请求或计费；手动重试前应考虑这一点。

## 验收状态

- 48 项免费客户端测试通过。
- 默认全量 pytest：407 passed / 19 skipped（18 项 R + 1 项真实 LLM），未发送 DeepSeek 请求。
- 首次显式测试因 **LLM_KEY_MISSING** 失败，未发送 HTTP；历史失败报告保留。
- 用户配置 Key 后，2026-09-16 08:39:29 UTC 显式真实测试 **1 passed**，收到 API 实际回复 `OK`，finish_reason=`stop`。
- 请求模型和返回模型均为 `deepseek-flash`；实际 usage：prompt_tokens=16、completion_tokens=1、total_tokens=17，缓存命中 0。
- 实测 HTTP 耗时 0.709012 秒；单次请求，无自动重试。未读取 RNA-seq 输入或执行 Skill。

验收已满足：本机 Key 可认证、单次真实 HTTPS 请求成功、非空 assistant 响应且正常结束、实际 model/usage 已记录，验收报告不包含 Key。
**Phase 6.0 验收通过。** 停在本阶段，不进入 6.1；本次成功不代表后续意图识别或 Tool Calling 已验证。
