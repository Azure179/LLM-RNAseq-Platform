"""Independent PCA skill using the shared validated R execution lifecycle."""

from datetime import datetime, timezone
from importlib.resources import as_file, files
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from bulk_rnaseq import __version__
from bulk_rnaseq.artifacts import parse_provenance
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import PcaRunResult
from bulk_rnaseq.pca_artifacts import PCA_FILES, verify_pca_outputs
from bulk_rnaseq.r_runner import run_rscript
from bulk_rnaseq.run_io import analysis_run, capture_streams, publish_result, sha256, snapshot_input
from bulk_rnaseq.validation import validate_pca_inputs


def _gene_metrics(stdout: str, n_nonzero_genes: int) -> dict[str, int]:
    fields = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
    try:
        n_vst = int(fields["N_GENES_VST"])
        n_pca = int(fields["N_GENES_PCA"])
        if n_vst != n_nonzero_genes or not 2 <= n_pca <= n_vst:
            raise ValueError("Unexpected PCA gene counts")
    except (KeyError, ValueError) as error:
        raise SkillError("OUTPUT_INVALID", "R did not report valid VST/PCA gene counts.") from error
    return {"n_genes_vst": n_vst, "n_genes_pca": n_pca}


def run_pca(
    count_matrix: Path, metadata: Path, group_column: str, output_dir: Path,
    *, timeout: float = 600, rscript_executable: str | Path = "Rscript",
) -> PcaRunResult:
    """Run blind VST + PCA; return verified artifacts or a structured SkillError."""
    with analysis_run(output_dir, "pca", PCA_FILES,
                      inputs={"count_matrix": str(count_matrix), "metadata": str(metadata)}) as run:
        started = datetime.now(timezone.utc).isoformat()
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise SkillError("INVALID_PARAMETER", "timeout must be a finite positive number.")
        counts_path = Path(count_matrix).expanduser().resolve()
        metadata_path = Path(metadata).expanduser().resolve()
        run.logger.info("Inputs: counts=%s metadata=%s group_column=%r", counts_path, metadata_path, group_column)
        with TemporaryDirectory(prefix=".work-", dir=run.output) as temporary:
            stage = Path(temporary)
            counts_copy, metadata_copy = stage / "counts.csv", stage / "metadata.csv"
            snapshot_input(counts_path, counts_copy)
            snapshot_input(metadata_path, metadata_copy)
            overview = validate_pca_inputs(counts_copy, metadata_copy, group_column)
            run.logger.info("Input validation passed: genes=%d samples=%d groups=%d",
                            overview.gene_count, len(overview.sample_ids), len(set(overview.sample_groups)))
            input_hashes = {"count_matrix": sha256(counts_copy), "metadata": sha256(metadata_copy)}
            with as_file(files("bulk_rnaseq.r_scripts")) as scripts:
                script = scripts / "transform_pca.R"
                script_hash = sha256(script)
                vst_hash = sha256(scripts / "vst_common.R")
                run.logger.info("Starting Rscript: executable=%s timeout=%s", rscript_executable, timeout)
                execution = run_rscript(script, (
                    "--counts", str(counts_copy), "--metadata", str(metadata_copy),
                    "--group-column", group_column, "--output-dir", str(stage),
                    "--vst-module", str(scripts / "vst_common.R"),
                ), rscript_executable=rscript_executable, timeout=timeout)
            capture_streams(run.output, execution.stdout, execution.stderr)
            run.logger.info("Rscript exited: return_code=%s", execution.return_code)
            metrics = verify_pca_outputs(stage, overview)
            metrics.update(_gene_metrics(execution.stdout, len(overview.analyzed_gene_ids)))
            metrics["genes_input"] = overview.gene_count
            provenance = parse_provenance(execution.stdout)
            warnings: list[dict[str, str]] = []
            if execution.stderr.strip():
                warnings.append({"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."})
                run.logger.warning("R wrote diagnostics; inspect r_stderr.log.")
            if len(set(overview.sample_groups)) == 1:
                warnings.append({"code": "SINGLE_GROUP", "message": "All samples share one group; PCA remains an exploratory sample comparison."})
            result = PcaRunResult(run.run_id, run.output,
                                  run.output / PCA_FILES[0], run.output / PCA_FILES[1],
                                  run.output / PCA_FILES[2], run.output / "summary.json",
                                  execution.stdout, execution.stderr)
            summary: dict[str, Any] = {
                **result.to_dict(), "started_at": started,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "inputs": {"count_matrix": str(counts_path), "metadata": str(metadata_path)},
                "parameters": {"group_column": group_column, "design": "~1",
                               "transformation": "varianceStabilizingTransformation",
                               "blind": True, "fit_type_requested": "parametric",
                               "size_factor_type": "ratio", "gene_selection": "all_variable_vst_genes",
                               "pca_center": True, "pca_scale": False, "timeout_seconds": timeout},
                "metrics": metrics,
                "provenance": {"package_version": __version__, **provenance,
                               "input_sha256": input_hashes, "r_script_sha256": script_hash,
                               "vst_module_sha256": vst_hash,
                               "artifact_sha256": {name: sha256(stage / name) for name in PCA_FILES}},
                "process": {"return_code": execution.return_code,
                            "stdout": execution.stdout, "stderr": execution.stderr},
                "warnings": warnings,
            }
            run.logger.info("PCA artifacts verified: PC1=%.4f%% PC2=%.4f%%",
                            metrics["pc1_variance"], metrics["pc2_variance"])
            for name in PCA_FILES:
                (stage / name).replace(run.output / name)
        # Same success-marker contract as DEG: summary is published last.
        result = publish_result(result, summary)
        return result
