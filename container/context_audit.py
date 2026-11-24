"""Inventory this Dockerfile's deny-by-default build context without reading secrets.

This static inventory is NOT a substitute for Docker's actual transferred bytes.
Fail if the allowlist changes, so an approximate parser cannot silently bless it.
"""

import argparse
import json
import logging
from pathlib import Path
import re

ALLOWED = {"!Dockerfile", "!.dockerignore", "!pyproject.toml", "!README.md", "!LICENSE",
           "!src/", "!src/**", "!app/", "!app/**", "!container/", "!container/**"}
EXCLUSIONS = {"**/__pycache__/", "**/*.egg-info/", "**/*.py[cod]", "**/.env", "**/.env.*", "**/*.log",
              "**/secrets.toml", "**/.Rhistory", "**/.RData"}
SECRET = re.compile(rb"sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def inventory(root: Path) -> dict:
    rules = [line.strip() for line in (root / ".dockerignore").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if (rules[0] != "**" or set(r for r in rules if r.startswith("!")) != ALLOWED
            or set(r for r in rules if not r.startswith("!")) != {"**", *EXCLUSIONS}
            or any(r.startswith("!") for r in rules[-len(EXCLUSIONS):])):
        raise ValueError("Docker context policy changed; review the inventory implementation")
    candidates = [root / p for p in ("Dockerfile", ".dockerignore", "pyproject.toml", "README.md", "LICENSE")]
    candidates += [p for folder in ("src", "app", "container") for p in (root / folder).rglob("*") if p.is_file()]
    included = []
    for path in sorted(candidates):
        if ("__pycache__" in path.parts or any(part.endswith(".egg-info") for part in path.parts)
                or path.suffix in {".pyc", ".pyo", ".pyd", ".log"}
                or path.name in {".env", "secrets.toml", ".Rhistory", ".RData"} or path.name.startswith(".env.")):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Linked/out-of-root build input rejected")
        if SECRET.search(path.read_bytes()):
            raise ValueError("Potential credential in allowed source: " + path.relative_to(root).as_posix())
        included.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size})
    return {"kind": "static allowlist inventory; not Docker transfer measurement", "files": included,
            "file_count": len(included), "total_bytes": sum(p["bytes"] for p in included),
            "private_inputs_included": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    payload = inventory(Path(__file__).resolve().parents[1])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logging.info("Static context: %d files, %d bytes; no Docker build has been implied", payload["file_count"], payload["total_bytes"])
