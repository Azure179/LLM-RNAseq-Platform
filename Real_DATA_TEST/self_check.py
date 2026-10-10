"""Runner-only control-flow tests. Test doubles never enter real results directories."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_real_data_test as runner
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.run_io import write_json
from prepare_inputs import local_path, write_table
from build_asc_crosswalk import build_crosswalk, candidate_gene, queries


class RunnerChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="rnaseq-runner-self-check-")
        self.root = Path(self.temporary.name)
        self.logger = logging.Logger("runner-self-check")
        self.logger.addHandler(logging.NullHandler())
        self.run = runner.IntegrationRun(self.root, self.logger, {"treatment": "OT", "control": "T"})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fail(self) -> None:
        raise SkillError("RSCRIPT_FAILED", "Controlled test exception, no R process was run.")

    def test_hard_validation_failure_skips_all_skills(self) -> None:
        with patch.object(runner, "prepare", side_effect=SkillError("FILE_NOT_FOUND", "test")), patch.object(runner, "run_pca") as pca:
            self.assertEqual(runner.pipeline(self.run, False), 1)
        pca.assert_not_called()
        self.assertEqual(self.run.report["steps"]["kegg"]["status"], "skipped")

    def test_visualization_failure_does_not_block_next_step(self) -> None:
        self.run.step("volcano", self.fail, kind="non_critical_visualization_failure")
        self.assertEqual(self.run.step("later", lambda: {"executed": True}), {"executed": True})
        self.assertEqual(self.run.finish(), 2)

    def test_go_failure_does_not_block_kegg(self) -> None:
        self.run.step("go", self.fail)
        self.run.step("kegg", lambda: {})
        self.assertEqual(self.run.report["steps"]["kegg"]["status"], "succeeded")
        self.assertEqual(self.run.finish(), 2)

    def test_valid_empty_enrichment_is_not_failure(self) -> None:
        path = self.root / "test-double.json"
        write_json(path, {"metrics": {"n_enriched": 0}, "provenance": {"artifact_sha256": {}}, "warnings": []})
        self.run.step("go", lambda: SimpleNamespace(summary_json=path, output_dir=self.root))
        self.assertEqual(self.run.report["steps"]["go"]["status"], "empty")
        self.assertEqual(self.run.finish(), 0)

    def test_heatmap_no_genes_is_valid_empty(self) -> None:
        def empty() -> None:
            raise SkillError("NO_SIGNIFICANT_GENES", "test")
        self.run.step("heatmap", empty)
        self.assertEqual(self.run.report["steps"]["heatmap"]["status"], "empty")
        self.assertEqual(self.run.report["failed_steps"], [])

    def test_missing_artifact_is_failure(self) -> None:
        path = self.root / "test-double.json"
        write_json(path, {"metrics": {}, "provenance": {"artifact_sha256": {"missing.csv": "bad"}}})
        self.run.step("pca", lambda: SimpleNamespace(summary_json=path, output_dir=self.root))
        self.assertEqual(self.run.report["steps"]["pca"]["error"]["error"]["code"], "OUTPUT_INVALID")

    def test_unexpected_exception_is_structured(self) -> None:
        self.run.step("example", lambda: 1 / 0)
        self.assertEqual(self.run.report["steps"]["example"]["error"]["error"]["code"], "INTEGRATION_ERROR")

    def test_path_cannot_escape_dataset(self) -> None:
        with self.assertRaises(SkillError):
            local_path(self.root, "../outside")

    def test_prepared_inputs_are_not_overwritten(self) -> None:
        path = self.root / "test.csv"
        write_table(path, ["gene_id"], [["original"]])
        before = path.read_bytes()
        with self.assertRaises(SkillError):
            write_table(path, ["gene_id"], [["changed"]])
        self.assertEqual(path.read_bytes(), before)

    def test_zero_mapping_is_measured_zero_not_successful_enrichment(self) -> None:
        self.assertEqual(runner.coverage({"rna-Asc00001.1"}, {"gene-Asc00001"}), {"mapped": 0, "total": 1, "fraction": 0.0})

    def test_annotation_failure_preserves_validated_expression_inputs(self) -> None:
        config = runner.read_json(runner.ROOT / "config/real_test_config.json")
        (self.root / "input").mkdir()
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "source_hashes", return_value={}), \
             patch.object(runner, "prepare_counts", return_value={"genes": ["test-gene"]}), \
             patch.object(runner, "prepare_annotations", side_effect=SkillError("MAPPING_FORMAT_ERROR", "test")):
            result = runner.prepare(config, False, self.logger)
        self.assertEqual(result["data"]["genes"], ["test-gene"])
        self.assertEqual(result["annotation_error"]["error"]["code"], "MAPPING_FORMAT_ERROR")

    def test_asc_rule_preserves_leading_zeros_and_accepts_isoforms(self) -> None:
        self.assertEqual(candidate_gene("rna-Asc00001.1"), "gene-Asc00001")
        self.assertEqual(candidate_gene("rna-Asc00001.12"), "gene-Asc00001")

    def test_asc_rule_does_not_guess_other_formats(self) -> None:
        for query in ("rna-Other00001.1", "rna-Asc00001", "rna-Asc00001.1.extra", " rna-Asc00001.1", "rna-Asc00001.0"):
            self.assertIsNone(candidate_gene(query))

    def test_crosswalk_audits_unmatched_and_many_to_one(self) -> None:
        counts, annotation = self.root / "counts.csv", self.root / "egg.tsv"
        write_table(counts, ["gene_id", "sample"], [["gene-Asc00001", 10], ["gene-Asc00002", 20]])
        write_table(annotation, ["query", "GOs", "KEGG_Pathway"],
                    [["rna-Asc00001.1", "-", "-"], ["rna-Asc00001.2", "-", "-"], ["rna-Asc99999.1", "-", "-"], ["other", "-", "-"]], "\t")
        original = annotation.read_bytes()
        report = build_crosswalk(counts, annotation, self.root / "crosswalk")
        self.assertEqual(report["query_status_counts"], {"mapped": 2, "gene_not_in_counts": 1, "unsupported_query_format": 1})
        self.assertEqual(report["count_gene_coverage"]["mapped"], 1)
        self.assertEqual(report["n_multi_transcript_genes"], 1)
        self.assertFalse(report["reference_validated"])
        self.assertEqual(annotation.read_bytes(), original)

    def test_duplicate_eggnog_query_is_rejected(self) -> None:
        annotation = self.root / "egg.tsv"
        write_table(annotation, ["query", "GOs", "KEGG_Pathway"], [["rna-Asc00001.1", "-", "-"]] * 2, "\t")
        with self.assertRaises(SkillError) as caught:
            list(queries(annotation))
        self.assertEqual(caught.exception.code, "DUPLICATE_QUERY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
