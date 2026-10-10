# Count / metadata 自动格式适配

Files 页的 Count Matrix / Metadata 现在按文件内容识别分隔符，不再只依赖扩展名。

## 支持的输入

- UTF-8 文本（含 BOM），逗号、Tab 或分号分隔。
- `.csv`、`.tsv`、`.txt`，以及实际内容为文本的 `.xls`。
- Count 第一列 `gene_id`、`Geneid`、`Gene_ID`、`gene id` 等已知别名（大小写不敏感），在分析副本中统一为 `gene_id`。
- Metadata 的 sample/sample_id/sampleid/sample id、group/condition 等已知表头支持大小写和表头两端空格适配，分析副本统一为 sample_id/condition。
- 明确的初始 `##`、`# Program:`、`# Command:` 注释和空行可跳过；数据区不任意跳过错误行。

不再需要仅为解决表头或错扩展名而手工编辑文件。例如 Tab 分隔但命名为 `.csv` 的表，仍能识别为 Tab 文本。`gene_count_matrix.xls` 可以直接上传，不必改名。

## 不做的“修复”

基因 ID、样本名、数据行顺序、count 和 metadata 值均保持原样。不会去掉 `.bam`、重命名 rna/gene ID、自动匹配不一致样本、转置矩阵、舍入小数、填充 NA、删除重复基因或归一化数据。

未知的首列名（如泛化的 `id`）、缺失表头、歧义分隔符、列数不一致等仍然拒绝。一个表头有多个可行分隔符时不猜测；请正确引用含分隔符的列名或导出清晰的 CSV/TSV。

二进制 Excel `.xls` / `.xlsx` 不在 count/metadata 自动转换范围内。检查到 Excel 文件签名会提示导出为 UTF-8 CSV/TSV；改后缀不能将二进制工作簿变成文本。GO/KEGG/eggNOG 既有 XLSX 功能保持不变。

完整 featureCounts 文件如果还有 Chr/Start/End/Strand/Length 等注释列，不自动将其删除或当成样本；本功能面向第一列基因 ID、其余列均为 raw counts 的矩阵。

## 工程机制

`delimited_import.py` 有界读取表头识别分隔符，然后逐行生成 CSV；不为格式识别将完整矩阵读入内存。检测仅决定文本读法，转换后仍调用原来的 scan_count_matrix / scan_metadata 进行完整检查。

原文件按既有不可变版本保留在 original/，标准 CSV 位于 analysis/。转换成功、数值与样本检查通过后才更新 project.json；失败不会替换旧绑定。COUNT_HEADER_ADAPTED、DELIMITER_DETECTED、METADATA_COLUMNS_ADAPTED 等信息保留在导入记录与 UI warnings 中。这些转换提示不表示分析失败。

本次未修改任何 Skill 的统计逻辑，也没有改变严格 CSV 直接调用 Skill 的契约；格式适配发生在 FileImportService / Files UI 的导入边界。

## 修改文件

- `src/bulk_rnaseq/workspace/delimited_import.py`：文本格式检测与流式读取。
- `src/bulk_rnaseq/workspace/import_validation.py`：表头标准化、metadata 别名、文件角色提示复用检测。
- `src/bulk_rnaseq/workspace/file_import.py`：允许 count/metadata 的文本 `.xls`。
- `app/views/files.py`：上传扩展名和适配说明。
- `tests/test_delimited_import.py`：新增 34 项测试，包含扩展名不符、BOM、metadata 顺序、数值拒绝、原件保留、失败不替换、二进制 Excel 拒绝及真实 UI 代码的 AppTest。

## 真实文件验证

在临时项目中，通过正式 FileImportService 导入原始 `Real_DATA_TEST/input/gene_count_matrix.xls`，并导入 `metadata.all_samples.csv`：

- 24,581 个基因，9 个样本；O/OT/T 每组3个样本。
- 非负整数检查通过；count 与 metadata 样本完全对应。
- 逐行比较分析副本与原文件，除表头和分隔符外所有字段相同。
- 原文件 hash 未变；未覆盖已有项目、未运行 R/DeepSeek。

上述证据是后端实际导入与 UI 组件测试，不冒充浏览器鼠标验收。

本次验证：新增专项 34 passed；现有导入/eggNOG/UI 专项 143 passed；全量 883 passed、60 skipped（50.44 秒）。默认未开启 API/R 集成测试。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_delimited_import.py -q
.\.venv\Scripts\python.exe -m pytest -q
```
