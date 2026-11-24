"""Shared, explicit enrichment policy; no statistical calculations in Python."""

from typing import Any

from bulk_rnaseq.enrichment_inputs import EnrichmentInputs


def engine_provenance(prepared: EnrichmentInputs) -> dict[str, Any]:
    """Record engine arguments separately from public post-filter thresholds."""
    return {
        "engine": "clusterProfiler", "function": "enricher", "pAdjustMethod": "BH",
        "pvalueCutoff": 1.0, "qvalueCutoff": 1.0,
        "minGSSize": 1, "maxGSSize": len(prepared.universe),
        "qvalue_filter": "not applied; use native unfiltered result slot",
        "multiple_testing_family": "mapped terms with positive query overlap",
        "universe_size": prepared.metrics["n_background_input"],
        "mapped_universe_size": len(prepared.universe),
        "target_gene_count": prepared.metrics["n_query_input"],
        "mapped_target_gene_count": len(prepared.query),
    }
