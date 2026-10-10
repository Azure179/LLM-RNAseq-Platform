"""Check PCA artifacts against validated inputs before publishing success."""

import csv
import json
import math
from pathlib import Path
from typing import Any

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import ExpressionOverview
from bulk_rnaseq.plot_artifacts import check_png


PCA_FILES = ("pca_coordinates.csv", "pca_variance.json", "pca_plot.png")


def verify_pca_outputs(stage: Path, overview: ExpressionOverview) -> dict[str, Any]:
    for name in PCA_FILES:
        path = stage / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SkillError("OUTPUT_MISSING", "PCA did not create a required non-empty output.",
                             details={"artifact": name})
    try:
        with (stage / PCA_FILES[0]).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != ["sample", "PC1", "PC2", "group"]:
                raise ValueError("Unexpected PCA coordinate columns")
            rows = list(reader)
        if len(rows) != len(overview.sample_ids):
            raise ValueError("Unexpected sample count in PCA coordinates")
        pc1, pc2 = [], []
        for row, sample, group in zip(rows, overview.sample_ids, overview.sample_groups):
            if None in row or row["sample"] != sample or row["group"] != group:
                raise ValueError("PCA samples/groups/order differ from validated metadata")
            pc1.append(float(row["PC1"]))
            pc2.append(float(row["PC2"]))
        if not all(math.isfinite(value) for value in pc1 + pc2):
            raise ValueError("Non-finite PCA coordinates")
        variance = json.loads((stage / PCA_FILES[1]).read_text(encoding="utf-8"))
        if not isinstance(variance, dict) or set(variance) != {"PC1", "PC2"}:
            raise ValueError("Unexpected variance JSON")
        for value in variance.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 100:
                raise ValueError("Variance must be a finite percentage in (0, 100]")
        if variance["PC1"] + variance["PC2"] > 100 + 1e-7 or variance["PC2"] > variance["PC1"] + 1e-7:
            raise ValueError("Invalid ordering or sum of explained variances")
        energy1, energy2 = math.fsum(x * x for x in pc1), math.fsum(x * x for x in pc2)
        if min(energy1, energy2) <= 0:
            raise ValueError("Degenerate PCA coordinates")
        for scores, energy in ((pc1, energy1), (pc2, energy2)):
            if abs(math.fsum(scores)) > 1e-6 * math.sqrt(energy * len(scores)):
                raise ValueError("PCA coordinates are not centered")
        if abs(math.fsum(x * y for x, y in zip(pc1, pc2))) > 1e-6 * math.sqrt(energy1 * energy2):
            raise ValueError("Principal components are not orthogonal")
        if not math.isclose(energy1 / energy2, variance["PC1"] / variance["PC2"], rel_tol=1e-6):
            raise ValueError("Coordinates disagree with reported variance ratio")
        check_png(stage / PCA_FILES[2])
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, csv.Error) as error:
        raise SkillError("OUTPUT_INVALID", "PCA artifacts failed validation.", details={"reason": str(error)}) from error
    return {"n_samples": len(rows), "n_groups": len(set(overview.sample_groups)),
            "pc1_variance": variance["PC1"], "pc2_variance": variance["PC2"]}
