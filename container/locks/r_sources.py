"""Fetch exact R source archives for a private local CRAN-format repository."""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile


def fetch(row: dict[str, str], bioc: str, directory: Path) -> str:
    name, version, source = (row[key] for key in ("package", "version", "source"))
    if source == "R":
        return "-"
    base = ("https://cloud.r-project.org" if source == "CRAN"
            else f"https://bioconductor.org/packages/{bioc}/{source}")
    filename = f"{name}_{version}.tar.gz"
    target = directory / filename
    expected = row.get("sha256")
    if expected and target.is_file():
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                return expected
    for subdir in ("", f"Archive/{name}/"):
        url = f"{base}/src/contrib/{subdir}{filename}"
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            tmp = Path(stream.name)
        try:
            response = subprocess.run(
                ["curl", "-LsS", "--retry", "3", "--max-time", "240", "-o", str(tmp),
                 "-w", "%{http_code}", url], capture_output=True, text=True, check=True)
            if response.stdout in {"404", "410"}:
                continue
            if response.stdout != "200":
                raise ValueError(f"HTTP {response.stdout} fetching {url}")
            with tmp.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if expected and checksum != expected:
                raise ValueError(f"Source hash mismatch for {filename}")
            tmp.replace(target)
            return checksum
        finally:
            tmp.unlink(missing_ok=True)
    raise ValueError(f"Exact source unavailable: {source}/{filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with args.lock.open(encoding="utf-8") as stream:
        header = next(stream).strip()
        bioc = header.split(" Bioconductor=")[1]
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if not args.snapshot and not all(row.get("sha256") for row in rows):
        raise ValueError("Restore requires a complete checksummed R lock")
    with ThreadPoolExecutor(max_workers=6) as pool:
        sums = list(pool.map(lambda row: fetch(row, bioc, args.directory), rows))
    if args.snapshot:
        print(header)
        writer = csv.DictWriter(sys.stdout, fieldnames=["package", "version", "source", "sha256"],
                                delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(dict(row, sha256=value) for row, value in zip(rows, sums, strict=True))
