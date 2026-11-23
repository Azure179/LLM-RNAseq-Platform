"""Run the public demonstration from any directory. Requires Python 3 and Docker Compose."""
import argparse
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-running", action="store_true", help="Keep the two services open for browsing")
    args = parser.parse_args()
    stack = "rnaseq-demo-" + uuid4().hex[:12]
    folder = ROOT / "outputs" / "public-demo" / stack
    folder.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DEEPSEEK_", "COMPOSE_", "RNASEQ_"))}
    env.update(RNASEQ_VOLUME_NAME=stack + "-workspace", RNASEQ_IMAGE="bulk-rnaseq:public-demo",
               RNASEQ_API_PUBLISHED_PORT="0", RNASEQ_UI_PUBLISHED_PORT="0", DEEPSEEK_API_KEY="")
    with TemporaryDirectory() as temp:
        dotenv = Path(temp) / "empty.env"
        dotenv.touch()
        base = ["docker", "compose", "--env-file", str(dotenv), "-f", str(ROOT / "compose.yaml"), "-p", stack]

        def compose(*arguments: str) -> None:
            subprocess.run([*base, *arguments], cwd=ROOT, env=env, check=True)

        compose("config", "-q")
        try:
            compose("up", "-d", "--build", "--wait", "--wait-timeout", "240")
            compose("exec", "-T", "api", "mkdir", "/tmp/public-demo")
            for name in ["examples/data/counts.csv", "examples/data/metadata.csv",
                         "tests/fixtures/ci_science/go.tsv", "tests/fixtures/ci_science/kegg.tsv",
                         "examples/demo/version_guard.py"]:
                compose("cp", str(ROOT / name), "api:/tmp/public-demo/" + Path(name).name)
            smoke = ("exec", "-T", "api", "python", "/opt/rnaseq/container/smoke_runtime.py")
            compose(*smoke, "run", "--project", "PublicDemo", "--inputs", "/tmp/public-demo",
                    "--counts", "counts.csv", "--metadata", "metadata.csv", "--go", "go.tsv", "--kegg", "kegg.tsv",
                    "--treatment", "treatment", "--control", "control")
            compose(*smoke, "verify-reuse", "--project", "PublicDemo")
            compose("exec", "-T", "api", "python", "/tmp/public-demo/version_guard.py")
            for name in ("summary.json", "volcano.png"):
                compose("cp", "api:/tmp/public-demo/" + name, str(folder / name))
            print("Demo evidence:", folder.relative_to(ROOT))
            if args.keep_running:
                print("Open the Streamlit address below, then select PublicDemo:")
                compose("port", "streamlit", "8501")
        finally:
            if args.keep_running:
                print(f"Stop this demo from the repository root: docker compose -p {stack} down")
            else:
                compose("down")
            print("Retained demo volume:", env["RNASEQ_VOLUME_NAME"])


if __name__ == "__main__":
    main()
