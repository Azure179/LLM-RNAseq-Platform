"""Local and Actions entry point. Only summary.json is intended for upload."""
import argparse
import hashlib
from email.parser import BytesParser
from zipfile import ZipFile
from collections import Counter
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ci.security import check_repository, check_wheel


class Blocked(RuntimeError):
    """A mandatory tool or environment prerequisite is unavailable."""


class Gate:
    def __init__(self, name: str):
        self.name = name
        self.directory = ROOT / "outputs/ci" / name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []
        self.tests = Counter()
        self.stage = "preflight"
        self.started = monotonic()

    def check(self, label, fn, *args):
        self.stage = label
        start = monotonic()
        try:
            result = fn(*args)
        except Exception as exc:
            self.checks.append({"check": label, "status": "BLOCKED" if isinstance(exc, Blocked) else "FAIL",
                                "seconds": round(monotonic() - start, 2)})
            raise
        self.checks.append({"check": label, "status": "PASS", "seconds": round(monotonic() - start, 2)})
        return result

    def save(self, status: str, error: str | None = None) -> None:
        payload = {"job": self.name, "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
                   "python": platform.python_version(), "command": f"python scripts/ci/gate.py {self.name}",
                   "check_counts": dict(Counter(item["status"] for item in self.checks)),
                   "status": status, "seconds": round(monotonic() - self.started, 2),
                   "checks": self.checks, "pytest": dict(self.tests), "error_summary": error}
        text = json.dumps(payload, indent=2)
        (self.directory / "summary.json").write_text(text + "\n", encoding="utf-8")
        print(text, flush=True)


def command(*args: str, **kwargs) -> None:
    subprocess.run(list(args), check=True, **kwargs)


def locks() -> None:
    from packaging.requirements import Requirement
    from container.locks.snapshot_python import snapshot

    declarations = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    reqs = [s for s in (ROOT / "requirements.txt").read_text().splitlines() if s and not s.startswith("#")]
    if set(map(Requirement, declarations)) != set(map(Requirement, reqs)):
        raise ValueError("requirements.txt and project dependencies disagree")
    lock = ROOT / "container/locks/python-windows-dev.txt"
    pins = [s.split()[0] for s in lock.read_text().splitlines() if s and not s.startswith("#")]
    if snapshot([*declarations, "pytest"]) != pins:
        raise ValueError("Installed Windows test closure disagrees with the lock")
    build = (ROOT / "container/locks/build-requirements.txt").read_text().split()[0]
    name, expected = build.split("==")
    if version(name) != expected:
        raise ValueError("Package build backend differs from the lock")
    reviewed = json.loads((ROOT / "tests/fixtures/ci_science/reference.json").read_bytes())["environment"]
    for filename, field in (("container/python-constraints.txt", "python_lock_sha256"),
                            ("container/locks/r-linux.tsv", "r_lock_sha256")):
        if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != reviewed[field]:
            raise ValueError(f"Scientific lock differs from reviewed environment: {filename}")
    command(sys.executable, "-m", "pip", "check")


