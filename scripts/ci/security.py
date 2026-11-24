"""Inspect publishable files without displaying credential values or workspace contents."""
from pathlib import Path, PurePosixPath
import re
import subprocess
from zipfile import ZipFile

from container.context_audit import SECRET, inventory

# Exact synthetic tokens used by existing redaction tests. Other bytes still get scanned.
TEST_SENTINELS = {
    "tests/llm/test_client.py": b"NOT-A-REAL-KEY-test-only",
    "tests/llm/test_final_response.py": b"fixture-only-final-response",
    "tests/llm/test_tool_probe.py": b"fixture-not-real-tool-probe",
    "tests/llm/test_tool_runner.py": b"fixture-runner-credential",
    "tests/test_workflow_final_response.py": b"unit-fixture-workflow-summary-only",
}
PUBLIC_TABLES = {
    "examples/data/counts.csv", "examples/data/metadata.csv",
    "examples/go/synthetic_mapping.tsv", "examples/kegg/synthetic_mapping.tsv",
    "container/locks/r-linux.tsv", "tests/fixtures/ci_science/go.tsv",
    "tests/fixtures/ci_science/kegg.tsv",
}
PRIVATE_ROOTS = {"workspace", "outputs", "logs", "runtime", ".venv", "real_data_ui_test",
                 "real_data_e2e_test", "real_data_phase7_e2e"}
PRIVATE_EXTENSIONS = {".log", ".pem", ".key", ".pfx", ".rds", ".rdata", ".gz", ".zip",
                      ".whl", ".tar", ".bam", ".fastq", ".fq", ".xls", ".xlsx"}
ASSIGNMENT = re.compile(rb"(?im)^[ \t]*(?:DEEPSEEK_API_KEY|GITHUB_TOKEN|AWS_SECRET_ACCESS_KEY)[ \t]*=[ \t]*['\"]?[a-zA-Z0-9_/-]{16,}")


def inspect_name(name: str) -> None:
    path = PurePosixPath(name.lower())
    private_role = path.parts[:2] in {("real_data_test", i) for i in ("input", "results", "logs")}
    if (path.parts[0] in PRIVATE_ROOTS or private_role or ".." in path.parts or path.is_absolute()
            or any(part in {"outputs", "logs", "results", "result_index"} for part in path.parts[:-1])
            or ".env" == path.name or (".env." in path.name and not path.name.endswith(".env.example"))
            or path.name.endswith(".env") or path.name in {"secrets.toml", ".git-credentials", ".netrc"}
            or path.suffix in PRIVATE_EXTENSIONS
            or (path.suffix in {".csv", ".tsv"} and name not in PUBLIC_TABLES)):
        raise ValueError(f"Private or unreviewed artifact: {name}")


def inspect_bytes(name: str, data: bytes) -> None:
    # Check every match individually: an allowed test token cannot mask another key.
    allowed = b"sk-" + TEST_SENTINELS.get(name, b"")
    if any(m.group() != allowed for m in SECRET.finditer(data)) or ASSIGNMENT.search(data):
        raise ValueError(f"Potential credential in {name}")


def git_names(root: Path, *args: str) -> list[str]:
    raw = subprocess.check_output(["git", *args, "-z"], cwd=root)
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def check_repository(root: Path) -> None:
    for name in git_names(root, "ls-files", "--cached", "--others", "--exclude-standard"):
        inspect_name(name)
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Linked publishable file: {name}")
        if path.is_file():
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError(f"Review oversized file: {name}")
            inspect_bytes(name, path.read_bytes())
    # A working-tree edit may hide different bytes already staged for commit.
    for name in git_names(root, "diff", "--name-only", "--diff-filter=MDT"):
        size = int(subprocess.check_output(["git", "cat-file", "-s", ":" + name], cwd=root))
        if size > 8 * 1024 * 1024:
            raise ValueError(f"Review oversized staged file: {name}")
        inspect_bytes(name, subprocess.check_output(["git", "show", ":" + name], cwd=root))
    for item in inventory(root)["files"]:
        inspect_name(item["path"])


def check_wheel(wheel: Path) -> None:
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            inspect_name(name)
            if not (name.startswith("bulk_rnaseq/") or ".dist-info/" in name):
                raise ValueError(f"Unexpected wheel member: {name}")
            inspect_bytes(name, archive.read(name))

