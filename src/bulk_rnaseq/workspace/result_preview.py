"""Bounded display helpers: no statistical analysis and no writes."""

import csv
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterator
import warnings

import pandas as pd
from PIL import Image, UnidentifiedImageError

from bulk_rnaseq.workspace.errors import ProjectError


MAX_PREVIEW_ROWS = 1000
MAX_TABLE_BYTES = 4 * 1024 * 1024
MAX_COLUMNS = 200


@dataclass
class TablePreview:
    frame: pd.DataFrame
    truncated: bool
    bytes_read: int
    total_rows: int | None


def preview_table(path: Path, *, limit: int = 100) -> TablePreview:
    if type(limit) is not int or not 1 <= limit <= MAX_PREVIEW_ROWS:
        raise ProjectError("INVALID_PREVIEW_LIMIT", "预览行数必须为 1–1000。")
    if path.suffix.lower() not in {".csv", ".tsv"}:
        raise ProjectError("UNSUPPORTED_PREVIEW", "仅支持 CSV / TSV 表格预览。")
    bytes_read = 0
    def bounded_lines(handle) -> Iterator[str]:
        nonlocal bytes_read
        while True:
            data = handle.readline(MAX_TABLE_BYTES - bytes_read + 1)
            if not data:
                return
            bytes_read += len(data)
            if bytes_read > MAX_TABLE_BYTES:
                raise ProjectError("TABLE_PREVIEW_TOO_LARGE", "预览内容超过 4 MiB，请减少行数或下载文件。")
            yield data.decode("utf-8-sig" if bytes_read == len(data) else "utf-8")
    try:
        with path.open("rb") as handle:
            reader = csv.reader(bounded_lines(handle), delimiter="\t" if path.suffix.lower() == ".tsv" else ",", strict=True)
            columns = next(reader, None)
            if not columns or len(columns) > MAX_COLUMNS or len(set(columns)) != len(columns) or any(not c for c in columns):
                raise ProjectError("INVALID_TABLE_HEADER", "表头为空、重复或超过 200 列。")
            rows = []
            truncated = False
            for row in reader:
                if len(row) != len(columns):
                    raise ProjectError("INVALID_TABLE_ROW", "表格预览发现列数不一致。")
                if len(rows) == limit:
                    truncated = True
                    break
                rows.append(row)
        # Keep IDs, leading zeroes, NA and displayed scientific precision intact.
        return TablePreview(pd.DataFrame(rows, columns=columns, dtype=str), truncated, bytes_read,
                            None if truncated else len(rows))
    except (OSError, UnicodeError, csv.Error):
        raise ProjectError("TABLE_PREVIEW_FAILED", "表格无法按 UTF-8 CSV/TSV 安全预览；可下载原文件。") from None


def validate_image(data: bytes) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"} or image.width * image.height > 40_000_000:
                    raise ValueError("Unsupported dimensions or format")
                image.verify()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ProjectError("INVALID_RESULT_IMAGE", "图片无法预览，或尺寸超过 4000 万像素。") from None
