"""Disposable Compose projects for the two opt-in gates (public fixtures only)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
from urllib.request import urlopen

from container.compose_checks import validate_runtime, validate_spec
from scripts.ci.gate import Blocked, Gate, ROOT


def run_level(gate: Gate) -> None:
    if not shutil.which("docker"):
        raise Blocked("Docker CLI missing")
    try:
        engine = subprocess.check_output(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                                         text=True, stderr=subprocess.PIPE, timeout=30).strip()
    except (subprocess.SubprocessError, OSError) as exc:
        raise Blocked("Docker Engine unavailable") from exc
    if engine not in {"linux/x86_64", "linux/amd64"}:
        raise Blocked("Requires a Linux amd64 Docker Engine")

    project = "rnaseq-ci-" + uuid4().hex[:16]
    volume = project + "-workspace"
    image = "bulk-rnaseq:" + project
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "RNASEQ_", "DEEPSEEK_"))}
    env.update(RNASEQ_VOLUME_NAME=volume, RNASEQ_IMAGE=image, RNASEQ_API_PUBLISHED_PORT="0",
               RNASEQ_UI_PUBLISHED_PORT="0", DEEPSEEK_API_KEY="")
    with TemporaryDirectory() as tmp, (gate.directory / "docker-private.log").open("w", encoding="utf-8") as log:
        empty = Path(tmp) / "empty.env"
        empty.touch()
        compose = ["docker", "compose", "--env-file", str(empty), "-f", str(ROOT/"compose.yaml"), "-p", project]

        def run(*args, capture=False):
            proc = subprocess.run(args, env=env, text=True, encoding="utf-8", errors="replace",
                                  stdout=subprocess.PIPE if capture else log, stderr=log)
            if proc.returncode:
                # Do not include full commands, Compose config or inspect documents in public errors.
                raise RuntimeError(f"{args[0]} exited {proc.returncode} during {gate.stage}")
            return proc.stdout.strip() if capture else None

        def health(service):
            cid = run(*compose, "ps", "-q", service, capture=True)
            inspected = json.loads(run("docker", "inspect", cid, capture=True))[0]
            result = validate_runtime(inspected)
            assert result["health"] == "healthy" and inspected["Mounts"][0]["Name"] == volume
            return cid

        def scientific(api):
            run("docker", "exec", api, "mkdir", "/tmp/public-fixture")
            for file in ("examples/data/counts.csv", "examples/data/metadata.csv",
                         "tests/fixtures/ci_science/go.tsv", "tests/fixtures/ci_science/kegg.tsv",
                         "tests/fixtures/ci_science/reference.json", "scripts/ci/scientific.py"):
                run("docker", "cp", str(ROOT / file), api + ":/tmp/public-fixture/")
            run("docker", "exec", api, "python", "/opt/rnaseq/container/smoke_runtime.py", "run",
                "--project", "PublicCI", "--inputs", "/tmp/public-fixture", "--counts", "counts.csv",
                "--metadata", "metadata.csv", "--go", "go.tsv", "--kegg", "kegg.tsv",
                "--treatment", "treatment", "--control", "control")
            run("docker", "exec", api, "python", "/tmp/public-fixture/scientific.py", "/workspace/PublicCI",
                "/tmp/public-fixture", "/tmp/public-fixture/reference.json")

        def snapshot(cid, outcome):
            code = ("from pathlib import Path; import json,sys; sys.path.insert(0,'/opt/rnaseq/container'); "
                    "from bulk_rnaseq.workspace import ProjectManager; from runtime_checks import result_snapshot; "
                    "print(json.dumps(result_snapshot(ProjectManager(Path('/workspace')),sys.argv[1])))")
            return json.loads(run("docker", "exec", cid, "python", "-c", code, outcome["project"], capture=True))

        services = ["api"] if gate.name == "scientific" else ["api", "streamlit"]
        def cleanup():
            run(*compose, "down", "--timeout", "20")
            # Only remove this run's UUID volume after confirming Compose ownership.
            exists = run("docker", "volume", "ls", "-q", "--filter", "name=" + volume, capture=True).splitlines()
            if volume in exists:
                info = json.loads(run("docker", "volume", "inspect", volume, capture=True))[0]
                if info["Labels"].get("com.docker.compose.project") != project:
                    raise ValueError("Refusing to clean a volume with a different owner")
                run("docker", "volume", "rm", volume)
            if run("docker", "image", "ls", "-q", image, capture=True):
                run("docker", "image", "rm", image)

        try:
            gate.check("compose specification", lambda: validate_spec(json.loads(run(
                *compose, "config", "--format", "json", capture=True))))
            gate.check("build current Dockerfile", run, *compose, "build", "api")
            gate.check("start and wait", run, *compose, "up", "-d", "--no-build", "--wait", "--wait-timeout",
                       "180", *services)
            api = gate.check("API health and runtime security", health, "api")
            if gate.name == "scientific":
                gate.check("six real Skills, versioned numeric baseline and lineage", scientific, api)
            else:
                ui = gate.check("Streamlit health and runtime security", health, "streamlit")
                port = run(*compose, "port", "api", "8000", capture=True).rsplit(":", 1)[1]
                def access_log_privacy():
                    marker = "public-ci-probe-" + uuid4().hex
                    with urlopen(f"http://127.0.0.1:{port}/health?probe={marker}", timeout=10) as response:
                        assert response.status == 200
                        request_id = response.headers["X-Request-ID"]
                    logs = subprocess.check_output(["docker", "logs", api], env=env, stderr=subprocess.STDOUT)
                    assert request_id and request_id.encode() in logs
                    assert marker.encode() not in logs
                gate.check("route audit without raw query disclosure", access_log_privacy)
                def simultaneous():
                    return json.loads(run(sys.executable, str(ROOT / "tests/integration/check_deg_guard_containers.py"),
                                          api, ui, port, capture=True))
                outcome = gate.check("cross-container concurrent DEG reuse and atomic index", simultaneous)
                before = snapshot(api, outcome)
                assert set(before) == {outcome["source"], *outcome["children"]}
                gate.check("stop and recreate both services", run, *compose, "down")
                gate.check("restart from same volume", run, *compose, "up", "-d", "--no-build", "--wait",
                           "--wait-timeout", "180")
                def verify_recovery():
                    for service in services:
                        assert snapshot(health(service), outcome) == before, "Artifact, index or parent changed"
                gate.check("persistent artifacts and lineage on both entries", verify_recovery)
        finally:
            cleanup()

