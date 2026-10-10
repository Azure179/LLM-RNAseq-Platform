# Phase 6.5.3：Streamlit Base UI

2026-09-22：实现、离线测试、真实文件 AppTest 上传通过；**浏览器手工验收未完成，不能宣称全部验收通过。**
没有 Chat、DeepSeek 请求、Skill 执行、Workflow、Agent、分析结果解释或 Phase 6.5.4 实现。

## 启动与操作

在软件项目根目录运行（无需激活虚拟环境）：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/main.py
```

激活虚拟环境后，用户要求的 `streamlit run app/main.py` 同样可用。
新环境按原方式安装：`python -m pip install -r requirements-dev.txt`，再 `python -m pip install -e .`。

浏览器打开 `http://127.0.0.1:8501`，直接进入 Workspace，没有 Landing Page。
默认只监听本机，不是公网服务、也没有多用户认证。不要未经额外安全设计就对外暴露。

1. 左侧 **+ New Project**，填写名称和描述，点击 **Create**；自动切换到新项目。
2. 打开 **Files** 页签，展开对应文件角色，点击浏览文件或拖入文件。
3. 点击 **Import Count Matrix / Metadata / GO Mapping / KEGG Mapping**。仅选择文件不会自动保存。
4. 等待验证，查看顶部样本/分组/基因数，以及侧栏状态。JSON 摘要可展开查看，不由 UI 重算。
5. 已有同角色文件时，勾选 **Confirm Replace** 后才能点击 Replace；Cancel 取消当前选择。
6. Remove 需要勾选确认，调用后端解除绑定；不删除源文件和历史项目副本。
7. Download 分为 **Prepare download → Download**，只加载当前项目已验证的原始上传副本。count/metadata 的分析适配副本不冒充原始文件下载。
8. Projects 下拉框可以切换已有项目。**Refresh from disk** 重新读取磁盘；浏览器重开后重新选择项目即可恢复。

用户不需要在 PowerShell 中输入数据文件路径。PowerShell 只用来启动应用。

## 文件与目录

```text
app/
├── __init__.py
├── main.py
├── state.py
├── components/
│   ├── __init__.py
│   ├── common.py
│   ├── sidebar.py
│   ├── project_header.py
│   └── readiness.py
└── views/
    ├── __init__.py
    ├── assistant.py
    ├── results.py
    ├── files.py
    └── project.py
```

| 新增/修改 | 作用 |
|---|---|
| `app/main.py` 新增 | 启动配置、后端服务构造、四页签、异常边界；以 `pathlib` 定位仓库 |
| `app/state.py` 新增 | 唯一 WorkspaceState、初始化/切换/刷新/清空、统一 widget key 生成 |
| `app/components/common.py` 新增 | success/warning/error 展示；操作后统一 refresh + rerun |
| `app/components/sidebar.py` 新增 | ProjectManager 驱动的项目创建/列表/切换，以及文件和 readiness 状态 |
| `app/components/project_header.py` 新增 | 当前项目及 Samples / Groups / Genes / Imported inputs 指标 |
| `app/components/readiness.py` 新增 | 直接展示后端 ready/data_ready/missing/errors，不写另一套依赖规则 |
| `app/views/assistant.py` 新增 | 明确的聊天未启用占位，无可发送聊天输入 |
| `app/views/results.py` 新增 | 只读目录概览：文件数、summary.json 存在性、选中结果目录 |
| `app/views/files.py` 新增 | 原生 uploader、摘要、替换/取消/解绑、按需下载 |
| `app/views/project.py` 新增 | 名称/描述/时间/逻辑相对路径、显示名重命名；无项目删除按钮 |
| `app/` 下三个 `__init__.py` 新增 | 模块边界，不含业务实现 |
| `src/bulk_rnaseq/workspace/ui_adapter.py` 新增 | 非 Streamlit 依赖的上传临时文件适配、安全下载与只读目录列表、文件大小格式化 |
| `.streamlit/config.toml` 新增 | 中性浅色主题、300 MiB 上传上限、本机监听、关闭使用遥测、隐藏异常详情 |
| `tests/test_workspace_ui.py` 新增 | 普通 Python 测试及 Streamlit AppTest 测试 |
| `examples/workspace_ui_test.py` 新增 | 显式真实文件 AppTest 上传/会话恢复测试，不覆盖已有项目 |
| `requirements.txt` / `pyproject.toml` 修改 | Streamlit 1.64.x 依赖；pytest 加入 app 包的根路径 |
| `.gitignore` 修改 | 忽略未来可能出现的本机 `.streamlit/secrets.toml` |
| `README.md` / 本文 | 启动方式、状态和验收边界 |

