"""GO-specific ontology selection over shared enrichment inputs."""

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.go_mapping import GoMapping
from bulk_rnaseq.enrichment_inputs import EnrichmentInputs as GoInputs, prepare_enrichment, read_gene_list
from bulk_rnaseq.enrichment_inputs import validate_enrichment_parameters


def validate_go_parameters(pvalue: float, padj: float, ontology: str, timeout: float) -> None:
    validate_enrichment_parameters(pvalue, padj, timeout)
    if ontology not in ("ALL", "BP", "MF", "CC"):
        raise SkillError("INVALID_PARAMETER", "Require thresholds in [0,1], timeout > 0 and ontology ALL/BP/MF/CC.")


def prepare_go(query: frozenset[str], background: frozenset[str], mapping: GoMapping, ontology: str) -> GoInputs:
    if ontology != "ALL" and any(not meta[1] for meta in mapping.terms.values()):
        raise SkillError("ONTOLOGY_INFORMATION_REQUIRED", "BP/MF/CC filtering requires ontology annotation for every GO term; use ALL for eggNOG.")
    pairs = frozenset((gene, term) for gene, term in mapping.pairs
                      if ontology == "ALL" or mapping.terms[term][1] == ontology)
    return prepare_enrichment(query, background, pairs)
