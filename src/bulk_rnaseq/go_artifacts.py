"""GO artifact schema over the shared ORA verifier."""

from pathlib import Path
from bulk_rnaseq.go_data import GoInputs
from bulk_rnaseq.go_mapping import GoMapping
from bulk_rnaseq.enrichment_artifacts import verify_enrichment_outputs

GO_COLUMNS = ("go_id", "description", "ontology", "overlap_count", "query_size", "term_size",
              "universe_size", "gene_ratio", "bg_ratio", "fold_enrichment", "pvalue", "padj", "genes")
GO_FILES = ("go_enrichment.csv", "go_all_results.csv", "go_dotplot.png", "go_barplot.png",
            "query_used.csv", "universe_used.csv", "mapping_used.csv")


def verify_go_outputs(stage: Path, prepared: GoInputs, mapping: GoMapping,
                      pvalue_threshold: float, padj_threshold: float, stdout: str) -> tuple[int, dict[str, str]]:
    return verify_enrichment_outputs(stage, prepared, mapping.terms, pvalue_threshold, padj_threshold, stdout,
                                     prefix="go", id_column="go_id", columns=GO_COLUMNS, artifact_files=GO_FILES,
                                     has_ontology=True)
