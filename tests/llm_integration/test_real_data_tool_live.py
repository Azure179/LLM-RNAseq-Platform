"""Real-data single DEG E2E, requiring BOTH --run-llm and --run-r. No workflow."""

from pathlib import Path
import json

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.tool_runner import ToolRunner
from bulk_rnaseq.run_io import sha256


@pytest.mark.llm
@pytest.mark.integration
def test_real_dataset_deg(request: pytest.FixtureRequest) -> None:
    root = Path(__file__).resolve().parents[2]
    data = root / "Real_DATA_TEST"
    config = json.loads((data / "config/real_test_config.json").read_text(encoding="utf-8"))
    counts, metadata = data / config["count_matrix"], data / config["metadata"]
    before = (sha256(counts), sha256(metadata))
    context = ExecutionContext(project_root=root, output_root=data / "results/phase_6_3", bindings={
        "rnaseq.deg": {"count_matrix_path": str(counts), "metadata_path": str(metadata)}},
        timeout_seconds=config["timeout_seconds"], rscript_executable=request.config.getoption("--rscript"))
    result = ToolRunner(DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file")))),
                        create_default_registry()).run("以 T 为对照比较 OT 的差异表达。", context)
    assert result.status == "succeeded", result.model_dump()
    assert result.skill_name == "rnaseq.deg"
    assert result.arguments["treatment"] == "OT" and result.arguments["control"] == "T"
    assert result.skill_result["metadata"]["processes"][0]["return_code"] == 0
    assert all(Path(value).is_file() for value in result.skill_result["outputs"].values())
    assert (sha256(counts), sha256(metadata)) == before
    # Compare real metrics and table hash against the earlier direct-Skill real-data baseline.
    baseline_path = data / "results/02_deg/summary.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert result.skill_result["summary"] == baseline["metrics"]
    assert sha256(Path(result.skill_result["outputs"]["deg_csv"])) == sha256(data / "results/02_deg/deg_results.csv")
