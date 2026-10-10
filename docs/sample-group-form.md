# 无需 Metadata 文件：填写样本分组

## 普通用户怎么用

1. 在 Files 导入 Count Matrix。
2. 下方 Metadata 区域自动列出 count matrix 中的全部样本，显示 **填写样本分组（推荐，无需上传 Metadata）**。
3. 在每个 `Group for 样本名` 输入框中填写真实实验组名，例如 T、OT、O。同组样本使用完全一致的组名。
4. 点击 **Save sample groups**，系统自动生成、验证并绑定 Metadata，显示组名及每组样本数。
5. 进入 Assistant 请求 PCA，或明确指定 treatment/control 做 DEG。

无需输入文件路径、编写 CSV 或调用 DeepSeek 来填写分组。初始组名留空，不按样本名前缀推断；样本名不可编辑。已有经验证的分组可预填，但仍需用户确认保存修改。

旧 Metadata 文件上传入口作为可选方式保留。已绑定 Metadata 时，表单保存前需勾选 **Confirm Replace Metadata with these groups**。旧版本保留；表单只生成 sample_id/condition 两列，因此会替换而不是合并旧文件的额外注释列，界面有明确提示。

## 校验与恢复

- 必须先有合法的、校验记录完整的 count matrix。
- 必须为所有样本填写组名，不能增删样本；空值、NA、首尾空格及换行/Tab 被拒绝。
- 组名区分大小写。不会自动合并 T/t，不自动指定实验组或对照组。
- 可以保存只有一个组的 Metadata（例如用于 PCA），但这不代表可以完成两组 DEG。分析时仍应用既有生物学重复与比较参数检查。
- 保存失败不覆盖旧绑定；成功后走原有 Metadata importer，使用相同的样本校验、版本目录、hash、日志和原子提交。
- 已保存分组随项目恢复，也可以下载自动生成的 user_sample_groups.csv；未保存的表单草稿不承诺跨浏览器会话保留。
- 表单绑定项目版本。若在其他页面修改项目或输入，旧表单不能保存；点击 **Refresh sample list** 重新加载。刷新会丢弃未保存草稿，不会猜测新的分组。
- 切换项目会清理 UI 选择，不带入另一个项目的分组。

## 本次新增工程概念

**UI 输入与执行文件分离**：用户填写表单，后端生成标准 Metadata，再复用既有 importer。Skill 的输入协议和统计代码无需变化。

**过期表单保护**：提交时携带表单打开时的项目版本，而不是提交时才读取一个“最新版本”。保存过程前后均检查版本，避免把旧样本分组写到新输入上。

## 文件清单

- `src/bulk_rnaseq/workspace/sample_groups.py`：读取样本与旧分组、检查完整分组、生成临时 Metadata 并调用已有导入服务。
- `src/bulk_rnaseq/workspace/file_import.py`：新增 get_sample_groups/save_sample_groups 入口；普通导入增加可选 expected_updated_at 前置版本检查，原提交阶段的版本检查保留。
- `app/components/sample_groups.py`：本地分组表单、替换确认、版本提示，不操作文件或调用 LLM。
- `app/views/files.py`：Metadata 默认展示表单；保留可选文件上传、历史版本和下载。
- `tests/test_sample_groups.py`：20 项新增后端与 AppTest 测试。
- README / 本说明：用户操作与限制。

## 验证

新增专项 20 passed，涵盖无需文件保存、顺序保持、重启恢复、项目隔离、空/非法组名、缺失样本、确认替换、旧版本保留、过期表单、保存期间并发修改和损坏旧 Metadata 的恢复。

全量免费回归：903 passed、60 skipped，61.20 秒。未开启真实 API/R 集成测试。

实际 count 文件在临时项目中完成生成验证：24,581 个基因、9 个样本，使用已有真实 metadata 明确提供的 O/OT/T 各3个样本分组（不是按名字推断）。保存、重新打开服务、执行上下文绑定和 PCA 输入就绪检查均通过；没有修改用户项目，没有运行 R 或 DeepSeek。

UI 验证使用 Streamlit AppTest，不冒充浏览器鼠标验收。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_sample_groups.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

当前采用适合常规 Bulk RNA-seq 小批量样本的逐样本输入框；没有新增复杂分组编辑器、自动分类、Workflow 或统计功能。
