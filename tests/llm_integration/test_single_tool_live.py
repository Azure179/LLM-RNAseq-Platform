"""Real model decisions; --run-r additionally enables real toy DESeq2/PCA execution."""

import csv
from datetime import datetime, timezone
from pathlib import Path
import shutil

import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.tool_runner import ToolRunner
from bulk_rnaseq.run_io import write_json


CASES = [
    ("deg_explicit", "比较 OT 和 T 的差异表达，其中 OT 是实验组，T 是对照组。", "rnaseq.deg"),
    ("deg_relative", "以 T 为 control，看看 OT 相对于 T 有哪些差异表达基因。", "rnaseq.deg"),
    ("pca", "帮我检查一下样本整体表达模式，画 PCA。", "rnaseq.pca"),
    ("missing", "帮我做差异表达。", "needs_input"),
    ("unsupported", "帮我做单细胞 Seurat clustering。", "unsupported"),
    ("ambiguous", "比较 OT 和 T 的差异表达。", "needs_input"),
]


@pytest.fixture(scope="module")
def live_context(request: pytest.FixtureRequest):
    root = Path(__file__).resolve().parents[2]
    directory = Path(request.config.getoption("--llm-report-dir")).resolve() / datetime.now(timezone.utc).strftime("single_%Y%m%dT%H%M%S%fZ")
    data = directory / "inputs"
    data.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(root / "examples/data/counts.csv", data / "counts.csv")
    # New copy only. Relabel the documented synthetic toy data for OT/T prompts.
    with (root / "examples/data/metadata.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with (data / "metadata.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "condition"])
        writer.writeheader()
        writer.writerows({**row, "condition": {"treatment": "OT", "control": "T"}[row["condition"]]} for row in rows)
    context = ExecutionContext(project_root=root, output_root=directory / "runs", bindings={
        "rnaseq.deg": {"count_matrix_path": str(data / "counts.csv"), "metadata_path": str(data / "metadata.csv")},
        "rnaseq.pca": {"count_matrix": str(data / "counts.csv"), "metadata": str(data / "metadata.csv")}},
        confirmed_parameters={"rnaseq.pca": {"group_column": "condition"}},
        rscript_executable=request.config.getoption("--rscript"))
    records = []
    yield context, directory, records


@pytest.mark.llm
@pytest.mark.parametrize("case_id,text,expected", CASES, ids=[case[0] for case in CASES])
def test_real_single_tool(case_id: str, text: str, expected: str, request: pytest.FixtureRequest,
                          live_context: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    context, directory, records = live_context
    execute = request.config.getoption("--run-r")
    registry = create_default_registry()
    if not execute or not expected.startswith("rnaseq."):
        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("This case must not execute any Skill")
        monkeypatch.setattr(registry, "execute", forbidden)
        monkeypatch.setattr("subprocess.run", forbidden)
    client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
    result = ToolRunner(client, registry).run(text, context, execute=execute)
    record = {"case": case_id, "user_input": text, "expected": expected, "execute_enabled": execute,
              "result": result.model_dump(), "pass": False}
    try:
        if expected.startswith("rnaseq."):
            assert result.skill_name == expected
            assert result.status == ("succeeded" if execute else "validated"), result.model_dump()
            if expected == "rnaseq.deg":
                assert result.arguments["treatment"] == "OT" and result.arguments["control"] == "T"
            if execute:
                assert result.skill_result["status"] == "succeeded"
                assert all(Path(path).is_file() for path in result.skill_result["outputs"].values())
        else:
            assert result.status == expected, result.model_dump()
            assert result.skill_result is None
            if expected == "needs_input":
                assert {"treatment", "control"} <= set(result.missing_parameters)
        record["pass"] = True
    finally:
        records.append(record)
        usages = [r["result"]["llm_response"]["usage"] for r in records if r["result"]["llm_response"] and r["result"]["llm_response"]["usage"]]
        write_json(directory / "single_tool_test_results.json", {"cases": records, "n_passed": sum(r["pass"] for r in records),
            "request_count": sum(r["result"]["request_count"] for r in records),
            "usage_available_responses": {key: sum(u.get(key, 0) for u in usages) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}})
