# Phase 6.5.5：Results Browser

## 结论与范围

结果浏览功能和自动化验收已完成；**浏览器手工验收尚未完成，不能宣布全部验收通过**。本阶段只读取、展示和下载现有结果，不执行 R、DeepSeek 或 Registry，不更改 SkillResult、统计方法、原始结果或旧测试数据。不进入 Phase 6.5.6。

## 1. 新增 / 修改文件

| 文件 | 作用 |
| --- | --- |
| `src/bulk_rnaseq/workspace/result_manager.py`（新增） | 项目级只读索引、RunInfo、ArtifactInfo、路径校验、下载及预览入口 |
| `src/bulk_rnaseq/workspace/result_preview.py`（新增） | 有行数和字节上限的 CSV/TSV 读取、图片验证 |
| `app/views/results.py` | 筛选、run 列表、摘要、参数、附件预览、下载及异常提示 |
| `app/state.py` | 增加 selected_run_id，切换项目时清空选择 |
| `app/views/assistant.py` | 聊天按钮定位实际 run，兼容后端 request ID 和失败记录 |
| `app/views/project.py` | 简单历史计数与最近分析 |
| `app/main.py` | 为 Results 页面传入现有项目服务 |
| `tests/test_result_manager.py`（新增） | 只读、安全、预览上限、异常、项目隔离及 AppTest 测试 |
| `examples/results_browser_test.py`（新增） | 六类已有真实结果的独立组件验收与文件校验报告 |
| `requirements.txt`、`pyproject.toml` | 将已安装的 pandas、Pillow 声明为直接依赖，不新增 UI 框架 |
| `README.md`、本说明 | 操作方法、设计与验收范围 |

另在 `workspace/Real_DATA_UI_TEST/results/phase_6_5_5_existing/` 添加已有真实 Volcano、Heatmap、GO、KEGG 和历史失败 GO 的副本。源数据来自 `Real_DATA_TEST/results/`，逐文件校验 SHA-256；已有目标不覆盖，不改写 summary。原项目已有的真实 PCA/DEG 继续直接使用。

## 2. ResultManager 与唯一事实来源

检查发现当前没有统一 analysis_history.json。结果通常为 `results/{request_id}/skill/summary.json`，外层保存 tool_execution.json；Phase 4 还有 legacy summary 和 failure.json。

因此以现存 SkillResult / summary 为事实来源，运行时构建只读索引，不维护另一份持久化分析历史。优先读取 summary，兼容 skill_result.json 和旧结果 schema；tool_execution.json 补充执行阶段和失败请求。外层请求记录与内层分析结果合并，避免同一分析列两次。

ResultManager 提供 list_runs、list_skill_runs、get_run、get_run_summary、list_artifacts、get_artifact、preview_table、preview_image、preview_json、download、validate_result_path 和 refresh。UI 不直接扫描目录或读取结果表。每次页面渲染重建小索引；Refresh 清除页面选择控件状态后重新读取，无持久化缓存失效问题。

## 3. RunInfo

RunInfo 是内存中的展示模型，不是新的分析输出协议：

- 身份：run_id、skill_name、backend_request_id。
- 状态：status、stage、error_code、message、final_response_status。
- 时间：created_at（UTC）、time_source；无分析时间时明确显示文件修改时间。
- 分析内容：comparison、parameters、summary、warnings。
- 浏览信息：artifacts、相对路径 source/directory、browser_warnings、triggered_by。

实际分析 run_id 为主键，后端 request ID 仅作为聊天定位别名。重复 run_id 不静默覆盖，显示诊断并拒绝模糊定位。未执行的 needs_input/unsupported 请求与实际分析成功明确区分。

## 4. Results 布局与使用

1. 双击项目根目录 `Start_RNAseq.cmd`，保持本地启动窗口运行。
2. 左侧选择项目，例如 `Real_DATA_UI_TEST`；进入 Results。
3. 上方按 Skill、Status、Newest/Oldest 筛选；中间查看 run 列表。
4. 用 Analysis run 选择分析；下方显示状态、时间、比较方向、触发请求、warnings、摘要卡片和参数。
5. 选择附件类别与文件，预览或 Download；Refresh Results 重新发现结果。

