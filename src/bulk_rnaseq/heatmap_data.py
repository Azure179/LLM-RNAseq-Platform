"""VST-matrix validation and deterministic Top DEG / row-Z-score preparation."""

from dataclasses import dataclass
import math
from pathlib import Path
from statistics import mean, stdev

from bulk_rnaseq.deg_data import DegRecord
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import csv_rows, MISSING, NUMERIC, _header, _identifier


@dataclass(frozen=True, slots=True)
class ExpressionMatrix:
    samples: tuple[str, ...]
    values: dict[str, tuple[float, ...]]


def read_normalized_expression(path: Path) -> ExpressionMatrix:
    """Caller declares VST input; its provenance cannot be inferred from CSV values."""
    values: dict[str, tuple[float, ...]] = {}
    with csv_rows(path, "EXPRESSION_FORMAT_ERROR") as rows:
        header = _header(rows, "EXPRESSION_FORMAT_ERROR")
        if header[0] != "gene_id":
            raise SkillError("EXPRESSION_FORMAT_ERROR", "First expression column must be gene_id.")
        if len(header) < 3:
            raise SkillError("INSUFFICIENT_SAMPLES", "Heatmap row scaling requires at least two samples.")
        for line, row in enumerate(rows, 2):
            if not row:
                continue
            if len(row) != len(header):
                raise SkillError("EXPRESSION_FORMAT_ERROR", "Expression row width differs from header.", details={"row": line})
            _identifier(row[0], "gene_id", line)
            if row[0] in values:
                raise SkillError("DUPLICATE_ID", "Expression gene_id must be unique.", details={"gene_id": row[0]})
            numbers: list[float] = []
            for column, value in zip(header[1:], row[1:]):
                if value.strip().upper() in MISSING:
                    raise SkillError("MISSING_VALUE", "VST matrix cannot contain missing values.", details={"row": line, "sample_id": column})
                try:
                    if not NUMERIC.fullmatch(value.strip()):
                        raise ValueError("Expected numeric notation")
                    number = float(value)
                    if not math.isfinite(number):
                        raise ValueError("Non-finite expression")
                except ValueError as cause:
                    raise SkillError("EXPRESSION_VALUE_INVALID", "VST expression must be finite numeric values.",
                                     details={"row": line, "sample_id": column}) from cause
                numbers.append(number)
            values[row[0]] = tuple(numbers)
    if not values:
        raise SkillError("EXPRESSION_FORMAT_ERROR", "Expression matrix contains no gene rows.")
    return ExpressionMatrix(tuple(header[1:]), values)


def validate_heatmap_parameters(top_n: int, padj_threshold: float, log2fc_threshold: float, timeout: float) -> None:
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise SkillError("INVALID_PARAMETER", "top_n must be a positive integer.")
    for name, value in (("padj_threshold", padj_threshold), ("log2fc_threshold", log2fc_threshold), ("timeout", timeout)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SkillError("INVALID_PARAMETER", f"{name} must be a finite number.")
    if not 0 < padj_threshold < 1 or log2fc_threshold < 0 or timeout <= 0:
        raise SkillError("INVALID_PARAMETER", "Require 0 < padj_threshold < 1, log2fc_threshold >= 0, timeout > 0.")


def prepare_heatmap(matrix: ExpressionMatrix, records: list[DegRecord], top_n: int,
                    padj_threshold: float, log2fc_threshold: float,
                    *, gene_list: list[str] | None = None, ranking_method: str = "padj",
                    ) -> tuple[list[DegRecord], list[tuple[float, ...]], dict[str, int]]:
    common = set(matrix.values) & {r.original["gene_id"] for r in records}
    if not common:
        raise SkillError("NO_COMMON_GENES", "DEG result and expression matrix have no common gene IDs.")
    significant = [r for r in records if r.padj is not None and r.log2fc is not None
                   and r.padj < padj_threshold and abs(r.log2fc) >= log2fc_threshold and r.log2fc != 0]
    if not significant:
        raise SkillError("NO_SIGNIFICANT_GENES", "No DEG meets the heatmap thresholds with a non-zero effect.")
    significant.sort(key=lambda r: (r.padj, -abs(r.log2fc), r.original["gene_id"]))
    if ranking_method == "abs_log2fc":
        significant.sort(key=lambda r: (-abs(r.log2fc), r.padj, r.original["gene_id"]))
    if gene_list is not None:
        significant = [r for r in significant if r.original["gene_id"] in gene_list]
    selected: list[DegRecord] = []
    scaled: list[tuple[float, ...]] = []
    missing = constant = usable = 0
    for record in significant:
        values = matrix.values.get(record.original["gene_id"])
        if values is None:
            missing += 1
            continue
        try:
            center, spread = mean(values), stdev(values)
            if spread == 0:
                constant += 1
                continue
            z = tuple((v - center) / spread for v in values)
            if not all(math.isfinite(v) for v in z):
                raise ValueError("Non-finite row Z-score")
        except (ValueError, OverflowError) as cause:
            raise SkillError("EXPRESSION_VALUE_INVALID", "Expression range does not permit finite row scaling.",
                             details={"gene_id": record.original["gene_id"]}) from cause
        usable += 1
        if len(selected) < top_n:
            selected.append(record)
            scaled.append(z)
    if not selected:
        raise SkillError("NO_PLOTTABLE_GENES", "No significant shared gene has sample-to-sample variation.",
                         details={"missing_expression": missing, "constant_expression": constant})
    metrics = {"n_genes_requested": top_n, "n_genes_used": len(selected), "n_samples": len(matrix.samples),
               "n_common_genes": len(common), "n_significant": len(significant), "n_usable": usable,
               "n_significant_missing_expression": missing, "n_constant_significant": constant,
               "n_deg_missing_statistics": sum(r.padj is None or r.log2fc is None for r in records)}
    return selected, scaled, metrics
