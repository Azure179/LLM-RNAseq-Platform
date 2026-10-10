"""User-requested Asc naming-rule adapter, not a reference-validated annotation."""

import argparse
from collections import Counter
from collections.abc import Iterator
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from run_real_data_test import ROOT, coverage, now, prepare, read_json
from prepare_inputs import local_path, write_table
from bulk_rnaseq.enrichment_inputs import gene_identifier, read_gene_list, table_rows
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.run_io import reserve_output, sha256, write_json

RULE = r"rna-(Asc[0-9]+)\.([1-9][0-9]*)"


def candidate_gene(query: str) -> str | None:
    match = re.fullmatch(RULE, query)
    return "gene-" + match[1] if match else None


def queries(path: Path) -> Iterator[str]:
    """Stream only the query column; GO/KEGG parsing stays in existing readers."""
    header: list[str] | None = None
    seen: set[str] = set()
    with table_rows(path) as rows:
        for number, row in enumerate(rows, 1):
            if not row:
                continue
            if header is None:
                candidate = [str(value) for value in row]
                candidate[0] = candidate[0].removeprefix("#")
                if {"query", "GOs", "KEGG_Pathway"} <= set(candidate):
                    if len(candidate) != len(set(candidate)):
                        raise SkillError("MAPPING_FORMAT_ERROR", "Duplicate annotation column names.")
                    header = candidate
                elif str(row[0]).startswith("#"):
                    continue
                else:
                    raise SkillError("MAPPING_FORMAT_ERROR", "Expected eggNOG query/GOs/KEGG_Pathway header.")
                continue
            if str(row[0]).startswith("##"):
                continue
            if len(row) != len(header):
                raise SkillError("MAPPING_FORMAT_ERROR", "Annotation row width differs from header.", details={"row": number})
            query = gene_identifier(row[header.index("query")], number)
            if query in seen:
                raise SkillError("DUPLICATE_QUERY", "Repeated eggNOG query ID requires review before mapping.", details={"query": query})
            seen.add(query)
            yield query
    if header is None or not seen:
        raise SkillError("MAPPING_FORMAT_ERROR", "No eggNOG query records found.")


def build_crosswalk(counts: Path, annotations: Path, output: Path) -> dict[str, Any]:
    genes, duplicates = read_gene_list(counts)
    if duplicates:
        raise SkillError("DUPLICATE_ID", "Count gene IDs must be unique.")
    before = {"counts": sha256(counts), "annotations": sha256(annotations)}
    audit: list[list[str]] = []
    pairs: list[list[str]] = []
    multiplicity: Counter[str] = Counter()
    for query in queries(annotations):
        gene = candidate_gene(query)
        status = "unsupported_query_format" if gene is None else "gene_not_in_counts" if gene not in genes else "mapped"
        audit.append([query, gene or "", status])
        if status == "mapped":
            pairs.append([gene, query])
            multiplicity[gene] += 1
    if before != {"counts": sha256(counts), "annotations": sha256(annotations)}:
        raise SkillError("SOURCE_CHANGED", "Input changed while building the crosswalk.")
    if not pairs:
        raise SkillError("NO_MAPPED_GENES", "The requested naming rule has no count-gene matches.")
    with reserve_output(output, str(uuid4())):
        write_table(output / "gene_id_mapping.tsv", ["gene_id", "query"], sorted(pairs), "\t")
        write_table(output / "query_mapping_audit.tsv", ["query", "candidate_gene_id", "status"], audit, "\t")
        write_table(output / "count_genes_without_query.tsv", ["gene_id"], ([gene] for gene in sorted(genes - multiplicity.keys())), "\t")
        write_table(output / "multi_transcript_genes.tsv", ["gene_id", "n_queries"],
                    ([gene, count] for gene, count in sorted(multiplicity.items()) if count > 1), "\t")
        report = {"status": "succeeded", "created_at": now(), "basis": "user_requested_name_rule", "reference_validated": False,
                  "rule": {"fullmatch": RULE, "replacement": "gene- + captured Asc identifier", "preserve_leading_zeros": True},
                  "aggregation": "union/deduplicate gene-term associations in existing readers; never sum counts",
                  "n_queries": len(audit), "query_status_counts": dict(Counter(row[2] for row in audit)),
                  "count_gene_coverage": coverage(set(multiplicity), set(genes)),
                  "n_multi_transcript_genes": sum(count > 1 for count in multiplicity.values()),
                  "source_sha256": before, "artifact_sha256": {path.name: sha256(path) for path in output.glob("*.tsv")}}
        write_json(output / "summary.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-inputs", action="store_true", help="Prepare new standard GO/KEGG inputs using the existing adapter; no R or analysis")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        base = read_json(ROOT / "config/real_test_config.json")
        config = read_json(ROOT / "config/real_test_config.asc_mapped.json")
        report = build_crosswalk(local_path(ROOT, base["count_matrix"]), local_path(ROOT, base["source_annotations"]),
                                 local_path(ROOT, config["gene_id_mapping"]).parent)
        logging.info("Crosswalk: %s", json.dumps(report, ensure_ascii=False))
        if args.prepare_inputs:
            logs = ROOT / "logs"
            logs.mkdir(exist_ok=True)
            with run_logger(logs / "asc_mapping_preparation.log", str(uuid4())) as logger:
                prepared = prepare(config, False, logger)
            if prepared.get("annotation_error"):
                raise SkillError("ANNOTATION_PREPARATION_FAILED", "See asc_mapping_preparation.log.", details=prepared["annotation_error"])
            annotation = prepared["annotation"]
            deg, _ = read_gene_list(ROOT / "results/02_deg/deg_filtered.csv")
            genes = set(prepared["data"]["genes"])
            checks = {"basis": report["basis"], "reference_validated": False}
            for name, key in (("go_bp", "go_usable_genes"), ("kegg", "kegg_genes")):
                mapped = set(annotation[key])
                checks[name] = {"count_matrix": coverage(mapped, genes), "existing_deg": coverage(mapped, set(deg))}
            checks["deg_source_sha256"] = sha256(ROOT / "results/02_deg/deg_filtered.csv")
            write_json(local_path(ROOT, config["prepared_dir"]) / "mapping_coverage.json", checks)
            logging.info("Prepared mapping coverage: %s", json.dumps(checks))
        return 0
    except Exception as cause:
        error = cause if isinstance(cause, SkillError) else SkillError("ID_MAPPING_FAILED", "Crosswalk preparation failed.", details={"reason": str(cause)})
        error.operation = "asc_id_mapping"
        logging.error("%s", json.dumps(error.to_dict(), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
