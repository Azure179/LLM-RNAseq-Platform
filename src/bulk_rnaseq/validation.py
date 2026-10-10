"""CSV and biological preconditions, enforced before launching R."""

from collections.abc import Iterator
from contextlib import contextmanager
import csv
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
from typing import TypedDict

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.models import ExpressionOverview, InputOverview


MISSING = {"", "NA", "N/A", "NAN", "NULL", "NONE"}
MAX_R_INTEGER = 2_147_483_647
NUMERIC = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def validate_parameters(treatment: str, control: str, alpha: float,
                        lfc_threshold: float, timeout: float) -> None:
    for field, value in (("treatment", treatment), ("control", control)):
        if not isinstance(value, str) or value.strip().upper() in MISSING:
            raise SkillError("INVALID_PARAMETER", f"{field} must be a non-empty group name.")
        if value != value.strip():
            raise SkillError("INVALID_PARAMETER", f"{field} must not have surrounding spaces.")
    if treatment == control:
        raise SkillError("IDENTICAL_GROUPS", "Treatment and control must be different.")
    for field, value in (("alpha", alpha), ("lfc_threshold", lfc_threshold), ("timeout", timeout)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SkillError("INVALID_PARAMETER", f"{field} must be a finite number.")
    if not 0 < alpha < 1 or lfc_threshold < 0 or timeout <= 0:
        raise SkillError("INVALID_PARAMETER", "Require 0 < alpha < 1, lfc_threshold >= 0, timeout > 0.")


def require_input_file(path: Path) -> None:
    if not path.exists():
        raise SkillError("FILE_NOT_FOUND", "Input file does not exist.", details={"path": str(path)})
    if not path.is_file():
        raise SkillError("INPUT_NOT_FILE", "Input path is not a regular file.", details={"path": str(path)})


@contextmanager
def csv_rows(path: Path, format_code: str) -> Iterator[Iterator[list[str]]]:
    """Read strict UTF-8 CSV and translate decoding/I/O failures."""
    require_input_file(path)
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield csv.reader(handle, strict=True)
    except (UnicodeError, csv.Error) as error:
        raise SkillError(format_code, "Expected a well-formed UTF-8 CSV file.",
                         details={"path": str(path)}) from error
    except OSError as error:
        raise SkillError("INPUT_READ_ERROR", "Unable to read input file.",
                         details={"path": str(path)}) from error


def _identifier(value: str, field: str, row: int) -> None:
    if value.strip().upper() in MISSING:
        raise SkillError("MISSING_VALUE", "Required identifier or condition is missing.",
                         details={"field": field, "row": row})
    if value != value.strip():
        raise SkillError("INVALID_IDENTIFIER", "Identifiers must not have surrounding spaces.",
                         details={"field": field, "row": row})


def _header(rows: Iterator[list[str]], code: str) -> list[str]:
    header = next(rows, None)
    if not header:
        raise SkillError(code, "CSV must contain a header and data rows.")
    for name in header:
        _identifier(name, "header", 1)
    if len(header) != len(set(header)):
        raise SkillError("DUPLICATE_ID", "CSV column names must be unique.")
    return header


def _count(value: str, row: int, sample: str) -> int:
    details = {"row": row, "sample_id": sample}
    if value.strip().upper() in MISSING:
        raise SkillError("MISSING_VALUE", "Count value is missing.", details=details)
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise SkillError("COUNT_NOT_NUMERIC", "Count must be numeric.", details=details) from error
    if not number.is_finite():
        raise SkillError("COUNT_NOT_FINITE", "Count must be finite.", details=details)
    if not NUMERIC.fullmatch(value.strip()):
        raise SkillError("COUNT_NOT_NUMERIC", "Use ordinary decimal numeric notation.", details=details)
    if number < 0:
        raise SkillError("NEGATIVE_COUNT", "Raw counts must be non-negative.", details=details)
    if number != number.to_integral_value():
        raise SkillError("NON_INTEGER_COUNT", "Raw counts must be integers.", details=details)
    if number > MAX_R_INTEGER:
        raise SkillError("COUNT_OUT_OF_RANGE", "Count exceeds the R integer range.", details=details)
    return int(number)


class CountScan(TypedDict):
    gene_ids: frozenset[str]
    nonzero_gene_ids: frozenset[str]
    sample_names: tuple[str, ...]
    min_count: int
    max_count: int


def scan_count_matrix(counts: Path, *, min_samples: int = 2) -> CountScan:
    """One-pass shared count validation; imports may inspect a single-sample file.

    Skill callers retain the original minimum of two samples and all other checks.
    Gene sets stay internal, never used as fabricated analysis results.
    """
    if type(min_samples) is not int or min_samples < 1:
        raise SkillError("INVALID_PARAMETER", "min_samples must be a positive integer.")
    genes: set[str] = set()
    nonzero: set[str] = set()
    minimum, maximum = MAX_R_INTEGER, 0
    with csv_rows(counts, "COUNT_FORMAT_ERROR") as rows:
        header = _header(rows, "COUNT_FORMAT_ERROR")
        if header[0] != "gene_id" or len(header) < min_samples + 1:
            raise SkillError("COUNT_FORMAT_ERROR", "Expected gene_id followed by sample columns.")
        samples = tuple(header[1:])
        sample_totals = [0] * len(samples)
        for row_number, row in enumerate(rows, 2):
            if not row:
                continue  # Ignore blank lines, matching R's CSV reader.
            if len(row) != len(header):
                raise SkillError("COUNT_FORMAT_ERROR", "Count row width differs from header.",
                                 details={"row": row_number})
            _identifier(row[0], "gene_id", row_number)
            if row[0] in genes:
                raise SkillError("DUPLICATE_ID", "gene_id must be unique.", details={"row": row_number})
            genes.add(row[0])
            values = [_count(value, row_number, sample) for value, sample in zip(row[1:], samples)]
            minimum, maximum = min(minimum, min(values)), max(maximum, max(values))
            if any(values):
                nonzero.add(row[0])
            sample_totals = [total + value for total, value in zip(sample_totals, values)]
    if not genes:
        raise SkillError("COUNT_FORMAT_ERROR", "Count matrix has no gene rows.")
    if not nonzero:
        raise SkillError("NO_ANALYZABLE_GENES", "Count matrix contains only zero counts.")
    if not all(sample_totals):
        raise SkillError("ZERO_LIBRARY", "Each sample must have a positive library size.")

    return {"gene_ids": frozenset(genes), "nonzero_gene_ids": frozenset(nonzero), "sample_names": samples,
            "min_count": minimum, "max_count": maximum}


def validate_expression_inputs(counts: Path, metadata: Path, group_column: str) -> ExpressionOverview:
    """Shared CSV checks, without imposing DEG's two-group design."""
    data = scan_count_matrix(counts)
    samples = data["sample_names"]
    ordered_groups = validate_metadata(metadata, samples, group_column)
    return ExpressionOverview(len(data["gene_ids"]), samples, data["nonzero_gene_ids"], ordered_groups)


def scan_metadata(metadata: Path, group_column: str) -> dict[str, str]:
    """Validate annotations independently; preserve their original sample order."""
    if not isinstance(group_column, str) or not group_column.strip() or group_column != group_column.strip():
        raise SkillError("INVALID_PARAMETER", "group_column must be a non-empty name without surrounding spaces.")
    groups: dict[str, str] = {}
    with csv_rows(metadata, "METADATA_FORMAT_ERROR") as rows:
        header = _header(rows, "METADATA_FORMAT_ERROR")
        if "sample_id" not in header:
            raise SkillError("METADATA_FORMAT_ERROR", "Metadata requires a sample_id column.")
        if group_column not in header:
            raise SkillError("GROUP_COLUMN_NOT_FOUND", "Requested group column is absent from metadata.",
                             details={"group_column": group_column, "available": header})
        sample_index, group_index = header.index("sample_id"), header.index(group_column)
        for row_number, row in enumerate(rows, 2):
            if not row:
                continue
            if len(row) != len(header):
                raise SkillError("METADATA_FORMAT_ERROR", "Metadata row width differs from header.",
                                 details={"row": row_number})
            # Extra metadata columns are ignored by the model but still checked for missing cells.
            for field, value in zip(header, row):
                _identifier(value, field, row_number)
            sample, group = row[sample_index], row[group_index]
            if sample in groups:
                raise SkillError("DUPLICATE_ID", "Metadata sample_id must be unique.")
            groups[sample] = group
    if not groups:
        raise SkillError("METADATA_FORMAT_ERROR", "Metadata has no sample rows.")
    return groups


def validate_metadata(metadata: Path, samples: tuple[str, ...], group_column: str) -> tuple[str, ...]:
    """Check annotations and return groups aligned to the supplied sample order."""
    groups = scan_metadata(metadata, group_column)
    if set(samples) != set(groups):
        raise SkillError("SAMPLE_MISMATCH", "Count and metadata sample sets differ.", details={
            "missing_in_metadata": sorted(set(samples) - set(groups)),
            "missing_in_counts": sorted(set(groups) - set(samples)),
        })
    return tuple(groups[sample] for sample in samples)


def validate_inputs(counts: Path, metadata: Path, treatment: str, control: str) -> InputOverview:
    """Preserve DEG's validation interface and two-group biological preconditions."""
    try:
        overview = validate_expression_inputs(counts, metadata, "condition")
    except SkillError as error:
        if error.code == "GROUP_COLUMN_NOT_FOUND":
            raise SkillError("METADATA_FORMAT_ERROR", "Metadata requires sample_id and condition columns.") from error
        raise
    observed = set(overview.sample_groups)
    if not {treatment, control}.issubset(observed):
        raise SkillError("GROUP_NOT_FOUND", "Requested treatment or control is absent.", details={
            "requested": [treatment, control], "available": sorted(observed),
        })
    if observed != {treatment, control}:
        raise SkillError("UNSUPPORTED_GROUPS", "Only the requested two groups are supported.")
    n_treatment = overview.sample_groups.count(treatment)
    n_control = overview.sample_groups.count(control)
    if min(n_treatment, n_control) < 2:
        raise SkillError("INSUFFICIENT_REPLICATES", "At least two independent biological samples per group are required.",
                         details={"treatment_samples": n_treatment, "control_samples": n_control})
    return InputOverview(overview.gene_count, overview.sample_ids, overview.analyzed_gene_ids,
                         n_treatment, n_control)


def validate_pca_inputs(counts: Path, metadata: Path, group_column: str) -> ExpressionOverview:
    """PCA labels do not impose a contrast or per-group replicate requirement."""
    if not isinstance(group_column, str) or not group_column.strip() or group_column != group_column.strip():
        raise SkillError("INVALID_PARAMETER", "group_column must be a non-empty column name without surrounding spaces.")
    overview = validate_expression_inputs(counts, metadata, group_column)
    if len(overview.sample_ids) < 3:
        raise SkillError("INSUFFICIENT_SAMPLES", "A two-dimensional PCA requires at least three samples.")
    if len(overview.analyzed_gene_ids) < 2:
        raise SkillError("INSUFFICIENT_GENES", "PCA requires at least two non-zero genes; VST also needs adequate data to fit dispersions.")
    return overview
