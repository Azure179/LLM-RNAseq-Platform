"""Explicit built-in bindings; manifest entry_point strings are documentation, not imports."""

from importlib.resources import files
import json

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.registry import Executor, SkillRegistry
from bulk_rnaseq.skills import run_deg, run_pca, run_volcano, run_heatmap, run_go_enrichment, run_kegg_enrichment


def create_default_registry() -> SkillRegistry:
    bindings: dict[str, Executor | None] = {
        "rnaseq.deg": run_deg,
        "rnaseq.pca": run_pca,
        "rnaseq.volcano": run_volcano,
        "rnaseq.heatmap": run_heatmap,
        "rnaseq.go": run_go_enrichment,
        "rnaseq.kegg": run_kegg_enrichment,
        "rnaseq.gsea": None,
    }
    registry = SkillRegistry()
    resources = files("bulk_rnaseq.manifests")
    for name, executor in bindings.items():
        try:
            manifest = json.loads(resources.joinpath(name + ".json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as cause:
            raise SkillError("REGISTRY_CONFIG_ERROR", "Unable to read built-in manifest.",
                             operation="registry", details={"name": name}) from cause
        if manifest.get("name") != name:
            raise SkillError("INVALID_MANIFEST", "Built-in manifest name does not match its binding.", operation="registry")
        registry.register(manifest, executor)
    return registry
