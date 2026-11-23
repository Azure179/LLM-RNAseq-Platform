"""Free contract/path/output tests; never launch Docker or an LLM."""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from container.isolated.contract import ExecutionRequest, transition
from container.isolated.launcher import TrustedLauncher, specification, validate_output, reject_links
from bulk_rnaseq.workspace import ProjectManager


def payload():
    return {'project_id':'demo','source_result_ref':{'run_id':'real-run'}}


@pytest.mark.parametrize('field', ['command','code','python','r_source','image','docker_flags','mount_path','daemon','entrypoint'])
def test_forbidden_fields(field):
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({**payload(),field:'untrusted'})


@pytest.mark.parametrize('changes', [
    {'project_id':'../other'}, {'project_id':'C:\\private'}, {'approved_task_type':'shell'},
    {'resource_profile':'unlimited'}, {'execution_id':'../x'}, {'execution_status':'SUCCEEDED'},
    {'validated_parameters':{'command':'id'}}, {'source_result_ref':{'run_id':'../x'}},
    {'source_result_ref':{'run_id':'a','artifact_id':'../../secret'}}, {'output_expectations':'anything'},
    {'input_artifact_refs':('source','source')},
])
def test_invalid_contract(changes):
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({**payload(),**changes})


def test_valid_contract_and_unique_ids():
    a=ExecutionRequest.model_validate(payload()); b=ExecutionRequest.model_validate(payload())
    assert a.execution_id!=b.execution_id
    assert a.resource_profile=='smoke-small'


@pytest.mark.parametrize('end',['SUCCEEDED','FAILED','TIMED_OUT','CANCELLED'])
def test_lifecycle(end):
    assert transition(transition(transition('CREATED','VALIDATED'),'RUNNING'),end)==end
    with pytest.raises(ValueError):
        transition(end,'RUNNING')


def test_no_skip_validation():
    with pytest.raises(ValueError):
        transition('CREATED','RUNNING')


def test_trusted_config_and_mount_boundaries(tmp_path):
    m=ProjectManager(tmp_path/'workspace')
    with pytest.raises(ValueError):
        TrustedLauncher(m,tmp_path/'stage','user/image:latest')
    with pytest.raises(ValueError):
        TrustedLauncher(m,m.root/'staging','sha256:'+'a'*64)
    command=specification('sha256:'+'a'*64,'trusted',tmp_path/'stage')
    for value in ['none','--read-only','ALL','no-new-privileges=true','32','128m','0.5','10001:10001']:
        assert value in command
    assert not any('docker.sock' in value or 'privileged' in value or 'unconfined' in value for value in command)
    assert command[-3:]==['sha256:'+'a'*64,'-I','/opt/rnaseq/isolated_worker.py']
    assert 'max-file=2' in command  # local log driver compression rejects max-file=1.


def receipt():
    return {'schema_version':1,'execution_id':'a'*32,'input_sha256':'b'*64,'input_bytes':4,
        'checks':{k:True for k in ['non_root','input_readonly','root_readonly','workspace_readonly','no_secret',
            'no_docker_socket','no_production_project','network_denied','no_new_privileges','seccomp_filter','no_capabilities']},
        'limits':{'cpu.max':'50000 100000','memory.max':'134217728','pids.max':'32'},'runtime_versions':{'python':'3.13.2'}}


@pytest.mark.parametrize('defect',['none','missing','wrong_hash','extra_file','oversize','bad_check','bad_limit','bad_json'])
def test_output_validation(tmp_path,defect):
    r=receipt()
    if defect=='missing': r.pop('checks')
    if defect=='wrong_hash': r['input_sha256']='c'*64
    if defect=='bad_check': r['checks']['network_denied']=False
    if defect=='bad_limit': r['limits']['memory.max']='max'
    (tmp_path/'receipt.json').write_text(json.dumps(r))
    if defect=='extra_file': (tmp_path/'extra').write_text('no')
    if defect=='oversize': (tmp_path/'receipt.json').write_text(' '*16385)
    if defect=='bad_json': (tmp_path/'receipt.json').write_text('{')
    if defect=='none': assert validate_output(tmp_path,'a'*32,'b'*64,4)==r
    else:
        with pytest.raises(ValueError): validate_output(tmp_path,'a'*32,'b'*64,4)


def test_link_rejected_without_windows_symlink_privilege(tmp_path,monkeypatch):
    original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda p: p==tmp_path or original(p))
    with pytest.raises(ValueError): reject_links(tmp_path/'output')


def test_missing_project_never_starts_docker(tmp_path,monkeypatch):
    monkeypatch.setattr('container.isolated.launcher.docker',lambda *a,**k: pytest.fail('Docker must not start'))
    m=ProjectManager(tmp_path/'workspace')
    result=TrustedLauncher(m,tmp_path/'stage','sha256:'+'a'*64).execute(ExecutionRequest.model_validate(payload()))
    assert result['execution_status']=='FAILED' and result['output_checksums']=={}
