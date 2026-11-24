"""Opt-in: exercise automatic selection through HTTP and a second container.

Pass the two dedicated test container names and the published loopback API port.
Creates a uniquely named synthetic project in their shared workspace.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Barrier
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "container"))
from dual_validation import python


SETUP = """
import json, sys
from pathlib import Path
from tempfile import TemporaryDirectory
from bulk_rnaseq.workspace import ProjectManager, FileImportService
m = ProjectManager(Path('/workspace'))
m.create_project(sys.argv[1])
with TemporaryDirectory() as temporary:
    for role, text in zip(('count_matrix', 'metadata'), sys.argv[2:]):
        path = Path(temporary) / (role + '.csv')
        path.write_text(text, encoding='utf-8')
        assert FileImportService(m).import_file(sys.argv[1], path, role).status == 'succeeded'
print(json.dumps(True))
"""
UI = """
import json, sys
from pathlib import Path
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.api.execution import public_result
manager = ProjectManager(Path('/workspace'))
result = SingleSkillService(create_default_registry(), manager).execute(
    'rnaseq.volcano', sys.argv[1], {'top_n_labels': 2})
print(json.dumps(public_result(result, manager)))
"""
CHECK = """
import json, sys
from pathlib import Path
root = Path('/workspace') / sys.argv[1]
assert not (root.parent / '.workspace.lock').exists()
rows = [json.loads(p.read_bytes()) for p in (root/'results/result_index').glob('*.json')]
assert len(rows) == 3 and sum(r['skill_name']=='rnaseq.deg' for r in rows) == 1
assert {r['run_id'] for r in rows} == set(sys.argv[2:])
for row in rows:
    if row['run_id'] == sys.argv[2]:
        continue
    assert row['parent_run_ids'] == [sys.argv[2]]
    assert (root/'results'/row['outputs']['plot']).read_bytes().startswith(b'\\x89PNG')
    state = json.loads((root/'results/workflows'/row['workflow_id']/'workflow_result.json').read_bytes())
    record = state['step_records']['skill']
    assert record['run_id'] == row['run_id'] and record['status'] == 'succeeded'
    assert set(record['verified_deg_input_sha256']) == {'count_matrix', 'metadata'}
print(json.dumps(True))
"""


def check(api: str, ui: str, port: int) -> dict:
    project = "Guard_" + uuid4().hex[:12]
    demo = ROOT / "examples/data"
    python(api, SETUP, project, (demo / "counts.csv").read_text(), (demo / "metadata.csv").read_text())
    gate = Barrier(2)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=180) as client:
        def post(skill: str, parameters: dict) -> dict:
            response = client.post(f"/skills/rnaseq.{skill}/run", json={"project_id": project, "parameters": parameters})
            response.raise_for_status()
            return response.json()

        parent = post("deg", {"treatment": "treatment", "control": "control"})["run_id"]

        def ui_call():
            gate.wait(timeout=20)
            return python(ui, UI, project)

        with ThreadPoolExecutor() as pool:
            future = pool.submit(ui_call)
            gate.wait(timeout=20)
            children = [post("volcano", {"top_n_labels": 3}), future.result()]
    ids = [r["run_id"] for r in children]
    assert len(set(ids)) == 2
    for r in children:
        assert r["status"] == "succeeded" and r["metadata"]["parent_run_ids"] == [parent]
        assert r["metadata"]["upstream_sources"]["deg_result_file"]["reuse_scope"] == "current_input_snapshot"
    for container in (api, ui):
        assert python(container, CHECK, project, parent, *ids)
    return {"project": project, "source": parent, "children": ids, "status": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("api")
    parser.add_argument("ui")
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    print(json.dumps(check(args.api, args.ui, args.port)))
