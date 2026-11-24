"""Verify and use the fixed actionlint Windows binary for Fast CI."""
import hashlib
from io import BytesIO
from pathlib import Path
import subprocess
from urllib.request import urlopen
from zipfile import ZipFile


def lint(root: Path) -> None:
    cache = root / "outputs/ci/tools"
    cache.mkdir(parents=True, exist_ok=True)
    exe = cache / "actionlint.exe"
    if not exe.is_file():
        url = ("https://github.com/rhysd/actionlint/releases/download/v1.7.12/"
               "actionlint_1.7.12_windows_amd64.zip")
        with urlopen(url, timeout=90) as response:
            archive = response.read()
        if hashlib.sha256(archive).hexdigest() != "6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9":
            raise ValueError("actionlint archive checksum mismatch")
        with ZipFile(BytesIO(archive)) as z:
            exe.write_bytes(z.read("actionlint.exe"))
    if hashlib.sha256(exe.read_bytes()).hexdigest() != "54ca21be3de4c7cfa26914aa8b61bd76bf573ef3caac5f80d110558cdf241718":
        raise ValueError("actionlint executable checksum mismatch")
    files = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not {"ci.yml", "scientific-validation.yml", "docker-integration.yml"} <= {p.name for p in files}:
        raise ValueError("Missing mandatory workflow definition")
    subprocess.run([str(exe), "-shellcheck=", "-pyflakes=", *map(str, files)], cwd=root, check=True)

