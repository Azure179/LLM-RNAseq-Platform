# Phase 6.5.1：Project Manager / Workspace

2026-09-19 完成。本阶段只实现项目管理基础层；没有安装 Streamlit、导入表达矩阵、调用 R/DeepSeek，未进入 6.5.2。

## 为什么这样设计

用户分析项目与软件代码是两种不同对象。UI 不应该自己拼接路径、读写 project.json，更不能再实现一套分析流程。

本阶段仅建立：`调用者 → ProjectManager → project.json / 项目目录 → ProjectContext`。
未来的导入、UI、Orchestrator 接入依次在后续子阶段完成。

- 现有 `ExecutionContext` 的 project_root 并不要求是源码根；未来可以设为本阶段的用户项目根。无需改动既有 LLM/Skill。
- 复用 `run_io.write_json` 的临时文件＋原子替换，避免半截 JSON；复用 `run_logger` 和纯 JSON 解码工具。
- 项目管理失败使用 `ProjectError`，字段与现有错误习惯一致，但不伪装成 `rnaseq.project` 分析结果。
- `project_name` 是可修改的显示名称，`ProjectSnapshot.key` 是不可变的初始目录名。**rename_project 不移动目录**，避免结果/日志中的既有路径突然失效。界面以后显示名称，调用 API 使用 key。
- delete_project 要求明确确认，且只移入 workspace 的 `.trash/`；没有永久删除或自动清空回收区功能。
- 一个 workspace 的管理写操作采用排他锁，避免两个调用者同时修改项目资料；这不是分析作业锁或多用户数据库。

## 文件清单

| 文件 | 职责 |
|---|---|
| `src/bulk_rnaseq/workspace/__init__.py` | 对外导出 API 和类型 |
| `src/bulk_rnaseq/workspace/models.py` | Pydantic metadata/files、便携名称和路径规则、Snapshot/Context |
| `src/bulk_rnaseq/workspace/errors.py` | 安全结构化 ProjectError |
| `src/bulk_rnaseq/workspace/manager.py` | 七个项目 API、原子持久化、目录隔离、日志、可恢复删除 |
| `tests/test_workspace.py` | 85 项测试，其中本机 84 通过、1 环境性跳过 |
| `.gitignore` | 新增 `/workspace/`，避免提交用户输入、结果和回收区 |
| `README.md` | 更新阶段状态、文档入口和停止点 |
| 本文 | 本阶段设计和验收说明 |

运行演示另外生成了 `workspace/Demo_Project/project.json`、五个子目录以及 `logs/project.log`。它不是 toy 数据的替代，也没有拷贝真实输入文件。

## Workspace 结构

`ProjectManager` 接收明确的 `Path`，不硬编码本机绝对路径、不搬迁软件仓库。
示例使用仓库根下独立且被 Git 忽略的 workspace；用户也可以指定仓库外的数据目录。

```text
workspace/
├── Demo_Project/          # 已创建的空示例
│   ├── project.json
│   ├── input/
│   ├── results/
│   ├── logs/
│   │   └── project.log
│   ├── reports/
│   └── runtime/
├── Black_porgy/           # 创建后才存在
├── .trash/               # 首次删除项目时创建；不是系统回收站
└── .workspace.lock       # 仅管理写操作期间存在
```

创建先在 workspace 的 `.creating-<uuid>/` 内写完整骨架，再发布到目标目录；不会覆盖已存在的项目或普通目录。
意外写入失败可能留下隐藏的 staging 目录，列表不会把它当成有效项目；不会为“清理”而递归删除不明目录。

## project.json

```json
{
  "schema_version": 1,
  "project_name": "Demo_Project",
  "created_at": "2026-09-19T03:48:20.012281+00:00",
  "updated_at": "2026-09-19T03:48:20.012281+00:00",
  "description": "Phase 6.5.1 empty workspace demo; no analysis data imported.",
  "files": {
    "count_matrix": null,
    "metadata": null,
    "go_mapping": null,
    "kegg_mapping": null
  },
  "analysis": {},
  "last_run": null
}
```

这是本轮真实空示例的 metadata。时间由程序生成，使用带时区的 UTC。
schema_version 用于后续显式兼容/迁移，不默默接受未知版本。
本阶段 analysis/last_run 为预留字段，只允许 `{}` / `null`；后续设计分析历史契约时再扩展，不能在此塞入任意路径、密钥或大表。

文件引用如 `input/count_matrix.csv` 才是允许的持久化形式；拒绝绝对路径、Windows drive/UNC、`..`、反斜杠及隐藏敏感文件引用。
只验证文件引用的安全和存在性，不读取生物学数据、不判断它是不是合法 counts。

## Project Manager API

| 方法 | 返回值 / 行为 |
|---|---|
| `create_project(project_name, description="")` | 完整骨架＋ProjectSnapshot；大小写不敏感地拒绝重名 |
| `open_project(project_key)` | 从磁盘重新读取的 ProjectSnapshot；不会自动覆盖或修复损坏内容 |
| `list_projects()` | 有效 ProjectSnapshot 列表；隐藏目录不列出，损坏项目记录 warning 后跳过 |
| `rename_project(project_key, new_name)` | 仅修改显示名称和 updated_at；key/目录不变 |
| `update_project_metadata(project_key, changes)` | 本阶段允许 `{"description": "..."}`；其他字段受保护 |
| `get_project_context(project_key)` | 重新读取文件引用并生成 ProjectContext，不运行分析 |
| `delete_project(project_key, confirm=True)` | 移入 `.trash/<key>--<uuid>/`；返回准确的恢复目录 Path |