Project 页显示总记录数、成功/失败数及最近记录。Files 页仍负责输入文件，Results 负责分析输出。界面默认使用相对路径和文件名，不默认展示运行过程输出。

## 5–8. 附件、预览与下载

| 类型 | 行为与边界 |
| --- | --- |
| PNG / JPG / JPEG | Figures 默认优先展示；适配容器宽度。Pillow 验证内容，20 MiB / 4000 万像素上限 |
| CSV / TSV | 默认 100 行，可选 20、50、500、1000；最多 4 MiB 读取量、200 列；保留文本 ID、NA 和原精度 |
| JSON | 2 MiB 上限，compact view 限制层级、数组及字符串，隐藏内部过程字段和完整绝对路径字段 |
| TEXT / UNKNOWN | Other files 提供原文件下载，不在页面默认展开日志或未知内容 |

类型按扩展名识别，图片另校验实际内容。表格只读取所选行数及用于判断截断的下一行，不为获取总行数扫描完整文件；未到 EOF 时显示“total rows not scanned”。长单元格显示前 500 字符。不会把完整大表长期保存在 session_state。

DEG 优先展示实际 gene_id、baseMean、log2FoldChange、lfcSE、stat、pvalue、padj。Up/Down 仅对已有 filtered_csv 的当前预览按效应方向过滤，不重新判断显著性。GO/KEGG 按真实 padj（或 p.adjust）对当前读取的预览行排序，**不承诺全文件 Top N**。

下载使用延迟回调，点击时读取原文件，最多 300 MiB；不会因页面刷新提前加载所有大文件。下载内容不经过排序、截断或格式化。自动化测试比对原文件字节和 SHA-256；真实浏览器点击及保存文件的手工验收仍待完成。

## 9. 路径安全

ResultManager 绑定当前项目，复用 ProjectManager 的安全路径检查，并额外要求 resolve 后仍位于该项目的 results 根目录。拒绝 `..`、越界绝对路径、跨项目路径、symlink/junction，以及未在该 run 输出中声明的附件。发现、预览、下载时均重新检查；文件在索引后被删除或替换也会拒绝读取。

旧 schema 的相对 artifacts 按原 summary 所在目录解释；不会按 basename 任意搜索文件或自动重写新版绝对输出路径。因此移动了新版结果目录、却未迁移其绝对路径时，会提示不可访问，不偷偷读取原项目内容。

这是单用户本地项目边界防护，不是防御恶意并发文件系统修改的操作系统级沙箱，也不是多用户权限系统。compact view 不等于全面敏感信息脱敏；下载始终保留原文件。

## 10. Chat 与 Results

Assistant 的 View Results 设置 selected_run_id，并切换到 Results；兼容旧消息中的 backend_request_id。Results 从现有 chat history 读取关联的原用户请求，显示 Triggered by，不复制整段聊天。

区分“统计分析成功”与“LLM 总结失败”：后一种仍展示真实成功结果并提示总结失败，避免把实际分析错误标记为失败。

## 11. Failed / Warning / Empty / Missing

- Failed：显示原 error_code、简短 message、stage；附件可以为空，不展示完整 traceback。
- Warning：保留原 warnings；文件缺失等浏览器诊断独立展示，不改写分析状态。成功且有警告的 run 同时匹配 Success 和 Warning 筛选。
- Empty：成功 GO/KEGG 且 n_enriched=0 显示“分析成功，当前阈值下无显著结果”，不是 Failed。
- Missing artifact：显示文件缺失，其他 run 仍可浏览。
- 未提供的摘要字段：显示“未提供”，不猜测。当前 DEG summary 没有 NS 字段，页面不自行相减；genes_analyzed 与 genes_with_pvalue 仍有不同含义。

## 12. 自动化测试

本阶段全量免费回归：**829 passed、60 skipped，43.98 秒**。随后新增十万行预览测试，Results 专项重跑 **26 passed，4.44 秒**；没有把此前全量结果冒充新增测试后的完整重跑。`pip check` 无依赖问题。

专项覆盖列表/筛选/排序、路径越界、跨项目、缺失文件、声明附件、旧 summary、重复 ID、请求去重、聊天关联、失败与空结果、PNG/损坏图、JSON、CSV/TSV、多行引号、读取限额、十万行表格和 AppTest 项目切换。测试夹具明确为合成测试数据，不当作真实分析结果。