ProjectManager、FileImportService、统计脚本和原有 validation **未修改**。旧单元测试与 toy/真实输入未删除或替换。

## 页面布局

左侧 Workspace：创建、项目列表、刷新、四类文件状态、六个 Skill readiness。
中间：紧凑项目标题、四个 metrics、原生 Assistant / Results / Files / Project tabs。
采用 Streamlit 原生容器、轻边框、浅灰中性色和 monospace 文件名，无营销 Hero、渐变或脆弱 DOM CSS。

Assistant 只显示说明及依赖。Results 只读当前项目 results/，不打开完整表格/图片，不把目录存在误判为分析成功。
Files 展示 imported summary、验证结果、warnings、大小与操作按钮。Project 不默认展示本机绝对路径。

## 本阶段新增工程概念

### UI 状态不是业务状态

`st.session_state["workspace_ui"]` 保存一个 WorkspaceState，而不是散落的业务键。
核心字段：current_project、current_project_context、project_readiness、file_status、selected_tab、selected_result。
另含暂时的 notice、widget_generation、按需下载缓冲。

`project.json` 和项目文件仍为事实来源。切换项目会清空旧下载、结果选择、通知和上传 widget 身份；不会把 A 项目的待上传文件误用到 B 项目。
导入/替换/解绑/重命名后统一刷新并 rerun。跨会话修改了 project.json 时，下一次 rerun 按 updated_at 自动刷新；外部只改文件内容时使用 Refresh from disk 重新做完整性检查。
关闭页面会丢失暂存 UI 状态，但不会丢失已导入文件。新会话不擅自选项目，用户重新打开即可恢复。

### Transport Adapter 不等于另一个导入系统

UploadedFile 的 `name` 和二进制流进入独立 adapter；核心业务层不接收 Streamlit 类型。
adapter 在项目 runtime 的随机临时目录分块写入文件，先拒绝路径分隔符/不安全名称、检查大小，然后调用原 `FileImportService.import_file(..., replace=...)`。
文件验证、复制到 input、版本保存、project.json 绑定仍完全归导入服务。临时上传在成功/失败后清理。

UI 不直接 open/write project.json，不实现 GO 正则、样本验证、Registry 或 R 调用。
下载由服务按当前 Project+FileRole 查找路径，不接受用户传入任意下载路径；核验绑定与 hash 后读取项目原件。
目录列表有 10,000 条目扫描上限，跳过符号链接和 junction，遇到截断明确提示。只遍历 Project/results。

### Readiness 与文件导入状态分开

顶部 Imported inputs=4/4 仅说明四种输入有效（可能有 warning），不是“所有分析可执行”。
DEG 无 treatment/control 显示 Needs parameters；Volcano/Heatmap 无 DEG result 显示 Incomplete；GO/KEGG 仍需 gene_list 和 universe/analysis_genes。
本阶段不增加比较参数表单、不猜测方向、不自动生成基因集或背景。
判断来源为原 Manifest-driven readiness，UI 只翻译展示状态。注册表中的 GSEA 仍未实现，不伪装为可执行。

### 用户错误与开发日志分开

预期错误显示简短 message，Technical details 展开显示 error_code 及允许展示的 row/sample mismatch 等字段。
不把本机绝对路径、OS 异常 repr、Python traceback 填进页面。未预期错误由顶层边界记录服务器日志，页面仅显示通用错误。
正常导入/替换/解绑仍记录项目 `logs/import.log`；创建/重命名走既有项目日志。
页面无 API Key 输入框，不读取 `.env` 或 DeepSeek Key。

## 测试与真实数据

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workspace_ui.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check

