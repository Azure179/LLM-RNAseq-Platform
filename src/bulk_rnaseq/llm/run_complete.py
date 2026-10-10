"""Explicit paid CLI: one tool request, one real Skill, one grounded summary."""

import argparse
import logging
from pathlib import Path

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.llm.tool_runner import parse_object


LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paid single-tool analysis + grounded DeepSeek summary (at most two requests).")
    parser.add_argument("text")
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-root", type=Path, help="Optional trusted host output directory, inside project_root.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        raw = parse_object(args.context.read_text(encoding="utf-8-sig"))
        if args.output_root is not None:
            raw["output_root"] = str(args.output_root)
        context = ExecutionContext.model_validate(raw)
        client = DeepSeekClient(load_deepseek_config(args.env_file))
        result = SingleRequestRunner(client, create_default_registry()).run(args.text, context)
    except (DeepSeekError, SkillError) as error:
        LOGGER.error("%s: %s", error.code, str(error))
        return 1
    except (OSError, UnicodeError, ValueError, TypeError):
        LOGGER.error("INVALID_EXECUTION_CONTEXT: Cannot read a valid execution configuration.")
        return 1
    LOGGER.info("request_id=%s status=%s code=%s request_count=%s usage=%s", result.execution.request_id,
                result.status, result.error_code, result.request_count, result.usage)
    if result.final_response:
        LOGGER.info("%s", result.final_response)
    elif result.final is not None:
        LOGGER.error("%s", result.final.message)
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
