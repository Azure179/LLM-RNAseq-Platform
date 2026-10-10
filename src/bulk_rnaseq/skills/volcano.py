"""Render an existing DEG result: no statistical inference or DESeq2 execution."""

import csv
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from bulk_rnaseq import __version__
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import VolcanoRunResult
from bulk_rnaseq.plot_artifacts import check_png
from bulk_rnaseq.plot_options import color_hex, validate_display, validate_gene_labels
from bulk_rnaseq.r_runner import run_rscript
from bulk_rnaseq.run_io import analysis_run, capture_streams, publish_result, sha256, snapshot_input
from bulk_rnaseq.volcano_data import PLOT_COLUMNS, prepare_plot_data, read_deg_results, validate_volcano_parameters


VOLCANO_FILES = ("volcano_plot.png", "volcano_plot_data.csv")


def _verify_render(stage: Path, data_hash: str, stdout: str, n_plotted: int) -> str:
    for name in VOLCANO_FILES:
        if not (stage / name).is_file() or not (stage / name).stat().st_size:
            raise SkillError("OUTPUT_MISSING", "Volcano did not create a required non-empty artifact.",
                             details={"artifact": name})
    try:
        check_png(stage / VOLCANO_FILES[0])
        if sha256(stage / VOLCANO_FILES[1]) != data_hash:
            raise ValueError("Renderer modified prepared plotting data")
        fields = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
        if not fields["R_VERSION"].strip() or int(fields["N_PLOTTED"]) != n_plotted:
            raise ValueError("Renderer provenance or point count disagrees with Python")
    except (ValueError, KeyError, OSError) as cause:
        raise SkillError("OUTPUT_INVALID", "Volcano artifacts failed validation.",
                         details={"reason": str(cause)}) from cause
    return fields["R_VERSION"]


def run_volcano(
    deg_result_file: Path, output_dir: Path, padj_threshold: float = 0.05,
    log2fc_threshold: float = 1.0, top_n_labels: int = 10,
    *, label_genes: list[str] | None = None, color_up: str = "#D54E4E",
    color_down: str = "#3974B5", color_ns: str = "#A8A8A8", title: str = "Volcano plot",
    figure_width: int = 1200, figure_height: int = 900,
    timeout: float = 600, rscript_executable: str | Path = "Rscript",
) -> VolcanoRunResult:
    """Consume a full DEG CSV and return verified artifacts or a structured SkillError."""
    with analysis_run(output_dir, "volcano", VOLCANO_FILES,
                      inputs={"deg_result_file": str(deg_result_file)}) as run:
        started = datetime.now(timezone.utc).isoformat()
        validate_volcano_parameters(padj_threshold, log2fc_threshold, top_n_labels, timeout)
        validate_display(title, figure_width, figure_height)
        explicit_labels = validate_gene_labels(label_genes)
        colors = [color_hex(c) for c in (color_up, color_down, color_ns)]
        source = Path(deg_result_file).expanduser().resolve()
        run.logger.info("Existing DEG input: %s; no statistical computation will be run", source)
        with TemporaryDirectory(prefix=".work-", dir=run.output) as temporary:
            stage = Path(temporary)
            snapshot = stage / "deg_input.csv"
            snapshot_input(source, snapshot)
            input_hash = sha256(snapshot)
            records = read_deg_results(snapshot)
            rows, metrics, zero_height = prepare_plot_data(records, padj_threshold, log2fc_threshold, top_n_labels)
            warnings: list[dict[str, Any]] = []
            plottable = {row["gene_id"] for row in rows if row["plotted"] == "true"}
            for row in rows:
                if row["gene_id"] in explicit_labels and row["plotted"] == "true":
                    row["label"] = row["gene_id"]
            metrics["n_labels"] = sum(bool(row["label"]) for row in rows)
            missing_labels = sorted(set(explicit_labels) - plottable)
            if missing_labels:
                warnings.append({"code": "LABEL_GENES_UNAVAILABLE", "message": "Requested exact gene IDs were absent or unplottable; no alias inference performed.",
                                 "details": {"genes": missing_labels}})
            if metrics["n_excluded"]:
                warnings.append({"code": "GENES_NOT_PLOTTED", "message": "Missing padj/log2FC: retained as NS in CSV, omitted from plot.",
                                 "count": metrics["n_excluded"]})
            if metrics["n_padj_zero"]:
                warnings.append({"code": "ZERO_PADJ_DISPLAY", "message": "Zero padj uses a finite display height only; source values are unchanged.",
                                 "count": metrics["n_padj_zero"], "display_height": zero_height})
            if not metrics["n_plotted"]:
                warnings.append({"code": "NO_PLOTTABLE_GENES", "message": "Plot contains an explanatory message, not data points."})
            plot, plot_data = (stage / name for name in VOLCANO_FILES)
            with plot_data.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=PLOT_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            data_hash = sha256(plot_data)
            resource = files("bulk_rnaseq.r_scripts").joinpath("plot_volcano.R")
            with as_file(resource) as script:
                script_hash = sha256(script)
                run.logger.info("Starting base-R renderer: executable=%s; genes=%d", rscript_executable, metrics["n_total"])
                execution = run_rscript(script, (
                    "--data", str(plot_data), "--plot", str(plot),
                    "--padj-threshold", str(padj_threshold), "--lfc-threshold", str(log2fc_threshold),
                    "--color-up", colors[0], "--color-down", colors[1], "--color-ns", colors[2],
                    "--title", title, "--width", str(figure_width), "--height", str(figure_height),
                ), rscript_executable=rscript_executable, timeout=timeout)
            capture_streams(run.output, execution.stdout, execution.stderr)
            run.logger.info("Renderer exited: return_code=%s", execution.return_code)
            r_version = _verify_render(stage, data_hash, execution.stdout, metrics["n_plotted"])
            if execution.stderr.strip():
                warnings.append({"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."})
            for warning in warnings:
                run.logger.warning("%s: %s", warning["code"], warning["message"])
            result = VolcanoRunResult(run.run_id, run.output, run.output / plot.name,
                                      run.output / plot_data.name, run.output / "summary.json",
                                      execution.stdout, execution.stderr)
            summary = {
                **result.to_dict(), "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
                "inputs": {"deg_result_file": str(source)},
                "parameters": {"padj_threshold": padj_threshold, "log2fc_threshold": log2fc_threshold,
                               "top_n_labels": top_n_labels, "timeout_seconds": timeout,
                               "label_genes": label_genes, "color_up": color_up, "color_down": color_down,
                               "color_ns": color_ns, "title": title, "figure_width": figure_width, "figure_height": figure_height,
                               "y_axis": "-log10(padj)", "zero_padj_display_height": zero_height,
                               "zero_log2fc_category": "NS", "label_order": "padj asc, abs(log2FC) desc, gene_id asc"},
                "metrics": metrics, "warnings": warnings,
                "provenance": {"package_version": __version__, "r_version": r_version,
                               "input_sha256": {"deg_result_file": input_hash}, "r_script_sha256": script_hash,
                               "artifact_sha256": {name: sha256(stage / name) for name in VOLCANO_FILES}},
                "process": {"return_code": execution.return_code, "stdout": execution.stdout, "stderr": execution.stderr},
            }
            for name in VOLCANO_FILES:
                (stage / name).replace(run.output / name)
        result = publish_result(result, summary)
        run.logger.info("Volcano completed: up=%d down=%d ns=%d", metrics["n_up"], metrics["n_down"], metrics["n_ns"])
        return result