# 已存在同名项目会拒绝覆盖；复测请换新名称
.\.venv\Scripts\python.exe examples/workspace_ui_test.py --project-name Real_DATA_UI_TEST_653_repeat
```

新增 25 项测试覆盖：初始化、项目切换/刷新/恢复、四类上传、危险文件名、上传大小、复制失败、替换/失败保留原绑定、过期/跨项目下载、解绑、只读结果列表、目录扫描上限、文件大小、AppTest 创建/错误/上传/替换/取消/解绑/重命名/下载准备、新会话恢复、UI 不调用统计或 LLM 的架构约束。

最终回归：**25 个本阶段测试通过；全量 780 passed、54 skipped，27.80s**。53 项跳过来自未开启的 R/API 集成测试，1 项是原有 Windows 符号链接创建权限不足。`pip check` 无依赖冲突。

Windows asyncio 的内部 socketpair 会触发原有默认网络拦截。**仅新增 AppTest fixture** 为其提供自建 127.0.0.1 临时端口通信；普通 socket.connect/create_connection 仍被阻断，并断言 DeepSeek 地址不可连接。没有修改原 conftest、放开 API 或使用 `--run-llm`。

### 真实 AppTest 结果（不是浏览器手工结果）

保留旧 `workspace/Real_DATA_UI_TEST`。新建 `workspace/Real_DATA_UI_TEST_653`，通过 Streamlit AppTest 的 Create 表单与 file_uploader → Import 按钮依次导入真实文件。
来源是历史已准备的 `Real_DATA_TEST/input/prepared_asc/` 四个文件；本阶段不修改 gene ID。

| 项目 | 实测 |
|---|---|
| AppTest 上传链 | succeeded，23.608s |
| Counts | 24,581 genes；6 samples |
| Groups | OT 3、T 3 |
| Sample matching | PASS，集合和顺序一致 |
| GO coverage | 17,069 / 24,581 = 69.44% |
| KEGG coverage | 10,100 / 24,581 = 41.09%，低覆盖 warning 保留 |
| 新会话打开项目 | 绑定与摘要恢复 |
| 源文件完整性 | 四个 SHA-256 均不变 |
| PCA | Ready |
| DEG | data_ready；缺 treatment/control，没有猜测 |
| GO/KEGG/Heatmap/Volcano | 显示真实缺失依赖 |
| R/DeepSeek | 0 次；项目 results/ 为空 |

机器报告：`workspace/Real_DATA_UI_TEST_653/reports/streamlit_apptest.json`。
服务实际启动成功，`/_stcore/health` 返回 HTTP 200，`pip check` 通过。

## 手工验收：尚未完成

computer-use 已初始化，但尝试观察 Windows Edge 窗口时返回：无法足够可靠地确定浏览器当前 URL，工具终止本轮操作。
遵照该技能的停止规则，没有继续浏览器/桌面操作或绕过限制。

因此 **AppTest 通过不能等价为视觉、文件选择框、浏览器下载、页面刷新全部验收通过**。
请用户在本机打开应用，验证：

- [ ] 空项目页、侧栏、四页签在自己的屏幕上布局正常。
- [ ] 创建新项目、自动切换、浏览器刷新后重新选择能恢复。
- [ ] 从系统文件选择框上传四类真实文件，指标与上表一致。
- [ ] 项目切换、Replace 确认与 Cancel 正常；错误文件显示友好提示。
- [ ] 在新测试项目中 Remove 仅解绑、历史副本仍保留。
- [ ] Download 在浏览器中确实下载所选项目原始上传文件。
- [ ] Project 重命名正常；Results 无结果时有空状态；页面无 traceback。

## 限制与结论

- 仅本地、单用户开发工具，无认证/并发作业管理，不能直接公网部署。
- 上传上限 300 MiB；Streamlit 在内存接收文件，GO 解析也需要内存，未做多用户压力测试。
- 读取 summary/hash 的刷新在真实数据上可能耗时；只在打开/修改/显式刷新时进行，不每次点击都重解析大表。
- 下载只按需缓存一个原始文件；完整结果图/表预览留到后续。
- Session State 不跨浏览器永久保存“当前选中项目”；已保存业务数据可恢复。
- Streamlit 固定 1.64.x，使用该版本原生有状态 tabs 和 AppTest file_uploader；没有 UI 第三方组件或 DOM hack。

**实现与自动化部分通过，完整 Phase 6.5.3 验收待浏览器手工检查补齐。停止，不进入 Phase 6.5.4。**

参考官方文档：[有状态 tabs](https://docs.streamlit.io/develop/api-reference/layout/st.tabs)、[file_uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader)。