十万行测试分别预览 100/1000 行，断言只读取小于文件 1/50 的字节，不完整扫描、不依赖容易波动的时间阈值。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_result_manager.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest -q --tb=short
.\.venv\Scripts\python.exe -m pip check
```

默认测试不访问 DeepSeek、不执行 R。没有修改已有统计测试来降低标准。

## 13. Real_DATA_UI_TEST

使用已有真实输出，六类结果均通过组件预览、下载字节校验和原文件未变验证：

| Skill | 原 summary 中的真实值 / 验证 |
| --- | --- |
| PCA | 6 samples，2 groups；PC1 85.9088594454781%，PC2 4.76667160361138%；真实图片与坐标 |
| DEG | genes_analyzed 24310，genes_with_pvalue 24206；Up 6474，Down 7387；真实 DEG 表 |
| Volcano | 24310 genes，Up 6474，Down 7387，NS 10449；真实图和 plot data |
| Heatmap | 实际使用 50 genes、6 samples；真实图片与矩阵 |
| GO | 输入 13861，映射 9757；显著 terms 70；真实表与图 |
| KEGG | 输入 13861，映射 5896；显著 pathways 10；真实表与图 |

历史真实 GO `NO_MAPPED_GENES` 失败记录可显示。真实聊天按钮可定位已有分析 run。空富集分支由明确的单元测试 / AppTest 夹具覆盖，不将它描述为本阶段新增真实富集结果。

命令（仅浏览已有文件；加 `--prepare-existing` 会在缺少目标时复制上述历史真实结果，不覆盖已有目标）：

```powershell
.\.venv\Scripts\python.exe examples/results_browser_test.py --prepare-existing
```

本次报告：`workspace/Real_DATA_UI_TEST/reports/results_browser_630005e1-36d5-4c9c-97e3-0d7bf2c60423/report.json`，状态 passed，11.943 秒，DeepSeek/R 调用均为 0。记录每个 run、原摘要、附件 hash、复制来源和聊天关联。

第一次组件验收脚本使用了错误的 AppTest 图片元素名 `imgs`；修正为实际 `image` 后通过。属于测试适配问题，未修改图像、统计输出或分析行为。第一次失败报告也保留，没有覆盖。

## 14. 手工 UI 验收：待完成

computer-use 工具无法可靠识别当前 Windows 浏览器 URL，为安全起见终止操作；之后未继续发出浏览器输入。遵循 computer-use 的 Interrupted Turns 指引："If Computer Use reports that the turn ended or that the user stopped Computer Use, stop issuing app input."

因此 AppTest 通过不等同于实际浏览器显示和保存文件通过。用户可用已有双击入口依次检查：

- [ ] Results 列表、Skill/状态筛选、run 选择和刷新。
- [ ] DEG/PCA/GO/KEGG 摘要、参数和实际图像显示。
- [ ] PCA、Volcano、Heatmap 图片比例和清晰度。
- [ ] CSV/TSV 100/1000 行预览、JSON 展开。
- [ ] 点击 Download 并确认浏览器保存文件。
- [ ] 历史失败 GO、警告和缺失附件提示。
- [ ] 成功空富集提示（当前真实演示项目无新增空结果；自动化分支已覆盖）。
- [ ] Chat → Results 的精确定位、Triggered by。
- [ ] 切换另一个项目不混合结果。

## 15–16. 限制与验收结论

这是单机、小型项目的只读结果浏览器：索引最多扫描 10000 个目录项，超限明确诊断；没有数据库、全表查询、无限下载、压缩打包、文件迁移或复杂搜索。结果表仅支持 UTF-8 CSV/TSV，JSON 预览面向对象型结果文件，图片仅 PNG/JPEG。旧记录缺失时间会使用明确标注的修改时间。

新增工程概念重点是：**读模型与分析结果分离、项目边界校验、有限读取、延迟下载、分析 run ID 与请求 ID 分离、成功分析与总结失败分离**。它们提升可追踪性和稳定性，不改变 RNA-seq 方法。

代码与自动化真实结果验收通过；手工浏览器验收未完成，Phase 6.5.5 总体验收保留此待办。停止于此，不进入下一阶段。
