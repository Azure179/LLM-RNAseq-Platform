"""Offline KEGG ORA over explicit gene/pathway associations and eligible background."""

import csv
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory

from bulk_rnaseq import __version__
from bulk_rnaseq.enrichment_artifacts import verify_enrichment_outputs
from bulk_rnaseq.enrichment_inputs import prepare_enrichment, read_gene_list, validate_enrichment_parameters
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.kegg_mapping import read_kegg_mapping
from bulk_rnaseq.models import KeggRunResult
from bulk_rnaseq.r_runner import run_rscript
from bulk_rnaseq.run_io import analysis_run, capture_streams, publish_result, sha256, snapshot_input

KEGG_COLUMNS = ("pathway_id", "description", "overlap_count", "query_size", "term_size",
                "universe_size", "gene_ratio", "bg_ratio", "fold_enrichment", "pvalue", "padj", "genes")
KEGG_FILES = ("kegg_enrichment.csv", "kegg_all_results.csv", "kegg_dotplot.png", "kegg_barplot.png",
              "query_used.csv", "universe_used.csv", "mapping_used.csv", "pathway_id_aliases.csv")


def run_kegg_enrichment(
    gene_list: Path, gene_to_pathway_mapping: Path, output_dir: Path, *,
    universe: Path | None = None, analysis_genes: Path | None = None,
    padj_threshold: float = 0.05, pvalue_threshold: float = 1.0,
    mapping_sheet: str | None = None, timeout: float = 600,
    rscript_executable: str | Path = "Rscript",
) -> KeggRunResult:
    """Require universe OR analysis_genes; never infer gene IDs, species, or pathway names."""
    with analysis_run(output_dir, "kegg_enrichment", KEGG_FILES,
                      inputs={"gene_list": str(gene_list), "gene_to_pathway_mapping": str(gene_to_pathway_mapping),
                              "universe": str(universe) if universe is not None else None,
                              "analysis_genes": str(analysis_genes) if analysis_genes is not None else None}) as run:
        started = datetime.now(timezone.utc).isoformat()
        validate_enrichment_parameters(pvalue_threshold, padj_threshold, timeout)
        if universe is None and analysis_genes is None:
            raise SkillError("BACKGROUND_REQUIRED", "Provide universe or analysis_genes; annotation does not define RNA-seq background.")
        if universe is not None and analysis_genes is not None:
            raise SkillError("INVALID_PARAMETER", "Provide only one background source.")
        if mapping_sheet is not None and (not isinstance(mapping_sheet, str) or not mapping_sheet.strip()):
            raise SkillError("INVALID_PARAMETER", "mapping_sheet must be a non-empty sheet name.")
        origin = "explicit_universe" if universe is not None else "analysis_genes"
        background_path = universe if universe is not None else analysis_genes
        inputs = {"gene_list": Path(gene_list).expanduser().resolve(),
                  "gene_to_pathway_mapping": Path(gene_to_pathway_mapping).expanduser().resolve(),
                  origin: Path(background_path).expanduser().resolve()}
        with TemporaryDirectory(prefix=".work-", dir=run.output) as temporary, as_file(files("bulk_rnaseq.r_scripts")) as scripts:
            stage = Path(temporary)
            copies = {key: stage / (key + path.suffix) for key, path in inputs.items()}
            for key, path in inputs.items():
                snapshot_input(path, copies[key])
            input_hashes = {key: sha256(path) for key, path in copies.items()}
            mapping = read_kegg_mapping(copies["gene_to_pathway_mapping"], mapping_sheet)
            query, query_dropped = read_gene_list(copies["gene_list"])
            background, background_dropped = read_gene_list(copies[origin], analysis_background=analysis_genes is not None)
            prepared = prepare_enrichment(query, background, mapping.pairs)
            warnings = prepared.warnings
            aliases_changed = sum(raw != canonical for raw, canonical in mapping.aliases)
            for code, count, message in (
                ("UNANNOTATED_MAPPING_ROWS", mapping.n_unannotated_rows, "Rows without pathway associations were skipped; KO IDs were not substituted."),
                ("DUPLICATE_ASSOCIATIONS", mapping.n_duplicate_pairs, "Repeated gene/pathway associations were deduplicated after pathway normalization."),
                ("PATHWAY_IDS_NORMALIZED", aliases_changed, "Reference ko/map representations were unified; see pathway_id_aliases.csv."),
                ("DUPLICATE_QUERY_GENES", query_dropped, "Repeated query IDs were deduplicated."),
                ("BACKGROUND_ROWS_EXCLUDED", background_dropped, "Duplicate or statistically ineligible background rows were excluded."),
                ("PATHWAY_NAMES_UNAVAILABLE", sum(not mapping.terms[t][0] for t in prepared.term_genes), "Pathway ID used as label when no name was supplied; no names inferred."),
            ):
                if count:
                    warnings.append({"code": code, "count": count, "message": message})
            for name, genes in (("query_used.csv", prepared.query), ("universe_used.csv", prepared.universe)):
                with (stage / name).open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["gene_id"])
                    writer.writerows((gene,) for gene in sorted(genes))
            with (stage / "mapping_used.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["gene_id", "pathway_id", "pathway_name"])
                for term, genes in prepared.term_genes.items():
                    writer.writerows((gene, term, mapping.terms[term][0] or term) for gene in sorted(genes))
            with (stage / "pathway_id_aliases.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["input_pathway_id", "canonical_pathway_id"])
                writer.writerows(sorted(mapping.aliases))
            protected = {name: sha256(stage / name) for name in KEGG_FILES[4:]}
            run.logger.info("KEGG prepared: query=%d universe=%d pathways=%d namespace=%s",
                            len(prepared.query), len(prepared.universe), len(prepared.term_genes), mapping.namespace)
            script = scripts / "kegg_enrichment.R"
            execution = run_rscript(script, (
                "--query", str(stage / "query_used.csv"), "--universe", str(stage / "universe_used.csv"),
                "--mapping", str(stage / "mapping_used.csv"), "--output", str(stage),
                "--pvalue", str(pvalue_threshold), "--padj", str(padj_threshold)),
                rscript_executable=rscript_executable, timeout=timeout)
            capture_streams(run.output, execution.stdout, execution.stderr)
            run.logger.info("KEGG R completed: return_code=%d", execution.return_code)
            n_enriched, versions = verify_enrichment_outputs(
                stage, prepared, mapping.terms, pvalue_threshold, padj_threshold, execution.stdout,
                prefix="kegg", id_column="pathway_id", columns=KEGG_COLUMNS, artifact_files=KEGG_FILES)
            if any(sha256(stage / name) != digest for name, digest in protected.items()):
                raise SkillError("OUTPUT_INVALID", "R modified prepared enrichment input files.")
            if not n_enriched:
                warnings.append({"code": "NO_ENRICHED_TERMS", "message": "No pathways pass thresholds; empty table and explanatory plots are valid results."})
            if execution.stderr.strip():
                warnings.append({"code": "R_DIAGNOSTICS", "message": "Inspect r_stderr.log."})
            for warning in warnings:
                run.logger.warning("%s: %s", warning["code"], warning["message"])
            result = KeggRunResult(run.run_id, run.output, execution.stdout, execution.stderr)
            summary = {**result.to_dict(), "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
                       "inputs": {key: str(path) for key, path in inputs.items()},
                       "parameters": {"padj_threshold": padj_threshold, "pvalue_threshold": pvalue_threshold,
                                      "mapping_sheet": mapping_sheet, "timeout": timeout},
                       "methods": {"test": "R stats::fisher.test", "alternative": "greater", "adjustment": "BH",
                                   "background_source": origin, "background_rule": "eligible input genes intersect supplied pathway mapping",
                                   "annotation_source": "user_mapping", "mapping_format": mapping.format,
                                   "pathway_namespace": mapping.namespace, "gene_id_mapping": "exact user gene IDs; no conversion",
                                   "pathway_id_normalization": "path: prefix removed; koNNNNN -> mapNNNNN; deduplicate gene/pathway pairs",
                                   "pathway_name_source": "user-supplied or ID fallback; no online lookup",
                                   "threshold_rule": "padj <= threshold AND pvalue <= threshold AND overlap > 0",
                                   "multiple_testing_family": "all pathways in effective universe, including zero-overlap pathways",
                                   "term_size_filter": None, "plot_top_n": 20},
                       "metrics": {**prepared.metrics, "n_enriched": n_enriched, "n_mapping_rows": mapping.n_rows,
                                   "n_mapping_pairs": len(mapping.pairs), "n_pathway_aliases_normalized": aliases_changed,
                                   "n_background_rows_excluded": background_dropped},
                       "warnings": warnings,
                       "provenance": {"package_version": __version__, **versions, "input_sha256": input_hashes,
                                      "r_scripts_sha256": {script.name: sha256(script), "enrichment_common.R": sha256(scripts / "enrichment_common.R")},
                                      "artifact_sha256": {name: sha256(stage / name) for name in KEGG_FILES}}}
            for name in KEGG_FILES:
                (stage / name).replace(run.output / name)
        result = publish_result(result, summary)
        return result
