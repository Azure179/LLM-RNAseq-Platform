"""Free policy regression. Actual YAML resolution/build/runtime are explicit checks."""
from copy import deepcopy
from pathlib import Path

import pytest

from container.compose_checks import validate_spec


def document() -> dict:
    common = dict(image='local:image', user='10001:10001', restart='unless-stopped', init=True,
        cap_drop=['ALL'], security_opt=['no-new-privileges:true'], build={'context': '.'},
        volumes=[{'type':'volume','source':'workspace','target':'/workspace'}],
        environment={'RNASEQ_WORKSPACE_ROOT':'/workspace','RNASEQ_RSCRIPT':'Rscript'},
        logging={'driver':'local','options':{'max-file':'3'}})
    services = {}
    for name, port, path in [('api',8000,'/health'),('streamlit',8501,'/_stcore/health')]:
        services[name] = deepcopy(common)
        services[name].update(ports=[{'host_ip':'127.0.0.1','target':port}],
                              healthcheck={'test':['CMD','python',path]})
    return {'services':services}


def test_valid_policy_does_not_return_secrets():
    spec = document()
    spec['services']['api']['environment']['DEEPSEEK_API_KEY'] = 'private-test-value'
    assert 'private-test-value' not in str(validate_spec(spec))


@pytest.mark.parametrize('field,value', [
    ('user','0:0'), ('privileged',True), ('depends_on',{'api':{}}),
    ('network_mode','host'), ('restart','no'), ('init',False), ('cap_drop',[]),
    ('security_opt',[]), ('ports',[{'host_ip':'0.0.0.0','target':8000}]),
    ('volumes',[{'type':'bind','source':'/var/run/docker.sock','target':'/var/run/docker.sock'}]),
    ('environment',{'RNASEQ_WORKSPACE_ROOT':'/elsewhere','RNASEQ_RSCRIPT':'Rscript'}),
    ('build',{'args':{'DEEPSEEK_API_KEY':'not-allowed'}}),
    ('logging',{'driver':'local','options':{'max-file':'1'}}), ('image','other:image'),
])
def test_unsafe_deployment_rejected(field, value):
    spec = document()
    spec['services']['api'][field] = value
    with pytest.raises(AssertionError):
        validate_spec(spec)


def test_worker_cannot_be_a_default_service():
    spec = document()
    spec['services']['worker'] = deepcopy(spec['services']['api'])
    with pytest.raises(AssertionError):
        validate_spec(spec)


def test_bind_directory_must_already_exist():
    spec = document()
    for service in spec['services'].values():
        service['volumes'] = [dict(type='bind', source='C:/Workspace with spaces', target='/workspace',
                                   bind={'create_host_path':False})]
    assert validate_spec(spec)['mount_type'] == 'bind'
    spec['services']['api']['volumes'][0]['bind']['create_host_path'] = True
    with pytest.raises(AssertionError):
        validate_spec(spec)


def test_static_files_keep_secret_and_worker_boundaries():
    root = Path(__file__).resolve().parents[1]
    compose = (root/'compose.yaml').read_text()
    assert 'depends_on:' not in compose and 'docker.sock' not in compose
    assert '127.0.0.1:${RNASEQ_API_PUBLISHED_PORT:-8000}' in compose
    assert '127.0.0.1:${RNASEQ_UI_PUBLISHED_PORT:-8501}' in compose
    assert 'DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}' in compose
    assert 'context: .' in compose and 'dockerfile: Dockerfile' in compose
    assert 'create_host_path: false' in (root/'compose.bind.yaml').read_text()
    assert 'DEEPSEEK_API_KEY=\n' in (root/'container/compose.env.example').read_text()
