"""Explicit gene-to-pathway input; KO IDs are not pathway IDs."""

from dataclasses import dataclass
from pathlib import Path
import re

from bulk_rnaseq.enrichment_inputs import gene_identifier, table_rows
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import MISSING


@dataclass(frozen=True)
class KeggMapping:
    pairs: frozenset[tuple[str, str]]
    terms: dict[str, tuple[str, str]]
    aliases: frozenset[tuple[str, str]]
    namespace: str
    format: str
    n_rows: int
    n_unannotated_rows: int
    n_duplicate_pairs: int


def normalize_pathway(value: str) -> tuple[str, str]:
    """Normalize only documented reference representations; never infer a species."""
    bare = value.removeprefix("path:")
    match = re.fullmatch(r"([a-z]{2,4})([0-9]{5})", bare)
    if not match:
        raise SkillError("INVALID_PATHWAY_ID", "Expected map/ko or organism-prefixed pathway ID; KO K numbers and modules are not pathways.",
                         details={"value": value})
    prefix, number = match.groups()
    if prefix in {"ec", "rn"}:
        raise SkillError("UNSUPPORTED_PATHWAY_NAMESPACE", "EC/reaction pathway representations are not supported; provide gene-to-map or gene-to-organism-pathway associations.")
    if prefix in {"map", "ko"}:
        return "map" + number, "map"
    return bare, prefix


def read_kegg_mapping(path: Path, sheet: str | None = None) -> KeggMapping:
    pairs: set[tuple[str, str]] = set()
    terms: dict[str, tuple[str, str]] = {}
    aliases: set[tuple[str, str]] = set()
    namespaces: set[str] = set()
    header: list[str] | None = None
    mode = ""
    n_rows = unannotated = duplicates = 0
    with table_rows(path, sheet) as rows:
        for number, row in enumerate(rows, 1):
            if not row or all(value is None or value == "" for value in row):
                continue
            first = str(row[0] or "")
            if header is None:
                candidate = [str(value or "").strip() for value in row]
                if candidate[0] == "#query":
                    candidate[0] = "query"
                if {"gene_id", "pathway_id"} <= set(candidate):
                    mode = "custom"
                elif {"query", "KEGG_Pathway"} <= set(candidate):
                    mode = "eggnog"
                elif {"query", "KEGG_ko"} <= set(candidate):
                    raise SkillError("PATHWAY_MAPPING_REQUIRED", "eggNOG KEGG_ko alone is insufficient; supply KEGG_Pathway or an explicit gene-to-pathway mapping.")
                elif first.startswith("#"):
                    continue
                else:
                    raise SkillError("MAPPING_FORMAT_ERROR", "Require gene_id/pathway_id or eggNOG query/KEGG_Pathway columns.", details={"row": number})
                if any(not name for name in candidate) or len(candidate) != len(set(candidate)):
                    raise SkillError("MAPPING_FORMAT_ERROR", "Mapping header requires unique non-empty names.")
                header = candidate
                continue
            if first.startswith("##") or first == "#":
                continue
            if len(row) != len(header):
                raise SkillError("MAPPING_FORMAT_ERROR", "Mapping row width differs from header.", details={"row": number})
            fields = dict(zip(header, row))
            gene = gene_identifier(fields["gene_id" if mode == "custom" else "query"], number)
            value = fields["pathway_id" if mode == "custom" else "KEGG_Pathway"]
            n_rows += 1
            if value is None or str(value).strip().upper() in MISSING or value == "-":
                unannotated += 1
                continue
            if not isinstance(value, str):
                raise SkillError("INVALID_PATHWAY_ID", "Pathway IDs must be text with an explicit namespace.", details={"row": number})
            name = str(fields.get("pathway_name") or "").strip() if mode == "custom" else ""
            if name.upper() in MISSING or name == "-":
                name = ""
            if any(char in name for char in "\r\n\t") or name.startswith("="):
                raise SkillError("MAPPING_FORMAT_ERROR", "pathway_name must be literal single-line text.")
            tokens = re.split("[,;]", value) if mode == "eggnog" else [value]
            for token in tokens:
                raw_id = token.strip()
                pathway, namespace = normalize_pathway(raw_id)
                namespaces.add(namespace)
                if len(namespaces) > 1:
                    raise SkillError("MIXED_PATHWAY_NAMESPACES", "Use reference map pathways or one organism namespace per run, not a mixture.",
                                     details={"namespaces": sorted(namespaces)})
                previous = terms.get(pathway, ("", ""))[0]
                if previous and name and previous != name:
                    raise SkillError("MAPPING_CONFLICT", "A canonical pathway has conflicting names.", details={"pathway_id": pathway})
                terms[pathway] = (previous or name, "")
                aliases.add((raw_id, pathway))
                duplicates += (gene, pathway) in pairs
                pairs.add((gene, pathway))
    if header is None:
        raise SkillError("MAPPING_FORMAT_ERROR", "Mapping header is missing.")
    if not pairs:
        raise SkillError("NO_PATHWAY_ASSOCIATIONS", "Mapping contains no gene-to-pathway associations; KO-only/unannotated rows cannot be used.")
    return KeggMapping(frozenset(pairs), terms, frozenset(aliases), next(iter(namespaces)), mode, n_rows, unannotated, duplicates)
