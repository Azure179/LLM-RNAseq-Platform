"""Top DEG heatmap using supplied VST expression or the PCA shared VST module."""

import csv
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from bulk_rnaseq import __version__
from bulk_rnaseq.artifacts import parse_provenance
from bulk_rnaseq.deg_data import REQUIRED_COLUMNS, read_deg_results
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.heatmap_data import prepare_heatmap, read_normalized_expression, validate_heatmap_parameters
from bulk_rnaseq.models import HeatmapRunResult
from bulk_rnaseq.plot_artifacts import check_png
from bulk_rnaseq.plot_options import validate_display, validate_gene_labels
from bulk_rnaseq.r_runner import RExecutionResult, run_rscript
from bulk_rnaseq.run_io import analysis_run, capture_streams, publish_result, sha256, snapshot_input
from bulk_rnaseq.validation import validate_expression_inputs, validate_metadata


HEATMAP_FILES = ("heatmap.png", "heatmap_matrix.csv", "selected_genes.csv", "normalized_expression.csv")


def run_heatmap(
    deg_result_file: Path, metadata: Path, group_column: str, output_dir: Path,
    *, normalized_expression: Path | None = None, raw_counts: Path | None = None,
    top_n: int = 50, padj_threshold: float = 0.05, log2fc_threshold: float = 1.0,
    gene_list: list[str] | None = None, ranking_method: str = "padj", show_gene_names: bool = True,
    title: str = "Top DEG expression heatmap", figure_width: int = 1500, figure_height: int | None = None,
    timeout: float = 600, rscript_executable: str | Path = "Rscript",
) -> HeatmapRunResult:
    """Return verified heatmap artifacts; supplied normalized input must be VST expression."""
    with analysis_run(output_dir, "heatmap", HEATMAP_FILES,
                      inputs={"deg_result_file": str(deg_result_file), "metadata": str(metadata),
                              "normalized_expression": str(normalized_expression) if normalized_expression is not None else None,
                              "raw_counts": str(raw_counts) if raw_counts is not None else None}) as run:
        started = datetime.now(timezone.utc).isoformat()
        validate_heatmap_parameters(top_n, padj_threshold, log2fc_threshold, timeout)
        validate_display(title, figure_width, 1000 if figure_height is None else figure_height)
        validate_gene_labels(gene_list)
        if ranking_method not in {"padj", "abs_log2fc"} or type(show_gene_names) is not bool:
            raise SkillError("INVALID_PARAMETER", "Unsupported ranking method or show_gene_names flag.")
        if (normalized_expression is None) == (raw_counts is None):
            raise SkillError("INVALID_PARAMETER", "Provide exactly one of normalized_expression or raw_counts.")
        mode = "raw_counts" if raw_counts is not None else "normalized_expression"
        source = raw_counts if raw_counts is not None else normalized_expression
        inputs = {mode: Path(source).expanduser().resolve(), "metadata": Path(metadata).expanduser().resolve(),
                  "deg_result_file": Path(deg_result_file).expanduser().resolve()}
        stdout = stderr = ""
        processes: list[dict[str, Any]] = []

        def execute(script: Path, arguments: tuple[str, ...]) -> RExecutionResult:
            nonlocal stdout, stderr
            run.logger.info("Starting stage: %s executable=%s", script.name, rscript_executable)
            try:
                execution = run_rscript(script, arguments, rscript_executable=rscript_executable, timeout=timeout)
            except SkillError as error:
                error.stdout = stdout + f"[{script.name}]\n" + error.stdout
                error.stderr = stderr + f"[{script.name}]\n" + error.stderr
                raise
            stdout += f"[{script.name}]\n" + execution.stdout
            if execution.stderr:
                stderr += f"[{script.name}]\n" + execution.stderr
            capture_streams(run.output, stdout, stderr)
            processes.append({"stage": script.name, "return_code": execution.return_code,
                              "stdout": execution.stdout, "stderr": execution.stderr})
            run.logger.info("Stage completed: %s return_code=%d", script.name, execution.return_code)
            return execution

        with TemporaryDirectory(prefix=".work-", dir=run.output) as temporary, as_file(files("bulk_rnaseq.r_scripts")) as scripts:
            stage = Path(temporary)
            normalized = stage / "normalized_expression.csv"
            copies = {mode: stage / "counts.csv" if raw_counts is not None else normalized,
                      "metadata": stage / "metadata.csv", "deg_result_file": stage / "deg.csv"}
            for key, path in inputs.items():
                snapshot_input(path, copies[key])
            input_hashes = {key: sha256(path) for key, path in copies.items()}
            records = read_deg_results(copies["deg_result_file"])
            provenance: dict[str, Any] = {"package_version": __version__, "input_sha256": input_hashes}
            script_hashes = {"plot_heatmap.R": sha256(scripts / "plot_heatmap.R")}
            if raw_counts is not None:
                overview = validate_expression_inputs(copies[mode], copies["metadata"], group_column)
                if not overview.analyzed_gene_ids.intersection(r.original["gene_id"] for r in records):
                    raise SkillError("NO_COMMON_GENES", "DEG result and non-zero count genes have no intersection.")
                script_hashes.update({name: sha256(scripts / name) for name in ("normalize_vst.R", "vst_common.R")})
                vst_run = execute(scripts / "normalize_vst.R", (
                    "--counts", str(copies[mode]), "--output", str(normalized), "--vst-module", str(scripts / "vst_common.R")))
                if not normalized.is_file():
                    raise SkillError("OUTPUT_MISSING", "VST did not create normalized_expression.csv.")
                provenance.update(parse_provenance(vst_run.stdout))
            matrix = read_normalized_expression(normalized)
            if raw_counts is not None:
                if matrix.samples != overview.sample_ids or set(matrix.values) != overview.analyzed_gene_ids:
                    raise SkillError("OUTPUT_INVALID", "VST gene/sample IDs differ from the validated raw input.")
            groups = validate_metadata(copies["metadata"], matrix.samples, group_column)
            selected, scaled, numeric_metrics = prepare_heatmap(matrix, records, top_n, padj_threshold, log2fc_threshold,
                                                               gene_list=gene_list, ranking_method=ranking_method)
            metrics: dict[str, Any] = {**numeric_metrics, "n_groups": len(set(groups)), "group_column": group_column}
            warnings: list[dict[str, Any]] = []
            if gene_list is not None:
                unused = sorted(set(gene_list) - {r.original["gene_id"] for r in selected})
                if unused:
                    warnings.append({"code": "GENE_LIST_NOT_SELECTED", "message": "Some requested IDs did not pass existing heatmap selection or top_n limit.",
                                     "details": {"genes": unused}})
            for count_key, code, message in (
                ("n_significant_missing_expression", "DEG_NOT_IN_EXPRESSION", "Significant genes absent from expression were skipped."),
                ("n_constant_significant", "CONSTANT_GENES_SKIPPED", "Constant significant genes cannot be row-scaled and were skipped."),
                ("n_deg_missing_statistics", "DEG_MISSING_STATISTICS", "DEG rows missing padj/log2FC were excluded from selection."),
            ):
                if metrics[count_key]:
                    warnings.append({"code": code, "message": message, "count": metrics[count_key]})
            if len(selected) < top_n:
                warnings.append({"code": "FEWER_GENES_THAN_REQUESTED", "message": "Used all available variable significant genes.",
                                 "requested": top_n, "used": len(selected)})
            if normalized_expression is not None:
                warnings.append({"code": "VST_INPUT_DECLARED", "message": "Caller declares VST input; CSV alone cannot establish normalization provenance."})
            with (stage / "heatmap_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["gene_id", *matrix.samples])
                writer.writerows([record.original["gene_id"], *z] for record, z in zip(selected, scaled))
            with (stage / "selected_genes.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=("rank", *REQUIRED_COLUMNS))
                writer.writeheader()
                writer.writerows({"rank": rank, **record.original} for rank, record in enumerate(selected, 1))
            annotations = stage / "annotations.csv"
            with annotations.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["sample_id", "group"])
                writer.writerows(zip(matrix.samples, groups))
            protected = {name: sha256(stage / name) for name in (*HEATMAP_FILES[1:], "annotations.csv")}
            rendered = execute(scripts / "plot_heatmap.R", (
                "--matrix", str(stage / "heatmap_matrix.csv"), "--annotations", str(annotations),
                "--group-column", group_column, "--plot", str(stage / "heatmap.png"),
                "--title", title, "--width", str(figure_width), "--height", str(figure_height or max(1000, min(3000, 400 + len(selected) * 28))),
                "--show-gene-names", str(show_gene_names).lower()))
            if not (stage / "heatmap.png").is_file():
                raise SkillError("OUTPUT_MISSING", "Renderer did not create heatmap.png.")
            try:
                check_png(stage / "heatmap.png")
                if any(sha256(stage / name) != digest for name, digest in protected.items()):
                    raise ValueError("Renderer changed prepared data")
                fields = dict(line.split("=", 1) for line in rendered.stdout.splitlines() if "=" in line)
                if not fields["R_VERSION"].strip() or int(fields["N_GENES"]) != len(selected) or int(fields["N_SAMPLES"]) != len(matrix.samples):
                    raise ValueError("Renderer version or dimensions disagree with prepared data")
            except (OSError, ValueError, KeyError) as cause:
                raise SkillError("OUTPUT_INVALID", "Heatmap artifact verification failed.", details={"reason": str(cause)}) from cause
            provenance.update({"r_version": fields["R_VERSION"], "r_scripts_sha256": script_hashes,
                               "artifact_sha256": {name: sha256(stage / name) for name in HEATMAP_FILES}})
            if stderr.strip():
                warnings.append({"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."})
            for warning in warnings:
                run.logger.warning("%s: %s", warning["code"], warning["message"])
            result = HeatmapRunResult(run.run_id, run.output, stdout, stderr)
            summary = {**result.to_dict(), "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
                       "inputs": {key: str(path) for key, path in inputs.items()},
                       "parameters": {"input_mode": mode, "group_column": group_column, "top_n": top_n,
                                      "padj_threshold": padj_threshold, "log2fc_threshold": log2fc_threshold,
                                      "gene_list": gene_list, "ranking_method": ranking_method, "show_gene_names": show_gene_names,
                                      "title": title, "figure_width": figure_width, "figure_height": figure_height,
                                      "normalization": "DESeq2 blind VST" if raw_counts is not None else "caller-supplied VST",
                                      "design": "~1" if raw_counts is not None else None,
                                      "blind": True if raw_counts is not None else None,
                                      "size_factor_type": "ratio" if raw_counts is not None else None,
                                      "fit_type_requested": "parametric" if raw_counts is not None else None,
                                      "display_scale": "row_zscore_ddof1", "clustering": "none",
                                      "gene_order": "padj asc, abs(log2FC) desc, gene_id asc" if ranking_method == "padj" else "abs(log2FC) desc, padj asc, gene_id asc",
                                      "sample_order": list(matrix.samples), "timeout_per_process": timeout},
                       "metrics": metrics, "sample_groups": dict(zip(matrix.samples, groups)),
                       "warnings": warnings, "provenance": provenance, "processes": processes}
            for name in HEATMAP_FILES:
                (stage / name).replace(run.output / name)
        result = publish_result(result, summary)
        return result
