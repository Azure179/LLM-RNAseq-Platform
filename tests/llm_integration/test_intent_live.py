"""Actual DeepSeek semantic tests. Expectations are assertions, never response substitutes."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent import IntentParser
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import write_json


CASES = [
    ("01_deg", "比较 OT 和 T 的差异表达，OT 是实验组，T 是对照。", "deg", {"treatment": "OT", "control": "T"}, {}),
    ("02_control", "以 T 作为 control，分析 OT 相对于 T 的变化。", "deg", {"treatment": "OT", "control": "T"}, {}),
    ("03_threshold", "比较 OT 和 T，padj < 0.01，|log2FC| >= 2。", "deg", {"alpha": .01, "lfc_threshold": 2}, {}),
    ("04_missing", "帮我做差异表达。", "deg", {}, {}),
    ("05_pca", "画一下样本 PCA。", "pca", {}, {}),
    ("06_clustering", "检查一下这些样本聚类有没有异常。", "pca", {}, {}),
    ("07_volcano", "根据差异表达结果画火山图。", "volcano", {}, {}),
    ("08_heatmap", "画 Top 30 差异基因热图。", "heatmap", {"top_n": 30}, {}),
    ("09_go_up", "对上调基因做 GO 富集。", "go", {}, {}),
    ("10_kegg", "看看差异基因有哪些 KEGG pathway。", "kegg", {}, {}),
    ("11_unsupported", "帮我做单细胞 Seurat 分析。", None, {}, {}),
    ("12_vs_forward", "使用 DESeq2 分析 OT vs T。", "deg", {"treatment": "OT", "control": "T"}, {}),
    ("13_vs_reverse", "用 DESeq2 分析 T versus OT。", "deg", {"treatment": "T", "control": "OT"}, {}),
    ("14_ambiguous", "比较 OT 和 T 的差异表达。", "deg", {}, {}),
    ("15_labels_override", "比较 OT vs T 的差异，明确以 OT 为 control，T 为实验组。", "deg", {"treatment": "T", "control": "OT"}, {}),
    ("16_pca_context", "画一下样本 PCA。", "pca", {"group_column": "group"}, {"rnaseq.pca": {
        "count_matrix": "declared/counts.csv", "metadata": "declared/metadata.csv", "group_column": "group", "output_dir": "declared/pca"}}),
    ("17_volcano_context", "把差异分析结果画成 volcano plot。", "volcano", {}, {"rnaseq.volcano": {
        "deg_result_file": "declared/deg_results.csv", "output_dir": "declared/volcano"}}),
    ("18_gsea", "对这些基因进行 GSEA 分析。", None, {}, {}),
    ("19_conflicting", "OT 是实验组、T 是对照；同时 T 是实验组、OT 是对照。做差异表达。", "deg", {}, {}),
    ("20_overall", "看一下不同组之间的整体表达差异。", "pca", {}, {}),
]


@pytest.fixture(scope="module")
def intent_records(request: pytest.FixtureRequest):
    destination = Path(request.config.getoption("--llm-report-dir")) / datetime.now(timezone.utc).strftime("intent_%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    yield destination, records


@pytest.mark.llm
@pytest.mark.parametrize("case_id,user_text,skill,expected,context", CASES, ids=[case[0] for case in CASES])
def test_real_intent(case_id: str, user_text: str, skill: str | None, expected: dict, context: dict,
                     request: pytest.FixtureRequest, intent_records: tuple,
                     monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Phase 6.1 must never execute a Skill or subprocess")
    monkeypatch.setattr(SkillRegistry, "execute", forbidden)
    monkeypatch.setattr(SkillRegistry, "get_executor", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    destination, records = intent_records
    record = {"test_case": case_id, "user_input": user_text, "context": context,
              "expected_skill": "rnaseq." + skill if skill else None, "expected_parameters": expected,
              "actual_skill": None, "model": None, "raw_response": None, "parsed_intent": None,
              "validation_result": "not_run", "pass": False, "usage": None, "latency_seconds": None,
              "skill_execution_status": "not_executed"}
    try:
        parser = IntentParser(DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file")))), create_default_registry())
        result = parser.parse(user_text, context=context)
        intent = result.intent
        record.update(model=result.response.model, raw_response=result.response.content,
                      parsed_intent=intent.model_dump(), validation_result="passed", actual_skill=intent.skill_name,
                      usage=result.response.usage, latency_seconds=result.response.latency_seconds, prompt_sha256=result.prompt_sha256)
        assert intent.skill_name == record["expected_skill"]
        for name, value in expected.items():
            assert intent.parameters[name] == value
        if skill is None:
            assert intent.status == "unsupported"
        elif context:
            assert intent.status == "ready"
        else:
            assert intent.status == "needs_input"  # Required data paths were intentionally not supplied.
        if case_id in {"03_threshold", "04_missing", "14_ambiguous", "19_conflicting"}:
            assert {"treatment", "control"} <= set(intent.missing_parameters)
            assert "treatment" not in intent.parameters and "control" not in intent.parameters
        if case_id == "09_go_up":
            assert intent.gene_subset == "up"
        if case_id in {"05_pca", "06_clustering", "20_overall"}:
            assert "group_column" in intent.missing_parameters
        if case_id == "07_volcano":
            assert "deg_result_file" in intent.missing_parameters
        record["pass"] = True
    except DeepSeekError as error:
        record.update(validation_result="failed", error=error.to_dict())
        response = getattr(error, "response", None)
        if response is not None:
            record.update(model=response.model, raw_response=response.content, usage=response.usage,
                          latency_seconds=response.latency_seconds, prompt_sha256=getattr(error, "prompt_sha256", None))
        pytest.fail(f"{error.code}: {error}", pytrace=False)
    except AssertionError as error:
        record["assertion_failure"] = str(error)
        raise
    finally:
        records.append(record)
        usage_totals = {key: sum(r["usage"][key] for r in records if r["usage"] and key in r["usage"])
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        write_json(destination / "llm_intent_test_results.json", {
            "cases": records, "n_passed": sum(r["pass"] for r in records), "n_cases": len(records),
            "usage_totals_available_responses": usage_totals,
            "n_responses_with_usage": sum(r["usage"] is not None for r in records)})
