"""Independent real-data integration runner. No statistics are implemented here."""

import argparse
from collections.abc import Callable
import csv
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.enrichment_inputs import read_gene_list
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.run_io import reserve_output, sha256, write_json
from bulk_rnaseq.skills import run_deg, run_go_enrichment, run_heatmap, run_kegg_enrichment, run_pca, run_volcano
from bulk_rnaseq.validation import validate_inputs, validate_pca_inputs
from prepare_inputs import local_path, prepare_annotations, prepare_counts, write_table


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def source_hashes(config: dict[str, Any]) -> dict[str, str]:
    keys = ("source_counts", "source_metadata", "source_annotations", "pathway_names")
    paths = [local_path(ROOT, config[key]) for key in keys]
    paths.append(local_path(ROOT, config["go"]["ontology_file"]))
    if config.get("gene_id_mapping"):
        paths.append(local_path(ROOT, config["gene_id_mapping"]))
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths if path.is_file()}


def prepare(config: dict[str, Any], reuse: bool, logger: logging.Logger) -> dict[str, Any]:
    """A cache can only be reused when its source, settings and derived hashes match."""
    prepared_dir = local_path(ROOT, config.get("prepared_dir", "input"))
    manifest = prepared_dir / "preparation.json"
    identity = {key: value for key, value in config.items() if key not in {"results", "logs", "rscript", "timeout_seconds"}}
    sources = source_hashes(config)
    if reuse:
        cached = read_json(manifest)
        if cached["config"] != identity or cached["source_sha256"] != sources:
            raise SkillError("PREPARATION_CHANGED", "Source or preparation settings changed; do not reuse these prepared inputs.")
        for name, digest in cached["prepared_sha256"].items():
            path = local_path(ROOT, name)
            if not path.is_file() or sha256(path) != digest:
                raise SkillError("PREPARATION_CHANGED", "A prepared input is missing or changed.", details={"path": name})
        validate_inputs(local_path(ROOT, config["count_matrix"]), local_path(ROOT, config["metadata"]), config["treatment"], config["control"])
        validate_pca_inputs(local_path(ROOT, config["count_matrix"]), local_path(ROOT, config["metadata"]), config["group_column"])
        logger.info("Verified prepared-input cache and repeated package validators.")
        return cached
    if manifest.exists():
        raise SkillError("PREPARED_INPUT_EXISTS", "Use --reuse-prepared; existing inputs will not be overwritten.")
    data = prepare_counts(ROOT, config)
    logger.info("Raw counts validated: %s", json.dumps({k: v for k, v in data.items() if k != "genes"}, ensure_ascii=False))
    annotation_start = perf_counter()
    annotation_error = None
    try:
        annotation = prepare_annotations(ROOT, config, set(data["genes"]))
    except (SkillError, OSError) as cause:
        # Annotation failure does not invalidate independently validated raw counts.
        error = cause if isinstance(cause, SkillError) else SkillError("ANNOTATION_READ_ERROR", "Unable to prepare annotation inputs.")
        error.operation = "annotation_preparation"
        annotation_error = error.to_dict()
        logger.error("Annotation preparation failed: code=%s message=%s", error.code, error)
        annotation = {"go_usable_genes": [], "go_any_annotation_genes": [], "kegg_genes": [], "unresolved_go_ids": 0}
    logger.info("Annotation preparation completed in %.3f seconds", perf_counter() - annotation_start)
    generated = [config[key] for key in ("count_matrix", "metadata", "gene_to_go", "gene_to_kegg")]
    generated += [(prepared_dir / name).relative_to(ROOT).as_posix() for name in ("count_matrix.all_samples.csv", "metadata.all_samples.csv", "go_unresolved_terms.tsv", "pathway_id_aliases.tsv")]
    cached = {"created_at": now(), "config": identity, "source_sha256": sources,
              "prepared_sha256": {name: sha256(local_path(ROOT, name)) for name in generated if local_path(ROOT, name).is_file()},
              "data": data, "annotation": annotation, "annotation_error": annotation_error,
              "ontology_source": "https://current.geneontology.org/ontology/go-basic.obo"}
    write_json(manifest, cached)
    return cached


def coverage(mapped: set[str], genes: set[str]) -> dict[str, Any]:
    return {"mapped": len(mapped & genes), "total": len(genes), "fraction": len(mapped & genes) / len(genes) if genes else None}


