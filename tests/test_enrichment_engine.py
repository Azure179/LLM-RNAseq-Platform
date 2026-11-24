"""Free regression tests for the explicit clusterProfiler boundary and provenance."""

import json
from pathlib import Path

import pytest

from bulk_rnaseq.enrichment_engine import engine_provenance
from bulk_rnaseq.enrichment_inputs import prepare_enrichment
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.go_enrichment import run_go_enrichment
from test_go import fake_go, go_files


def test_policy_distinguishes_declared_and_mapped_background() -> None:
    data = prepare_enrichment(frozenset({"a", "unmapped"}), frozenset({"a", "b", "unmapped"}),
                              frozenset({("a", "hit"), ("b", "zero")}))
    policy = engine_provenance(data)
    assert policy["target_gene_count"] == 2 and policy["mapped_target_gene_count"] == 1
    assert policy["universe_size"] == 3 and policy["mapped_universe_size"] == 2
    assert data.metrics["n_terms_annotated"] == 2 and data.metrics["n_terms_tested"] == 1
    assert (policy["minGSSize"], policy["maxGSSize"]) == (1, 2)
    assert policy["pvalueCutoff"] == policy["qvalueCutoff"] == 1


@pytest.mark.parametrize("failure", ["missing_version", "missing_term", "legacy_family"])
def test_invalid_engine_artifacts_rejected(failure, go_files, tmp_path, monkeypatch) -> None:
    def invalid(command, **kwargs):
        result = fake_go(command, **kwargs)
        output = Path(command[command.index("--output") + 1])
        if failure == "missing_version":
            result.stdout = result.stdout.replace("CLUSTERPROFILER_VERSION=test-fixture", "NO_ENGINE_VERSION=fixture")
        else:
            path = output / "go_all_results.csv"
            lines = path.read_text().splitlines()
            if failure == "missing_term":
                path.write_text("\n".join(lines[:-1]) + "\n")
            else:
                path.write_text("\n".join(lines) + '\nGO:0006281,Synthetic association set,BP,0,5,15,20,0,.75,0,1,1,\n')
        return result
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", invalid)
    with pytest.raises(SkillError) as error:
        run_go_enrichment(*go_files[:2], tmp_path / "out", universe=go_files[2])
    assert error.value.code == "OUTPUT_INVALID"


def test_published_engine_policy_and_versions(go_files, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", fake_go)
    result = run_go_enrichment(*go_files[:2], tmp_path / "out", universe=go_files[2], padj_threshold=.01)
    payload = json.loads(result.summary_json.read_text())
    assert payload["provenance"]["clusterProfiler_version"] == "test-fixture"
    assert payload["provenance"]["enrichit_version"] == "test-fixture"
    assert payload["provenance"]["pvalueCutoff"] == 1
    assert payload["parameters"]["padj_threshold"] == .01
    assert payload["methods"]["test"] == "clusterProfiler::enricher"
