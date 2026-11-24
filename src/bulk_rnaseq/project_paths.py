"""Versioned project-relative references at the JSON persistence boundary.

Runtime Skill interfaces still receive native absolute Paths. The prefix separates
new project-relative references from legacy results-directory-relative strings.
No filesystem root is guessed or substituted when reading legacy records.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

PREFIX = "project-path:v1:"
TEXT_FIELDS = {"content", "message", "description", "user_request", "user_message", "assistant_message",
               "reason", "title", "stdout", "stderr", "final_response", "prompt"}


def _error(code: str, message: str) -> Exception:
    # Avoid importing workspace's service facade during run_io initialization.
    from bulk_rnaseq.workspace.errors import ProjectError
    return ProjectError(code, message)


class ProjectPaths:
    def __init__(self, root: Path, project_key: str) -> None:
        self.root = root.absolute()
        self.key = project_key

    def resolve(self, value: str | Path, *, must_exist: bool = False) -> Path:
        if self.root.resolve() != self.root:
            raise _error("PROJECT_PATH_NOT_ALLOWED", "Project root has changed; reopen the workspace.")
        text = str(value)
        if text.startswith(PREFIX):
            identity, separator, text = text[len(PREFIX):].partition(":")
            if not separator or identity != self.key:
                raise _error("PROJECT_CONTEXT_MISMATCH", "Path reference belongs to another project.")
            if Path(text).is_absolute() or PureWindowsPath(text).drive:
                raise _error("PROJECT_PATH_NOT_ALLOWED", "Portable references must contain only a relative path.")
        raw = Path(text)
        windows = PureWindowsPath(text)
        if windows.drive and not raw.is_absolute():
            raise _error("LEGACY_PATH_UNAVAILABLE", "Legacy Windows path cannot be resolved on this host; explicit migration is required.")
        if raw.is_absolute():
            if not raw.is_relative_to(self.root):
                raise _error("PROJECT_PATH_NOT_ALLOWED", "Absolute path is outside this project; automatic remapping is forbidden.")
            relative = raw.relative_to(self.root)
        else:
            posix = PurePosixPath(text)
            if (not text or "\\" in text or ":" in text or posix.is_absolute()
                    or text != posix.as_posix() or ".." in posix.parts):
                raise _error("PROJECT_PATH_NOT_ALLOWED", "Expected a canonical project-relative POSIX path.")
            from bulk_rnaseq.workspace.models import validate_name
            for part in posix.parts:
                try:
                    validate_name(part)
                except ValueError:
                    raise _error("PROJECT_PATH_NOT_ALLOWED", "Path component is not portable between Windows and Linux.") from None
            relative = Path(*posix.parts)
        if ".." in relative.parts:
            raise _error("PROJECT_PATH_NOT_ALLOWED", "Parent traversal is forbidden.")
        current = self.root
        for part in (None, *relative.parts):
            if part is not None:
                current /= part
            if current.is_symlink() or current.is_junction():
                raise _error("PROJECT_PATH_NOT_ALLOWED", "Linked project paths are forbidden.")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise _error("PROJECT_PATH_NOT_ALLOWED", "Path escapes this project.")
        if must_exist and not resolved.exists():
            raise _error("PROJECT_RESOURCE_MISSING", "Project resource is missing; no legacy path was remapped.")
        return resolved

    def reference(self, path: str | Path) -> str:
        resolved = self.resolve(path)
        return PREFIX + self.key + ":" + resolved.relative_to(self.root.resolve()).as_posix()

    def encode(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: item if key in TEXT_FIELDS else self.encode(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.encode(item) for item in value]
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            if value.startswith(PREFIX):
                self.resolve(value)  # Do not persist forged cross-project references.
            elif "\n" not in value and "\x00" not in value:
                path = Path(value)
                if path.is_absolute() and path.is_relative_to(self.root):
                    return self.reference(path)
        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: item if key in TEXT_FIELDS else self.decode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if isinstance(value, str) and value.startswith(PREFIX):
            return str(self.resolve(value))
        return value


def paths_for_document(path: Path) -> ProjectPaths | None:
    """Only actual project-owned documents opt into portable persistence.

    Standalone CLI output, R staging JSON and external demo files keep their
    existing contract. Project discovery never opens credentials or parses data.
    """
    for root in path.absolute().parents:
        if (root / "project.json").is_file() and all((root / name).is_dir() for name in ("input", "results", "runtime")):
            return ProjectPaths(root, root.name)
    return None