def wheel_check() -> None:
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        command(sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
                "--no-build-isolation", "-w", temp, str(ROOT))
        wheel, = directory.glob("*.whl")
        check_wheel(wheel)
        from packaging.requirements import Requirement
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        with ZipFile(wheel) as archive:
            entry, = (n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            meta = BytesParser().parsebytes(archive.read(entry))
        assert meta["Name"] == project["name"] and meta["Version"] == project["version"]
        assert set(map(Requirement, meta.get_all("Requires-Dist"))) == set(map(Requirement, project["dependencies"]))
        target = directory / "installed"
        command(sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                "--target", str(target), str(wheel))
        command(sys.executable, "-I", "-c",
                "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                "import bulk_rnaseq; assert Path(bulk_rnaseq.__file__).is_relative_to(sys.argv[1]); "
                "from bulk_rnaseq.builtin_registry import create_default_registry; "
                "assert len(create_default_registry().list_skills()) >= 6; "
                "from bulk_rnaseq.cli import main; assert main([]) == 0; "
                "from importlib.resources import files; "
                "resources = files('bulk_rnaseq.r_scripts'); "
                "assert all(resources.joinpath(p.name).is_file() for p in Path(sys.argv[2]).glob('*.R'))",
                str(target), str(ROOT / "src/bulk_rnaseq/r_scripts"), cwd=temp)


REQUIRED = {"test_api_contract_freeze", "test_workflow_validator", "test_workflow_executor",
            "test_result_manager", "test_result_reuse", "test_deg_reuse_guard", "test_customization",
            "test_api_security", "test_isolated_execution", "test_index_contention", "test_dependency_locks"}


def default_tests(gate: Gate) -> None:
    # Raw diagnostics remain local and are never an Actions artifact.
    with TemporaryDirectory() as tmp:
        xml = Path(tmp) / "pytest.xml"
        with (gate.directory / "pytest-private.log").open("w", encoding="utf-8") as log:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short", "--strict-markers",
                                   f"--junitxml={xml}"], stdout=log, stderr=subprocess.STDOUT)
        passed_modules = set()
        unexpected = []
        if xml.is_file():
            for case in ET.parse(xml).iter("testcase"):
                module = case.get("classname", "").split(".")
                skipped = case.find("skipped")
                if skipped is not None:
                    gate.tests["skipped"] += 1
                    reason = skipped.get("message", "")
                    optional_module = ("integration" in module or "llm_integration" in module
                                       or case.get("name", "").split("[")[0] in {"test_registry_real_deg", "test_real_workflows"})
                    allowed_opt_in = optional_module and reason in {"Use --run-llm to explicitly allow paid API tests.",
                                                "Use --run-r to enable real R tests."}
                    allowed_link = ("test_workspace" in module and case.get("name") == "test_linked_input_rejected"
                                    and reason.startswith("Creating Windows symlinks requires"))
                    if not (allowed_opt_in or allowed_link):
                        unexpected.append(case.get("name"))
                elif case.find("error") is not None or case.find("failure") is not None:
                    gate.tests["failed"] += 1
                else:
                    gate.tests["passed"] += 1
                    passed_modules.update(module)
        if proc.returncode or unexpected or REQUIRED - passed_modules or not gate.tests["passed"]:
            raise ValueError("Default test failure, unexpected skip, or required suite missing")
        gate.tests.setdefault("failed", 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=["fast", "scientific", "docker"])
    level = parser.parse_args().level
    os.chdir(ROOT)
    gate = Gate(level)
    try:
        if platform.python_version() != "3.13.2" or not __debug__:
            raise Blocked("Use Python 3.13.2 with assertions enabled")
        if level == "fast":
            if sys.platform != "win32":
                raise Blocked("Fast CI uses the Windows development lock")
            from scripts.ci.lint_workflows import lint
            gate.check("actionlint syntax and workflow semantics", lint, ROOT)
            gate.check("publishable files and Docker context", check_repository, ROOT)
            gate.check("locked dependencies and pip check", locks)
            gate.check("wheel build, contents and isolated registry import", wheel_check)
            gate.check("default pytest and mandatory suites", default_tests, gate)
        else:
            from scripts.ci.docker_gates import run_level
            run_level(gate)
    except Exception as exc:
        status = "BLOCKED" if isinstance(exc, Blocked) else "FAIL"
        gate.save(status, f"{gate.stage}: {type(exc).__name__}. Inspect the local job diagnostics.")
        # An Actions console is public evidence. Do not print exceptions with raw subprocess data.
        (gate.directory / "error-private.txt").write_text(str(exc), encoding="utf-8")
        return 1
    gate.save("PASS")
    return 0


if __name__ == "__main__":
    # Allow helpers to import the same exception class when invoked as a script.
    sys.modules["scripts.ci.gate"] = sys.modules[__name__]
    raise SystemExit(main())

