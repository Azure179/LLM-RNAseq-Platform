"""Shared reader for existing DEG results; never performs statistical inference."""

from dataclasses import dataclass
from decimal import Decimal
import math
from pathlib import Path

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import csv_rows, MISSING, NUMERIC

REQUIRED_COLUMNS = ("gene_id", "log2FoldChange", "pvalue", "padj")


@dataclass(frozen=True, slots=True)
class DegRecord:
    original: dict[str, str]
    log2fc: float | None
    padj: float | None


def _number(value: str, column: str, row: int) -> float | None:
    if value.strip().upper() in MISSING:
        return None
    try:
        if not NUMERIC.fullmatch(value.strip()):
            raise ValueError("Expected decimal numeric notation")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite value")
        if column in {"pvalue", "padj"} and not 0 <= number <= 1:
            raise ValueError("Probability outside [0, 1]")
        if number == 0 and Decimal(value) != 0:
            raise ValueError("Non-zero value underflows floating-point precision")
    except ValueError as cause:
        raise SkillError("DEG_VALUE_INVALID", "Expected representable finite log2FC or a probability in [0, 1].",
                         details={"column": column, "row": row, "value": value, "reason": str(cause)}) from cause
    return number


def read_deg_results(path: Path) -> list[DegRecord]:
    """Accept the DEG Skill's full CSV; preserve the four source fields verbatim."""
    records: list[DegRecord] = []
    seen: set[str] = set()
    with csv_rows(path, "DEG_FORMAT_ERROR") as rows:
        header = next(rows, None)
        if not header or any(not name.strip() for name in header) or len(header) != len(set(header)):
            raise SkillError("DEG_FORMAT_ERROR", "DEG CSV requires unique non-empty column names.")
        missing = sorted(set(REQUIRED_COLUMNS) - set(header))
        if missing:
            raise SkillError("DEG_COLUMNS_MISSING", "DEG CSV is missing required columns.",
                             details={"missing_columns": missing})
        for row_number, row in enumerate(rows, 2):
            if not row:
                continue
            if len(row) != len(header):
                raise SkillError("DEG_FORMAT_ERROR", "DEG row width differs from header.", details={"row": row_number})
            fields = dict(zip(header, row))
            gene = fields["gene_id"]
            if gene.strip().upper() in MISSING or gene != gene.strip():
                raise SkillError("INVALID_IDENTIFIER", "gene_id must be present without surrounding spaces.",
                                 details={"row": row_number})
            if gene in seen:
                raise SkillError("DUPLICATE_ID", "gene_id must be unique.", details={"gene_id": gene})
            seen.add(gene)
            lfc = _number(fields["log2FoldChange"], "log2FoldChange", row_number)
            pvalue = _number(fields["pvalue"], "pvalue", row_number)
            padj = _number(fields["padj"], "padj", row_number)
            if padj is not None and pvalue is None:
                raise SkillError("DEG_VALUE_INVALID", "A reported padj requires a reported pvalue.",
                                 details={"row": row_number})
            records.append(DegRecord({column: fields[column] for column in REQUIRED_COLUMNS}, lfc, padj))
    if not records:
        raise SkillError("DEG_EMPTY", "DEG CSV contains no gene rows; provide the full DEG result.")
    return records
