"""Execute fixed R scripts as subprocesses."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess

from bulk_rnaseq.exceptions import SkillError


@dataclass(frozen=True, slots=True)
class RExecutionResult:
    """Captured outcome of one Rscript process."""

    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str


def run_rscript(
    script_path: Path,
    arguments: Sequence[str],
    *,
    rscript_executable: str | Path = "Rscript",
    timeout: float = 600,
) -> RExecutionResult:
    """Run an R script without invoking a command shell."""

    command = (
        str(rscript_executable),
        str(script_path.expanduser().resolve()),
        *tuple(str(argument) for argument in arguments),
    )

    try:
        completed_process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        def decode(value: str | bytes | None) -> str:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        raise SkillError("RSCRIPT_TIMEOUT", "Rscript exceeded the configured time limit.",
                         details={"timeout_seconds": timeout},
                         stdout=decode(error.stdout), stderr=decode(error.stderr)) from error
    except FileNotFoundError as error:
        raise SkillError("RSCRIPT_NOT_FOUND", "Unable to start Rscript: executable not found.",
                         details={"executable": str(rscript_executable)}) from error
    except OSError as error:
        raise SkillError("RSCRIPT_START_FAILED", "Unable to start Rscript executable.",
                         details={"executable": str(rscript_executable)}) from error

    if completed_process.returncode != 0:
        raise SkillError("RSCRIPT_FAILED", "Rscript exited with a non-zero code. See R logs.",
                         details={"return_code": completed_process.returncode},
                         return_code=completed_process.returncode,
                         stdout=completed_process.stdout, stderr=completed_process.stderr)

    return RExecutionResult(
        command=command,
        return_code=completed_process.returncode,
        stdout=completed_process.stdout,
        stderr=completed_process.stderr,
    )
