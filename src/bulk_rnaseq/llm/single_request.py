"""Compose the existing one-tool runner and one summary request, with no retries."""

from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.execution_context import ExecutionContext
from bulk_rnaseq.llm.final_response import FinalResponseBuilder, FinalResponseResult
from bulk_rnaseq.llm.tool_runner import ToolExecutionResult, ToolRunner
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import write_json


class SingleRequestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["succeeded", "failed", "needs_input", "unsupported"]
    execution: ToolExecutionResult
    final: FinalResponseResult | None = None
    final_response: str = ""
    request_count: int = 0
    usage: dict[str, Any]
    error_code: str | None = None


def combine_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Unknown provider usage stays unknown, never silently interpreted as zero."""
    return {key: sum(u[key] for u in usages) if usages and all(u is not None and type(u.get(key)) is int for u in usages) else None
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")}


class SingleRequestRunner:
    def __init__(self, client: DeepSeekClient, registry: SkillRegistry) -> None:
        self.client, self.registry = client, registry

    def run(self, user_text: str, context: ExecutionContext, *,
            progress: Callable[[str, str | None], None] | None = None,
            before_execute: Callable[[str, dict[str, Any]], None] | None = None) -> SingleRequestResult:
        options = {"progress": progress, "before_execute": before_execute} if progress or before_execute else {}
        execution = ToolRunner(self.client, self.registry).run(user_text, context, **options)
        outcome = SingleRequestResult(status=execution.status, execution=execution, final_response=execution.message,
                                      request_count=execution.request_count,
                                      usage=combine_usage((execution.llm_response or {}).get("usage")),
                                      error_code=execution.error_code)
        # Missing information/unsupported/host errors are returned locally, never summarized as success.
        if execution.skill_result is None or (execution.status != "succeeded" and execution.skill_result.get("status") != "failed"):
            return outcome
        try:
            if progress:
                progress("summarizing", execution.skill_name)
            directory = context.local_path(context.output_root) / execution.request_id
            with run_logger(directory / "final_response.log", execution.request_id) as logger:
                final = FinalResponseBuilder(self.client).build(user_text, execution.skill_name or "", execution.skill_result,
                                                                request_id=execution.request_id, logger=logger)
                outcome.final = final
                outcome.status, outcome.final_response, outcome.error_code = final.status, final.final_response, final.error_code
                outcome.request_count += final.request_count
                usages = [(execution.llm_response or {}).get("usage")]
                if final.request_count:
                    usages.append(final.usage)
                outcome.usage = combine_usage(*usages)
                write_json(directory / "final_response.json", outcome.model_dump())
        except OSError:
            outcome.status, outcome.error_code = "failed", "FINAL_REPORT_IO_ERROR"
            outcome.final_response = "结果或总结可能已生成，但写入报告失败；请先检查当前 run，勿自动重跑分析。"
        return outcome
