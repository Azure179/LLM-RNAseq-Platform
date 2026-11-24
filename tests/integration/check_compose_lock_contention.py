"""Explicit real-container stress test for the two Phase 9.7 demo deployments."""
import argparse
from functools import partial
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Barrier
from time import monotonic
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "container"))
from dual_validation import EXECUTE, command, python

SNAP = """
import json, sys
from pathlib import Path
sys.path.insert(0, '/opt/rnaseq/container')
from runtime_checks import result_snapshot
from bulk_rnaseq.workspace import ProjectManager
print(json.dumps(result_snapshot(ProjectManager(Path('/workspace')), sys.argv[1])))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mount", choices=["named", "bind"])
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    named = args.mount == "named"
    prefix = "rnaseq-phase97" if named else "rnaseq-phase97-bind"
    project = "Compose_UI_Demo" if named else "Compose_Backend_Demo"
    source = "33674495-2802-4498-8211-55b1d3105bec" if named else "fa814b0a-9bb0-4724-aa49-76afcb58bf3b"
    api, ui = (prefix + "-" + name + "-1" for name in ("api", "streamlit"))
    report = ROOT / "outputs/phase9_7_1" / (args.mount + "-" + uuid4().hex[:8] + ".json")
    rounds = []
    for container in (api, ui):
        cfg = json.loads(command(["docker", "inspect", container]))[0]
        assert cfg["Config"]["Labels"]["com.docker.compose.project"] == prefix
        mount, = cfg["Mounts"]
        assert mount["Destination"] == "/workspace"
        if named:
            assert mount["Name"] == "rnaseq-phase97-20261030"
        else:
            assert mount["Source"].replace(chr(92), "/").endswith("/outputs/phase9_7/Windows Bind Workspace")
        assert cfg["State"]["Health"]["Status"] == "healthy"
    # Docker Desktop host mounts need longer for repeated artifact discovery.
    deadline = 180 if named else 600
    probe = partial(python, timeout=deadline)
    before = probe(api, SNAP, project)
    assert before[source]["skill"] == "rnaseq.deg"
    initial = dict(before)
    with httpx.Client(base_url=f"http://127.0.0.1:{18007 if named else 18008}", trust_env=False, timeout=deadline) as client:
        for i in range(args.rounds):
            gate = Barrier(2, timeout=10)
            def submit_api():
                gate.wait()
                response = client.post("/skills/rnaseq.volcano/run", json={
                    "project_id":project, "source_run_id":source, "parameters":{"top_n_labels":4}})
                response.raise_for_status()
                return response.json()
            def submit_ui():
                gate.wait()
                return probe(ui, EXECUTE, project, source)
            start = monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(f) for f in (submit_api, submit_ui)]
                pair = [f.result() for f in futures]
            assert all(r["status"] == "succeeded" and r["metadata"]["source_run_id"] == source for r in pair)
            ids = {r["run_id"] for r in pair}
            assert len(ids) == 2 and ids.isdisjoint(before)
            # Inspect durable registration before discovery has a chance to backfill.
            registration = """
import hashlib, json, sys
from pathlib import Path
root = Path("/workspace") / sys.argv[1] / "results"
rows = []
for identity in sys.argv[2:]:
    path = root / "result_index" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    row = json.loads(path.read_bytes())
    workflow = json.loads((root / "workflows" / row["workflow_id"] / "workflow_result.json").read_bytes())
    assert workflow["status"] == "succeeded"
    assert any(step["run_id"] == identity for step in workflow["step_results"].values())
    rows.append(row)
print(json.dumps(rows))
"""
            rows = probe(ui, registration, project, *sorted(ids))
            assert {row["run_id"] for row in rows} == ids
            assert all(row["parent_run_ids"] == [source] for row in rows)
            after = probe(ui, SNAP, project)
            assert after.keys() - before.keys() == ids and all(after.get(k)==r for k,r in before.items())
            for key in ids:
                row = after[key]
                assert row["skill"] == "rnaseq.volcano" and row["parents"] == [source]
                assert row["workflow_id"] and {"plot","plot_data"} <= row["hashes"].keys()
            rounds.append(dict(round=i+1, runs=sorted(ids), seconds=round(monotonic()-start, 2)))
            report.write_text(json.dumps(dict(source=source, rounds=rounds), indent=2))
            print(args.mount, rounds[-1], flush=True)
            before = after
    assert probe(api, SNAP, project) == before
    assert all(before[k]==r for k,r in initial.items())
    for n in (api,ui):
        command(["docker","exec",n,"test","!","-e","/workspace/.workspace.lock"])
        assert command(["docker","inspect",n,"--format","{{.State.Health.Status}}"]).strip()=="healthy"
    print("PASS", args.mount, len(rounds), flush=True)


if __name__ == "__main__":
    main()
