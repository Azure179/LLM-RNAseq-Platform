"""Phase 7.8 regression: multi-group project, unchanged two-group DEG contract."""
import csv
from pathlib import Path

import pytest

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.validation import validate_inputs
from bulk_rnaseq.workflow.comparison_inputs import prepare_comparison_inputs


@pytest.fixture
def three_groups(tmp_path):
    counts, metadata = tmp_path / "counts.csv", tmp_path / "metadata.csv"
    counts.write_text("gene_id,t1,o1,c1,t2,o2,c2\ng1,30,4,10,31,5,11\ng2,5,10,30,6,11,31\n")
    metadata.write_text("sample_id,condition\no2,O\nc2,T\nt2,OT\no1,O\nc1,T\nt1,OT\n")
    return counts, metadata, tmp_path / "bindings"


def test_pairwise_selection_preserves_names_order_counts_and_originals(three_groups):
    counts, metadata, folder = three_groups
    before = sha256(counts), sha256(metadata)
    result = prepare_comparison_inputs(counts, metadata, "OT", "T", folder)
    assert result["selected_samples"] == ["t1", "c1", "t2", "c2"]
    assert result["excluded_samples"] == ["o1", "o2"]
    assert list(csv.reader(Path(result["count_matrix_path"]).open()))[1] == ["g1", "30", "10", "31", "11"]
    assert validate_inputs(result["count_matrix_path"], result["metadata_path"], "OT", "T").treatment_samples == 2
    assert before == (sha256(counts), sha256(metadata))
    # Old direct Skill validation is deliberately unchanged.
    with pytest.raises(SkillError, match="Only the requested two groups"):
        validate_inputs(counts, metadata, "OT", "T")


def test_pairwise_does_not_hide_invalid_excluded_counts(three_groups):
    counts, metadata, folder = three_groups
    counts.write_text(counts.read_text().replace("30,4,10", "30,-4,10"))
    with pytest.raises(SkillError):
        prepare_comparison_inputs(counts, metadata, "OT", "T", folder)
    assert not folder.exists()


@pytest.mark.parametrize("treatment,control", [("X", "T"), ("T", "T")])
def test_invalid_comparison_rejected(three_groups, treatment, control):
    with pytest.raises(SkillError):
        prepare_comparison_inputs(*three_groups[:2], treatment, control, three_groups[2])
