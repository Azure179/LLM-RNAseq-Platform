"""Command-line entry points for independent RNA-seq skills."""

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
import tomllib
from typing import Any

from bulk_rnaseq import __version__
from bulk_rnaseq.config import load_config
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.logger import create_logger
from bulk_rnaseq.skills.deg import DegRunError, run_deg
from bulk_rnaseq.skills.pca import run_pca
from bulk_rnaseq.skills.volcano import run_volcano
from bulk_rnaseq.skills.heatmap import run_heatmap
from bulk_rnaseq.skills.go_enrichment import run_go_enrichment
from bulk_rnaseq.skills.kegg_enrichment import run_kegg_enrichment


def _operation(command: str | None) -> str:
    return {"pca": "pca", "volcano": "volcano", "heatmap": "heatmap", "go": "go_enrichment", "kegg": "kegg_enrichment"}.get(command, "differential_expression")


class SkillArgumentParser(argparse.ArgumentParser):
    """Argparse validation errors use the same JSON contract as analysis errors."""

    def error(self, message: str) -> None:
        operation = _operation(self.prog.rsplit(" ", 1)[-1])
        raise SkillError("INVALID_ARGUMENT", message, operation=operation)

    def parse_args(self, args: Sequence[str] | None = None,
                   namespace: argparse.Namespace | None = None) -> argparse.Namespace:
        parsed, unknown = self.parse_known_args(args, namespace)
        if unknown:
            operation = _operation(getattr(parsed, "command", None))
            raise SkillError("INVALID_ARGUMENT", "Unrecognized arguments: " + " ".join(unknown),
                             operation=operation)
        return parsed


