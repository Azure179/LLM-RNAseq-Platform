"""Bad inputs must fail before any R process is started."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.skills.deg import run_deg
from bulk_rnaseq.validation import validate_inputs


def test_normal_input(input_files: tuple[Path, Path]) -> None:
    overview = validate_inputs(*input_files, "treatment", "control")
    assert overview.gene_count == 3
    assert overview.analyzed_gene_ids == {"g1", "g2"}
    assert (overview.treatment_samples, overview.control_samples) == (2, 2)


@pytest.mark.parametrize("case,expected", [
    ("missing_counts", "FILE_NOT_FOUND"), ("missing_metadata", "FILE_NOT_FOUND"),
    ("input_directory", "INPUT_NOT_FILE"), ("group", "GROUP_NOT_FOUND"),
    ("identical", "IDENTICAL_GROUPS"), ("samples", "SAMPLE_MISMATCH"),
    ("fraction", "NON_INTEGER_COUNT"), ("negative", "NEGATIVE_COUNT"),
    ("missing_count", "MISSING_VALUE"), ("nan_count", "MISSING_VALUE"),
    ("missing_condition", "MISSING_VALUE"), ("missing_sample", "MISSING_VALUE"),
    ("missing_gene", "MISSING_VALUE"), ("missing_extra_metadata", "MISSING_VALUE"),
    ("one_replicate", "INSUFFICIENT_REPLICATES"), ("third_group", "UNSUPPORTED_GROUPS"),
    ("duplicate_sample", "DUPLICATE_ID"), ("duplicate_metadata", "DUPLICATE_ID"),
    ("duplicate_gene", "DUPLICATE_ID"), ("bad_header", "COUNT_FORMAT_ERROR"),
    ("bad_metadata_header", "METADATA_FORMAT_ERROR"), ("empty", "COUNT_FORMAT_ERROR"),
    ("no_genes", "COUNT_FORMAT_ERROR"), ("no_samples", "METADATA_FORMAT_ERROR"),
    ("ragged_counts", "COUNT_FORMAT_ERROR"), ("ragged_metadata", "METADATA_FORMAT_ERROR"),
    ("nonnumeric", "COUNT_NOT_NUMERIC"), ("infinite", "COUNT_NOT_FINITE"),
    ("overflow", "COUNT_OUT_OF_RANGE"), ("all_zero", "NO_ANALYZABLE_GENES"),
    ("zero_library", "ZERO_LIBRARY"), ("bad_encoding", "COUNT_FORMAT_ERROR"),
    ("whitespace", "INVALID_IDENTIFIER"), ("bad_alpha", "INVALID_PARAMETER"),
    ("bad_timeout", "INVALID_PARAMETER"), ("bad_lfc", "INVALID_PARAMETER"),
    ("underscore_number", "COUNT_NOT_NUMERIC"),
])
def test_invalid_input(
    case: str, expected: str, input_files: tuple[Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts, metadata = input_files
    c, m = counts.read_text(), metadata.read_text()
    kwargs = {}
    if case == "missing_counts": counts.unlink()
    elif case == "missing_metadata": metadata.unlink()
    elif case == "input_directory": counts.unlink(); counts.mkdir()
    elif case == "group": kwargs["treatment"] = "absent"
    elif case == "identical": kwargs["treatment"] = "control"
    elif case == "samples": metadata.write_text(m.replace("c1", "other"))
    elif case == "fraction": counts.write_text(c.replace("10,", "10.2,"))
    elif case == "negative": counts.write_text(c.replace("10,", "-1,"))
    elif case == "missing_count": counts.write_text(c.replace("10,", ","))
    elif case == "nan_count": counts.write_text(c.replace("10,", "NaN,"))
    elif case == "missing_condition": metadata.write_text(m.replace("c1,control", "c1,NA"))
    elif case == "missing_sample": metadata.write_text(m.replace("c1,control", ",control"))
    elif case == "missing_gene": counts.write_text(c.replace("g1,", ","))
    elif case == "missing_extra_metadata":
        metadata.write_text("sample_id,condition,extra\nc1,control,\nc2,control,x\nt1,treatment,x\nt2,treatment,x\n")
    elif case == "one_replicate": metadata.write_text(m.replace("c2,control", "c2,treatment"))
    elif case == "third_group": metadata.write_text(m.replace("c2,control", "c2,third"))
    elif case == "duplicate_sample": counts.write_text(c.replace("c2", "c1"))
    elif case == "duplicate_metadata": metadata.write_text(m.replace("c2", "c1"))
    elif case == "duplicate_gene": counts.write_text(c.replace("g2,", "g1,"))
    elif case == "bad_header": counts.write_text(c.replace("gene_id", "gene"))
    elif case == "bad_metadata_header": metadata.write_text(m.replace("condition", "group"))
    elif case == "empty": counts.write_text("")
    elif case == "no_genes": counts.write_text(c.splitlines()[0] + "\n")
    elif case == "no_samples": metadata.write_text("sample_id,condition\n")
    elif case == "ragged_counts": counts.write_text(c.replace("10,12", "10"))
    elif case == "ragged_metadata": metadata.write_text(m.replace("c1,control", "c1,control,extra"))
    elif case == "nonnumeric": counts.write_text(c.replace("10,", "abc,"))
    elif case == "infinite": counts.write_text(c.replace("10,", "Inf,"))
    elif case == "overflow": counts.write_text(c.replace("10,", "2147483648,"))
    elif case == "all_zero": counts.write_text("gene_id,c1,c2,t1,t2\ng1,0,0,0,0\n")
    elif case == "zero_library": counts.write_text(c.replace("g1,10,", "g1,0,").replace("g2,20,", "g2,0,"))
    elif case == "bad_encoding": counts.write_bytes(b"\xff\xfe invalid csv")
    elif case == "whitespace": metadata.write_text(m.replace("c1,control", " c1,control"))
    elif case == "bad_alpha": kwargs["alpha"] = float("nan")
    elif case == "bad_timeout": kwargs["timeout"] = -1
    elif case == "bad_lfc": kwargs["lfc_threshold"] = -1
    elif case == "underscore_number": counts.write_text(c.replace("10,", "1_0,"))
    process = Mock(side_effect=AssertionError("Invalid input must never reach R"))
    monkeypatch.setattr("bulk_rnaseq.r_runner.subprocess.run", process)
    output = tmp_path / "output"
    with pytest.raises(SkillError) as caught:
        run_deg(counts, metadata, kwargs.pop("treatment", "treatment"), "control", output, **kwargs)
    assert caught.value.code == expected
    process.assert_not_called()
    failure = json.loads((output / "failure.json").read_text())
    assert failure["error"]["code"] == expected
    assert failure["run_id"] == caught.value.run_id
    assert not (output / "summary.json").exists()
    assert not list(output.glob(".work-*"))
    assert not (output / ".run.lock").exists()


def test_reordered_metadata_is_valid(input_files: tuple[Path, Path]) -> None:
    counts, metadata = input_files
    lines = metadata.read_text().splitlines()
    metadata.write_text("\n".join([lines[0], *reversed(lines[1:])]) + "\n")
    assert validate_inputs(counts, metadata, "treatment", "control").sample_ids == ("c1", "c2", "t1", "t2")
