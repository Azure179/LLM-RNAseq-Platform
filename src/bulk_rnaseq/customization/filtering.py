"""Literal annotation matching and deterministic sorting, never biological inference."""

import csv
from pathlib import Path
from typing import Any
from bulk_rnaseq.deg_data import read_deg_results
from bulk_rnaseq.enrichment_inputs import table_rows, gene_identifier
from bulk_rnaseq.exceptions import SkillError
from .models import AnnotationFilterOptions


def annotation_categories(path: Path) -> set[str]:
    """Discover declared columns only. Actual filtering still fully parses the table."""
    with table_rows(path, None) as rows:
        for row in rows:
            if not row or not any(v not in (None, "") for v in row):
                continue
            fields = {str(v or "").strip() for v in row}
            if str(row[0]).startswith("#") and str(row[0]) != "#query":
                continue
            if "annotation" in fields:
                # Long-form source values cannot be inferred from the header.
                return {kind for entries in read_annotations(path).values() for kind, _ in entries}
            return ({"gene_description"} if fields & {"Description", "description", "gene_description"} else set()) | (
                {"GO"} if fields & {"go_id", "GOs"} else set()) | (
                {"KEGG"} if fields & {"pathway_id", "KEGG_Pathway"} else set())
    return set()


def read_annotations(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Accept eggNOG, custom GO/KEGG mapping or long gene_id/source/annotation TSV."""
    found: dict[str, list[tuple[str, str]]] = {}
    header = None
    with table_rows(path, None) as rows:
        for number, row in enumerate(rows, 1):
            if not row or all(v in (None, "") for v in row):
                continue
            if header is None:
                names = [str(v or "").strip() for v in row]
                if names[0] == "#query":
                    names[0] = "query"
                if names[0].startswith("#"):
                    continue
                if not ({"gene_id", "query"} & set(names)) or len(set(names)) != len(names) or "" in names:
                    raise SkillError("ANNOTATION_FORMAT_ERROR", "Annotation requires a gene_id or eggNOG query column.")
                if not set(names) & {"Description", "description", "gene_description", "go_id", "GOs", "pathway_id", "KEGG_Pathway", "annotation"}:
                    raise SkillError("ANNOTATION_FORMAT_ERROR", "No supported annotation fields were found.")
                header = names
                continue
            if str(row[0]).startswith("#"):
                continue
            if len(row) != len(header):
                raise SkillError("ANNOTATION_FORMAT_ERROR", "Annotation row width differs from header.")
            values = {k: str(v or "").strip() for k, v in zip(header, row)}
            gene = gene_identifier(values.get("gene_id", values.get("query")), number)
            entries = found.setdefault(gene, [])
            if "annotation" in values:
                category = values.get("source")
                if category not in {"gene_description", "GO", "KEGG"}:
                    raise SkillError("ANNOTATION_FORMAT_ERROR", "Long annotations require source=gene_description/GO/KEGG.")
                pairs = [(category, values["annotation"])]
            else:
                pairs = [("gene_description", values.get("gene_description", values.get("Description", values.get("description", "")))),
                         ("GO", " ".join([values.get("go_id", values.get("GOs", "")), values.get("go_name", "")])),
                         ("KEGG", " ".join([values.get("pathway_id", values.get("KEGG_Pathway", "")), values.get("pathway_name", "")]))]
            for category, text in pairs:
                if text.strip().upper() not in {"", "-", "NA", "N/A", "NAN"} and (category, text.strip()) not in entries:
                    entries.append((category, text.strip()))
    if header is None:
        raise SkillError("ANNOTATION_FORMAT_ERROR", "Empty annotation table.")
    return found


def filter_annotations(source: Path, annotation_files: list[Path], output: Path,
                       options: AnnotationFilterOptions) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Source is the existing DEG filtered_csv, already significant by source thresholds."""
    try:
        records = read_deg_results(source)
    except SkillError as error:
        if error.code != "DEG_EMPTY":
            raise
        records = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header, original = reader.fieldnames, {row["gene_id"]: row for row in reader}
    annotations: dict[str, list[tuple[str, str]]] = {}
    for path in annotation_files:
        for gene, rows in read_annotations(path).items():
            annotations.setdefault(gene, []).extend(rows)
    annotated_ids = {gene for gene, rows in annotations.items()
                     if any(not options.annotation_sources or kind in options.annotation_sources for kind, _ in rows)}
    if records and options.keywords and not set(original).intersection(annotated_ids):
        raise SkillError("ANNOTATION_ID_MISMATCH", "Annotation and DEG IDs have no intersection; no ID conversion or gene inference was applied.")
    selected, matches = [], []
    words = [word.casefold() for word in options.keywords]
    for record in records:
        if record.log2fc is None or record.padj is None:
            raise SkillError("INVALID_UPSTREAM_DEG", "Significant DEG source must contain finite statistics.")
        if options.gene_subset == "up" and record.log2fc <= 0 or options.gene_subset == "down" and record.log2fc >= 0:
            continue
        gene = record.original["gene_id"]
        evidence = [(kind, text, [options.keywords[i] for i, word in enumerate(words) if word in text.casefold()])
                    for kind, text in annotations.get(gene, []) if kind in options.annotation_sources]
        hit = {word for _, _, keys in evidence for word in keys}
        accepted = not words or (bool(hit) if options.match_mode == "any" else set(options.keywords) <= hit)
        if accepted:
            selected.append(record)
            matches.extend({"gene_id": gene, "source": kind, "annotation": text, "matched_keywords": ";".join(keys)}
                           for kind, text, keys in evidence if keys)
    def sort_key(record: Any) -> Any:
        return {"padj": record.padj, "log2fc": record.log2fc, "abs_log2fc": abs(record.log2fc), "gene_id": record.original["gene_id"]}[options.order_by]
    selected.sort(key=lambda r: r.original["gene_id"])
    selected.sort(key=sort_key, reverse=options.descending)
    for filename, fields, rows in (("filtered_deg.csv", header, [original[r.original["gene_id"]] for r in selected]),
                                   ("matched_annotations.csv", ["gene_id", "source", "annotation", "matched_keywords"], matches)):
        with (output / filename).open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        # Validate the actual persisted CSV, including row order and original values.
        with (output / filename).open(encoding="utf-8", newline="") as handle:
            persisted = csv.DictReader(handle)
            if persisted.fieldnames != fields or list(persisted) != rows:
                raise SkillError("CUSTOM_OUTPUT_VALIDATION_FAILED", "Filtered CSV differs from the verified selection.")
    warnings = []
    mapped = len(set(original).intersection(annotated_ids))
    if options.keywords:
        warnings.append({"code": "LITERAL_ANNOTATION_MATCH", "message": "Literal case-insensitive text matching only; no ontology expansion, translation inference, or gene-ID alias conversion."})
        if len(original) and mapped / len(original) < .5:
            warnings.append({"code": "LOW_ANNOTATION_COVERAGE", "message": "Fewer than half of source DEG IDs have annotation rows."})
    return {"n_source_genes": len(records), "n_matched_genes": len(selected), "n_annotated_genes": mapped,
            "n_evidence_rows": len(matches)}, warnings
