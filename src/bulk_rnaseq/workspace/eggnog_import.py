"""One annotation upload -> two immutable mapping versions -> one atomic binding.

Biological parsing belongs to the existing GO/KEGG readers. No ID inference,
annotation lookup, R execution or enrichment occurs during import.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.go_mapping import read_go_mapping
from bulk_rnaseq.kegg_mapping import read_kegg_mapping
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.validation import require_input_file
from bulk_rnaseq.workspace.errors import ProjectError
from bulk_rnaseq.workspace.file_models import FileRole, ImportResult
from bulk_rnaseq.workspace.import_validation import enrich_file_info, validate_file, warning
from bulk_rnaseq.workspace.models import validate_name

if TYPE_CHECKING:
    from bulk_rnaseq.workspace.file_import import FileImportService


def import_eggnog(service: "FileImportService", project_key: str, source_path: Path, *,
                  replace: bool = False, mapping_sheet: str | None = None) -> ImportResult:
    from bulk_rnaseq.workspace.file_import import failure

    action = "replace_eggnog" if replace else "import_eggnog"
    filename = None
    try:
        if type(replace) is not bool or (mapping_sheet is not None and
                (not isinstance(mapping_sheet, str) or not mapping_sheet.strip())):
            raise ProjectError("INVALID_IMPORT_PARAMETER", "Use a boolean replace and a non-empty sheet name.")
        source = Path(source_path)
        try:
            filename = validate_name(source.name)
        except ValueError:
            raise ProjectError("UNSAFE_FILENAME", "Choose a plain, portable annotation filename.") from None
        if source.suffix.lower() not in {".annotations", ".tsv", ".txt", ".csv", ".xlsx"}:
            raise ProjectError("IMPORT_FORMAT_UNSUPPORTED", "eggNOG import supports annotations/TSV/TXT/CSV/XLSX.")
        require_input_file(source)
        project = service.manager.open_project(project_key)
        roles = (FileRole.GO_MAPPING, FileRole.KEGG_MAPPING)
        previous = {role: getattr(project.metadata.files, role.value) for role in roles}
        if any(previous.values()) and not replace:
            raise ProjectError("FILE_ROLE_ALREADY_BOUND", "Confirm replacing both GO/KEGG bindings with eggNOG-derived mappings; previous files will be retained.")
        counts = service._record(project, FileRole.COUNT_MATRIX)
        peers = {FileRole.COUNT_MATRIX: counts} if counts else {}
        runtime = service.manager._safe_path(project.root / "runtime")
        with TemporaryDirectory(prefix="eggnog-", dir=runtime) as temporary:
            stage = Path(temporary)
            snapshot = stage / filename
            shutil.copyfile(source, snapshot)
            source_hash = sha256(snapshot)
            if sha256(source) != source_hash:
                raise ProjectError("IMPORT_SOURCE_CHANGED", "Annotation changed while copying; retry with a stable file.")
            mappings = {FileRole.GO_MAPPING: read_go_mapping(snapshot, mapping_sheet),
                        FileRole.KEGG_MAPPING: read_kegg_mapping(snapshot, mapping_sheet)}
            if any(mapping.format != "eggnog" for mapping in mappings.values()):
                raise ProjectError("EGGNOG_FORMAT_REQUIRED", "Use eggNOG query/GOs/KEGG_Pathway columns here; custom mappings use the separate upload entries.")
            batch_id = str(uuid4())
            bindings, summaries, warnings = {}, {}, []
            # Prepare and validate BOTH complete bundles before publishing metadata.
            for role, mapping in mappings.items():
                bundle = stage / role.value
                (bundle / "original").mkdir(parents=True)
                (bundle / "analysis").mkdir()
                original = bundle / "original" / filename
                shutil.copyfile(snapshot, original)
                table_name = "gene_to_go.tsv" if role == FileRole.GO_MAPPING else "gene_to_kegg.tsv"
                table = bundle / "analysis" / table_name
                with table.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, delimiter="\t")
                    writer.writerow(["gene_id", "go_id" if role == FileRole.GO_MAPPING else "pathway_id"])
                    writer.writerows(sorted(mapping.pairs))
                record = validate_file(table, role)
                record["summary"]["source_format"] = "eggnog"
                record["summary"]["source_rows"] = mapping.n_rows
                record["summary"]["source_unannotated_rows"] = mapping.n_unannotated_rows
                record["summary"]["source_duplicate_pairs"] = mapping.n_duplicate_pairs
                record["warnings"].append(warning("EGGNOG_MAPPING_GENERATED",
                    "Mapping generated from eggNOG; gene IDs are unchanged. Empty annotations were excluded and associations deduplicated.",
                    role=role.value, unannotated_rows=mapping.n_unannotated_rows, duplicate_pairs=mapping.n_duplicate_pairs))
                if role == FileRole.GO_MAPPING:
                    record["warnings"].append(warning("GO_METADATA_NOT_IN_EGGNOG",
                        "eggNOG supplies GO IDs, not GO term names/ontology. Enrichment resolves these through the existing GO annotation dependency."))
                info = enrich_file_info(role, record, {**peers, role: record})
                base = service.manager._safe_path(project.root / "input" / role.value)
                base.mkdir(exist_ok=True)
                destination = service.manager._safe_path(base / batch_id)
                relative = (destination / "analysis" / table_name).relative_to(project.root).as_posix()
                record.update(role=role.value, stored_path=relative, original_filename=filename,
                    original_path=(destination / "original" / filename).relative_to(project.root).as_posix(),
                    source_sha256=source_hash, stored_sha256=sha256(table),
                    imported_at=datetime.now(timezone.utc).isoformat(), mapping_sheet=None,
                    source_kind="eggnog", source_sheet=mapping_sheet, batch_id=batch_id,
                    previous_binding=previous[role])
                write_json(bundle / "import.json", record)
                bindings[role] = relative
                summaries[role.value] = {**info["summary"], "stored_path": relative}
                warnings.extend(info["warnings"])
            for role in roles:
                destination = service.manager._safe_path(project.root / "input" / role.value / batch_id)
                (stage / role.value).rename(destination)
            # A failed CAS leaves only unbound versions; it cannot partially replace
            # the active pair. Old versions are never deleted or silently merged.
            updated = service.manager.commit_file_bindings(project_key, bindings,
                                                           expected_updated_at=project.metadata.updated_at)
        if any(previous.values()):
            warnings.append(warning("PREVIOUS_VERSION_RETAINED", "Previous mapping files are retained. Both active bindings now use the confirmed eggNOG import."))
        result = ImportResult(status="succeeded", action=action, original_filename=filename,
            validation={"format": "PASS", "atomic_mapping_bindings": True}, summary=summaries,
            warnings=warnings, context=service._context(updated),
            message="eggNOG imported; GO and KEGG mapping files generated and bound.")
    except (SkillError, ProjectError, OSError, ValueError, TypeError, KeyError) as error:
        result = failure(action, "eggnog", error, filename)
    service._audit(project_key, result)
    return result
