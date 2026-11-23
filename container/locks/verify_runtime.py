"""Write image-local provenance only after validating both installed inventories."""
import csv
import hashlib
from importlib.metadata import distributions, version
from importlib.util import find_spec
import json
from pathlib import Path
import platform
import subprocess
import sys

from packaging.utils import canonicalize_name


def verify(root: Path) -> dict:
    pylock = root / "container/python-constraints.txt"
    rlock = root / "container/r-linux.tsv"
    expected_names = {"pip", "bulk-rnaseq-skill"}
    for pin in pylock.read_text().splitlines():
        if pin and not pin.startswith("#"):
            name, expected = pin.split()[0].split("==")
            expected_names.add(name)
            if version(name) != expected:
                raise ValueError(f"Python package drift: {name}")
    actual_names = {canonicalize_name(dist.metadata["Name"]) for dist in distributions()}
    if actual_names != expected_names:
        raise ValueError(f"Unexpected Python inventory: {actual_names ^ expected_names}")
    if sys.platform != "linux" or pylock.read_text().splitlines()[0] != f"# linux Python {platform.python_version()}":
        raise ValueError("Python interpreter differs from the lock")
    with rlock.open() as handle:
        header = next(handle).strip()
        expected_r = {r["package"]: r["version"] for r in csv.DictReader(handle, delimiter="\t")}
    proc = subprocess.run(["Rscript", "--vanilla", "-e",
        'x<-installed.packages();cat(jsonlite::toJSON(as.list(setNames(x[,"Version"],x[,"Package"])),auto_unbox=TRUE))'],
        capture_output=True, text=True, check=True)
    if json.loads(proc.stdout) != expected_r:
        raise ValueError("R package inventory differs from the lock")
    audit = json.loads((root / "r-runtime.json").read_text())
    rversion = audit["R"].split()[2]
    if header != f"# R={rversion} Bioconductor={audit['Bioconductor']}":
        raise ValueError("R/Bioconductor release differs from the lock")
    return {"scope": "image_build_verified", "python": platform.python_version(), "R": rversion,
            "Bioconductor": audit["Bioconductor"],
            **{name: expected_r[name] for name in ("DESeq2", "clusterProfiler", "ggplot2")},
            "python_lock_sha256": hashlib.sha256(pylock.read_bytes()).hexdigest(),
            "r_lock_sha256": hashlib.sha256(rlock.read_bytes()).hexdigest()}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    receipt = Path(find_spec("bulk_rnaseq").origin).with_name("_verified_environment.json")
    receipt.write_text(json.dumps(verify(root), indent=2) + "\n", encoding="utf-8")
