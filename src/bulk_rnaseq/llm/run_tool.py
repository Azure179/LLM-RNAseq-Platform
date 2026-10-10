"""Explicit one-request CLI; live API access, optionally one real Skill execution."""

import argparse
import logging
from pathlib import Path

from pydantic import ValidationError

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.tool_runner import ToolRunner, parse_object


LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One paid DeepSeek call followed by at most one real Skill. No workflow/retries.")
    parser.add_argument("text")
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-execute", action="store_true", help="Still calls DeepSeek, but validates without running the Skill.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        context = ExecutionContext.model_validate(parse_object(args.context.read_text(encoding="utf-8-sig")))
        client = DeepSeekClient(load_deepseek_config(args.env_file))
        result = ToolRunner(client, create_default_registry()).run(args.text, context, execute=not args.no_execute)
    except (DeepSeekError, SkillError) as error:
        LOGGER.error("%s: %s", error.code, str(error))
        return 1
    except (OSError, UnicodeError, ValueError, ValidationError):
        LOGGER.error("INVALID_EXECUTION_CONTEXT: Cannot read a valid context JSON configuration.")
        return 1
    LOGGER.info("request_id=%s status=%s stage=%s skill=%s code=%s missing=%s", result.request_id,
                result.status, result.stage, result.skill_name, result.error_code, result.missing_parameters)
    LOGGER.info("If created, the report is under the configured output_root / request_id / tool_execution.json.")
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
