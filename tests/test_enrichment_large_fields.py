"""Synthetic parser regression fixtures; no real biological results are fabricated."""

import csv
from pathlib import Path

import pytest

from bulk_rnaseq.enrichment_artifacts import verify_enrichment_outputs
from bulk_rnaseq.enrichment_inputs import prepare_enrichment
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.go_artifacts import GO_COLUMNS
from bulk_rnaseq.skills.kegg_enrichment import KEGG_COLUMNS
from plot_helpers import fixture_png


@pytest.mark.parametrize("prefix", ["go", "kegg"])
@pytest.mark.parametrize("corrupt", ["none", "membership", "oversize"])
def test_large_overlap_field_retains_membership_validation_and_csv_limit(
    tmp_path: Path, prefix: str, corrupt: str,
) -> None:
    genes = frozenset(f"gene-Test{i:05d}" for i in range(10000))
    term = "GO:0008150" if prefix == "go" else "map01100"
    prepared = prepare_enrichment(genes, genes, frozenset((gene, term) for gene in genes))
    reported = sorted(genes)
    if corrupt == "membership":
        reported[0] = "gene-Fake00000"  # Same width, wrong member must still be rejected.
    joined = ";".join(reported)
    if corrupt == "oversize":
        joined += "unexpected extra bytes beyond the input-derived bound"
    assert len(joined) > 131072
    row = [term, "Synthetic test", *(["BP"] if prefix == "go" else []),
           10000, 10000, 10000, 10000, 1, 1, 1, 1, 1, joined]
    columns = GO_COLUMNS if prefix == "go" else KEGG_COLUMNS
    files = (f"{prefix}_all_results.csv", f"{prefix}_enrichment.csv", f"{prefix}_dotplot.png", f"{prefix}_barplot.png")
    for name in files[:2]:
        with (tmp_path / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerow(row)
    for name in files[2:]:
        (tmp_path / name).write_bytes(fixture_png())
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(131072)
        kwargs = dict(prefix=prefix, id_column=columns[0], columns=columns, artifact_files=files, has_ontology=prefix == "go")
        if corrupt != "none":
            with pytest.raises(SkillError) as caught:
                verify_enrichment_outputs(tmp_path, prepared, {term: ("Synthetic test", "BP")}, 1, 1,
                                          "R_VERSION=fixture\nSTATS_VERSION=fixture\nN_TERMS_TESTED=1\nN_ENRICHED=1\n", **kwargs)
            expected = "Overlapping gene IDs" if corrupt == "membership" else "field larger than field limit"
            assert expected in caught.value.details["reason"]
        else:
            count, _ = verify_enrichment_outputs(tmp_path, prepared, {term: ("Synthetic test", "BP")}, 1, 1,
                                                 "R_VERSION=fixture\nSTATS_VERSION=fixture\nN_TERMS_TESTED=1\nN_ENRICHED=1\n", **kwargs)
            assert count == 1
        assert csv.field_size_limit() == 131072
    finally:
        csv.field_size_limit(previous)
