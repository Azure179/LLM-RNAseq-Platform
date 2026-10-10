"""Host-owned inventories. No filesystem paths are copied into a workflow plan."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from bulk_rnaseq.workspace.models import ProjectContext
from bulk_rnaseq.workspace.file_models import FileRole


@dataclass(frozen=True)
class HistoricalRun:
    project_id: str
    run_id: str
    skill_name: str
    status: str
    outputs: frozenset[str]


class HistoryLookup(Protocol):
    """Host supplies exact-run lookup, backed by ResultManager in a later phase.

    Implementations must verify artifacts exist within this project and return
    actual output keys, never predictions. No implicit 'latest' selection.
    """

    def lookup(self, project_id: str, run_id: str) -> HistoricalRun | None: ...


@dataclass(frozen=True)
class WorkflowContext:
    project_id: str
    available_inputs: frozenset[str]
    confirmed_parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: HistoryLookup | None = None
    # Host-only handle permits rechecking existence/readability after planning.
    # Never serialize this object into a model prompt or a WorkflowPlan.
    project: ProjectContext | None = field(default=None, repr=False)

    @classmethod
    def from_project_context(
        cls, project: ProjectContext, *,
        confirmed_parameters: dict[str, dict[str, Any]] | None = None,
        history: HistoryLookup | None = None,
    ) -> "WorkflowContext":
        """Check bound files, not biological content. Import validation remains authoritative.

        Pass confirmed_parameters from FileImportService.build_execution_context
        to reuse the actual group column; never guess group names or comparisons.
        """
        available = set()
        for file_role in FileRole:
            role = file_role.value
            path = getattr(project, role)
            if path is None:
                continue
            try:
                if (project.input_root.resolve().is_relative_to(project.project_root.resolve())
                        and path.is_file() and path.resolve().is_relative_to(project.input_root.resolve())):
                    with path.open("rb") as handle:
                        handle.read(1)  # Readability only, not expression/mapping validation.
                    available.add(role)
            except OSError:
                continue
        return cls(project.project_key, frozenset(available),
                   {} if confirmed_parameters is None else confirmed_parameters, history, project)
