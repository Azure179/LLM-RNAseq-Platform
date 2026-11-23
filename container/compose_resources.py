"""Explicit small-demo resource samples; not a peak profiler or a biological study."""
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event, Thread
from time import perf_counter

import httpx

from dual_validation import command


def main() -> None:
    out=Path(__file__).resolve().parents[1]/'outputs/phase9_7'
    stop=Event()
    report={'kind':'instantaneous samples, NOT measured peaks','samples':[],'requests':[],'llm_requests':0}
    stage='idle'

    def sample() -> None:
        while not stop.is_set():
            started=datetime.now(timezone.utc).isoformat()
            label=stage
            raw=command(['docker','stats','--no-stream','--format','{{json .}}',
                         'rnaseq-phase97-api-1','rnaseq-phase97-streamlit-1'])
            report['samples'].append({'time':started,'stage':label,
                                      'containers':[json.loads(line) for line in raw.splitlines()]})
            stop.wait(1)

    monitor=Thread(target=sample,daemon=True)
    monitor.start()
    try:
        with httpx.Client(base_url='http://127.0.0.1:18007',trust_env=False,timeout=180) as client:
            source=None
            for skill,parameters in [('deg',{'treatment':'treatment','control':'control'}),
                                     ('go',{'gene_subset':'up','ontology':'BP'}),('kegg',{'gene_subset':'up'})]:
                stage=skill
                start=perf_counter()
                body={'project_id':'Compose_Backend_Demo','parameters':parameters}
                if source:
                    body['source_run_id']=source
                response=client.post('/skills/rnaseq.'+skill+'/run',json=body)
                response.raise_for_status()
                result=response.json()
                assert result['status']=='succeeded',result
                if skill=='deg':
                    source=result['run_id']
                report['requests'].append({'skill':skill,'seconds':perf_counter()-start,
                    'run_id':result['run_id'],'summary':result['summary']})
    finally:
        stop.set(); monitor.join(timeout=15)
        assert not monitor.is_alive()
    report['workspace_bytes']=int(command(['docker','exec','rnaseq-phase97-api-1','du','-sb','/workspace']).split()[0])
    (out/'resources.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','samples':len(report['samples'])}))


if __name__=='__main__':
    main()
