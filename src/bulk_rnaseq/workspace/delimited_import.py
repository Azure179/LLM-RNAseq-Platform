"""Conservative text-table detection at the import boundary, not in Skills."""

from collections.abc import Iterator
from contextlib import contextmanager
import csv
from pathlib import Path

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.validation import require_input_file


TEXT_TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".xls"}
GENE_HEADERS = {"gene_id", "geneid", "gene id"}
SAMPLE_HEADERS = {"sample_id", "sample", "sampleid", "sample id"}
GROUP_HEADERS = {"condition", "group"}
MAX_HEADER_CHARS = 1024 * 1024


@contextmanager
def delimited_rows(path: Path) -> Iterator[tuple[Iterator[list[str]], str]]:
    """Detect comma/Tab/semicolon from a bounded header; stream the full table.

    File extensions never choose the delimiter. Quoted separators are handled by
    csv.reader. More than one plausible separator is rejected, never guessed.
    A text file named .xls is supported; a binary Excel workbook is not.
    """
    require_input_file(path)
    try:
        with path.open("rb") as probe:
            signature = probe.read(8)
        if signature.startswith((b"\xd0\xcf\x11\xe0", b"PK\x03\x04")):
            raise SkillError("IMPORT_FORMAT_UNSUPPORTED", "This is a binary Excel workbook. Export counts/metadata as UTF-8 CSV or TSV; renaming the extension is insufficient.")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for _ in range(100):
                offset = handle.tell()
                line = handle.readline(MAX_HEADER_CHARS + 1)
                if len(line) > MAX_HEADER_CHARS:
                    raise SkillError("IMPORT_FORMAT_ERROR", "Input header exceeds the text import limit.")
                if not line:
                    raise SkillError("IMPORT_EMPTY_FILE", "Input requires a header and data rows.")
                if line.strip() and not line.startswith(("##", "# Program:", "# Command:")):
                    break
            else:
                raise SkillError("IMPORT_FORMAT_ERROR", "No table header found within the first 100 lines.")
            candidates = []
            for delimiter in (",", "\t", ";"):
                try:
                    header = next(csv.reader([line], delimiter=delimiter, strict=True))
                except csv.Error:
                    continue
                if len(header) > 1:
                    candidates.append(delimiter)
            if len(candidates) > 1:
                raise SkillError("AMBIGUOUS_DELIMITER", "More than one delimiter is plausible. Use a consistently comma- or Tab-separated table, quoting separators inside column names.")
            # Single-column inputs proceed to the existing role-specific validator.
            delimiter = candidates[0] if candidates else ","
            handle.seek(offset)
            yield csv.reader(handle, delimiter=delimiter, strict=True), delimiter
    except (UnicodeError, csv.Error):
        raise SkillError("IMPORT_FORMAT_ERROR", "Expected a well-formed UTF-8 delimited text table with a single-line header.") from None
    except OSError:
        raise SkillError("INPUT_READ_ERROR", "Cannot read the selected text table.") from None
