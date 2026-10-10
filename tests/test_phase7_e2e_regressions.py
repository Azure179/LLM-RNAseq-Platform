"""Small, free reproductions of bugs exposed by the real Phase 7 UI session."""
from uuid import uuid4
import csv
from io import StringIO
import pytest

from test_workflow_chat_ui import host, run_workflow
from bulk_rnaseq.workspace.result_manager import ResultManager


@pytest.mark.parametrize("keyword,expected", [("sex differentiation", ["g1"]), ("absent-topic", [])])
def test_sort_recent_filtered_result_never_expands_back_to_original_deg(host, tmp_path, keyword, expected):
    controller, answers, _, calls, _ = host
    run_workflow(host)
    annotation = tmp_path / "egg.tsv"
    annotation.write_text("#query\tDescription\tGOs\tKEGG_Pathway\ng1\tsex differentiation\tGO:0000001\tmap00010\ng2\tlipid\tGO:0000002\tmap00020\n")
    assert controller.service.import_eggnog("First", annotation, replace=True).status == "succeeded"
    answers.extend([{"request_type": "customization", "target_skill": "rnaseq.deg"},
        {"mode": "annotation_filter", "target_skill": "rnaseq.deg", "parameters": {
            "gene_subset": "up", "keywords": [keyword], "annotation_sources": ["gene_description"]}}])
    filtered = controller.submit("First", "筛选上调sex differentiation基因", str(uuid4()))
    assert filtered.status == "succeeded" and filtered.summary["n_matched_genes"] == len(expected)
    answers.extend([{"request_type": "customization", "target_skill": "rnaseq.deg", "source_reference": "recent",
                     "source_kind": "filtered_deg"},
        {"mode": "annotation_filter", "target_skill": "rnaseq.deg", "parameters": {"order_by": "log2fc", "descending": True}}])
    before = list(calls)
    sorted_result = controller.submit("First", "把刚才筛选出的基因按照log2FoldChange从高到低排列", str(uuid4()))
    assert sorted_result.status == "succeeded", sorted_result
    assert sorted_result.source_run_id == filtered.run_id
    assert sorted_result.summary["n_matched_genes"] == len(expected)
    data = ResultManager(controller.manager, "First").download(sorted_result.run_id, "filtered_table")[1]
    assert [row["gene_id"] for row in csv.DictReader(StringIO(data.decode()))] == expected
    assert calls == before


def test_filtered_source_missing_does_not_fall_back_to_deg(host):
    controller, answers, _, calls, _ = host
    run_workflow(host)
    before = list(calls)
    answers.append({"request_type": "customization", "target_skill": "rnaseq.deg", "source_reference": "recent", "source_kind": "filtered_deg"})
    result = controller.submit("First", "排序刚才筛选出的基因", str(uuid4()))
    assert result.status == "needs_input" and result.error_code == "MISSING_UPSTREAM_DEPENDENCY"
    assert not result.source_options and calls == before


def test_annotation_discovery_does_not_parse_entire_wide_mapping(tmp_path, monkeypatch):
    from bulk_rnaseq.customization import filtering
    path = tmp_path / "annotation.tsv"
    path.write_text("gene_id\tgo_id\tgo_name\ng1\tGO:0000001\tactual annotation\n")
    monkeypatch.setattr(filtering, "read_annotations", lambda _: pytest.fail("Discovery must not fully parse a wide mapping"))
    assert filtering.annotation_categories(path) == {"GO"}
