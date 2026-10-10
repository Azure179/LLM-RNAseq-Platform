"""Explicit paid, single intent request. No Skill execution or automatic retries."""

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.intent import IntentParser
from bulk_rnaseq.run_io import write_json


LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One paid DeepSeek intent request; never executes a Skill.")
    parser.add_argument("text", help="Natural-language request (sent to DeepSeek).")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--context-file", type=Path, help="Optional trusted JSON parameters keyed by registered Skill name.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/deepseek_intents"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        # Fail before a paid request if the report destination cannot be created.
        run_dir.mkdir(parents=True, exist_ok=False)
        try:
            context = None
            if args.context_file is not None:
                try:
                    context = json.loads(args.context_file.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, ValueError):
                    raise DeepSeekError("LLM_INPUT_ERROR", "Unable to read a valid context JSON file.") from None
            config = load_deepseek_config(args.env_file)
            result = IntentParser(DeepSeekClient(config), create_default_registry()).parse(args.text, context=context)
            report = {"intent": result.intent.model_dump(), "model": result.response.model,
                      "usage": result.response.usage, "latency_seconds": result.response.latency_seconds,
                      "prompt_sha256": result.prompt_sha256, "skill_execution_status": "not_executed"}
        except DeepSeekError as error:
            # No raw provider response/user text is written by the manual entry point.
            report = error.to_dict()
            LOGGER.error("%s: %s", error.code, str(error))
        write_json(run_dir / "intent.json", report)
    except OSError:
        LOGGER.error("LLM_REPORT_IO_ERROR: Unable to create/write the intent report directory.")
        return 1
    LOGGER.info("Intent report: %s", run_dir / "intent.json")
    if "intent" in report:
        LOGGER.info("status=%s skill=%s missing=%s", report["intent"]["status"],
                    report["intent"]["skill_name"], report["intent"]["missing_parameters"])
    # needs_input and unsupported are valid intents, not transport/validation errors.
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
