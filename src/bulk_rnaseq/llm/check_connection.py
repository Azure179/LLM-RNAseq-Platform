"""Explicit, one-request connection check: python -m bulk_rnaseq.llm.check_connection."""

import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.run_io import write_json


PROMPT = "连接测试：请只回复 OK，不做任何分析。"
LOGGER = logging.getLogger(__name__)


def check_connection(env_file: Path | None = None) -> dict:
    report = {"test_case": "phase_6_0_connection", "started_at": datetime.now(timezone.utc).isoformat(),
              "user_input": PROMPT, "requested_model": None, "model": None, "usage": None,
              "latency_seconds": None, "api_response_received": False}
    try:
        config = load_deepseek_config(env_file)
        report["requested_model"] = config.model
        response = DeepSeekClient(config).chat(PROMPT)
        report.update(response.to_dict(), api_response_received=True)
        if response.finish_reason != "stop":
            raise DeepSeekError("LLM_INCOMPLETE_RESPONSE", "Response did not finish normally; inspect finish_reason before explicitly retrying.")
        report["status"] = "succeeded"
    except DeepSeekError as error:
        report.update(error.to_dict())
        report["api_response_received"] = report["api_response_received"] or "http_status" in error.details
        if "latency_seconds" in error.details:
            report["latency_seconds"] = error.details["latency_seconds"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit paid DeepSeek connection check (one request, no retries).")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/deepseek_connection"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        report = check_connection(args.env_file)
        write_json(run_dir / "connection.json", report)
    except OSError:
        LOGGER.error("Unable to write connection report; check output directory permissions.")
        return 1
    LOGGER.info("Connection status=%s model=%s usage=%s report=%s", report["status"], report["model"],
                report["usage"], run_dir / "connection.json")
    if report["status"] == "failed":
        LOGGER.error("%s: %s", report["error_code"], report["message"])
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
