"""Render existing enrichment CSV only. No enrichment tests or p-adjustment."""

import csv
import math
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.plot_artifacts import check_png
from bulk_rnaseq.plot_options import validate_display
from bulk_rnaseq.r_runner import run_rscript
from bulk_rnaseq.run_io import capture_streams, sha256
from .models import EnrichmentPlotOptions


def replot_enrichment(source: Path, output: Path, kind: str, options: EnrichmentPlotOptions, *,
                       rscript_executable: str = "Rscript", timeout: float = 600) -> dict[str, Any]:
    validate_display(options.title, options.figure_width, options.figure_height)
    id_key = "go_id" if kind == "go" else "pathway_id"
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header, rows = reader.fieldnames, list(reader)
    if not header or not {id_key, "description", "padj", "overlap_count", "gene_ratio", "fold_enrichment"} <= set(header):
        raise SkillError("ENRICHMENT_FORMAT_ERROR", "Existing enrichment result is missing required columns.")
    seen = set()
    try:
        for row in rows:
            if None in row or any(v is None for v in row.values()) or not row[id_key] or row[id_key] in seen:
                raise ValueError()
            seen.add(row[id_key])
            numbers = {key: float(row[key]) for key in ("padj", "overlap_count", "gene_ratio", "fold_enrichment")}
            if (any(not math.isfinite(v) or v < 0 for v in numbers.values()) or numbers["padj"] > 1
                    or numbers["gene_ratio"] > 1 or not numbers["overlap_count"].is_integer()):
                raise ValueError()
    except (ValueError, TypeError):
        raise SkillError("ENRICHMENT_FORMAT_ERROR", "Invalid numeric data or duplicate IDs in enrichment result.") from None
    rows.sort(key=lambda r: r[id_key])
    rows.sort(key=lambda r: float(r[options.order_by]), reverse=options.order_by != "padj")
    selected = rows[:options.top_n]
    data = output / "plot_data.csv"
    with data.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(selected)
    protected = sha256(data)
    with as_file(files("bulk_rnaseq.r_scripts").joinpath("replot_enrichment.R")) as script:
        execution = run_rscript(script, ("--data", str(data), "--output", str(output), "--id-column", id_key,
            "--title", options.title, "--width", str(options.figure_width), "--height", str(options.figure_height),
            "--show-count", str(options.show_count).lower()), rscript_executable=rscript_executable, timeout=timeout)
        script_hash = sha256(script)
    capture_streams(output, execution.stdout, execution.stderr)
    try:
        if sha256(data) != protected:
            raise ValueError("Renderer changed input")
        fields = dict(line.split("=", 1) for line in execution.stdout.splitlines() if "=" in line)
        if not fields.get("R_VERSION", "").strip() or int(fields.get("N_DISPLAYED", "-1")) != len(selected):
            raise ValueError("Renderer provenance or displayed count differs from selected rows")
        for filename in ("dotplot.png", "barplot.png"):
            check_png(output / filename)
    except (ValueError, OSError, SkillError):
        raise SkillError("CUSTOM_OUTPUT_VALIDATION_FAILED", "Replot artifacts failed validation.") from None
    return {"n_available": len(rows), "n_displayed": len(selected), "r_script_sha256": script_hash,
            "r_version": fields["R_VERSION"],
            "process": {"return_code": execution.return_code, "stdout": execution.stdout, "stderr": execution.stderr}}
