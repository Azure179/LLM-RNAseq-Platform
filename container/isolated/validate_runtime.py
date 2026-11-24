"""Explicit trusted-host integration harness; no LLM, no production registration."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from bulk_rnaseq.workspace import ProjectManager
from bulk_rnaseq.run_io import sha256
from .contract import ExecutionRequest, Source
from .launcher import TrustedLauncher, checked


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--staging',type=Path,required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--source-run',required=True)
    parser.add_argument('--image-digest',required=True,help='Locally audited immutable image ID, not LLM input')
    args=parser.parse_args()
    manager=ProjectManager(args.workspace)
    launcher=TrustedLauncher(manager,args.staging,args.image_digest)
    before={str(p.relative_to(manager.root)):sha256(p) for p in manager.root.rglob('*') if p.is_file()}
    reports=[]
    for task,expected in [('smoke','SUCCEEDED'),('smoke','SUCCEEDED'),('test_failure','FAILED'),
                          ('test_bad_output','FAILED'),('test_timeout','TIMED_OUT'),('test_oom','FAILED')]:
        request=ExecutionRequest(project_id=args.project,source_result_ref=Source(run_id=args.source_run),approved_task_type=task)
        result=launcher.execute(request); reports.append(result)
        print(json.dumps({'task':task,'status':result['execution_status'],'exit':result['exit_code'],
                          'reason':result['termination_reason'],'cleanup':result['cleanup_complete']}),flush=True)
        assert result['execution_status']==expected and result['cleanup_complete'],result
        if task=='test_oom': assert result['termination_reason']=='MEMORY_LIMIT'
        if expected!='SUCCEEDED': assert result['output_checksums']=={}
    cancel=Event()
    request=ExecutionRequest(project_id=args.project,source_result_ref=Source(run_id=args.source_run),approved_task_type='test_timeout')
    with ThreadPoolExecutor() as pool:
        future=pool.submit(launcher.execute,request,cancel=cancel)
        deadline=monotonic()+20
        record=args.staging/request.execution_id/'execution.json'
        while not future.done() and monotonic()<deadline:
            if record.exists() and json.loads(record.read_text())['execution_status']=='RUNNING': break
            sleep(.05)
        cancel.set(); result=future.result(timeout=30); reports.append(result)
    assert result['execution_status']=='CANCELLED' and result['cleanup_complete']
    assert not checked('ps','-aq','--filter','label=org.rnaseq.isolated=phase96').strip()
    # Existing indexes may be backfilled by ResultManager discovery; artifact/source files must not change.
    after={str(p.relative_to(manager.root)):sha256(p) for p in manager.root.rglob('*') if p.is_file()}
    assert all(after.get(k)==v for k,v in before.items()),'Source workspace content changed'
    assert all('result_index' in k for k in set(after)-set(before))
    evidence={'status':'PASS','executions':reports,'source_files_unchanged':len(before),
              'registered_worker_results':0,'containers_remaining':0}
    (args.staging/'runtime_report.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('Runtime isolation checks PASS')


if __name__=='__main__':
    main()
