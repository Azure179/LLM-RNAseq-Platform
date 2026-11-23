"""Deterministic, project-local result selection. Never infer intent from user text."""

from typing import Literal
from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_ref import ResultResolution
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workspace.deg_reuse import DegInputSnapshot, eligibility


class ResultResolver:
    def __init__(self, manager: ProjectManager, project_key: str) -> None:
        self.manager, self.project_key = manager, project_key

    def find_compatible_result(self, *, skill_name: str, required_outputs: tuple[str, ...] = (),
                               run_id: str | None = None, treatment: str | None = None,
                               control: str | None = None,
                               selection: Literal["unspecified", "recent", "latest"] = "unspecified",
                               expected_deg_parameters: dict | None = None) -> ResultResolution:
        browser = ResultManager(self.manager, self.project_key)
        refs = browser.list_result_refs()
        candidates = [r for r in refs if r.status == "succeeded" and r.skill_name == skill_name
            and (treatment is None or r.treatment == treatment)
            and (control is None or r.control == control) and set(required_outputs) <= r.outputs.keys()]
        if run_id is not None:
            candidates = [r for r in candidates if r.run_id == run_id]
        elif selection == "recent":
            by_id = {r.run_id: r for r in refs}
            compatible = {r.run_id for r in candidates}

            def ancestors(ids: set[str]) -> set[str]:
                seen = set()
                while ids:
                    level = ids - seen
                    found = level & compatible
                    if found:
                        return found  # Nearest compatible generation, not both child and parent.
                    seen.update(level)
                    ids = {parent for item in level if item in by_id for parent in by_id[item].parent_run_ids}
                return set()

            candidates_by_id = {r.run_id: r for r in candidates}
            candidates = []
            for row in reversed(ChatStore(self.manager, self.project_key).history()):
                if row.get("role") != "assistant":
                    continue
                previous = row.get("result", {})
                ids = {previous.get("run_id"),
                       *[s.get("run_id") for s in previous.get("steps", [])]} - {None}
                found = ancestors(ids)
                if not found and previous.get("source_run_id"):
                    found = ancestors({previous["source_run_id"]})
                if found:
                    candidates = [candidates_by_id[item] for item in sorted(found)]
                    break
            # 'Just now' with no compatible chat lineage never falls back to a global latest run.
        elif selection == "latest" and candidates:
            if any(r.time_source != "recorded" for r in candidates):
                return ResultResolution(status="ambiguous", candidates=candidates, error_code="RESULT_TIME_UNAVAILABLE")
            candidates = [max(candidates, key=lambda r: r.sort_key)]
        current = None
        if candidates and skill_name == "rnaseq.deg" and run_id is None:
            try:
                current = DegInputSnapshot.current(self.manager, self.project_key)
            except (SkillError, ProjectError) as error:
                return ResultResolution(status="not_found", error_code=error.code)
            checked = [(r, eligibility(browser, r, current, expected_deg_parameters)) for r in candidates]
            candidates = [r for r, code in checked if code == "eligible"]
            if not candidates:
                return ResultResolution(status="not_found", error_code=checked[0][1])
            required_outputs = required_outputs or ("deg_csv",)
        # Verify AFTER semantic selection: an altered 'just now/latest' result must
        # not silently fall back to an older valid analysis.
        history = ProjectHistory(self.manager, self.project_key)
        verified = []
        for ref in candidates:
            try:
                for output in required_outputs:
                    history.artifact(ref.run_id, output)
                verified.append(ref)
            except (SkillError, ProjectError):
                continue
        candidates = verified
        if len(candidates) == 1:
            return ResultResolution(status="resolved", result=candidates[0],
                                    current_input_sha256=current.model_dump() if current else None)
        return ResultResolution(status="ambiguous" if candidates else "not_found", candidates=candidates,
            error_code="SOURCE_SELECTION_REQUIRED" if candidates else "MISSING_UPSTREAM_DEPENDENCY")