class IntegrationRun:
    def __init__(self, output: Path, logger: logging.Logger, config: dict[str, Any]) -> None:
        self.output, self.logger, self.config = output, logger, config
        self.started = perf_counter()
        self.report: dict[str, Any] = {
            "status": "running", "dataset": "Real_DATA_TEST", "comparison": f"{config['treatment']}_vs_{config['control']}",
            "started_at": now(), "steps": {}, "key_results": {}, "warnings": [], "failed_steps": [],
            "resolved_config": config, "results_dir": str(output),
        }

    def save(self) -> None:
        write_json(self.output / "real_data_test_summary.json", self.report)

    def warning(self, code: str, message: str, **details: Any) -> None:
        self.report["warnings"].append({"code": code, "message": message, **details})
        self.logger.warning("%s: %s %s", code, message, details)

    def step(self, name: str, action: Callable[[], Any], *, kind: str = "hard_failure") -> dict[str, Any] | None:
        started = perf_counter()
        entry: dict[str, Any] = {"status": "running", "started_at": now()}
        self.report["steps"][name] = entry
        self.logger.info("Step started: %s", name)
        self.save()
        try:
            result = action()
            if hasattr(result, "summary_json"):
                payload = read_json(result.summary_json)
                for filename, digest in payload["provenance"]["artifact_sha256"].items():
                    artifact = result.output_dir / filename
                    if not artifact.is_file() or sha256(artifact) != digest:
                        raise SkillError("OUTPUT_INVALID", "Artifact is missing or its hash differs from the Skill summary.", details={"path": str(artifact)})
                entry.update(summary_json=str(result.summary_json), metrics=payload["metrics"],
                             output_bytes=sum(p.stat().st_size for p in result.output_dir.rglob("*") if p.is_file()))
                for warning in payload.get("warnings", []):
                    self.warning(warning["code"], warning["message"], step=name)
                entry["status"] = "empty" if name in {"go", "kegg"} and payload["metrics"]["n_enriched"] == 0 else "succeeded"
            else:
                payload = result
                entry["status"] = "succeeded"
            return payload
        except Exception as cause:
            error = cause if isinstance(cause, SkillError) else SkillError("INTEGRATION_ERROR", "Integration step failed; inspect the private log for diagnostics.", details={"exception_type": type(cause).__name__})
            if name in {"validation", "verification", "integration"}:
                error.operation = "real_data_" + name
            if not isinstance(cause, SkillError):
                self.logger.exception("Unexpected integration failure")
            empty = name == "heatmap" and error.code == "NO_SIGNIFICANT_GENES"
            entry.update(status="empty" if empty else "failed", failure_kind="valid_empty_result" if empty else kind, error=error.to_dict())
            if empty:
                self.warning(error.code, str(error), step=name)
            else:
                self.report["failed_steps"].append(name)
                self.logger.error("Step failed: %s code=%s message=%s", name, error.code, error)
            return None
        finally:
            entry.update(completed_at=now(), elapsed_seconds=round(perf_counter() - started, 3))
            self.logger.info("Step finished: %s status=%s elapsed=%ss output=%s", name, entry["status"], entry["elapsed_seconds"], entry.get("summary_json"))
            self.save()

    def skip(self, name: str, reason: str, *, empty: bool = False) -> None:
        self.report["steps"][name] = {"status": "empty" if empty else "skipped", "reason": reason}
        self.warning("VALID_EMPTY_QUERY" if empty else "DEPENDENCY_UNAVAILABLE", reason, step=name)
        self.save()

    def finish(self) -> int:
        failed = self.report["failed_steps"]
        hard = any(name in failed for name in ("validation", "deg", "integration"))
        status = "failed" if hard else "partial" if failed else "success"
        self.report.update(status=status, completed_at=now(), elapsed_seconds=round(perf_counter() - self.started, 3))
        self.save()
        self.logger.info("Final status=%s; summary=%s", status, self.output / "real_data_test_summary.json")
        return 1 if hard else 2 if failed else 0


