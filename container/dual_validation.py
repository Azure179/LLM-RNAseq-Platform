"""Explicit host-side runtime checks for an already UI-created disposable project.

No LLM calls or synthetic results. Run after the manual/browser UI portion.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Any

import httpx


def command(args: list[str], *, source: str | None = None, timeout: int = 180) -> str:
    outcome = subprocess.run(args, input=source, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    if outcome.returncode:
        raise RuntimeError(f"Validation command failed: exit={outcome.returncode}; {outcome.stderr[-1500:]}")
    return outcome.stdout


def python(container: str, source: str, *args: str, timeout: int = 180) -> Any:
    return json.loads(command(["docker", "exec", "-i", container, "python", "-", *args], source=source, timeout=timeout))


SNAPSHOT = '''
import json, sys
from pathlib import Path
sys.path.insert(0, '/opt/rnaseq/container')
from bulk_rnaseq.workspace import ProjectManager
from runtime_checks import result_snapshot, check_previews, check_path_boundaries
m=ProjectManager(Path('/workspace')); p=sys.argv[1]
root=m.get_project_context(p).project_root
requests=[json.loads(f.read_text()) for f in sorted((root/'runtime/chat_requests').glob('*.json'))]
assert all(r['status']=='completed' for r in requests)
print(json.dumps(dict(results=result_snapshot(m,p), previews=check_previews(m,p),
    boundaries=check_path_boundaries(m,p), requests=requests,
    json_files=sum(1 for f in root.rglob('*.json') if json.loads(f.read_text()) is not None))))
'''


EXECUTE = '''
import json, sys
from pathlib import Path
from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.api.execution import public_result
m=ProjectManager(Path('/workspace'))
s=SingleSkillService(create_default_registry(),m)
r=s.execute('rnaseq.volcano',sys.argv[1],{'top_n_labels':3},source_run_id=sys.argv[2])
assert r.result.status=='succeeded', r.result.error_code
print(json.dumps(public_result(r,m)))
'''


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['snapshot','reuse','concurrent'])
    parser.add_argument('--project',default='Dual_UI_Demo')
    parser.add_argument('--prefix',default='rnaseq-phase95')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    api,ui=args.prefix+'-api',args.prefix+'-ui'
    started=perf_counter()
    with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False,timeout=180) as client:
        def get(path: str) -> dict:
            response=client.get(path); response.raise_for_status(); return response.json()
        health=get('/health'); ready=get('/ready'); skills=get('/skills')
        before=get(f'/projects/{args.project}/results')
        degs=[r for r in before['results'] if r['skill_name']=='rnaseq.deg']
        assert degs and all(r['status']=='succeeded' for r in degs)
        # Exact UI single-Skill identity, obtained from its persisted real chat record.
        initial=python(ui,SNAPSHOT,args.project)
        request=next(r['result'] for r in initial['requests'] if r['result']['request_type']=='single_skill')
        source=request['run_id']; assert source in {r['run_id'] for r in degs}
        assert next(r for r in degs if r['run_id']==source)['summary']==request['summary']
        def run_api() -> dict:
            response=client.post('/skills/rnaseq.volcano/run',json={
                'project_id':args.project,'source_run_id':source,'parameters':{'top_n_labels':4}})
            response.raise_for_status(); result=response.json()
            assert result['metadata']['source_run_id']==source
            return result
        added=[]
        if args.mode=='reuse':
            added=[run_api()]
        elif args.mode=='concurrent':
            with ThreadPoolExecutor(max_workers=2) as pool:
                calls=[pool.submit(run_api), pool.submit(python,ui,EXECUTE,args.project,source)]
                added=[f.result() for f in calls]
            assert len({r['run_id'] for r in added})==2
        after=get(f'/projects/{args.project}/results')
        assert {r['run_id'] for r in after['results'] if r['skill_name']=='rnaseq.deg'}=={r['run_id'] for r in degs}
        assert {r['run_id'] for r in added}<={r['run_id'] for r in after['results']}
        # Sequential scans compare identical snapshots; the writes above are concurrent.
        a=python(api,SNAPSHOT,args.project); b=python(ui,SNAPSHOT,args.project)
        assert a==b
        assert all(a['results'][key]==value for key,value in initial['results'].items())
        resources=command(['docker','stats','--no-stream','--format','{{json .}}',api,ui])
        report={'mode':args.mode,'status':'PASS','duration_seconds':round(perf_counter()-started,3),
            'health':health,'ready':ready,'skill_count':len(skills['skills']),
            'source_run_id':source,'added':added,'api_results':after,'snapshot':a,
            'resources':[json.loads(line) for line in resources.splitlines()]}
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':'PASS','mode':args.mode,'runs':len(a['results']),
                          'added':[r['run_id'] for r in added]}))


if __name__=='__main__':
    main()
