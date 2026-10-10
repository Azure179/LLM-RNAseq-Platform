"""Read custom and eggNOG GO associations; never infer species or change gene IDs."""

from dataclasses import dataclass
from pathlib import Path
import re

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import MISSING
# Compatibility exports for callers from Phase 4.4.
from bulk_rnaseq.enrichment_inputs import gene_identifier, table_rows


@dataclass(frozen=True)
class GoMapping:
    pairs: frozenset[tuple[str, str]]
    terms: dict[str, tuple[str, str]]  # GO ID -> (optional name, optional ontology)
    format: str
    n_rows: int
    n_unannotated_rows: int
    n_duplicate_pairs: int


def read_go_mapping(path: Path, sheet: str | None = None) -> GoMapping:
    pairs: set[tuple[str, str]] = set()
    terms: dict[str, tuple[str, str]] = {}
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
                if {"gene_id", "go_id"} <= set(candidate):
                    mode = "custom"
                elif {"query", "GOs"} <= set(candidate):
                    mode = "eggnog"
                elif first.startswith("#"):
                    continue
                else:
                    raise SkillError("MAPPING_FORMAT_ERROR", "Require gene_id/go_id or eggNOG query/GOs columns.",
                                     details={"row": number})
                if any(not item for item in candidate) or len(candidate) != len(set(candidate)):
                    raise SkillError("MAPPING_FORMAT_ERROR", "Mapping header must contain unique non-empty names.")
                header = candidate
                continue
            if first.startswith("##") or first == "#":
                continue
            if len(row) != len(header):
                raise SkillError("MAPPING_FORMAT_ERROR", "Mapping row width differs from header.", details={"row": number})
            fields = dict(zip(header, row))
            gene = gene_identifier(fields["gene_id" if mode == "custom" else "query"], number)
            value = fields["go_id" if mode == "custom" else "GOs"]
            n_rows += 1
            if value is None or str(value).strip().upper() in MISSING or value == "-":
                unannotated += 1
                continue
            go_ids = re.split("[,;]", str(value)) if mode == "eggnog" else [str(value)]
            # eggNOG Description describes a protein, not a GO term.
            name = str(fields.get("go_name") or "").strip() if mode == "custom" else ""
            ontology = str(fields.get("ontology") or "").strip() if mode == "custom" else ""
            if ontology not in {"", "BP", "MF", "CC"}:
                raise SkillError("MAPPING_FORMAT_ERROR", "ontology must be BP, MF, CC or empty.", details={"row": number})
            for token in go_ids:
                go_id = token.strip()
                if not re.fullmatch(r"GO:[0-9]{7}", go_id):
                    raise SkillError("INVALID_GO_ID", "GO IDs must match GO: followed by seven digits.",
                                     details={"row": number, "value": go_id})
                previous = terms.get(go_id, ("", ""))
                if any(a and b and a != b for a, b in zip(previous, (name, ontology))):
                    raise SkillError("MAPPING_CONFLICT", "A GO term has conflicting names or ontologies.", details={"go_id": go_id})
                terms[go_id] = (previous[0] or name, previous[1] or ontology)
                duplicates += (gene, go_id) in pairs
                pairs.add((gene, go_id))
    if header is None or not pairs:
        raise SkillError("MAPPING_FORMAT_ERROR", "Mapping must contain at least one valid gene-to-GO association.")
    return GoMapping(frozenset(pairs), terms, mode, n_rows, unannotated, duplicates)
