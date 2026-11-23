"""Separate, project-scoped customization planning and execution over existing backend."""

from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from bulk_rnaseq.customization.catalog import SKILLS, validate_decision
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.customization.models import CustomizationDecision
from bulk_rnaseq.customization.planner import CustomizationPlan, CustomizationPlanner
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.llm.client import ChatResponse, DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workspace.chat_router import ChatRoute, ChatRouter
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.customization_sources import imported_annotations, resolve_customization_source
from bulk_rnaseq.workspace.result_manager import ResultManager


class PreparedCustomization(BaseModel):
    """Public projection of an approved stored plan; no host path or LLM credentials."""
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    project_id: str
    source_run_id: str
    request: str = Field(min_length=1, max_length=4000)
    decision: CustomizationDecision


class CustomizationService:
    def __init__(self, registry: SkillRegistry, manager: ProjectManager, *, env_file: Path | None = None,
                 rscript_executable: str = "Rscript", timeout: float = 600) -> None:
        self.registry, self.manager = registry, manager
        self.env_file, self.rscript_executable, self.timeout = env_file, rscript_executable, timeout

    def create_client(self) -> DeepSeekClient:
        return DeepSeekClient(load_deepseek_config(self.env_file))

    def engine(self, key: str) -> CustomizationEngine:
        return CustomizationEngine(self.manager, key, self.registry,
            rscript_executable=self.rscript_executable, timeout=self.timeout)

    def plan(self, key: str, text: str, *, source_run_id: str | None = None) -> dict[str, Any]:
        project = self.manager.open_project(key)
        browser, store = ResultManager(self.manager, key), ChatStore(self.manager, key)
        # Fail on an explicit foreign/missing source BEFORE paying for model routing.
        source_skill = browser.get_run(source_run_id).skill_name if source_run_id else None
        with store.lock():
            snapshot = project.metadata.files.model_dump()
            client = self.create_client()
            route, routed = ChatRouter(client, self.registry).route(text, source_skill=source_skill)
            responses = [routed]
            result: dict[str, Any] = {"status": "needs_input", "plan": None, "required_fields": [],
                                     "source_options": [], "error_code": None, "message": "", "warnings": []}
            if route.request_type == "unsupported":
                result.update(status="unsupported", message="This request has no supported customization capability.")
            elif route.request_type != "customization" and not (
                route.request_type == "result_reuse" and route.target_skill == "rnaseq.volcano"
            ):
                # Existing DEG -> Volcano is a supported render-only engine input.
                # Other result reuse (e.g. new enrichment) is NOT customization.
                result.update(required_fields=["customization_request"], message="Describe a modification to an existing result, not a new analysis.")
            elif route.target_skill not in SKILLS:
                result.update(status="unsupported", message="This result type has no supported customization capability.")
            else:
                selection = resolve_customization_source(self.manager, key, route, source_run_id)
                if selection.status != "resolved":
                    result.update(error_code="AMBIGUOUS_UPSTREAM_RESULT" if selection.candidates else selection.error_code,
                        required_fields=["source_run_id"], message="Select a compatible source from this project.",
                        source_options=[{"run_id": ref.run_id, "skill_name": ref.skill_name,
                            "comparison": ref.comparison, "created_at": ref.created_at.isoformat()} for ref in selection.candidates])
                else:
                    annotations = imported_annotations(self.manager, key) if route.target_skill == "rnaseq.deg" else []
                    planned = CustomizationPlanner(client, self.registry).plan(text, target_skill=route.target_skill,
                        available_annotations=self.engine(key).available_annotations(annotations))
                    responses.append(planned.response)
                    if snapshot != self.manager.open_project(key).metadata.files.model_dump():
                        raise SkillError("PROJECT_INPUT_CHANGED", "Project bindings changed during planning.")
                    if planned.decision.mode == "unsupported":
                        result.update(status="unsupported", message=planned.decision.reason)
                    else:
                        ticket = PreparedCustomization(plan_id=uuid4().hex, project_id=key,
                            source_run_id=selection.result.run_id, request=text, decision=planned.decision)
                        path = store.path("customization_plans/" + ticket.plan_id + ".json")
                        path.parent.mkdir(exist_ok=True)
                        write_json(path, {"status": "planned", "plan": ticket.model_dump(mode="json"),
                            "provider_response": planned.response.to_dict(), "inputs_snapshot": snapshot,
                            "current_deg": selection.current_input_sha256})
                        result.update(status="ready", plan=ticket.model_dump(mode="json"))
            result.update(model=responses[-1].model, request_count=len(responses),
                usage={k: sum(r.usage[k] for r in responses) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                       if all(r.usage is not None and type(r.usage.get(k)) is int for r in responses)},
                latency_seconds=sum(r.latency_seconds for r in responses))
            return client.sanitize_for_audit(result)

    def run(self, key: str, ticket: PreparedCustomization) -> dict[str, Any]:
        self.manager.open_project(key)
        if key != ticket.project_id:
            raise SkillError("INVALID_CUSTOMIZATION_PLAN", "Plan belongs to another project.")
        store = ChatStore(self.manager, key)
        with store.lock():
            path = store.path("customization_plans/" + ticket.plan_id + ".json")
            if not path.is_file():
                raise SkillError("INVALID_CUSTOMIZATION_PLAN", "A plan generated for this project is required.")
            saved = store.read(path)
            if saved.get("plan") != ticket.model_dump(mode="json"):
                raise SkillError("INVALID_CUSTOMIZATION_PLAN", "Plan differs from the approved proposal; request a new plan.")
            if saved["status"] == "completed":
                return saved["report"]
            if saved["status"] != "planned":
                raise SkillError("CUSTOMIZATION_ALREADY_SUBMITTED", "Execution was submitted; inspect results before retrying.")
            if saved["inputs_snapshot"] != self.manager.open_project(key).metadata.files.model_dump():
                raise SkillError("PROJECT_INPUT_CHANGED", "Project inputs changed; request a new plan.")
            source = ResultManager(self.manager, key).get_run(ticket.source_run_id)
            if source.skill_name == "rnaseq.deg" and "current_deg" not in saved:
                raise SkillError("DEG_INPUT_VERSION_UNVERIFIED", "Plan predates DEG source verification. Request a new customization plan.")
            route = ChatRoute(request_type="customization", target_skill=ticket.decision.target_skill,
                              source_kind="filtered_deg" if source.skill_name == "rnaseq.filter_by_annotation" else "skill_result")
            selection = resolve_customization_source(self.manager, key, route, ticket.source_run_id)
            if selection.status != "resolved":
                raise SkillError("MISSING_UPSTREAM_DEPENDENCY", "The selected source is no longer usable.")
            files = imported_annotations(self.manager, key) if ticket.decision.mode == "annotation_filter" and ticket.decision.parameters.get("keywords") else []
            engine = self.engine(key)
            validate_decision(ticket.decision, self.registry, ticket.decision.target_skill, engine.available_annotations(files))
            raw = saved["provider_response"]
            # Restore the actual saved provider metadata, never fabricate a new LLM response.
            provider = ChatResponse(**raw)
            saved["status"] = "running"
            write_json(path, saved)  # At-most-once marker before analysis effects.
            report = engine.execute(CustomizationPlan(ticket.decision, provider, ticket.request),
                                    source_run_id=ticket.source_run_id, annotation_files=files, current_deg=saved.get("current_deg"))
            saved.update(status="completed", report=report)
            write_json(path, saved)
            return report