def pipeline(run: IntegrationRun, reuse: bool) -> int:
    config, output, logger = run.config, run.output, run.logger
    prepared = run.step("validation", lambda: prepare(config, reuse, logger))
    names = ("pca", "deg", "volcano", "heatmap", "go", "kegg")
    if prepared is None:
        for name in names:
            run.skip(name, "Validated real inputs are unavailable.")
        return run.finish()
    data, annotation = prepared["data"], prepared["annotation"]
    if annotation.get("id_mapping_basis") == "user_requested_name_rule":
        run.warning("NAME_RULE_MAPPING", "Gene/query mapping follows the user-requested Asc naming rule; it has not been validated against GTF/GFF.")
    annotation_error = prepared.get("annotation_error")
    if annotation_error:
        run.warning("ANNOTATION_PREPARATION_FAILED", "Annotation preparation failed; expression analysis can continue.", error=annotation_error)
    run.report["data_check"] = {key: value for key, value in data.items() if key != "genes"}
    run.report["provenance"] = {key: prepared[key] for key in ("source_sha256", "prepared_sha256", "ontology_source")}
    run.report["annotation"] = {key: value for key, value in annotation.items() if not isinstance(value, list)}
    genes = set(data["genes"])
    mapped = {"go": set(annotation["go_usable_genes"]), "kegg": set(annotation["kegg_genes"])}
    run.report["mapping_coverage"] = {name: {"status": "unavailable" if annotation_error else "measured", "count_matrix": None if annotation_error else coverage(ids, genes)} for name, ids in mapped.items()}
    run.report["mapping_coverage"]["go"]["count_matrix_any_ontology"] = coverage(set(annotation["go_any_annotation_genes"]), genes)
    for name in mapped:
        item = run.report["mapping_coverage"][name]["count_matrix"]
        logger.info("Preflight %s mapping coverage: %s", name, item)
        if item is None:
            continue
        if item["fraction"] < config["low_coverage_threshold"]:
            run.warning("LOW_MAPPING_COVERAGE", "Exact gene-ID mapping coverage is low. No gene IDs were inferred or replaced.", skill=name, coverage=item)
    if annotation["unresolved_go_ids"]:
        run.warning("UNRESOLVED_GO_IDS", "GO IDs absent/obsolete in the selected ontology are audited in the prepared input directory; no replacements or ancestor propagation.", count=annotation["unresolved_go_ids"])
    run.report["key_results"].update(n_genes=data["n_genes"], n_samples=data["n_samples"], n_up=None, n_down=None, n_go_terms=None, n_kegg_pathways=None)
    counts, metadata = local_path(ROOT, config["count_matrix"]), local_path(ROOT, config["metadata"])
    common = {"timeout": config["timeout_seconds"], "rscript_executable": config["rscript"]}
    alpha, lfc = config["deg"]["padj_threshold"], config["deg"]["log2fc_threshold"]
    pca = run.step("pca", lambda: run_pca(counts, metadata, config["group_column"], output / "01_pca", **common), kind="independent_analysis_failure")
    deg = run.step("deg", lambda: run_deg(counts, metadata, config["treatment"], config["control"], output / "02_deg", alpha=alpha, lfc_threshold=lfc, **common))
    if deg is None:
        for name in names[2:]:
            run.skip(name, "DEG did not publish a verified success result.")
        return run.finish()
    metrics = deg["metrics"]
    run.report["key_results"].update(n_up=metrics["upregulated_genes"], n_down=metrics["downregulated_genes"],
                                      n_ns=metrics["genes_analyzed"] - metrics["upregulated_genes"] - metrics["downregulated_genes"])
    full = output / "02_deg/deg_results.csv"
    filtered = output / "02_deg/deg_filtered.csv"
    with filtered.open(encoding="utf-8", newline="") as handle:
        selected = {row["gene_id"] for row in csv.DictReader(handle)}
    query = output / "deg_gene_list.csv"
    write_table(query, ["gene_id"], ([gene] for gene in sorted(selected)))
    background, excluded = read_gene_list(full, analysis_background=True)
    background_path = output / "analysis_background_gene_list.csv"
    write_table(background_path, ["gene_id"], ([gene] for gene in sorted(background)))
    run.report["background"] = {"source": str(full), "rule": "Existing Skill: full DEG rows with finite padj and log2FoldChange, then intersect the supplied mapping for each ontology/pathway set.",
                                "n_eligible": len(background), "n_excluded_rows": excluded, "audit_gene_list": str(background_path),
                                "effective_universe_candidates": None if annotation_error else {name: len(ids & background) for name, ids in mapped.items()}}
    for name in mapped:
        run.report["mapping_coverage"][name]["deg"] = None if annotation_error else coverage(mapped[name], selected)
        logger.info("Post-DEG %s mapping coverage: %s", name, run.report["mapping_coverage"][name]["deg"])
        if selected and not annotation_error and len(mapped[name] & selected) / len(selected) < config["low_coverage_threshold"]:
            run.warning("LOW_DEG_MAPPING_COVERAGE", "Selected DEG mapping coverage is low; gene identifiers were not changed.", skill=name, coverage=coverage(mapped[name], selected))
    volcano = run.step("volcano", lambda: run_volcano(full, output / "03_volcano", padj_threshold=alpha, log2fc_threshold=lfc, **common), kind="non_critical_visualization_failure")
    run.warning("VST_RECOMPUTED", "PCA does not export its VST matrix; Heatmap reuses the existing shared VST implementation on the identical six-sample counts.")
    heatmap = run.step("heatmap", lambda: run_heatmap(full, metadata, config["group_column"], output / "04_heatmap", raw_counts=counts,
                       top_n=config["heatmap"]["top_n"], padj_threshold=alpha, log2fc_threshold=lfc, **common), kind="non_critical_visualization_failure")
    if annotation_error:
        def annotation_unavailable() -> None:
            raise SkillError("ANNOTATION_PREPARATION_FAILED", "Enrichment cannot use an incompletely prepared mapping.", details={"cause": annotation_error})
        for name in ("go", "kegg"):
            run.step(name, annotation_unavailable)
    elif selected:
        go = run.step("go", lambda: run_go_enrichment(query, local_path(ROOT, config["gene_to_go"]), output / "05_go", analysis_genes=full,
                      ontology=config["go"]["ontology"], pvalue_threshold=config["go"]["pvalue_threshold"], padj_threshold=config["go"]["padj_threshold"], **common))
        kegg = run.step("kegg", lambda: run_kegg_enrichment(query, local_path(ROOT, config["gene_to_kegg"]), output / "06_kegg", analysis_genes=full,
                        padj_threshold=config["kegg"]["padj_threshold"], **common))
        for name, payload in (("go", go), ("kegg", kegg)):
            if payload is not None:
                run.report["key_results"]["n_go_terms" if name == "go" else "n_kegg_pathways"] = payload["metrics"]["n_enriched"]
    else:
        for name in ("go", "kegg"):
            run.skip(name, "DEG selection is empty; enrichment was not executed.", empty=True)
    def verify_connections() -> dict[str, Any]:
        if source_hashes(config) != prepared["source_sha256"]:
            raise SkillError("SOURCE_CHANGED", "An original input changed during the run.")
        if len(selected) != metrics["significant_genes"]:
            raise SkillError("OUTPUT_INVALID", "DEG gene-list count differs from the verified DEG summary.")
        if volcano:
            vm = volcano["metrics"]
            if (vm["n_up"], vm["n_down"], vm["n_total"]) != (metrics["upregulated_genes"], metrics["downregulated_genes"], metrics["genes_analyzed"]):
                raise SkillError("OUTPUT_INVALID", "Volcano and DEG classifications disagree.")
        return {"source_preserved": True, "deg_selection_verified": True, "volcano_deg_agree": True if volcano else None}
    run.report["verification"] = run.step("verification", verify_connections)
    return run.finish()


