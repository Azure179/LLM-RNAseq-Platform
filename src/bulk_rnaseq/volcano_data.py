"""Validate existing DEG results and derive auditable plotting data, without inference."""

import math

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.deg_data import DegRecord, REQUIRED_COLUMNS, read_deg_results


PLOT_COLUMNS = (*REQUIRED_COLUMNS, "category", "neg_log10_padj", "plotted",
                "label", "padj_zero_adjusted", "excluded_reason")




def validate_volcano_parameters(padj_threshold: float, log2fc_threshold: float,
                                top_n_labels: int, timeout: float) -> None:
    for name, value in (("padj_threshold", padj_threshold),
                        ("log2fc_threshold", log2fc_threshold), ("timeout", timeout)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SkillError("INVALID_PARAMETER", f"{name} must be a finite number.")
    if not 0 < padj_threshold < 1 or log2fc_threshold < 0 or timeout <= 0:
        raise SkillError("INVALID_PARAMETER", "Require 0 < padj_threshold < 1, log2fc_threshold >= 0, timeout > 0.")
    if isinstance(top_n_labels, bool) or not isinstance(top_n_labels, int) or top_n_labels < 0:
        raise SkillError("INVALID_PARAMETER", "top_n_labels must be a non-negative integer.")




def prepare_plot_data(records: list[DegRecord], padj_threshold: float,
                      log2fc_threshold: float, top_n_labels: int,
                      ) -> tuple[list[dict[str, str]], dict[str, int], float | None]:
    """Classify once in Python; R consumes these exact categories and coordinates."""
    positive_heights = [-math.log10(r.padj) for r in records if r.padj is not None and r.padj > 0]
    zero_height = max(10.0, -math.log10(padj_threshold) + 1, max(positive_heights, default=0) + 1)
    prepared: list[dict[str, str]] = []
    significant: list[DegRecord] = []
    for record in records:
        lfc, padj = record.log2fc, record.padj
        category = "NS"
        if lfc is not None and padj is not None and padj < padj_threshold:
            if lfc > 0 and lfc >= log2fc_threshold:
                category = "Up"
            elif lfc < 0 and lfc <= -log2fc_threshold:
                category = "Down"
        if category != "NS":
            significant.append(record)
        reasons = []
        if padj is None:
            reasons.append("padj_missing")
        if lfc is None:
            reasons.append("log2fc_missing")
        y = None if padj is None else zero_height if padj == 0 else -math.log10(padj)
        prepared.append({**record.original, "category": category,
                         "neg_log10_padj": "" if y is None else repr(y),
                         "plotted": str(not reasons).lower(), "label": "",
                         "padj_zero_adjusted": str(padj == 0).lower(),
                         "excluded_reason": ";".join(reasons)})
    # Stable tie-breaks make labels independent of CSV row ordering.
    significant.sort(key=lambda r: (r.padj, -abs(r.log2fc), r.original["gene_id"]))
    labels = {r.original["gene_id"] for r in significant[:top_n_labels]}
    for row in prepared:
        if row["gene_id"] in labels:
            row["label"] = row["gene_id"]
    metrics = {"n_total": len(prepared),
               "n_up": sum(r["category"] == "Up" for r in prepared),
               "n_down": sum(r["category"] == "Down" for r in prepared),
               "n_ns": sum(r["category"] == "NS" for r in prepared),
               "n_plotted": sum(r["plotted"] == "true" for r in prepared),
               "n_padj_missing": sum(r.padj is None for r in records),
               "n_log2fc_missing": sum(r.log2fc is None for r in records),
               "n_padj_zero": sum(r.padj == 0 for r in records), "n_labels": len(labels)}
    metrics["n_excluded"] = metrics["n_total"] - metrics["n_plotted"]
    return prepared, metrics, zero_height if metrics["n_padj_zero"] else None
