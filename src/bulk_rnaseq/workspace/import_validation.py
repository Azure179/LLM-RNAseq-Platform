"""Import adapters and summaries; all biological parsing reuses shared validators."""

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
import csv
from pathlib import Path
from typing import Any

from bulk_rnaseq.enrichment_inputs import table_rows
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.go_mapping import read_go_mapping
from bulk_rnaseq.kegg_mapping import read_kegg_mapping
from bulk_rnaseq.validation import require_input_file, scan_count_matrix, scan_metadata
from bulk_rnaseq.workspace.file_models import FileInspection, FileRole
from bulk_rnaseq.workspace.delimited_import import (
    GENE_HEADERS, SAMPLE_HEADERS, GROUP_HEADERS, TEXT_TABLE_SUFFIXES, delimited_rows,
)


@contextmanager
def inspection_rows(path: Path, sheet: str | None) -> Iterator[Iterator[list[Any]]]:
    if path.suffix.lower() in TEXT_TABLE_SUFFIXES and sheet is None:
        with delimited_rows(path) as (rows, _):
            yield rows
    else:
        with table_rows(path, sheet) as rows:
            yield rows


def warning(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def inspect_file(path: Path, *, mapping_sheet: str | None = None) -> FileInspection:
    """Header-based deterministic hints, never confirmation or validation proof."""
    try:
        with inspection_rows(path, mapping_sheet) as iterator:
            header = []
            for index, row in enumerate(iterator):
                if index >= 100:
                    break
                if not row or not any(value is not None and value != "" for value in row):
                    continue
                header = [str(v or "") for v in row]
                if header[0] == "#query":
                    header[0] = "query"
                    break
                if header[0].startswith("#"):
                    header = []
                    continue
                break
        fields = set(header)
        normalized = {name.strip().casefold() for name in header}
        candidates = []
        if {"gene_id", "go_id"} <= fields or {"query", "GOs"} <= fields:
            candidates.append(FileRole.GO_MAPPING)
        if {"gene_id", "pathway_id"} <= fields or {"query", "KEGG_Pathway"} <= fields:
            candidates.append(FileRole.KEGG_MAPPING)
        if normalized & SAMPLE_HEADERS and normalized & GROUP_HEADERS:
            candidates.append(FileRole.METADATA)
        if header and header[0].strip().casefold() in GENE_HEADERS and len(header) >= 2 and not candidates:
            if not fields & {"log2FoldChange", "padj", "baseMean", "pvalue"}:
                candidates.append(FileRole.COUNT_MATRIX)
        return FileInspection(status="succeeded", candidates=candidates,
                              suggested_role=candidates[0] if len(candidates) == 1 else None,
                              confidence=.8 if len(candidates) == 1 else 0,
                              reasons=["Header-based heuristic only; user confirmation and full validation are required."] +
                                      (["Multiple roles are possible (e.g. eggNOG GO and KEGG)."] if len(candidates) > 1 else []),
                              columns=header)
    except SkillError as error:
        return FileInspection(status="failed", error_code=error.code, message=str(error))


detect_file_role = inspect_file


def prepare_table(source: Path, role: FileRole, destination: Path, *,
                  sample_column: str | None = None, group_column: str | None = None) -> list[dict[str, Any]]:
    """Write a separate CSV adapter; preserve original bytes, values and row order."""
    if source.suffix.lower() not in TEXT_TABLE_SUFFIXES:
        raise SkillError("IMPORT_FORMAT_UNSUPPORTED", "Counts/metadata require delimited text (CSV/TSV/TXT or text .xls), not binary Excel/FASTQ.")
    require_input_file(source)
    changes = []
    try:
        with delimited_rows(source) as (rows, delimiter), destination.open("w", encoding="utf-8", newline="") as handle:
            header = next(rows, None)
            if not header:
                raise SkillError("IMPORT_EMPTY_FILE", "Input requires a header and data rows.")
            converted = list(header)
            if role == FileRole.COUNT_MATRIX and header[0].strip().casefold() in GENE_HEADERS:
                converted[0] = "gene_id"
                if converted != header:
                    changes.append(warning("COUNT_HEADER_ADAPTED", "Gene column header standardized to gene_id; gene IDs and sample names are unchanged.",
                                           source_column=header[0], target_column="gene_id"))
            if role == FileRole.METADATA:
                samples = [name for name in header if name.strip().casefold() in SAMPLE_HEADERS]
                groups = [name for name in header if name.strip().casefold() in GROUP_HEADERS]
                if sample_column is None:
                    if len(samples) != 1:
                        raise SkillError("METADATA_FORMAT_ERROR", "Require one sample_id/sample column or explicitly select sample_column.")
                    sample_column = samples[0]
                if group_column is None:
                    conditions = [name for name in groups if name.strip().casefold() == "condition"]
                    choices = conditions or groups
                    if len(choices) > 1:
                        raise SkillError("AMBIGUOUS_METADATA_COLUMNS", "Multiple group columns match; explicitly select group_column.")
                    group_column = choices[0] if choices else None
                if sample_column not in header or group_column not in header or sample_column == group_column:
                    raise SkillError("GROUP_COLUMN_NOT_FOUND", "Selected sample/group columns must exist and be distinct.")
                converted[header.index(sample_column)] = "sample_id"
                converted[header.index(group_column)] = "condition"
                if len(converted) != len(set(converted)):
                    raise SkillError("AMBIGUOUS_METADATA_COLUMNS", "Column adaptation would produce duplicate names.")
                if converted != header:
                    changes.append(warning("METADATA_COLUMNS_ADAPTED", "A separate analysis copy uses sample_id/condition.",
                                           sample_column=sample_column, group_column=group_column))
            writer = csv.writer(handle)
            writer.writerow(converted)
            writer.writerows(rows)
            expected = "," if source.suffix.lower() == ".csv" else "\t"
            if delimiter != "," or delimiter != expected:
                changes.append(warning("DELIMITER_DETECTED", "Input delimiter detected from content; a separate comma-separated analysis copy was generated.",
                                       delimiter={",": "comma", "\t": "tab", ";": "semicolon"}[delimiter],
                                       extension=source.suffix.lower()))
    except csv.Error:
        raise SkillError("IMPORT_FORMAT_ERROR", "Malformed delimited table.") from None
    if source.suffix.lower() == ".tsv":
        changes.append(warning("TSV_ADAPTED", "A separate CSV copy was prepared for existing Skills; original TSV is retained."))
    return changes


def validate_file(path: Path, role: FileRole, *, mapping_sheet: str | None = None) -> dict[str, Any]:
    """Small public summary plus private gene/sample indices; never loads matrices twice."""
    warnings = []
    indices: dict[str, Any] = {}
    if role == FileRole.COUNT_MATRIX:
        data = scan_count_matrix(path, min_samples=1)
        summary = {"n_genes": len(data["gene_ids"]), "n_samples": len(data["sample_names"]),
                   "sample_names": list(data["sample_names"]), "duplicated_gene_ids": 0, "missing_values": 0,
                   "integer_check": "PASS", "min_count": data["min_count"], "max_count": data["max_count"]}
        indices["gene_ids"] = sorted(data["gene_ids"])
        warnings.append(warning("RAW_COUNT_ORIGIN_NOT_VERIFIED", "Integer values alone cannot prove raw-count origin. Confirm these are not TPM/FPKM/normalized values."))
    elif role == FileRole.METADATA:
        groups = scan_metadata(path, "condition")
        counts = dict(Counter(groups.values()))
        summary = {"n_samples": len(groups), "sample_names": list(groups), "n_groups": len(counts),
                   "group_names": list(counts), "replicates_per_group": counts, "missing_values": 0,
                   "group_column": "condition"}
    else:
        reader = read_go_mapping if role == FileRole.GO_MAPPING else read_kegg_mapping
        mapping = reader(path, mapping_sheet)
        indices["gene_ids"] = sorted({gene for gene, _ in mapping.pairs})
        summary = {"mapped_genes": len(indices["gene_ids"]), "n_terms": len(mapping.terms),
                   "n_rows": mapping.n_rows, "n_pairs": len(mapping.pairs), "format": mapping.format,
                   "unannotated_rows": mapping.n_unannotated_rows, "duplicate_pairs": mapping.n_duplicate_pairs}
        if role == FileRole.GO_MAPPING:
            summary["ontology_complete"] = all(value[1] for value in mapping.terms.values())
        if mapping.n_unannotated_rows:
            warnings.append(warning("UNANNOTATED_MAPPING_ROWS", "Unannotated rows were excluded by the existing mapping reader.", count=mapping.n_unannotated_rows))
        if mapping.n_duplicate_pairs:
            warnings.append(warning("DUPLICATE_ASSOCIATIONS", "Duplicate associations are allowed and counted once by the existing reader.", count=mapping.n_duplicate_pairs))
    return {"summary": summary, "indices": indices, "warnings": warnings, "validation": {"format": "PASS"}}


def match_samples(counts: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    left, right = counts["summary"]["sample_names"], metadata["summary"]["sample_names"]
    return {"exact_match": set(left) == set(right), "order_difference": left != right and set(left) == set(right),
            "missing_in_metadata": sorted(set(left) - set(right)), "extra_in_metadata": sorted(set(right) - set(left))}


def enrich_file_info(role: FileRole, record: dict[str, Any], records: dict[FileRole, dict[str, Any]]) -> dict[str, Any]:
    """Compute dependency-sensitive summaries from current verified indices, not stale cached coverage."""
    info = {"summary": dict(record["summary"]), "validation": dict(record["validation"]),
            "warnings": list(record["warnings"])}
    counts, meta = records.get(FileRole.COUNT_MATRIX), records.get(FileRole.METADATA)
    if role in {FileRole.COUNT_MATRIX, FileRole.METADATA} and counts and meta:
        matching = match_samples(counts, meta)
        info["validation"]["sample_matching"] = matching
        if not matching["exact_match"]:
            raise SkillError("SAMPLE_MISMATCH", "Count and metadata sample sets differ.", details=matching)
        if matching["order_difference"]:
            info["warnings"].append(warning("SAMPLE_ORDER_DIFFERS", "Sample sets match; existing Skills align by sample name, not row position."))
    if role in {FileRole.GO_MAPPING, FileRole.KEGG_MAPPING}:
        if counts:
            genes = set(counts["indices"]["gene_ids"])
            mapped = len(genes.intersection(record["indices"]["gene_ids"]))
            fraction = mapped / len(genes)
            info["summary"]["coverage"] = {"mapped_genes": mapped, "total_count_genes": len(genes), "fraction": fraction}
            if fraction < .5:
                info["warnings"].append(warning("LOW_MAPPING_COVERAGE", "Less than half of count genes are mapped. Check gene ID compatibility; no gene IDs were changed.", fraction=fraction))
        else:
            info["summary"]["coverage"] = None
    return info


def check_record_shape(record: dict[str, Any], role: FileRole) -> None:
    """Reject corrupt cached indices before readiness uses them (not a signature).

    Counts themselves are still validated by Skills before execution. These local
    records are trusted project metadata, not proof against malicious editing.
    """
    summary, indices = record["summary"], record["indices"]
    if not isinstance(summary, dict) or not isinstance(indices, dict) or not isinstance(record["validation"], dict):
        raise ValueError("Invalid import summary")
    if not isinstance(record["warnings"], list) or any(not isinstance(w, dict) or not isinstance(w.get("code"), str)
                                                     or not isinstance(w.get("message"), str) for w in record["warnings"]):
        raise ValueError("Invalid warning list")
    if record["mapping_sheet"] is not None and not isinstance(record["mapping_sheet"], str):
        raise ValueError("Invalid sheet")
    if role == FileRole.METADATA:
        if not isinstance(summary["group_column"], str) or not isinstance(summary["replicates_per_group"], dict):
            raise ValueError("Invalid metadata record")
    else:
        genes = indices["gene_ids"]
        if not isinstance(genes, list) or not genes or any(not isinstance(g, str) or not g for g in genes):
            raise ValueError("Invalid gene index")
    if role in {FileRole.COUNT_MATRIX, FileRole.METADATA}:
        samples = summary["sample_names"]
        if not isinstance(samples, list) or not samples or any(not isinstance(s, str) or not s for s in samples):
            raise ValueError("Invalid sample index")
        if len(set(samples)) != len(samples) or summary["n_samples"] != len(samples):
            raise ValueError("Inconsistent sample index")
