"""Shared Chat/API source selection and imported annotation lookup; no intent parsing."""

from pathlib import Path

from bulk_rnaseq.run_io import sha256
from bulk_rnaseq.workspace import FileImportService, FileRole, ProjectManager, ProjectError
from bulk_rnaseq.workspace.chat_router import ChatRoute
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_ref import ResultResolution
from bulk_rnaseq.workspace.result_resolver import ResultResolver


def resolve_customization_source(manager: ProjectManager, key: str, route: ChatRoute,
                                 source_run_id: str | None = None) -> ResultResolution:
    browser = ResultManager(manager, key)
    source_skill = "rnaseq.filter_by_annotation" if route.source_kind == "filtered_deg" else route.target_skill
    identity = source_run_id or route.source_run_id
    if identity:
        source = browser.get_run(identity)
        allowed = {source_skill}
        if route.target_skill == "rnaseq.volcano":
            allowed.add("rnaseq.deg")
        if source.skill_name not in allowed:
            raise ProjectError("CUSTOMIZATION_SOURCE_MISMATCH", "Selected source is incompatible with this customization.")
        source_skill = source.skill_name
    elif not browser.list_runs(skill_name=source_skill, status="succeeded") and route.target_skill == "rnaseq.volcano":
        source_skill = "rnaseq.deg"
    return ResultResolver(manager, key).find_compatible_result(skill_name=source_skill, run_id=identity,
        treatment=route.treatment, control=route.control, selection=route.source_reference)


def imported_annotations(manager: ProjectManager, key: str) -> list[Path]:
    importer, project = FileImportService(manager), manager.open_project(key)
    paths, hashes = [], set()
    for role in (FileRole.GO_MAPPING, FileRole.KEGG_MAPPING):
        record = importer._record(project, role)
        if record:
            path = manager._safe_path(project.root / record["original_path"])
            checksum = sha256(path)
            if checksum not in hashes:
                paths.append(path)
                hashes.add(checksum)
    return paths
