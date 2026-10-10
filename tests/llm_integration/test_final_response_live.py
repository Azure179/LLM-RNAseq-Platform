"""Opt-in real DeepSeek summaries of real Skill outputs; no synthetic LLM decisions."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.final_response import FinalResponseBuilder
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.skills.kegg_enrichment import run_kegg_enrichment


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "Real_DATA_TEST"
SOURCES = {"deg": "02_deg/summary.json", "pca": "01_pca/summary.json",
           "go": "asc_mapped/05_go_retry/summary.json", "warning": "asc_mapped/05_go_retry/summary.json"}


def client(request: pytest.FixtureRequest) -> DeepSeekClient:
    return DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))


def report_dir(request: pytest.FixtureRequest) -> Path:
    path = Path(request.config.getoption("--llm-report-dir")) / "phase_6_4" / str(uuid4())
    path.mkdir(parents=True, exist_ok=False)
    return path


def load_real(path: Path) -> SkillResult:
    if not path.is_file():
        pytest.skip("Requires existing real-data Skill output: " + str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SkillResult.from_dict(payload) if payload.get("schema_version") == "2.0" else SkillResult.from_legacy_summary(payload)


@pytest.mark.llm
@pytest.mark.parametrize("case", ["deg", "pca", "go", "warning"])
def test_real_final_summary(case: str, request: pytest.FixtureRequest) -> None:
    path = DATA / "results" / SOURCES[case]
    source = load_real(path)
    directory = report_dir(request)
    user = {"deg": "请总结 OT 相对于 T 的差异表达结果。", "pca": "请总结样本 PCA 结果，不要推断组间显著性。",
            "go": "请总结已有 GO 富集结果。", "warning": "请总结 GO 结果，并保留全部分析注意事项。"}[case]
    result = FinalResponseBuilder(client(request)).build(user, source.skill_name, source)
    write_json(directory / "summary_test.json", {"case": case, "user_input": user, "source": str(path),
               "source_sha256": sha256(path), "result": result.model_dump(), "passed": result.status == "succeeded"})
    assert result.status == "succeeded", result.model_dump()
    assert result.request_count == 1 and result.grounded
    assert "GO:" not in result.final_response and "map000" not in result.final_response  # No terms were supplied.
    assert all(w["message"] in result.final_response for w in source.warnings)
    if case == "deg":
        assert f"上调基因数为{source.summary['upregulated_genes']}" in result.final_response
        assert f"下调基因数为{source.summary['downregulated_genes']}" in result.final_response
    if case == "pca":
        assert f"PC1解释方差为{source.summary['pc1_variance']:.2f}%" in result.final_response
        assert f"PC2解释方差为{source.summary['pc2_variance']:.2f}%" in result.final_response


@pytest.mark.llm
@pytest.mark.integration
def test_real_kegg_empty_summary(request: pytest.FixtureRequest) -> None:
    # Deliberately stricter threshold ONLY for this empty-branch test, not changed defaults.
    required = [DATA / "results/deg_gene_list.csv", DATA / "input/prepared_asc/gene_to_kegg.tsv",
                DATA / "results/02_deg/deg_results.csv"]
    if not all(p.is_file() for p in required):
        pytest.skip("Requires the prepared real KEGG dataset.")
    directory = report_dir(request)
    source = run_kegg_enrichment(required[0], required[1], directory / "real_empty_kegg",
                                analysis_genes=required[2], padj_threshold=0,
                                rscript_executable=request.config.getoption("--rscript")).to_skill_result()
    assert source.status == "succeeded" and source.summary["n_enriched"] == 0
    user = "请总结这次 KEGG 富集结果。"
    result = FinalResponseBuilder(client(request)).build(user, "rnaseq.kegg", source)
    write_json(directory / "summary_test.json", {"case": "kegg_empty", "user_input": user,
               "source": source.to_dict(), "result": result.model_dump(), "passed": result.status == "succeeded"})
    assert result.status == "succeeded" and "未检测到显著富集结果" in result.final_response
    assert "失败" not in result.final_response
    assert all(Path(path).is_file() for path in source.outputs.values())


@pytest.mark.llm
@pytest.mark.integration
def test_complete_real_deg_e2e(request: pytest.FixtureRequest) -> None:
    counts, metadata = DATA / "input/count_matrix.csv", DATA / "input/metadata.normalized.csv"
    if not counts.is_file() or not metadata.is_file():
        pytest.skip("Requires Real_DATA_TEST inputs.")
    before = (sha256(counts), sha256(metadata))
    context = ExecutionContext(project_root=ROOT, output_root=DATA / "results/phase_6_4", bindings={
        "rnaseq.deg": {"count_matrix_path": str(counts), "metadata_path": str(metadata)}},
        timeout_seconds=3600, rscript_executable=request.config.getoption("--rscript"))
    user = "比较 OT 和 T 的差异表达，OT作为实验组，T作为对照组。"
    result = SingleRequestRunner(client(request), create_default_registry()).run(user, context)
    directory = report_dir(request)
    write_json(directory / "e2e_test.json", {"case": "real_deg_e2e", "user_input": user, "result": result.model_dump(),
                                           "passed": result.status == "succeeded"})
    assert result.status == "succeeded", result.model_dump()
    assert result.execution.arguments["treatment"] == "OT" and result.execution.arguments["control"] == "T"
    assert result.request_count == 2 and result.execution.skill_name == "rnaseq.deg"
    source = result.execution.skill_result
    assert source["metadata"]["processes"][0]["return_code"] == 0
    assert all(Path(path).is_file() for path in source["outputs"].values())
    assert source["summary"] == load_real(DATA / "results/02_deg/summary.json").summary
    assert sha256(Path(source["outputs"]["deg_csv"])) == sha256(DATA / "results/02_deg/deg_results.csv")
    assert (sha256(counts), sha256(metadata)) == before
    assert result.final.grounded and result.final.model and result.final.usage
