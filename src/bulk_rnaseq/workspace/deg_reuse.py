"""Read-only evidence for reusing a pairwise DEG against the current analysis inputs."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.validation import scan_metadata, validate_parameters
from bulk_rnaseq.workspace.errors import ProjectError
from bulk_rnaseq.workspace.file_import import FileImportService
from bulk_rnaseq.workspace.file_models import FileRole
from bulk_rnaseq.workspace.manager import ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_ref import ResultRef


class DegInputSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    count_matrix: str = Field(pattern="^[0-9a-f]{64}$")
    metadata: str = Field(pattern="^[0-9a-f]{64}$")

    @classmethod
    def current(cls, manager: ProjectManager, key: str) -> "DegInputSnapshot":
        def paths() -> tuple[Path, Path]:
            context = manager.get_project_context(key, require_inputs=False)
            if not context.count_matrix or not context.metadata:
                raise SkillError("DEG_INPUT_VERSION_UNVERIFIED", "Current counts and metadata are required.")
            return context.count_matrix, context.metadata

        before = paths()
        project = manager.open_project(key)
        service = FileImportService(manager)
        hashes = {}
        try:
            for role, path in zip((FileRole.COUNT_MATRIX, FileRole.METADATA), before, strict=True):
                relative = getattr(project.metadata.files, role.value)
                if not relative or project.root / relative != path:
                    raise SkillError("PROJECT_INPUT_CHANGED", "DEG binding changed during verification.")
                if path.is_relative_to(project.root / "input" / role.value):
                    # _record validates original AND normalized bytes before returning the digest.
                    record = service._record(project, role)
                    hashes[role.value] = record["stored_sha256"]
                else:
                    # Older host-bound files do not have import bundles.
                    hashes[role.value] = sha256(path)
            snapshot = cls(**hashes)
        except OSError:
            raise SkillError("DEG_INPUT_VERSION_UNVERIFIED", "Current DEG inputs could not be read.") from None
        if paths() != before:
            raise SkillError("PROJECT_INPUT_CHANGED", "DEG input bindings changed during verification.")
        return snapshot

    def verify(self, manager: ProjectManager, key: str) -> None:
        if self != self.current(manager, key):
            raise SkillError("STALE_DEG_RESULT", "DEG source differs from the current counts or metadata.")


def eligibility(browser: ResultManager, ref: ResultRef, current: DegInputSnapshot,
                expected: dict | None = None) -> Literal["eligible", "STALE_DEG_RESULT", "DEG_INPUT_VERSION_UNVERIFIED"]:
    """Compare original project inputs, never a selected two-group matrix to the full matrix."""
    try:
        payload = browser._json(browser.root / ref.source)
        if ref.source.endswith("tool_execution.json"):
            payload = payload["skill_result"]
        if (payload.get("run_id"), payload.get("skill_name"), payload.get("status")) != (
            ref.run_id, "rnaseq.deg", "succeeded"
        ):
            return "DEG_INPUT_VERSION_UNVERIFIED"
        params = payload["inputs"]["parameters"]
        design_factor = params.get("design_factor")
        hashes = payload.get("metadata", {}).get("provenance", {}).get("input_sha256", {})
        selection = None
        if ref.workflow_id:
            directory = browser.validate_result_path(ref.outputs["deg_csv"]).parent.parent
            record = browser._json(directory / "provenance.json")
            if (directory.parent.parent != browser.root / "workflows" / ref.workflow_id
                    or record["run_id"] != ref.run_id or record["step_id"] != ref.step_id
                    or record["skill_name"] != "rnaseq.deg" or record["status"] != "succeeded"):
                return "DEG_INPUT_VERSION_UNVERIFIED"
            inputs = record["inputs"]
            if record.get("design_formula") not in (None, "~condition"):
                return "DEG_INPUT_VERSION_UNVERIFIED"
            if design_factor is None and record.get("design_formula") == "~condition":
                design_factor = "condition"
            selection = record.get("sample_selection")
            original = {}
            for role in ("count_matrix", "metadata"):
                binding = record["upstream_sources"][role + "_path"]
                if binding["source"] != "project_input" or binding["key"] != role:
                    return "DEG_INPUT_VERSION_UNVERIFIED"
                if ("derived_sha256" in binding) != (selection is not None):
                    return "DEG_INPUT_VERSION_UNVERIFIED"
                if role in hashes and hashes[role] != binding.get("derived_sha256", binding["sha256"]):
                    return "DEG_INPUT_VERSION_UNVERIFIED"
                original[role] = binding["sha256"]
            hashes = original
            if any(k in params and params[k] != inputs[k] for k in ("alpha", "lfc_threshold", "treatment", "control")):
                return "DEG_INPUT_VERSION_UNVERIFIED"
        else:
            inputs = params
        if design_factor != "condition":
            return "DEG_INPUT_VERSION_UNVERIFIED"
        validate_parameters(inputs["treatment"], inputs["control"], inputs["alpha"], inputs["lfc_threshold"], 1)
        if (inputs["treatment"], inputs["control"]) != (ref.treatment, ref.control):
            return "DEG_INPUT_VERSION_UNVERIFIED"
        if expected and any(inputs.get(k) != v for k, v in expected.items()):
            return "STALE_DEG_RESULT"
        if current != DegInputSnapshot(count_matrix=hashes["count_matrix"], metadata=hashes["metadata"]):
            return "STALE_DEG_RESULT"
        groups = scan_metadata(browser.context.metadata, "condition")
        contrast = {ref.treatment, ref.control}
        if not contrast <= set(groups.values()):
            return "DEG_INPUT_VERSION_UNVERIFIED"
        included = {sample for sample, group in groups.items() if group in contrast}
        if selection is None:
            if included != set(groups):
                return "DEG_INPUT_VERSION_UNVERIFIED"
        elif (set(selection["selected_samples"]) != included
              or set(selection["excluded_samples"]) != set(groups) - included):
            return "DEG_INPUT_VERSION_UNVERIFIED"
        return "eligible"
    except (KeyError, TypeError, ValueError, AttributeError, ProjectError, SkillError):
        return "DEG_INPUT_VERSION_UNVERIFIED"
