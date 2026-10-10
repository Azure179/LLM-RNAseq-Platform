"""Real DeepSeek, previously saved real WorkflowResults; no new analysis executions."""

import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workflow import WorkflowExecutor, WorkflowResult
from bulk_rnaseq.workflow.final_response import WorkflowFinalResponseBuilder

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def forbid_analysis(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Phase 7.5 live test must never execute an analysis")
    for target, method in ((SkillRegistry, "execute"), (WorkflowExecutor, "execute"),
                           (subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(target, method, forbidden)


@pytest.mark.llm
@pytest.mark.parametrize("case", ["deg_volcano", "deg_kegg_up", "branch_and_roots"])
def test_real_workflow_summary(case, request):
    candidates = []
    for path in (ROOT / "outputs" / "workflow_tests").glob("*/integration_report.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("case") == case and report.get("status") == "succeeded":
            candidates.append(path)
    if not candidates:
        pytest.skip("Requires a previously saved Phase 7.4 integration report: " + case)
    path = sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]
    before = sha256(path)
    source = WorkflowResult.model_validate(json.loads(path.read_text(encoding="utf-8"))["workflow_result"])
    client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
    result = WorkflowFinalResponseBuilder(client).build(source)
    directory = Path(request.config.getoption("--llm-report-dir")) / "phase_7_5" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "summary_test.json", client.sanitize_for_audit({
        "case": case, "source": str(path.relative_to(ROOT)), "source_sha256": before,
        "fixture_origin": "Previously computed real R results on demo counts with synthetic KEGG annotations",
        "result": result.model_dump(), "passed": result.status == "succeeded"}))
    assert sha256(path) == before
    assert result.status == "succeeded", result.model_dump()
    assert result.grounded and result.request_count == 1
    assert "上调基因数为9" in result.final_response and "下调基因数为9" in result.final_response
    if "kegg" in source.completed_steps:
        assert "上游已筛选DEG中的上调基因" in result.final_response
        assert "分析成功，但在当前阈值下未检测到显著富集结果" in result.final_response
    if "pca" in source.completed_steps:
        assert "PC1解释方差为98.40%" in result.final_response
