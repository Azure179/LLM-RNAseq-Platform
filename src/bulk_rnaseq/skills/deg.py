"""Validated two-group DEG execution, diagnostics, and success publication."""

from datetime import datetime, timezone
from importlib.resources import as_file, files
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from bulk_rnaseq import __version__
from bulk_rnaseq.artifacts import parse_provenance, verify_results
from bulk_rnaseq.exceptions import DegRunError, SkillError
from bulk_rnaseq.models import DegRunResult
from bulk_rnaseq.r_runner import run_rscript
from bulk_rnaseq.run_io import analysis_run, publish_result, sha256
from bulk_rnaseq.run_io import capture_streams as _capture_streams, snapshot_input as _snapshot
from bulk_rnaseq.validation import validate_inputs, validate_parameters


DEG_FILENAME = "deg_results.csv"
FILTERED_FILENAME = "deg_filtered.csv"
SUMMARY_FILENAME = "summary.json"


def _execute(
    counts: Path, metadata: Path, treatment: str, control: str, output: Path,
    run_id: str, alpha: float, lfc_threshold: float, timeout: float,
    rscript_executable: str | Path, logger: logging.Logger,
) -> DegRunResult:
    started = datetime.now(timezone.utc).isoformat()
    validate_parameters(treatment, control, alpha, lfc_threshold, timeout)
    logger.info("Parameters: treatment=%r control=%r alpha=%s lfc_threshold=%s timeout=%s",
                treatment, control, alpha, lfc_threshold, timeout)
    with TemporaryDirectory(prefix=".work-", dir=output) as temporary:
        stage = Path(temporary)
        counts_copy, metadata_copy = stage / "counts.csv", stage / "metadata.csv"
        _snapshot(counts, counts_copy)
        _snapshot(metadata, metadata_copy)
        overview = validate_inputs(counts_copy, metadata_copy, treatment, control)
        logger.info("Input validation passed: genes=%d samples=%d", overview.gene_count, len(overview.sample_ids))
        input_hashes = {"count_matrix": sha256(counts_copy), "metadata": sha256(metadata_copy)}
        full, filtered = stage / DEG_FILENAME, stage / FILTERED_FILENAME
        resource = files("bulk_rnaseq.r_scripts").joinpath("deseq2_deg.R")
        with as_file(resource) as script:
            script_hash = sha256(script)
            logger.info("Starting Rscript: executable=%s", rscript_executable)
            execution = run_rscript(script, (
                "--counts", str(counts_copy), "--metadata", str(metadata_copy),
                "--treatment", treatment, "--control", control,
                "--output", str(full), "--filtered-output", str(filtered),
                "--alpha", str(alpha), "--lfc-threshold", str(lfc_threshold),
            ), rscript_executable=rscript_executable, timeout=timeout)
        _capture_streams(output, execution.stdout, execution.stderr)
        logger.info("Rscript exited: return_code=%s", execution.return_code)
        if execution.stderr.strip():
            logger.warning("R wrote diagnostics; inspect r_stderr.log.")
        metrics = verify_results(full, filtered, overview, alpha, lfc_threshold)
        provenance = parse_provenance(execution.stdout)
        logger.info("Artifact verification passed: genes=%d DEG=%d", metrics["genes_analyzed"], metrics["significant_genes"])
        result = DegRunResult(run_id, output, output / DEG_FILENAME,
                              output / FILTERED_FILENAME, output / SUMMARY_FILENAME,
                              execution.stdout, execution.stderr)
        summary = {
            **result.to_dict(), "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "inputs": {"count_matrix": str(counts), "metadata": str(metadata)},
            "parameters": {"design_factor": "condition", "treatment": treatment,
                           "control": control, "alpha": alpha, "lfc_threshold": lfc_threshold,
                           "timeout_seconds": timeout},
            "metrics": {"genes_input": overview.gene_count,
                        "samples_input": len(overview.sample_ids),
                        "treatment_samples": overview.treatment_samples,
                        "control_samples": overview.control_samples, **metrics},
            "provenance": {"package_version": __version__, **provenance,
                           "input_sha256": input_hashes, "r_script_sha256": script_hash,
                           "artifact_sha256": {DEG_FILENAME: sha256(full), FILTERED_FILENAME: sha256(filtered)}},
            "process": {"return_code": execution.return_code,
                        "stdout": execution.stdout, "stderr": execution.stderr},
            "warnings": ([{"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."}]
                         if execution.stderr.strip() else []),
        }
        # Each rename is atomic, not a multi-file transaction. Publish summary LAST.
        full.replace(result.deg_csv)
        filtered.replace(result.filtered_csv)
    result = publish_result(result, summary)
    return result


def run_deg(
    count_matrix_path: Path, metadata_path: Path, treatment: str, control: str,
    output_dir: Path, *, alpha: float = 0.05, lfc_threshold: float = 1.0,
    timeout: float = 600, rscript_executable: str | Path = "Rscript",
) -> DegRunResult:
    """Return verified success or raise SkillError with code/message/details.

    Requires a new/empty output directory. Failures after reservation persist
    failure.json and run.log. A failure never publishes summary.json.
    """
    with analysis_run(output_dir, "differential_expression", (DEG_FILENAME, FILTERED_FILENAME),
                      inputs={"count_matrix": str(count_matrix_path), "metadata": str(metadata_path)}) as run:
        counts = Path(count_matrix_path).expanduser().resolve()
        metadata = Path(metadata_path).expanduser().resolve()
        run.logger.info("Inputs: counts=%s metadata=%s", counts, metadata)
        return _execute(counts, metadata, treatment, control, run.output, run.run_id,
                        alpha, lfc_threshold, timeout, rscript_executable, run.logger)
