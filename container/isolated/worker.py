"""Audited deterministic test worker. Never accepts code, paths or commands."""
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sys
import time


def denied_write(path: str) -> bool:
    try:
        with Path(path).open('ab') as handle:
            handle.write(b'probe')
    except OSError:
        return True
    return False


def main() -> None:
    request=json.loads(Path('/input/request.json').read_text())
    task=request['approved_task_type']
    data=Path('/input/source.csv').read_bytes()
    if task=='test_timeout':
        time.sleep(120)
    elif task=='test_oom':
        blocks=[]
        while True:
            blocks.append(bytearray(8*1024*1024))
    elif task=='test_failure':
        sys.stderr.write('Audited failure fixture\n'); sys.exit(23)
    elif task=='test_bad_output':
        Path('/output/receipt.json').write_text('{}'); return
    elif task!='smoke':
        raise ValueError('Unknown audited task')
    checks={
        'non_root':os.getuid()==10001,
        'input_readonly':denied_write('/input/source.csv'),
        'root_readonly':denied_write('/app/worker-write-probe'),
        'workspace_readonly':denied_write('/workspace/worker-write-probe'),
        'no_secret':not any(key.startswith('DEEPSEEK_') or key.endswith(('API_KEY','TOKEN','PASSWORD')) for key in os.environ),
        'no_docker_socket':not Path('/var/run/docker.sock').exists(),
        'no_production_project':not (Path('/workspace')/request['project_id']).exists(),
    }
    try:
        with socket.create_connection(('1.1.1.1',443),timeout=1):
            checks['network_denied']=False
    except OSError:
        checks['network_denied']=True
    status=Path('/proc/self/status').read_text()
    checks['no_new_privileges']='NoNewPrivs:\t1' in status
    checks['seccomp_filter']='Seccomp:\t2' in status
    checks['no_capabilities']='CapEff:\t0000000000000000' in status
    limits={name:Path('/sys/fs/cgroup',name).read_text().strip() for name in ('cpu.max','memory.max','pids.max')}
    receipt={'schema_version':1,'execution_id':request['execution_id'],
        'input_sha256':hashlib.sha256(data).hexdigest(),'input_bytes':len(data),
        'checks':checks,'limits':limits,'runtime_versions':{'python':platform.python_version()}}
    Path('/output/receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
    print('Audited receipt written',flush=True)


if __name__=='__main__':
    main()
