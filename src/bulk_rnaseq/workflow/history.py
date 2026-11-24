"""Exact, project-scoped historical artifacts. Never select the latest run implicitly."""

from pathlib import Path

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager
from .context import HistoricalRun


class ProjectHistory:
    def __init__(self, manager: ProjectManager, project_key: str) -> None:
        self.results = ResultManager(manager, project_key)
        self.project_key = project_key

    def lookup(self, project_id: str, run_id: str) -> HistoricalRun | None:
        if project_id != self.project_key:
            return None
        try:
            self.results.refresh()
            matches = [r for r in self.results.list_result_refs() if r.run_id == run_id]
            if len(matches) != 1:
                return None
            run = matches[0]
            keys = set()
            for key, path in run.outputs.items():
                try:
                    self.results.validate_result_path(path)
                    keys.add(key)
                except ProjectError:
                    pass
            return HistoricalRun(project_id, run_id, run.skill_name, run.status, frozenset(keys))
        except ProjectError as error:
            if error.code == "WORKSPACE_BUSY":
                raise SkillError("WORKSPACE_BUSY", "Workspace is busy; historical results have not been declared missing.",
                                 operation="workflow") from error
            return None

    def artifact(self, run_id: str, output: str) -> tuple[Path, str]:
        run = self.lookup(self.project_key, run_id)
        if run is None or run.status != "succeeded" or output not in run.outputs:
            raise SkillError("MISSING_UPSTREAM_DEPENDENCY", "Historical output is unavailable.", operation="workflow")
        info = next(r for r in self.results.list_result_refs() if r.run_id == run_id)
        path = self.results.validate_result_path(info.outputs[output])
        payload = self.results._json(self.results.root / info.source)
        if info.source.endswith("tool_execution.json"):
            payload = payload["skill_result"]
        from bulk_rnaseq.result_schema import SkillResult
        actual = SkillResult.from_dict(payload)
        if (actual.run_id != run_id or actual.skill_name != info.skill_name or actual.status != "succeeded"
                or output not in actual.outputs or self.results.validate_result_path(actual.outputs[output]) != path):
            raise SkillError("UPSTREAM_REFERENCE_MISMATCH", "Result index differs from its canonical SkillResult.", operation="workflow")
        provenance = (payload.get("metadata", {}).get("provenance", {})
                      if payload.get("schema_version") == "2.0" else payload.get("provenance", {}))
        expected = provenance.get("artifact_sha256", {}).get(path.name)
        if not expected:
            raise SkillError("UPSTREAM_PROVENANCE_MISSING", "Historical input requires its recorded artifact checksum.", operation="workflow")
        if sha256(path) != expected:
            raise SkillError("UPSTREAM_ARTIFACT_CHANGED", "Historical artifact checksum does not match its recorded result.", operation="workflow")
        return path, expected
