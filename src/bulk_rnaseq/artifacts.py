"""Validate real R outputs before publishing success."""

import csv
import math
from pathlib import Path

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import InputOverview


COLUMNS = ["gene_id", "baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj"]


def _number(value: str, column: str) -> float | None:
    if value in {"", "NA"}:
        if column == "baseMean":
            raise ValueError("baseMean must be present")
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite result")
    if column in {"pvalue", "padj"} and not 0 <= number <= 1:
        raise ValueError("Probability outside [0, 1]")
    if column in {"baseMean", "lfcSE"} and number < 0:
        raise ValueError("Negative mean/standard error")
    return number


def _read_table(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise SkillError("OUTPUT_MISSING", "R did not create a required non-empty output file.",
                         details={"artifact": path.name})
    records: dict[str, dict[str, str]] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != COLUMNS:
                raise ValueError("Unexpected columns")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Malformed row")
                gene = row["gene_id"]
                if not gene or gene in records:
                    raise ValueError("Missing/duplicate gene ID")
                values = {key: _number(row[key], key) for key in COLUMNS[1:]}
                if values["padj"] is not None and (values["pvalue"] is None or values["log2FoldChange"] is None):
                    raise ValueError("Adjusted p-value without p-value or effect")
                records[gene] = row
    except (ValueError, UnicodeError, csv.Error, OSError) as error:
        raise SkillError("OUTPUT_INVALID", "R result CSV has invalid columns or values.",
                         details={"artifact": path.name}) from error
    return records


def verify_results(full_path: Path, filtered_path: Path, overview: InputOverview,
                   alpha: float, lfc_threshold: float) -> dict[str, int]:
    full = _read_table(full_path)
    filtered = _read_table(filtered_path)
    if set(full) != overview.analyzed_gene_ids:
        raise SkillError("OUTPUT_INVALID", "Result gene IDs differ from the non-zero input gene set.")
    selected: dict[str, dict[str, str]] = {}
    padj_na = pvalues = padj_significant = up = down = 0
    for gene, row in full.items():
        padj = _number(row["padj"], "padj")
        lfc = _number(row["log2FoldChange"], "log2FoldChange")
        pvalues += _number(row["pvalue"], "pvalue") is not None
        padj_na += padj is None
        if padj is not None and padj < alpha:
            padj_significant += 1
            if lfc is not None and abs(lfc) >= lfc_threshold:
                selected[gene] = row
                up += lfc > 0
                down += lfc < 0
    if filtered != selected:
        raise SkillError("OUTPUT_INVALID", "Filtered DEG rows disagree with the declared thresholds/full results.")
    return {"genes_analyzed": len(full), "genes_with_pvalue": pvalues, "padj_na": padj_na,
            "padj_significant_genes": padj_significant, "significant_genes": len(selected),
            "upregulated_genes": up, "downregulated_genes": down}


def parse_provenance(stdout: str) -> dict[str, str]:
    provenance = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"R_VERSION", "DESEQ2_VERSION"} and value.strip():
            provenance[key.lower()] = value.strip()
    if len(provenance) != 2:
        raise SkillError("OUTPUT_INVALID", "R output does not contain required version information.")
    return provenance
