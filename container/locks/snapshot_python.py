"""Snapshot the installed closure of an accepted environment using pip's marker rules."""
import argparse
import hashlib
from importlib.metadata import distribution
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename


def snapshot(roots: list[str]) -> list[str]:
    pending = [(Requirement(root), "") for root in roots]
    versions = {}
    visited = set()
    while pending:
        req, extra = pending.pop()
        if req.marker and not req.marker.evaluate({"extra": extra}):
            continue
        dist = distribution(req.name)
        if dist.version not in req.specifier:
            raise ValueError(f"{req} is incompatible with installed {dist.version}")
        name = canonicalize_name(req.name)
        versions[name] = dist.version
        for active in {"", *req.extras}:
            if (name, active) in visited:
                continue
            visited.add((name, active))
            pending.extend((Requirement(dep), active) for dep in dist.requires or [])
    return [f"{name}=={versions[name]}" for name in sorted(versions)]


def wheel_pins(pins: list[str]) -> list[str]:
    """Let target-platform pip select its wheel and hash the actual bytes."""
    with TemporaryDirectory() as directory:
        subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                        "--no-deps", "--dest", directory, *pins], stdout=sys.stderr, check=True)
        hashes = {}
        for wheel in Path(directory).glob("*.whl"):
            name, _, _, _ = parse_wheel_filename(wheel.name)
            with wheel.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        return [f"{pin} --hash=sha256:{hashes[pin.split('==')[0]]}" for pin in pins]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pyproject", type=Path)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--wheel-hashes", action="store_true")
    args = parser.parse_args()
    roots = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    if args.dev:
        roots.append("pytest")
    print(f"# {sys.platform} Python {sys.version.split()[0]}")
    pins = snapshot(roots)
    print(*(wheel_pins(pins) if args.wheel_hashes else pins), sep="\n")
