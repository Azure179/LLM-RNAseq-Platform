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
            run = self.results.get_run(run_id)
            if run.run_id != run_id:
                return None  # A backend request ID is not a Skill run ID.
            keys = set()
            for artifact in run.artifacts:
                if artifact.artifact_id != "result_record":
                    try:
                        self.results.get_artifact(run_id, artifact.artifact_id)
                        keys.add(artifact.artifact_id)
                    except ProjectError:
                        pass
            return HistoricalRun(project_id, run_id, run.skill_name, run.status, frozenset(keys))
        except ProjectError:
            return None

    def artifact(self, run_id: str, output: str) -> tuple[Path, str]:
        run = self.lookup(self.project_key, run_id)
        if run is None or run.status != "succeeded" or output not in run.outputs:
            raise SkillError("MISSING_UPSTREAM_DEPENDENCY", "Historical output is unavailable.", operation="workflow")
        info = self.results.get_run(run_id)
        artifact = self.results.get_artifact(run_id, output)
        path = self.results.validate_result_path(artifact.relative_path)
        payload = self.results._json(self.results.root / info.source)
        if info.source.endswith("tool_execution.json"):
            payload = payload["skill_result"]
        provenance = (payload.get("metadata", {}).get("provenance", {})
                      if payload.get("schema_version") == "2.0" else payload.get("provenance", {}))
        expected = provenance.get("artifact_sha256", {}).get(path.name)
        if not expected:
            raise SkillError("UPSTREAM_PROVENANCE_MISSING", "Historical input requires its recorded artifact checksum.", operation="workflow")
        if sha256(path) != expected:
            raise SkillError("UPSTREAM_ARTIFACT_CHANGED", "Historical artifact checksum does not match its recorded result.", operation="workflow")
        return path, expected
