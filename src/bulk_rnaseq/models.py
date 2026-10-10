"""Small typed data contracts; no framework is needed for the current CLI."""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Self
from bulk_rnaseq.result_schema import SkillResult


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultEnvelope:
    """Compatibility facade: retain old constructors/Path attributes, share one result."""
    _result: SkillResult | None = field(default=None, repr=False, compare=False)

    def _legacy_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_skill_result(self) -> SkillResult:
        return SkillResult.from_dict(self._result.to_dict()) if self._result is not None else SkillResult.from_legacy_summary(self._legacy_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self._result or SkillResult.from_legacy_summary(self._legacy_dict())
        return result.to_dict(include_legacy=True)

    def with_summary(self, summary: dict[str, Any]) -> Self:
        return replace(self, _result=SkillResult.from_legacy_summary(summary))

    @property
    def skill_name(self) -> str:
        return self.to_skill_result().skill_name

    @property
    def inputs(self) -> dict[str, Any]:
        return self.to_skill_result().inputs

    @property
    def outputs(self) -> dict[str, str]:
        return self.to_skill_result().outputs

    @property
    def summary(self) -> dict[str, Any]:
        return self.to_skill_result().summary

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return self.to_skill_result().warnings

    @property
    def metadata(self) -> dict[str, Any]:
        return self.to_skill_result().metadata


@dataclass(frozen=True, slots=True)
class ExpressionOverview:
    """Shared counts/metadata facts, in count-matrix sample order."""

    gene_count: int
    sample_ids: tuple[str, ...]
    analyzed_gene_ids: frozenset[str]
    sample_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InputOverview:
    gene_count: int
    sample_ids: tuple[str, ...]
    analyzed_gene_ids: frozenset[str]
    treatment_samples: int
    control_samples: int


@dataclass(frozen=True, slots=True)
class DegRunResult(ResultEnvelope):
    run_id: str
    output_dir: Path
    deg_csv: Path
    filtered_csv: Path
    summary_json: Path
    stdout: str
    stderr: str
    status: str = "succeeded"
    return_code: int = 0

    def _legacy_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "operation": "differential_expression",
            "run_id": self.run_id, "status": self.status,
            "output_dir": str(self.output_dir), "deg_csv": str(self.deg_csv),
            "filtered_csv": str(self.filtered_csv), "summary_json": str(self.summary_json),
            "artifacts": {"deg_csv": self.deg_csv.name,
                          "filtered_csv": self.filtered_csv.name,
                          "summary_json": self.summary_json.name},
            "return_code": self.return_code, "stdout": self.stdout,
            "stderr": self.stderr, "error": None,
        }


@dataclass(frozen=True, slots=True)
class PcaRunResult(ResultEnvelope):
    run_id: str
    output_dir: Path
    coordinates_csv: Path
    variance_json: Path
    plot_png: Path
    summary_json: Path
    stdout: str
    stderr: str
    status: str = "succeeded"
    return_code: int = 0

    def _legacy_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "operation": "pca",
            "run_id": self.run_id, "status": self.status,
            "output_dir": str(self.output_dir),
            "coordinates_csv": str(self.coordinates_csv),
            "variance_json": str(self.variance_json), "plot_png": str(self.plot_png),
            "summary_json": str(self.summary_json),
            "artifacts": {"coordinates": self.coordinates_csv.name,
                          "variance": self.variance_json.name, "plot": self.plot_png.name,
                          "summary_json": self.summary_json.name},
            "return_code": self.return_code, "stdout": self.stdout,
            "stderr": self.stderr, "error": None,
        }


@dataclass(frozen=True, slots=True)
class VolcanoRunResult(ResultEnvelope):
    run_id: str
    output_dir: Path
    plot_png: Path
    plot_data_csv: Path
    summary_json: Path
    stdout: str
    stderr: str
    status: str = "succeeded"
    return_code: int = 0

    def _legacy_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "operation": "volcano",
            "run_id": self.run_id, "status": self.status,
            "output_dir": str(self.output_dir), "plot_png": str(self.plot_png),
            "plot_data_csv": str(self.plot_data_csv), "summary_json": str(self.summary_json),
            "artifacts": {"plot": self.plot_png.name, "plot_data": self.plot_data_csv.name,
                          "summary_json": self.summary_json.name},
            "return_code": self.return_code, "stdout": self.stdout,
            "stderr": self.stderr, "error": None,
        }


@dataclass(frozen=True, slots=True)
class HeatmapRunResult(ResultEnvelope):
    run_id: str
    output_dir: Path
    stdout: str
    stderr: str
    status: str = "succeeded"
    return_code: int = 0

    @property
    def summary_json(self) -> Path:
        return self.output_dir / "summary.json"

    def _legacy_dict(self) -> dict[str, Any]:
        artifacts = {"plot": "heatmap.png", "matrix": "heatmap_matrix.csv", "selected_genes": "selected_genes.csv",
                     "normalized_expression": "normalized_expression.csv", "summary_json": "summary.json"}
        return {"schema_version": "1.0", "operation": "heatmap", "run_id": self.run_id,
                "status": self.status, "output_dir": str(self.output_dir), "summary_json": str(self.summary_json),
                "artifacts": artifacts, "return_code": self.return_code,
                "stdout": self.stdout, "stderr": self.stderr, "error": None}


@dataclass(frozen=True, slots=True)
class GoRunResult(ResultEnvelope):
    run_id: str
    output_dir: Path
    stdout: str
    stderr: str
    status: str = "succeeded"
    return_code: int = 0

    @property
    def summary_json(self) -> Path:
        return self.output_dir / "summary.json"

    def _legacy_dict(self) -> dict[str, Any]:
        artifacts = {"enrichment": "go_enrichment.csv", "all_results": "go_all_results.csv",
                     "dotplot": "go_dotplot.png", "barplot": "go_barplot.png", "query": "query_used.csv",
                     "universe": "universe_used.csv", "mapping": "mapping_used.csv", "summary_json": "summary.json"}
        return {"schema_version": "1.0", "operation": "go_enrichment", "run_id": self.run_id,
                "status": self.status, "output_dir": str(self.output_dir), "summary_json": str(self.summary_json),
                "artifacts": artifacts, "return_code": self.return_code,
                "stdout": self.stdout, "stderr": self.stderr, "error": None}


@dataclass(frozen=True, slots=True)
class KeggRunResult(ResultEnvelope):
    run_id: str
    output_dir: Path
    stdout: str
    stderr: str
    status: str = "succeeded"
    return_code: int = 0

    @property
    def summary_json(self) -> Path:
        return self.output_dir / "summary.json"

    def _legacy_dict(self) -> dict[str, Any]:
        artifacts = {"enrichment": "kegg_enrichment.csv", "all_results": "kegg_all_results.csv",
                     "dotplot": "kegg_dotplot.png", "barplot": "kegg_barplot.png", "query": "query_used.csv",
                     "universe": "universe_used.csv", "mapping": "mapping_used.csv",
                     "pathway_aliases": "pathway_id_aliases.csv", "summary_json": "summary.json"}
        return {"schema_version": "1.0", "operation": "kegg_enrichment", "run_id": self.run_id,
                "status": self.status, "output_dir": str(self.output_dir), "summary_json": str(self.summary_json),
                "artifacts": artifacts, "return_code": self.return_code,
                "stdout": self.stdout, "stderr": self.stderr, "error": None}
