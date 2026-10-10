"""One real paid request, only with --run-llm. No fake fallback and no RNA-seq execution."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bulk_rnaseq.llm.check_connection import check_connection
from bulk_rnaseq.run_io import write_json


@pytest.mark.llm
def test_real_deepseek_connection(request: pytest.FixtureRequest) -> None:
    run_dir = Path(request.config.getoption("--llm-report-dir")) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    report = check_connection(Path(request.config.getoption("--llm-env-file")))
    write_json(run_dir / "connection.json", report)
    assert report["status"] == "succeeded", f"{report.get('error_code')}: {report.get('message')} (see {run_dir / 'connection.json'})"
    assert report["api_response_received"] and report["model"] and report["content"]
    assert report["finish_reason"] == "stop"
