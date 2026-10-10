"""Shared ORA artifact verification; never recomputes statistical tests."""

import csv
import math
from pathlib import Path
from threading import Lock

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.enrichment_inputs import EnrichmentInputs
from bulk_rnaseq.plot_artifacts import check_png


_CSV_LIMIT_LOCK = Lock()


def verify_enrichment_outputs(stage: Path, prepared: EnrichmentInputs, terms: dict[str, tuple[str, str]],
                              pvalue_threshold: float, padj_threshold: float, stdout: str, *,
                              prefix: str, id_column: str, columns: tuple[str, ...],
                              artifact_files: tuple[str, ...], has_ontology: bool = False) -> tuple[int, dict[str, str]]:
    missing = [name for name in artifact_files if not (stage / name).is_file()]
    if missing:
        raise SkillError("OUTPUT_MISSING", "Enrichment run did not generate required artifacts.", details={"files": missing})
    try:
        # R serializes all overlapping query IDs into one semicolon-separated cell.
        # Its legitimate length can exceed Python csv's default 128 KiB limit.
        field_limit = max(sum(len(gene) for gene in prepared.query) + max(0, len(prepared.query) - 1),
                          max((len(value) for term, metadata in terms.items() for value in (term, *metadata)), default=0))

        def read(name: str) -> list[dict[str, str]]:
            # field_size_limit is process-global: serialize our temporary changes
            # and restore the caller's setting even when parsing raises an error.
            with _CSV_LIMIT_LOCK:
                previous = csv.field_size_limit()
                try:
                    csv.field_size_limit(max(previous, field_limit))
                    with (stage / name).open(encoding="utf-8-sig", newline="") as handle:
                        reader = csv.DictReader(handle)
                        if tuple(reader.fieldnames or ()) != columns:
                            raise ValueError("Unexpected Enrichment output columns")
                        result = list(reader)
                        if any(None in row or None in row.values() for row in result):
                            raise ValueError("Malformed Enrichment output row")
                        return result
                finally:
                    csv.field_size_limit(previous)

        rows = read(f"{prefix}_all_results.csv")
        if len(rows) != len(prepared.term_genes) or {r[id_column] for r in rows} != set(prepared.term_genes):
            raise ValueError("Tested terms differ from effective universe annotations")
        n, N = len(prepared.query), len(prepared.universe)
        for row in rows:
            members = prepared.term_genes[row[id_column]]
            hit = members & prepared.query
            counts = (len(hit), n, len(members), N)
            if tuple(int(row[key]) for key in ("overlap_count", "query_size", "term_size", "universe_size")) != counts:
                raise ValueError("Enrichment membership counts disagree with inputs")
            reported_genes = list(filter(None, row["genes"].split(";")))
            if len(reported_genes) != len(hit) or set(reported_genes) != hit:
                raise ValueError("Overlapping gene IDs disagree with inputs")
            name, ontology = terms[row[id_column]]
            if row["description"] != (name or row[id_column]) or (has_ontology and row["ontology"] != (ontology or "UNKNOWN")):
                raise ValueError("Enrichment term metadata disagree with mapping")
            for key, expected in (("gene_ratio", len(hit) / n), ("bg_ratio", len(members) / N),
                                  ("fold_enrichment", len(hit) / n / (len(members) / N))):
                if not math.isclose(float(row[key]), expected, rel_tol=1e-10, abs_tol=1e-15):
                    raise ValueError("Enrichment ratios disagree with input counts")
            p, q = float(row["pvalue"]), float(row["padj"])
            if not (math.isfinite(p) and math.isfinite(q) and 0 <= p <= 1 and 0 <= q <= 1 and q + 1e-14 >= p):
                raise ValueError("Invalid enrichment probabilities")
        enriched = read(f"{prefix}_enrichment.csv")
        expected_rows = [r for r in rows if int(r["overlap_count"]) > 0 and float(r["pvalue"]) <= pvalue_threshold
                         and float(r["padj"]) <= padj_threshold]
        if enriched != expected_rows:
            raise ValueError("Filtered enrichment does not match thresholds/all results")
        for name in (f"{prefix}_dotplot.png", f"{prefix}_barplot.png"):
            check_png(stage / name)
        fields = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
        if (not fields["R_VERSION"].strip() or not fields["STATS_VERSION"].strip()
                or int(fields["N_TERMS_TESTED"]) != len(rows) or int(fields["N_ENRICHED"]) != len(enriched)):
            raise ValueError("R provenance disagrees with artifacts")
    except (OSError, UnicodeError, csv.Error, ValueError, KeyError, TypeError) as cause:
        raise SkillError("OUTPUT_INVALID", "Enrichment artifact verification failed.", details={"reason": str(cause)}) from cause
    return len(enriched), {"r_version": fields["R_VERSION"], "stats_version": fields["STATS_VERSION"]}
