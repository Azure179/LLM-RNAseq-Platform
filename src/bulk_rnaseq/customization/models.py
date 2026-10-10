"""Customization contracts; Skill parameter definitions remain in their Manifests."""

from typing import Any, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class EnrichmentPlotOptions(Model):
    top_n: int = Field(default=20, ge=1, le=200)
    order_by: Literal["padj", "overlap_count", "fold_enrichment"] = "padj"
    title: str = Field(default="Enrichment results", max_length=160)
    figure_width: int = Field(default=1400, ge=600, le=3000)
    figure_height: int = Field(default=1000, ge=600, le=3000)
    show_count: bool = True


class AnnotationFilterOptions(Model):
    gene_subset: Literal["all", "up", "down"] = "all"
    keywords: list[str] = Field(default_factory=list, max_length=30)
    annotation_sources: list[Literal["gene_description", "GO", "KEGG"]] = Field(default_factory=list)
    match_mode: Literal["any", "all"] = "any"
    order_by: Literal["padj", "log2fc", "abs_log2fc", "gene_id"] = "padj"
    descending: bool = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.keywords and not self.annotation_sources:
            raise ValueError("Keywords require an annotation source")
        if any(not word.strip() or len(word) > 160 or any(ord(c) < 32 for c in word) for word in self.keywords):
            raise ValueError("Use short non-empty literal keywords")
        if len(self.keywords) != len(set(self.keywords)) or len(self.annotation_sources) != len(set(self.annotation_sources)):
            raise ValueError("Duplicate keywords or sources")
        return self


class CustomizationDecision(Model):
    mode: Literal["parameter_override", "annotation_filter", "unsupported"]
    target_skill: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1500)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.mode == "unsupported" and (self.parameters or not self.reason.strip()):
            raise ValueError("Unsupported needs a reason and no executable parameters")
        return self
