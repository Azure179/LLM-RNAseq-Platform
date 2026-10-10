"""Reserve an empty output directory and publish JSON by atomic file replacement."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
from typing import Any, TypeVar
from uuid import uuid4

from bulk_rnaseq.exceptions import SkillError
from bulk_rnaseq.logging_config import run_logger
from bulk_rnaseq.validation import require_input_file
from bulk_rnaseq.models import ResultEnvelope


ResultT = TypeVar("ResultT", bound=ResultEnvelope)


def publish_result(result: ResultT, summary: dict[str, Any]) -> ResultT:
    """Publish exactly the same JSON snapshot that Python/CLI callers receive."""
    completed = result.with_summary(summary)
    write_json(completed.summary_json, completed.to_dict())
    return completed


@contextmanager
def reserve_output(path: Path, run_id: str) -> Iterator[None]:
    lock = path / ".run.lock"
    owned = False
    try:
        if path.exists() and not path.is_dir():
            raise SkillError("OUTPUT_NOT_DIRECTORY", "Output path must be a directory.")
        path.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive creation prevents two cooperating runs sharing one directory.
            with lock.open("x", encoding="utf-8") as handle:
                owned = True
                handle.write(run_id)
        except FileExistsError as error:
            raise SkillError("OUTPUT_BUSY", "Output directory is reserved by another run.") from error
        if any(entry != lock for entry in path.iterdir()):
            raise SkillError("OUTPUT_NOT_EMPTY", "Choose a new or empty output directory; existing files are preserved.")
    except OSError as error:
        if owned:
            lock.unlink(missing_ok=True)
        raise SkillError("OUTPUT_NOT_WRITABLE", "Unable to create or write output directory.",
                         details={"path": str(path)}) from error
    except SkillError:
        if owned:
            lock.unlink(missing_ok=True)
        raise
    try:
        yield
    finally:
        if owned:
            lock.unlink(missing_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Never expose a partially written JSON document."""
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", dir=path.parent, prefix=".json-", suffix=".tmp",
                                encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def capture_streams(output: Path, stdout: str, stderr: str) -> None:
    (output / "r_stdout.log").write_text(stdout, encoding="utf-8")
    (output / "r_stderr.log").write_text(stderr, encoding="utf-8")


def snapshot_input(source: Path, destination: Path) -> None:
    require_input_file(source)
    try:
        shutil.copyfile(source, destination)
    except OSError as error:
        raise SkillError("INPUT_READ_ERROR", "Unable to copy input into the run directory.",
                         details={"path": str(source)}) from error


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    output: Path
    logger: logging.Logger


def _record_failure(context: RunContext, error: SkillError, artifacts: tuple[str, ...]) -> None:
    context.logger.error("Run failed: code=%s message=%s", error.code, error)
    try:
        # Reservation guarantees these are this run's files, never an older run's.
        for name in ("summary.json", *artifacts):
            (context.output / name).unlink(missing_ok=True)
        if error.stdout or error.stderr:
            capture_streams(context.output, error.stdout, error.stderr)
        write_json(context.output / "failure.json", error.to_dict())
    except OSError:
        context.logger.exception("Unable to persist failure diagnostics; CLI/API error remains authoritative.")


@contextmanager
def analysis_run(output_dir: Path, operation: str, artifacts: tuple[str, ...], *,
                 inputs: dict[str, Any] | None = None) -> Iterator[RunContext]:
    """Shared directory/log/failure lifecycle; each skill owns its statistics and summary."""
    run_id = str(uuid4())
    try:
        output = Path(output_dir).expanduser().resolve()
        with reserve_output(output, run_id):
            with run_logger(output / "run.log", run_id) as logger:
                context = RunContext(run_id, output, logger)
                logger.info("Run started: operation=%s", operation)
                try:
                    yield context
                except Exception as cause:
                    if isinstance(cause, SkillError):
                        error = cause
                    elif isinstance(cause, OSError):
                        error = SkillError("IO_ERROR", "Filesystem operation failed; inspect run.log.")
                        logger.exception("Filesystem failure")
                    else:
                        error = SkillError("INTERNAL_ERROR", "Unexpected analysis failure; inspect run.log.")
                        logger.exception("Unexpected failure")
                    error.run_id, error.operation = run_id, operation
                    error.inputs = inputs or {}
                    _record_failure(context, error, artifacts)
                    if error is cause:
                        raise
                    raise error from cause
    except SkillError as error:
        error.run_id, error.operation = run_id, operation
        error.inputs = inputs or {}
        raise
    except (OSError, ValueError, TypeError) as cause:
        error = SkillError("IO_ERROR", "Unable to initialize or close the run directory.", operation=operation)
        error.run_id = run_id
        error.inputs = inputs or {}
        raise error from cause
