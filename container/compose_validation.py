"""Opt-in host checks for the disposable rnaseq-phase97 Compose deployment.

Never deletes volumes. Never prints or saves full Compose config/docker inspect.
No DeepSeek requests. Browser tests are separate and cannot be inferred from HTTP.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter, sleep
from typing import Any

import httpx

from compose_checks import validate_spec, validate_runtime
from dual_validation import command, python, SNAPSHOT, EXECUTE
from dual_lock_check import check as check_lock

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase9_7'
PROJECT='rnaseq-phase97'
API='rnaseq-phase97-api-1'
UI='rnaseq-phase97-streamlit-1'
COMPOSE=['docker','compose','-p',PROJECT,'--env-file',str(OUT/'deployment.env'),
         '-f',str(ROOT/'compose.yaml'),'-f',str(ROOT/'compose.cached.yaml')]


def inspect(name: str) -> dict:
    data=json.loads(command(['docker','inspect',name]))[0]
    assert data['Config']['Labels']['com.docker.compose.project']==PROJECT
    return data


def healthy(name: str, timeout: int=100) -> dict:
    started=perf_counter()
    states=[]
    while perf_counter()-started<timeout:
        state=inspect(name)['State']
        value=state.get('Health',{}).get('Status',state['Status'])
        if not states or states[-1]!=value:
            states.append(value)
        if value=='healthy' and state['Running']:
            return {'states':states,'seconds':perf_counter()-started}
        sleep(1)
    raise AssertionError('Health did not recover: '+name)


def snapshot(project: str) -> dict:
    source=(ROOT/'container/compose_probe.py').read_text(encoding='utf-8')
    # Execute the checked-in test script via stdin; no persistent code mount.
    a=python(API, source, 'snapshot','--project',project)['snapshot']
    b=python(UI, source, 'snapshot','--project',project)['snapshot']
    assert a==b
    return a


def get(client: httpx.Client, path: str) -> dict:
    response=client.get(path); response.raise_for_status(); return response.json()


def run() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['audit','reuse','concurrent','snapshot','persistence','faults','locks'])
    args=parser.parse_args()
    started=perf_counter()
    report: dict[str,Any]={'mode':args.mode,'status':'failed','llm_requests':0}
    with httpx.Client(base_url='http://127.0.0.1:18007',trust_env=False,timeout=240) as client:
        for name in (API,UI):
            inspect(name)  # Refuse unrelated resources before mutations.
        if args.mode=='audit':
            document=json.loads(command(COMPOSE+['config','--format','json']))
            report['spec']=validate_spec(document)
            report['runtime']={name:validate_runtime(inspect(name)) for name in (API,UI)}
            report['startup']={name:healthy(name) for name in (API,UI)}
            report['health']=get(client,'/health'); report['ready']=get(client,'/ready')
            assert httpx.get('http://127.0.0.1:18507/_stcore/health',trust_env=False).status_code==200
            report['image_bytes']=json.loads(command(['docker','image','inspect',document['services']['api']['image']]))[0]['Size']
            report['idle_samples']=[json.loads(line) for line in command(['docker','stats','--no-stream','--format','{{json .}}',API,UI]).splitlines()]
            report['workspace_bytes']=int(command(['docker','exec',API,'du','-sb','/workspace']).split()[0])
        elif args.mode in ('reuse','concurrent'):
            project='Compose_UI_Demo'
            before=get(client,f'/projects/{project}/results')
            degs={r['run_id'] for r in before['results'] if r['skill_name']=='rnaseq.deg'}
            assert len(degs)==1, 'Require the exact single real UI DEG'
            source=next(iter(degs))
            initial=snapshot(project)
            def api_run() -> dict:
                response=client.post('/skills/rnaseq.volcano/run',json={'project_id':project,
                    'source_run_id':source,'parameters':{'top_n_labels':4}})
                response.raise_for_status()
                result=response.json()
                assert result['status']=='succeeded' and result['metadata']['source_run_id']==source
                return result
            if args.mode=='reuse':
                added=[api_run()]
            else:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    tasks=[pool.submit(api_run),pool.submit(python,UI,EXECUTE,project,source)]
                    added=[task.result() for task in tasks]
            after=snapshot(project)
            assert all(after['results'][k]==v for k,v in initial['results'].items())
            assert {k for k,v in after['results'].items() if v['skill']=='rnaseq.deg'}==degs
            assert {r['run_id'] for r in added}<=set(after['results'])
            report.update(source_run_id=source,added=added,snapshot=after)
        elif args.mode=='snapshot':
            report['projects']={name:snapshot(name) for name in ('Compose_UI_Demo','Compose_Backend_Demo')}
        elif args.mode=='persistence':
            names=('Compose_UI_Demo','Compose_Backend_Demo')
            before={p:snapshot(p) for p in names}
            ids={n:inspect(n)['Id'] for n in (API,UI)}
            command(COMPOSE+['down'])  # Deliberately no -v.
            command(COMPOSE+['up','-d','--no-build','--wait'])
            after={p:snapshot(p) for p in names}
            assert before==after
            assert all(inspect(n)['Id']!=ids[n] for n in ids)
            recreated={n:inspect(n)['Id'] for n in ids}
            command(COMPOSE+['up','-d','--no-build','--force-recreate','--wait'])
            assert before=={p:snapshot(p) for p in names}
            assert all(inspect(n)['Id']!=recreated[n] for n in ids)
            report.update(down_up='PASS',force_recreate='PASS',projects=before)
            source=(ROOT/'container/compose_probe.py').read_text(encoding='utf-8')
            report['reuse_after_recreate']=python(API,source,'reuse')
        elif args.mode=='locks':
            report['named']=check_lock(['docker','exec','-i',API,'python'],['docker','exec','-i',UI,'python'])
            # SIGKILL leaves an exclusive-create file. Verify explicit, fail-closed recovery.
            prelude="from pathlib import Path; from bulk_rnaseq.workspace import ProjectManager, ProjectError; m=ProjectManager(Path('/workspace')); "
            source=prelude+"import os; print(os.getpid(),flush=True)\nwith m._write_lock():\n print('LOCKED',flush=True)\n input()"
            holder=subprocess.Popen(['docker','exec','-i',API,'python','-u','-c',source],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                pid=int(holder.stdout.readline().strip())
                assert holder.stdout.readline().strip()=='LOCKED'
                command(['docker','exec',API,'kill','-9',str(pid)])
                holder.communicate(timeout=20)
                assert holder.returncode!=0
                observed=command(['docker','exec',UI,'python','-c',prelude+"\ntry:\n with m._write_lock(): pass\nexcept ProjectError as e: print(e.code)"])
                assert observed.strip()=='WORKSPACE_BUSY'
                # All service writers stopped first. Only remove this test's confirmed orphan.
                command(COMPOSE+['stop'])
                command(COMPOSE+['run','--rm','--no-deps','api','python','-c',
                    "from pathlib import Path; p=Path('/workspace/.workspace.lock'); assert p.is_file(); p.unlink()"])
                command(COMPOSE+['start','--wait'])
                report['kill_recovery']=check_lock(['docker','exec','-i',API,'python'],['docker','exec','-i',UI,'python'])
            finally:
                if holder.poll() is None:
                    holder.communicate('\n',timeout=20)
        else:
            report['failures']={}
            for service,name,other,url in [('api',API,UI,'http://127.0.0.1:18507/_stcore/health'),
                                            ('streamlit',UI,API,'http://127.0.0.1:18007/health')]:
                # Freeze the application (not Docker health exec) to prove health can become unhealthy.
                top=command(['docker','exec',name,'python','-c',
                    "from pathlib import Path; print(next(p.name for p in Path('/proc').iterdir() if p.name.isdigit() and p.name!='1' and b'-m\\x00' in (p/'cmdline').read_bytes() and (b'uvicorn' in (p/'cmdline').read_bytes() or b'streamlit' in (p/'cmdline').read_bytes())))"])
                pid=int(top.strip())
                command(['docker','exec',name,'kill','-STOP',str(pid)])
                try:
                    deadline=perf_counter()+85
                    while inspect(name)['State']['Health']['Status']!='unhealthy':
                        assert perf_counter()<deadline
                        sleep(2)
                    assert httpx.get(url,trust_env=False,timeout=5).status_code==200
                finally:
                    command(['docker','exec',name,'kill','-CONT',str(pid)])
                restored=healthy(name)
                old_count=inspect(name)['RestartCount']
                # Kill the child: init exits and Docker restart policy recovers the service.
                command(['docker','exec',name,'kill','-9',str(pid)])
                deadline=perf_counter()+40
                while inspect(name)['RestartCount']==old_count:
                    assert perf_counter()<deadline
                    sleep(1)
                report['failures'][service]={'unhealthy_detected':True,'peer_available':True,
                    'after_continue':restored,'automatic_restart':healthy(name)}
            report['projects']={p:snapshot(p) for p in ('Compose_UI_Demo','Compose_Backend_Demo')}
    report.update(status='PASS',seconds=perf_counter()-started)
    (OUT/(args.mode+'.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'mode':args.mode,'status':'PASS','seconds':report['seconds']}))


if __name__=='__main__':
    run()