`ProjectSnapshot` 提供 key、root、metadata。调用者修改内存快照不会修改磁盘；更新必须经过管理 API。
名称允许中文与空格，但不允许路径分隔符、控制字符、Windows 保留设备名、开头点、尾部点/空格等。

### Python 使用示例

```python
from pathlib import Path
from bulk_rnaseq.workspace import ProjectManager, ProjectError

manager = ProjectManager(Path("workspace"))
project = manager.open_project("Demo_Project")  # 本次已创建
context = manager.get_project_context(project.key)

# 新项目；同名已存在会返回结构化错误，不覆盖
# project = manager.create_project("Black_porgy", "黑鲷 OT/T 分析")

# 资料修改（按需调用）
# manager.update_project_metadata(project.key, {"description": "新的说明"})
# renamed = manager.rename_project(project.key, "我的演示项目")
# assert renamed.key == "Demo_Project"  # 路径身份不变

projects = manager.list_projects()

# 删除不是默认步骤。未来 UI 必须先向用户显示确认对话框。
# recovery_path = manager.delete_project(project.key, confirm=True)
```

UI 以后捕获 `ProjectError`，使用 `error.to_dict()` 中的 error_code/message 展示错误，不展示 traceback。
常见错误：PROJECT_EXISTS、INVALID_PROJECT_NAME、PROJECT_NOT_FOUND、PROJECT_METADATA_INVALID、PROJECT_LAYOUT_INVALID、PROJECT_FILE_MISSING、PROJECT_PATH_NOT_ALLOWED、WORKSPACE_BUSY、PROJECT_IO_ERROR。

## 路径与 Project Context

磁盘：仅保存项目内部相对引用，不保存软件目录、workspace 绝对路径或执行配置。
内存：`项目当前根目录 / 相对文件引用`，生成 Path 类型绝对路径。

ProjectContext 字段：project_key、project_name、project_root、input_root、output_root、logs_root、reports_root、runtime_root，以及 count_matrix、metadata、go_mapping、kegg_mapping。

- 新项目的四个文件字段均为 None，绝不假装输入已就绪。
- 已登记文件丢失：PROJECT_FILE_MISSING，不偷偷用另一个文件替代。
- 链接/junction 或路径越界：拒绝，避免通过项目引用访问外部文件。
- Context 是本地主机内部对象，不应直接发送给 DeepSeek，也不是现有 LLM ExecutionContext 的替代品。
- 本阶段不把通用文件角色转换成六个 Skill 的 executor 参数；该桥接和数据导入属于 6.5.2。

测试已将临时 workspace 复制到新位置，project.json 字节不变、所有已登记文件路径自动按新根生成。
这保证的是项目 metadata 和新 Context 的可迁移性；不是对旧 SkillResult 中所有绝对审计路径的自动重写。后续结果浏览器需要依据当前项目定位输出，保留原始审计事实。

## 删除与恢复

delete_project 不执行递归删除，只移动已验证的准确项目目录；输入、结果、project.json 都保留在返回的 recovery_path 中。
若要恢复：先确认没有活动管理/分析操作，确认原项目 key 的目标目录不存在，再将返回的那个回收子目录移回 workspace 下原来的 key；之后 open_project(key)。若已同名，不覆盖，先人工处理名称冲突。
本阶段没有恢复 UI、回收站清空、项目合并等功能。本轮只在 pytest 临时项目中测试了删除/恢复，未删除用户真实项目。

## 验证方式与结果

```powershell
# 仅项目管理测试，免费，不访问网络/R
.\.venv\Scripts\python.exe -m pytest tests/test_workspace.py -q

# 全量默认回归，仍不访问 DeepSeek/R
.\.venv\Scripts\python.exe -m pytest -q

# 新 Python 进程打开已经创建的空项目
.\.venv\Scripts\python.exe -c "from pathlib import Path; from bulk_rnaseq.workspace import ProjectManager; ProjectManager(Path('workspace')).open_project('Demo_Project')"
```

- 项目管理：**84 passed，1 skipped**。
- 全量：**656 passed，54 skipped**，14.00 秒。
- 唯一新增 skip：本机不允许创建实际 Windows 符号链接；跨平台模拟的 junction 拒绝测试正常通过，未声称实际符号链接创建测试通过。
- 覆盖创建、重复/非法名字、打开/列出、显示重命名、磁盘持久化、Context、迁移、路径攻击、损坏/超大/重复键 metadata、写入锁、原子写失败、确认删除和恢复。
- 独立进程重新打开本轮 Demo_Project 成功；Git 忽略规则生效；现有 toy/test data 和 Real_DATA_TEST 保留不变。
- 未调用 API 或 R，没有 API token 成本，没有新增依赖。

## 当前限制与停止点

1. 本地单用户、普通本地文件系统 MVP，不提供数据库、多用户事务或恶意同机进程沙箱。
2. 写入锁只约束 ProjectManager 调用者，不代表分析执行期间的锁；未来 UI/作业接入时需禁止运行中删除项目。
3. 崩溃可能留下 `.workspace.lock` 或 `.creating-*`。先确认没有活动进程，再人工检查；不会自动破锁或删除目录。
4. 删除目前仅为可恢复移动；rename 仅改显示名。稳定目录 key 与可变显示名称应在 UI 状态中分开使用。
5. 尚未实现文件导入/替换、RNA-seq 数据 validation 接入、聊天/分析历史、Streamlit 界面或 UI E2E。
6. Project Manager 不检测描述文本里用户主动粘贴的秘密；不要把 API Key 写入项目说明。软件只从既有安全配置读取密钥，项目管理层完全不读取 `.env`。

**Phase 6.5.1 验收通过（保留上述环境性 skip）。停止，等待确认后进入 6.5.2。**
