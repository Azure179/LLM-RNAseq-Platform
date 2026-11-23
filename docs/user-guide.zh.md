# 从表达矩阵到一张能追溯来源的图

先按[首页快速启动](../README.md#quick-start-on-windows)打开 Streamlit。初次体验可以直接使用[演示数据与自动流程](../examples/demo/README.md)。以下操作都在浏览器内完成。

## 建项目和分组

1. 在侧栏创建项目，填写 Project Name 并点 **Create**。在 **Projects** 确认当前项目。每个项目单独保存输入、聊天和结果。
2. 在 **Files** 的 Count Matrix 区上传基因计数表并执行导入。用测序计数程序产生的非负整数。每行一个唯一基因，每列一个样本。下面是公开文件的开头；分析时请下载[完整矩阵](../examples/data/counts.csv)。

   ```csv
   gene_id,control_1,control_2,control_3,treatment_1,treatment_2,treatment_3
   gene_001,102,118,95,812,865,790
   gene_002,510,475,532,61,74,56
   ```

3. 在 Metadata 区为每个识别出的样本填写组名，点 **Save sample groups**。也可上传下面这份[完整 metadata](../examples/data/metadata.csv)。样本名须与计数列完全对应，行序可以不同。每个比较组需要生物学重复。检查侧栏 readiness 后再运行分析。

   ```csv
   sample_id,condition
   control_1,control
   control_2,control
   control_3,control
   treatment_1,treatment
   treatment_2,treatment
   treatment_3,treatment
   ```

4. 做富集还需导入自己物种的注释。在 Files 可用优先展示的 eggNOG 入口生成两张映射表，也可分别上传[示例 GO](../tests/fixtures/ci_science/go.tsv) 和[示例 KEGG](../tests/fixtures/ci_science/kegg.tsv)。检查 gene ID 交集、coverage 和替换确认。详见[注释列要求](eggnog-import.md)、[输入格式转换](input-format-adaptation.md)及[样本分组表单](sample-group-form.md)。

## 如何提出具体要求

在 **Assistant** 输入任务。聊天需要自己的有效 DeepSeek key 和网络连接。下面是与现有参数契约相符的提示语；本阶段验证了相应本地运算，逐句付费模型验证为 **NOT RUN**。用自己的数据时，把组名换成 metadata 中的精确值。

| 想看什么 | 可以输入 |
|---|---|
| 某组相对另一组的变化 | 以 control 为对照，比较 treatment 相对于 control 的差异表达。 |
| 整体样本表达模式 | 画样本 PCA，按分组显示。 |
| 同一次 DEG 的分布 | 使用刚才 treatment vs control 的 DEG 绘制火山图。 |
| 差异基因在各样本的表达 | 用这次 DEG 绘制 Top 15 差异基因热图。 |
| 已有 DEG 的功能富集 | 对这次比较的上调基因做 GO 富集。或：对下调基因做 KEGG 富集。 |
| 改图并保留原图 | 使用同一个 DEG 重画火山图，上调基因用红色，最多标注 5 个基因。 |

如果实际组名是 OT 和 T，可以说“以 T 为对照比较 OT，并用同一次差异分析结果画火山图”。确认计划中 treatment=OT、control=T。存在多个来源时，在 **Source result** 中选择正确的一次，再继续。一次请求包含多个步骤时，检查计划中的比较、依赖及来源后再执行。

## 怎样读结果

- **DEG** 的正 log2FoldChange 表示 treatment 相对 control 表达更高。值为 1 对应估计表达量之比为 2。交换两组会改变方向。p-value 衡量单个基因检验的证据；padj 对多基因检验作校正。默认筛选为 padj < 0.05 且绝对 log2FoldChange ≥ 1。缺失 padj 不计为显著。
- **PCA** 使用 VST 表达值；坐标轴百分比表示该主成分解释的方差比例。样本间距适合探索重复的一致性，不能据此判定差异显著。**Heatmap** 展示经转换的表达模式；**Volcano** 使用已完成 DEG 的结果。
- **GO/KEGG** 的 all/up/down 指符合 DEG 阈值的全部、上调或下调目标。解释富集时同时看实际可检验且可映射的背景基因（universe）、实际映射的目标数和 BH 校正结果。零显著条目是有效输出。

## 浏览、下载和复用

在聊天点 **View Results** 或进入 **Results**。选中分析 run，预览 PNG 或表格，点下载按钮获取原文件。表格预览默认 100 行。在 **Result type** 选择 **Workflow** 查看历史步骤和每步状态，选择 **Customization** 查看来源和新产物。

同名替换 counts 或 metadata 后，请重新运行 DEG，使用最新输入继续分析。若明确选择旧 DEG，新图可能标为 `historical_run`；它对应旧输入。检查结果中的父 run ID 后再用于汇报。关闭再打开工作区时选择原项目，可继续查看历史结果。

## 没有 API Key，怎样运行一次 DEG？

正常部署中，在 Streamlit 创建名为 `Tutorial` 的项目、导入上面的两份完整 CSV。打开 [FastAPI 文档](http://127.0.0.1:8000/docs)，找到 **POST /skills/{skill_name}/run**，点 **Try it out**。在 skill_name 填 `rnaseq.deg`，请求正文填写：

```json
{
  "project_id": "Tutorial",
  "parameters": {"treatment": "treatment", "control": "control"}
}
```

点 **Execute**。成功时返回 `status`、`run_id` 和输出索引。在同一个项目的 Results 点 **Refresh Results** 即可查看。每次提交都是新的执行；重复查看历史结果只需刷新 Results。如果项目名称或组名不同，请替换成自己的实际值。
