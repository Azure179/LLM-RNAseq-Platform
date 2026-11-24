"""Host-only launcher. Not imported by API/UI; experimental outputs never registered."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from threading import Event
from time import monotonic
from typing import Any

from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.workspace import ProjectManager
from .contract import ExecutionRequest, transition

MAX_INPUT=4*1024*1024
MAX_OUTPUT=16384


class DockerFailure(RuntimeError):
    def __init__(self, operation: str, outcome: subprocess.CompletedProcess) -> None:
        self.code='DOCKER_'+operation.upper()+'_FAILED'
        self.outcome=outcome
        super().__init__(self.code)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def docker(*args: str, timeout: float=20) -> subprocess.CompletedProcess:
    return subprocess.run(['docker',*args],capture_output=True,text=True,encoding='utf-8',
                          errors='replace',timeout=timeout)


def checked(*args: str) -> str:
    result=docker(*args)
    if result.returncode:
        raise DockerFailure(args[0],result)
    return result.stdout


def reject_links(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in [path,*path.parents]):
        raise ValueError('LINKED_PATH_REJECTED')


def validate_output(output: Path, identity: str, checksum: str, size: int) -> dict:
    reject_links(output)
    paths=list(output.iterdir())
    if len(paths)!=1 or paths[0].name!='receipt.json':
        raise ValueError('UNEXPECTED_OUTPUT')
    file=paths[0]; reject_links(file)
    if not file.is_file() or file.stat().st_size>MAX_OUTPUT or file.stat().st_nlink!=1:
        raise ValueError('INVALID_OUTPUT_FILE')
    receipt=json.loads(file.read_text(encoding='utf-8'))
    if set(receipt)!={'schema_version','execution_id','input_sha256','input_bytes','checks','limits','runtime_versions'}:
        raise ValueError('INVALID_OUTPUT_STRUCTURE')
    expected={'non_root','input_readonly','root_readonly','workspace_readonly','no_secret','no_docker_socket',
              'no_production_project','network_denied','no_new_privileges','seccomp_filter','no_capabilities'}
    if (receipt['schema_version']!=1 or receipt['execution_id']!=identity or receipt['input_sha256']!=checksum
            or receipt['input_bytes']!=size or set(receipt['checks'])!=expected
            or not all(value is True for value in receipt['checks'].values())):
        raise ValueError('OUTPUT_VERIFICATION_FAILED')
    if receipt['limits']!={'cpu.max':'50000 100000','memory.max':'134217728','pids.max':'32'}:
        raise ValueError('RESOURCE_LIMIT_VERIFICATION_FAILED')
    if not re.fullmatch(r'3\.13\.\d+',receipt['runtime_versions'].get('python','')):
        raise ValueError('RUNTIME_MISMATCH')
    return receipt


def specification(image: str, name: str, folder: Path) -> list[str]:
    return ['create','--name',name,'--label','org.rnaseq.isolated=phase96',
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges=true',
        '--pids-limit','32','--memory','128m','--memory-swap','128m','--cpus','0.5','--user','10001:10001',
        '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777',
        '--tmpfs','/workspace:ro,nosuid,nodev,noexec,size=1m',
        '--mount',f'type=bind,source={folder / "input"},target=/input,readonly',
        '--mount',f'type=bind,source={folder / "output"},target=/output',
        '--log-driver','local','--log-opt','max-size=1m','--log-opt','max-file=2',
        '--entrypoint','python',image,'-I','/opt/rnaseq/isolated_worker.py']


class TrustedLauncher:
    def __init__(self, manager: ProjectManager, staging: Path, approved_image_digest: str) -> None:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',approved_image_digest):
            raise ValueError('A trusted local image ID is required, never a request-selected image')
        self.manager=manager
        reject_links(staging)
        self.staging=staging.resolve()
        if self.staging.is_relative_to(manager.root) or manager.root.is_relative_to(self.staging):
            raise ValueError('Staging must be separate from production workspace')
        self.image=approved_image_digest

    def execute(self, request: ExecutionRequest, *, cancel: Event | None=None) -> dict[str,Any]:
        request=ExecutionRequest.model_validate(request.model_dump())
        # The trusted caller approves a task before invoking this method. No automatic LLM approval.
        self.staging.mkdir(parents=True,exist_ok=True)
        reject_links(self.staging)
        root=self.staging/request.execution_id
        root.mkdir(exist_ok=False)
        for child in ('input','output','tmp','logs'):
            (root/child).mkdir()
        manifest={**request.model_dump(mode='json'),'experimental':True,'registered_result':False,
            'worker_image_digest':self.image,'runtime_versions':{},'input_checksums':{},'output_checksums':{},
            'start_time':now(),'end_time':None,'exit_code':None,'termination_reason':None,
            'state_history':['CREATED'],'cleanup_complete':False}
        name='rnaseq-worker-'+request.execution_id
        created=False
        def state(value: str) -> None:
            manifest['execution_status']=transition(manifest['execution_status'],value)
            manifest['state_history'].append(value)
            write_json(root/'execution.json',manifest)
        try:
            history=ProjectHistory(self.manager,request.project_id)
            source,checksum=history.artifact(request.source_result_ref.run_id,request.source_result_ref.artifact_id)
            reject_links(source)
            if source.stat().st_size>MAX_INPUT:
                raise ValueError('INPUT_TOO_LARGE')
            data=source.read_bytes()
            if len(data)>MAX_INPUT or hashlib.sha256(data).hexdigest()!=checksum:
                raise ValueError('INPUT_CHANGED')
            (root/'input/source.csv').write_bytes(data)
            write_json(root/'input/request.json',request.model_dump(mode='json'))
            manifest['input_checksums']={'source':checksum}
            image=json.loads(checked('image','inspect',self.image))[0]
            if image['Id']!=self.image:
                raise ValueError('IMAGE_MISMATCH')
            state('VALIDATED')
            if cancel is not None and cancel.is_set():
                state('CANCELLED'); manifest['termination_reason']='CANCEL_REQUESTED'; return manifest
            command=specification(self.image,name,root)
            manifest['docker_command']=command
            checked(*command); created=True
            checked('start',name); state('RUNNING')
            deadline=monotonic()+10
            while True:
                observed=json.loads(checked('inspect',name))[0]
                if not observed['State']['Running']:
                    break
                if cancel is not None and cancel.is_set():
                    manifest['termination_reason']='CANCEL_REQUESTED'; checked('kill',name); state('CANCELLED'); break
                if monotonic()>deadline:
                    manifest['termination_reason']='TIME_LIMIT'; checked('kill',name); state('TIMED_OUT'); break
                Event().wait(0.1)
            observed=json.loads(checked('inspect',name))[0]
            manifest['exit_code']=observed['State']['ExitCode']
            manifest['oom_killed']=observed['State']['OOMKilled']
            logs=docker('logs',name)
            (root/'logs/stdout.txt').write_text(logs.stdout,encoding='utf-8')
            (root/'logs/stderr.txt').write_text(logs.stderr,encoding='utf-8')
            manifest['logs_exit_code']=logs.returncode
            if logs.returncode:
                raise RuntimeError('LOG_CAPTURE_FAILED')
            if sha256(source)!=checksum or sha256(root/'input/source.csv')!=checksum:
                raise ValueError('SOURCE_MODIFIED')
            if manifest['execution_status']=='RUNNING':
                if manifest['oom_killed']:
                    manifest['termination_reason']='MEMORY_LIMIT'; state('FAILED')
                elif manifest['exit_code']!=0:
                    manifest['termination_reason']='WORKER_EXIT_NONZERO'; state('FAILED')
                else:
                    receipt=validate_output(root/'output',request.execution_id,checksum,len(data))
                    manifest['runtime_versions']=receipt['runtime_versions']
                    manifest['output_checksums']={'receipt.json':sha256(root/'output/receipt.json')}
                    manifest['isolation_checks']=receipt['checks']; manifest['limits']=receipt['limits']
                    # Success is published only after container cleanup below.
        except Exception as error:
            manifest['termination_reason']=getattr(error,'code',None) or type(error).__name__
            if isinstance(error,DockerFailure):
                manifest['launcher_exit_code']=error.outcome.returncode
                (root/'logs/launcher_stdout.txt').write_text(error.outcome.stdout,encoding='utf-8')
                (root/'logs/launcher_stderr.txt').write_text(error.outcome.stderr,encoding='utf-8')
            if manifest['execution_status'] in {'CREATED','VALIDATED','RUNNING'}:
                state('FAILED')
        finally:
            if created:
                try:
                    cleanup=docker('rm','-f','-v',name)
                    manifest['cleanup_complete']=cleanup.returncode==0
                except (OSError,subprocess.TimeoutExpired):
                    manifest['cleanup_complete']=False
                if not manifest['cleanup_complete']:
                    manifest['termination_reason']='CLEANUP_FAILED'
                    manifest['output_checksums']={}
                    if manifest['execution_status']=='RUNNING':
                        state('FAILED')
            else:
                manifest['cleanup_complete']=True
            if manifest['execution_status']=='RUNNING':
                state('SUCCEEDED')
            manifest['end_time']=now()
            write_json(root/'execution.json',manifest)
        return manifest