def _emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def build_parser(default_config_path: Path | None = None) -> argparse.ArgumentParser:
    """Build the command-line parser without performing any work."""

    parser = SkillArgumentParser(
        prog="bulk-rnaseq",
        description="Start the Bulk RNA-seq Skill project skeleton.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path or Path("config/default.toml"),
        help="Path to the TOML application configuration.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    deg_parser = subparsers.add_parser(
        "deg",
        help="Run the validated two-group DESeq2 workflow.",
    )
    deg_parser.add_argument("--counts", type=Path, required=True)
    deg_parser.add_argument("--metadata", type=Path, required=True)
    deg_parser.add_argument("--treatment", required=True)
    deg_parser.add_argument("--control", required=True)
    deg_parser.add_argument("--output-dir", type=Path, required=True)
    deg_parser.add_argument("--alpha", type=float, default=0.05)
    deg_parser.add_argument("--lfc-threshold", type=float, default=1.0)
    deg_parser.add_argument("--timeout", type=float, default=600)
    deg_parser.add_argument(
        "--rscript",
        default="Rscript",
        help="Rscript executable name or path.",
    )
    pca_parser = subparsers.add_parser("pca", help="Run blind DESeq2 VST followed by sample PCA.")
    pca_parser.add_argument("--counts", type=Path, required=True)
    pca_parser.add_argument("--metadata", type=Path, required=True)
    pca_parser.add_argument("--group-column", required=True)
    pca_parser.add_argument("--output-dir", type=Path, required=True)
    pca_parser.add_argument("--timeout", type=float, default=600)
    pca_parser.add_argument("--rscript", default="Rscript", help="Rscript executable name or path.")
    volcano_parser = subparsers.add_parser("volcano", help="Plot an existing full DEG CSV without running DESeq2.")
    volcano_parser.add_argument("--deg-result-file", type=Path, required=True)
    volcano_parser.add_argument("--output-dir", type=Path, required=True)
    volcano_parser.add_argument("--padj-threshold", type=float, default=0.05)
    volcano_parser.add_argument("--log2fc-threshold", type=float, default=1.0)
    volcano_parser.add_argument("--top-n-labels", type=int, default=10)
    volcano_parser.add_argument("--timeout", type=float, default=600)
    volcano_parser.add_argument("--rscript", default="Rscript", help="Base Rscript executable; DESeq2 is not used.")
    heatmap_parser = subparsers.add_parser("heatmap", help="Plot Top DEGs from VST expression or raw counts via shared VST.")
    expression = heatmap_parser.add_mutually_exclusive_group(required=True)
    expression.add_argument("--normalized-expression", type=Path, help="Caller-supplied VST CSV, not raw counts/TPM/FPKM.")
    expression.add_argument("--counts", type=Path, help="Raw counts to transform through shared blind VST.")
    heatmap_parser.add_argument("--deg-result-file", type=Path, required=True)
    heatmap_parser.add_argument("--metadata", type=Path, required=True)
    heatmap_parser.add_argument("--group-column", required=True)
    heatmap_parser.add_argument("--output-dir", type=Path, required=True)
    heatmap_parser.add_argument("--top-n", type=int, default=50)
    heatmap_parser.add_argument("--padj-threshold", type=float, default=0.05)
    heatmap_parser.add_argument("--log2fc-threshold", type=float, default=1.0)
    heatmap_parser.add_argument("--timeout", type=float, default=600)
    heatmap_parser.add_argument("--rscript", default="Rscript")
    go_parser = subparsers.add_parser("go", help="GO over-representation using user mapping and explicit analysis background.")
    go_parser.add_argument("--gene-list", type=Path, required=True)
    go_parser.add_argument("--gene-to-go-mapping", type=Path, required=True)
    background = go_parser.add_mutually_exclusive_group()
    background.add_argument("--universe", type=Path, help="Explicit eligible background gene list.")
    background.add_argument("--analysis-genes", type=Path, help="Full DEG CSV or eligible analysis genes; intersected with GO mapping.")
    go_parser.add_argument("--output-dir", type=Path, required=True)
    go_parser.add_argument("--pvalue-threshold", type=float, default=0.05)
    go_parser.add_argument("--padj-threshold", type=float, default=0.05)
    go_parser.add_argument("--ontology", choices=("ALL", "BP", "MF", "CC"), default="ALL")
    go_parser.add_argument("--mapping-sheet", help="Required for multi-sheet XLSX annotation.")
    go_parser.add_argument("--timeout", type=float, default=600)
    go_parser.add_argument("--rscript", default="Rscript")
    kegg_parser = subparsers.add_parser("kegg", help="Offline KEGG ORA from explicit gene-to-pathway mappings.")
    kegg_parser.add_argument("--gene-list", type=Path, required=True)
    kegg_parser.add_argument("--gene-to-pathway-mapping", type=Path, required=True)
    kegg_background = kegg_parser.add_mutually_exclusive_group()
    kegg_background.add_argument("--universe", type=Path, help="Explicit eligible RNA-seq background genes.")
    kegg_background.add_argument("--analysis-genes", type=Path, help="Full DEG CSV or eligible analysis gene list.")
    kegg_parser.add_argument("--output-dir", type=Path, required=True)
    kegg_parser.add_argument("--padj-threshold", type=float, default=0.05)
    kegg_parser.add_argument("--pvalue-threshold", type=float, default=1.0)
    kegg_parser.add_argument("--mapping-sheet")
    kegg_parser.add_argument("--timeout", type=float, default=600)
    kegg_parser.add_argument("--rscript", default="Rscript")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    default_config_path: Path | None = None,
) -> int:
    """Load configuration, initialize logging, and report readiness."""

    args = None
    try:
        args = build_parser(default_config_path).parse_args(argv)
        config = load_config(args.config)
        logger = create_logger(config.name, config.log_level)
    except SkillError as error:
        _emit_json(error.to_dict())
        return 2
    except (OSError, ValueError, tomllib.TOMLDecodeError) as cause:
        error = SkillError("CONFIG_ERROR", "Unable to load application configuration.",
                           details={"reason": str(cause)},
                           operation=_operation(getattr(args, "command", None)))
        _emit_json(error.to_dict())
        return 2

    if args.command == "deg":
        try:
            result = run_deg(
                count_matrix_path=args.counts,
                metadata_path=args.metadata,
                treatment=args.treatment,
                control=args.control,
                output_dir=args.output_dir,
                alpha=args.alpha,
                lfc_threshold=args.lfc_threshold,
                timeout=args.timeout,
                rscript_executable=args.rscript,
            )
        except DegRunError as error:
            logger.error("DEG run failed: [%s] %s", error.code, error)
            _emit_json(error.to_dict())
            return 1

        _emit_json(result.to_dict())
        return 0

    if args.command == "pca":
        try:
            result = run_pca(args.counts, args.metadata, args.group_column, args.output_dir,
                             timeout=args.timeout, rscript_executable=args.rscript)
        except SkillError as error:
            logger.error("PCA run failed: [%s] %s", error.code, error)
            _emit_json(error.to_dict())
            return 1
        _emit_json(result.to_dict())
        return 0

    if args.command == "volcano":
        try:
            result = run_volcano(args.deg_result_file, args.output_dir, args.padj_threshold,
                                 args.log2fc_threshold, args.top_n_labels,
                                 timeout=args.timeout, rscript_executable=args.rscript)
        except SkillError as error:
            logger.error("Volcano run failed: [%s] %s", error.code, error)
            _emit_json(error.to_dict())
            return 1
        _emit_json(result.to_dict())
        return 0

    if args.command == "heatmap":
        try:
            result = run_heatmap(args.deg_result_file, args.metadata, args.group_column, args.output_dir,
                                 normalized_expression=args.normalized_expression, raw_counts=args.counts,
                                 top_n=args.top_n, padj_threshold=args.padj_threshold, log2fc_threshold=args.log2fc_threshold,
                                 timeout=args.timeout, rscript_executable=args.rscript)
        except SkillError as error:
            logger.error("Heatmap run failed: [%s] %s", error.code, error)
            _emit_json(error.to_dict())
            return 1
        _emit_json(result.to_dict())
        return 0

    if args.command == "go":
        try:
            result = run_go_enrichment(args.gene_list, args.gene_to_go_mapping, args.output_dir,
                                       universe=args.universe, analysis_genes=args.analysis_genes,
                                       pvalue_threshold=args.pvalue_threshold, padj_threshold=args.padj_threshold,
                                       ontology=args.ontology, mapping_sheet=args.mapping_sheet,
                                       timeout=args.timeout, rscript_executable=args.rscript)
        except SkillError as error:
            logger.error("GO run failed: [%s] %s", error.code, error)
            _emit_json(error.to_dict())
            return 1
        _emit_json(result.to_dict())
        return 0

    if args.command == "kegg":
        try:
            result = run_kegg_enrichment(args.gene_list, args.gene_to_pathway_mapping, args.output_dir,
                                         universe=args.universe, analysis_genes=args.analysis_genes,
                                         padj_threshold=args.padj_threshold, pvalue_threshold=args.pvalue_threshold,
                                         mapping_sheet=args.mapping_sheet, timeout=args.timeout, rscript_executable=args.rscript)
        except SkillError as error:
            logger.error("KEGG run failed: [%s] %s", error.code, error)
            _emit_json(error.to_dict())
            return 1
        _emit_json(result.to_dict())
        return 0

    logger.info(
        "Project started successfully | environment=%s | config=%s",
        config.environment,
        config.source_path,
    )
    logger.info("Use the deg, pca, volcano, heatmap, go or kegg subcommand to run a skill.")
    return 0
