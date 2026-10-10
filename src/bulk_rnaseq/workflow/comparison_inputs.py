"""Prepare explicit pairwise INPUTS; never alter a Skill or the project originals."""

import csv
from pathlib import Path

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import validate_expression_inputs, validate_inputs


def prepare_comparison_inputs(counts: Path, metadata: Path, treatment: str,
                              control: str, folder: Path) -> dict[str, object]:
    overview = validate_expression_inputs(counts, metadata, "condition")
    observed = set(overview.sample_groups)
    if treatment == control:
        raise SkillError("INVALID_PARAMETER", "Treatment and control must differ.")
    if not {treatment, control} <= observed:
        raise SkillError("GROUP_NOT_FOUND", "Requested comparison is absent from metadata.")
    if observed == {treatment, control}:
        return {}
    selected = [sample for sample, group in zip(overview.sample_ids, overview.sample_groups)
                if group in {treatment, control}]
    folder.mkdir(exist_ok=True)
    pair_counts, pair_metadata = folder / "comparison_counts.csv", folder / "comparison_metadata.csv"
    with counts.open(encoding="utf-8-sig", newline="") as source, pair_counts.open("x", encoding="utf-8", newline="") as target:
        reader, writer = csv.reader(source), csv.writer(target)
        header = next(reader)
        columns = [0, *[header.index(sample) for sample in selected]]
        writer.writerow([header[i] for i in columns])
        for row in reader:
            if row:
                writer.writerow([row[i] for i in columns])
    with metadata.open(encoding="utf-8-sig", newline="") as source, pair_metadata.open("x", encoding="utf-8", newline="") as target:
        reader = csv.DictReader(source)
        rows = {row["sample_id"]: row for row in reader}
        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows[sample] for sample in selected)
    validate_inputs(pair_counts, pair_metadata, treatment, control)
    return {"count_matrix_path": pair_counts, "metadata_path": pair_metadata,
            "selected_samples": selected,
            "excluded_samples": [s for s in overview.sample_ids if s not in selected]}
