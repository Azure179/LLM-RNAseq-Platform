"""Independent GO ORA skill; user annotation only, never re-runs DEG analysis."""

import csv
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory

from bulk_rnaseq import __version__
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.go_artifacts import GO_FILES, verify_go_outputs
from bulk_rnaseq.go_data import prepare_go, read_gene_list, validate_go_parameters
from bulk_rnaseq.go_mapping import read_go_mapping
from bulk_rnaseq.models import GoRunResult
from bulk_rnaseq.r_runner import run_rscript
from bulk_rnaseq.run_io import analysis_run, capture_streams, publish_result, sha256, snapshot_input


def run_go_enrichment(
    gene_list: Path, gene_to_go_mapping: Path, output_dir: Path, *,
    universe: Path | None = None, analysis_genes: Path | None = None,
    pvalue_threshold: float = 0.05, padj_threshold: float = 0.05, ontology: str = "ALL",
    mapping_sheet: str | None = None, timeout: float = 600,
    rscript_executable: str | Path = "Rscript",
) -> GoRunResult:
    """Use explicit universe OR analysis-eligible genes intersected with supplied GO mapping.

    Full DEG CSV as analysis_genes excludes NA padj/log2FC. Plain lists declare
    eligibility themselves. Never substitute the entire annotation for the background.
    """
    with analysis_run(output_dir, "go_enrichment", GO_FILES,
                      inputs={"gene_list": str(gene_list), "gene_to_go_mapping": str(gene_to_go_mapping),
                              "universe": str(universe) if universe is not None else None,
                              "analysis_genes": str(analysis_genes) if analysis_genes is not None else None}) as run:
        started = datetime.now(timezone.utc).isoformat()
        validate_go_parameters(pvalue_threshold, padj_threshold, ontology, timeout)
        if universe is None and analysis_genes is None:
            raise SkillError("BACKGROUND_REQUIRED", "Provide universe or analysis_genes (full DEG CSV / eligible gene list); annotation alone cannot define RNA-seq background.")
        if universe is not None and analysis_genes is not None:
            raise SkillError("INVALID_PARAMETER", "Provide only one of universe or analysis_genes.")
        if mapping_sheet is not None and (not isinstance(mapping_sheet, str) or not mapping_sheet.strip()):
            raise SkillError("INVALID_PARAMETER", "mapping_sheet must be a non-empty sheet name.")
        origin = "explicit_universe" if universe is not None else "analysis_genes"
        background_path = universe if universe is not None else analysis_genes
        inputs = {"gene_list": Path(gene_list).expanduser().resolve(),
                  "gene_to_go_mapping": Path(gene_to_go_mapping).expanduser().resolve(),
                  origin: Path(background_path).expanduser().resolve()}
        with TemporaryDirectory(prefix=".work-", dir=run.output) as temporary, as_file(files("bulk_rnaseq.r_scripts")) as scripts:
            stage = Path(temporary)
            copies = {key: stage / (key + path.suffix) for key, path in inputs.items()}
            for key, path in inputs.items():
                snapshot_input(path, copies[key])
            input_hashes = {key: sha256(path) for key, path in copies.items()}
            mapping = read_go_mapping(copies["gene_to_go_mapping"], mapping_sheet)
            query, query_dropped = read_gene_list(copies["gene_list"])
            background, background_dropped = read_gene_list(copies[origin], analysis_background=analysis_genes is not None)
            prepared = prepare_go(query, background, mapping, ontology)
            warnings = prepared.warnings
            for code, count, message in (
                ("UNANNOTATED_MAPPING_ROWS", mapping.n_unannotated_rows, "Mapping rows without GO were skipped."),
                ("DUPLICATE_ASSOCIATIONS", mapping.n_duplicate_pairs, "Repeated gene/GO pairs were deduplicated."),
                ("DUPLICATE_QUERY_GENES", query_dropped, "Repeated query IDs were deduplicated."),
                ("BACKGROUND_ROWS_EXCLUDED", background_dropped, "Duplicate or statistically ineligible background rows were excluded."),
            ):
                if count:
                    warnings.append({"code": code, "count": count, "message": message})
            if any(not mapping.terms[term][1] for term in prepared.term_genes):
                warnings.append({"code": "ONTOLOGY_UNAVAILABLE", "message": "Unknown ontologies retained under ALL; no species/ontology inferred."})
            for name, genes in (("query_used.csv", prepared.query), ("universe_used.csv", prepared.universe)):
                with (stage / name).open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["gene_id"])
                    writer.writerows((gene,) for gene in sorted(genes))
            with (stage / "mapping_used.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["gene_id", "go_id", "go_name", "ontology"])
                for term, genes in prepared.term_genes.items():
                    name, term_ontology = mapping.terms[term]
                    writer.writerows((gene, term, name or term, term_ontology or "UNKNOWN") for gene in sorted(genes))
            protected = {name: sha256(stage / name) for name in GO_FILES[4:]}
            run.logger.info("GO prepared: mapped query=%d universe=%d terms=%d annotation=%s background=%s",
                            len(prepared.query), len(prepared.universe), len(prepared.term_genes), mapping.format, origin)
            script = scripts / "go_enrichment.R"
            execution = run_rscript(script, (
                "--query", str(stage / "query_used.csv"), "--universe", str(stage / "universe_used.csv"),
                "--mapping", str(stage / "mapping_used.csv"), "--output", str(stage),
                "--pvalue", str(pvalue_threshold), "--padj", str(padj_threshold)),
                rscript_executable=rscript_executable, timeout=timeout)
            capture_streams(run.output, execution.stdout, execution.stderr)
            run.logger.info("GO R process completed: return_code=%d", execution.return_code)
            n_enriched, versions = verify_go_outputs(stage, prepared, mapping, pvalue_threshold, padj_threshold, execution.stdout)
            if any(sha256(stage / name) != digest for name, digest in protected.items()):
                raise SkillError("OUTPUT_INVALID", "R modified prepared query/background/mapping files.")
            if not n_enriched:
                warnings.append({"code": "NO_ENRICHED_TERMS", "message": "No GO terms pass thresholds; empty table and explanatory plots are valid outputs."})
            if execution.stderr.strip():
                warnings.append({"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."})
            for warning in warnings:
                run.logger.warning("%s: %s", warning["code"], warning["message"])
            result = GoRunResult(run.run_id, run.output, execution.stdout, execution.stderr)
            summary = {**result.to_dict(), "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
                       "inputs": {key: str(path) for key, path in inputs.items()},
                       "parameters": {"pvalue_threshold": pvalue_threshold, "padj_threshold": padj_threshold,
                                      "ontology": ontology, "mapping_sheet": mapping_sheet, "timeout": timeout},
                       "methods": {"test": "R stats::fisher.test", "alternative": "greater", "adjustment": "BH",
                                   "background_source": origin, "background_rule": "eligible input genes intersect supplied ontology GO mapping",
                                   "annotation_source": "user_mapping", "mapping_format": mapping.format,
                                   "threshold_rule": "pvalue <= threshold AND padj <= threshold AND overlap > 0",
                                   "multiple_testing_family": "all terms in effective universe, including zero-overlap terms",
                                   "go_ancestor_propagation": False, "term_size_filter": None, "plot_top_n": 20},
                       "metrics": {**prepared.metrics, "n_enriched": n_enriched, "n_mapping_rows": mapping.n_rows,
                                   "n_mapping_pairs": len(mapping.pairs), "n_background_rows_excluded": background_dropped},
                       "warnings": warnings,
                       "provenance": {"package_version": __version__, **versions, "input_sha256": input_hashes,
                                      "r_scripts_sha256": {script.name: sha256(script), "enrichment_common.R": sha256(scripts / "enrichment_common.R")},
                                      "artifact_sha256": {name: sha256(stage / name) for name in GO_FILES}}}
            for name in GO_FILES:
                (stage / name).replace(run.output / name)
        result = publish_result(result, summary)
        return result
