"""Small, typed facts from verified SkillResult summaries; never open result tables."""

from dataclasses import dataclass
import json
import math
from typing import Any

from bulk_rnaseq.llm.exceptions import DeepSeekError
from bulk_rnaseq.result_schema import SkillResult


# Presentation labels, NOT a second input schema or statistical implementation.
METRICS = {
    "rnaseq.deg": {"genes_analyzed": "分析基因数", "genes_with_pvalue": "有有效pvalue的基因数",
                   "significant_genes": "差异基因数", "upregulated_genes": "上调基因数", "downregulated_genes": "下调基因数"},
    "rnaseq.pca": {"n_samples": "样本数", "n_groups": "分组数", "pc1_variance": "PC1解释方差",
                   "pc2_variance": "PC2解释方差"},
    "rnaseq.volcano": {"n_total": "结果基因数", "n_up": "上调基因数", "n_down": "下调基因数",
                       "n_ns": "NS基因数", "n_plotted": "实际绘图基因数"},
    "rnaseq.heatmap": {"n_genes_requested": "请求基因数", "n_genes_used": "实际热图基因数", "n_samples": "样本数"},
    "rnaseq.go": {"n_query_input": "输入基因数", "n_query_mapped": "实际映射基因数", "n_universe": "有效背景基因数",
                  "n_terms_tested": "检验GO条目数", "n_enriched": "显著GO条目数"},
    "rnaseq.kegg": {"n_query_input": "输入基因数", "n_query_mapped": "实际映射基因数", "n_universe": "有效背景基因数",
                    "n_terms_tested": "检验通路数", "n_enriched": "显著KEGG通路数"},
}
PARAMETERS = {
    "rnaseq.deg": {"treatment": "实验组", "control": "对照组", "alpha": "padj阈值（严格小于）",
                   "lfc_threshold": "绝对log2FC阈值（大于等于）"},
    "rnaseq.pca": {"group_column": "分组列"},
    "rnaseq.volcano": {"padj_threshold": "padj阈值（严格小于）", "log2fc_threshold": "绝对log2FC阈值（大于等于）"},
    "rnaseq.heatmap": {"group_column": "分组列", "padj_threshold": "padj阈值（严格小于）",
                       "log2fc_threshold": "绝对log2FC阈值（大于等于）"},
    "rnaseq.go": {"ontology": "GO类别", "pvalue_threshold": "pvalue阈值（小于等于）", "padj_threshold": "padj阈值（小于等于）"},
    "rnaseq.kegg": {"pvalue_threshold": "pvalue阈值（小于等于）", "padj_threshold": "padj阈值（小于等于）"},
}


@dataclass(frozen=True)
class FormattedResult:
    skill_name: str
    facts: tuple[str, ...]
    warnings: tuple[str, ...]
    output_names: tuple[str, ...]
    empty: bool

    def to_dict(self) -> dict[str, Any]:
        return {"skill_name": self.skill_name, "status": "succeeded", "facts": list(self.facts),
                "warnings": list(self.warnings), "output_names": list(self.output_names), "empty": self.empty,
                "scope": "Summary only; no gene/term names, tables, causal or group-significance conclusions supplied."}


def format_skill_result_for_llm(result: SkillResult) -> FormattedResult:
    """Fail closed on malformed metrics; keep complete result/provenance local."""
    try:
        if result.status != "succeeded" or result.skill_name not in METRICS:
            raise ValueError("Unsupported successful result")
        facts = []
        for key, label in METRICS[result.skill_name].items():
            value = result.summary[key]
            if key.endswith("_variance"):
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError("Invalid variance")
                display = f"{value:.2f}%"  # Display rounding only, original result unchanged.
            else:
                if type(value) is not int or value < 0:
                    raise ValueError("Invalid count")
                display = str(value)
            facts.append(f"{label}为{display}")
        if result.skill_name == "rnaseq.deg":
            s = result.summary
            # Existing DEG permits lfc_threshold=0: zero-effect selected rows are
            # neither up nor down. Do not introduce a stricter statistical rule.
            if not (s["upregulated_genes"] + s["downregulated_genes"] <= s["significant_genes"]
                    <= s["genes_with_pvalue"] <= s["genes_analyzed"]):
                raise ValueError("Inconsistent DEG counts")
        params = result.inputs.get("parameters", result.metadata.get("parameters", result.inputs))
        if not isinstance(params, dict):
            raise ValueError("Invalid parameters")
        for key, label in PARAMETERS[result.skill_name].items():
            if key not in params:
                facts.append(f"当前结果未提供{label}")
                continue
            value = params[key]
            if key in {"treatment", "control", "group_column", "ontology"}:
                if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
                    raise ValueError("Invalid label")
            elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid threshold")
            elif key in {"alpha", "padj_threshold", "pvalue_threshold"} and value > 1:
                raise ValueError("Invalid probability")
            facts.append(f"{label}为{value}")
        empty = result.skill_name in {"rnaseq.go", "rnaseq.kegg"} and result.summary["n_enriched"] == 0
        if empty:
            facts.append("分析成功，但在当前阈值下未检测到显著富集结果")
        warnings = []
        for item in result.warnings:
            # Preserve messages and count, not stderr, arbitrary details or host paths.
            suffix = ""
            count = item["details"].get("count")
            if type(count) is int and count >= 0:
                suffix = f" (count={count})"
            warnings.append(f"{item['code']}: {item['message']}{suffix}")
        formatted = FormattedResult(result.skill_name, tuple(facts), tuple(warnings), tuple(sorted(result.outputs)), empty)
        # Explicit bound rather than silently dropping warnings or truncating facts.
        if len(json.dumps(formatted.to_dict(), ensure_ascii=False)) > 10000:
            raise ValueError("Context exceeds bounded summary size")
        return formatted
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DeepSeekError("INVALID_SKILL_RESULT", "Cannot build a bounded, consistent context from this SkillResult.") from None