def execute(config: dict[str, Any], output: Path, logger: logging.Logger, reuse: bool) -> int:
    run = IntegrationRun(output, logger, config)
    try:
        return pipeline(run, reuse)
    except Exception as cause:
        def fail() -> None:
            raise cause
        run.step("integration", fail)
        for name in ("validation", "pca", "deg", "volcano", "heatmap", "go", "kegg"):
            if name not in run.report["steps"]:
                run.skip(name, "Integration bookkeeping failed; required inputs could not be established.")
        return run.finish()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/real_test_config.json", help="Relative to Real_DATA_TEST")
    parser.add_argument("--rscript", help="Optional Rscript executable override")
    parser.add_argument("--results-dir", help="New/empty directory relative to Real_DATA_TEST")
    parser.add_argument("--prepared-dir", help="New directory under input/ for changed annotation/config; existing prepared inputs are preserved")
    parser.add_argument("--reuse-prepared", action="store_true", help="Verify hashes and reuse prepared inputs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = read_json(local_path(ROOT, args.config))
        if args.rscript:
            config["rscript"] = args.rscript
        if args.results_dir:
            config["results"] = args.results_dir
        if args.prepared_dir:
            destination = local_path(ROOT, args.prepared_dir)
            if not destination.is_relative_to(ROOT / "input"):
                raise SkillError("CONFIG_ERROR", "Prepared inputs must stay under Real_DATA_TEST/input.")
            config["prepared_dir"] = destination.relative_to(ROOT).as_posix()
            for key in ("count_matrix", "metadata", "gene_to_go", "gene_to_kegg"):
                config[key] = (destination / Path(config[key]).name).relative_to(ROOT).as_posix()
        output, logs = local_path(ROOT, config["results"]), local_path(ROOT, config["logs"])
        if not output.is_relative_to(ROOT / "results"):
            raise SkillError("CONFIG_ERROR", "All analysis results must stay under Real_DATA_TEST/results.")
        logs.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid4())
        with reserve_output(output, run_id), run_logger(logs / "real_data_test.log", run_id) as logger:
            console = logging.StreamHandler()
            console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(console)
            try:
                return execute(config, output, logger, args.reuse_prepared)
            finally:
                logger.removeHandler(console)
                console.close()
    except Exception as cause:
        error = cause if isinstance(cause, SkillError) else SkillError("INTEGRATION_ERROR", "Cannot start or finish integration run.", details={"exception_type": type(cause).__name__, "reason": str(cause)})
        error.operation = "real_data_integration"
        logging.error("%s", json.dumps(error.to_dict(), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
