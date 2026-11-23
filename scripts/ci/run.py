"""Create a clean locked environment and run one local / Actions quality gate."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=["fast", "scientific", "docker"])
    level = parser.parse_args().level
    directory = ROOT / "outputs" / "ci" / level
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / "summary.json"
    report.unlink(missing_ok=True)
    start = monotonic()
    stage = "interpreter"
    try:
        if platform.python_version() != "3.13.2" or (level == "fast" and sys.platform != "win32"):
            raise ValueError("Unsupported interpreter/platform")
        with TemporaryDirectory(prefix="rnaseq-ci-") as temp:
            env = Path(temp) / "venv"
            stage = "create clean venv"
            subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
            python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            lock = ("container/locks/python-windows-dev.txt" if sys.platform == "win32"
                    else "container/python-constraints.txt")
            stage = "install locked dependencies"
            install = [str(python), "-m", "pip", "--isolated", "install", "--require-hashes",
                       "--only-binary=:all:", "--no-deps", "-r", lock]
            if level == "fast":
                install.extend(["-r", "container/locks/build-requirements.txt"])
            with (directory / "install-private.log").open("w", encoding="utf-8") as log:
                subprocess.run(install, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
            stage = "run gate"
            clean = {k: v for k, v in os.environ.items() if not k.startswith(
                ("DEEPSEEK_", "RNASEQ_", "PYTEST_", "PYTHONPATH", "PYTHONOPTIMIZE"))}
            clean.update(PYTHONUTF8="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONNOUSERSITE="1")
            proc = subprocess.run([str(python), "scripts/ci/gate.py", level], cwd=ROOT, env=clean)
            if not report.is_file():
                raise RuntimeError("Gate did not produce a report")
            return proc.returncode
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        payload = {"job": level, "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
                   "python": platform.python_version(), "command": f"python scripts/ci/run.py {level}",
                   "status": "BLOCKED" if stage in {"interpreter","create clean venv"} else "FAIL",
                   "checks": [], "pytest": {}, "seconds": round(monotonic()-start, 2),
                   "error_summary": f"{stage}: {type(exc).__name__}"}
        report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

