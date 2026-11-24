"""A simulated process exit leaves a checkpoint, never a synthetic success.

Default unit tests use fake_dispatch. The separate Docker probe actually kills a
container with a real R child and checks the same read-only diagnostic via HTTP.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.workspace.result_manager import ResultManager
from test_workflow_executor import host, fake_dispatch, executor, validate, deg, pca


def test_unfinished_checkpoint_survives_reload_without_becoming_success(host, fake_dispatch, monkeypatch):
    manager, registry = host
    original = registry.execute

    class SimulatedProcessExit(BaseException):
        pass

    def interrupted(name, inputs):
        if name == 'rnaseq.pca':
            raise SimulatedProcessExit()
        return original(name, inputs)

    monkeypatch.setattr(registry, 'execute', interrupted)
    checked = validate(host, deg(), pca())
    with pytest.raises(SimulatedProcessExit):
        executor(host).execute(checked)
    workflow_id = checked.validated_plan.workflow_id
    folder = manager.get_project_context('demo').output_root/'workflows'/workflow_id
    before = {path: path.read_bytes() for path in folder.rglob('*') if path.is_file()}
    state = json.loads((folder/'workflow_state.json').read_text())
    assert state['steps']['deg']['status']=='succeeded' and state['steps']['pca']['status']=='running'
    assert not (folder/'workflow_result.json').exists()
    manager.create_project('other')
    with TestClient(create_app(workspace_root=manager.root)) as client:
        result = client.get('/projects/demo/results').json()
        assert len(result['results'])==1 and result['results'][0]['skill_name']=='rnaseq.deg'
        assert any('WORKFLOW_NOT_FINALIZED: '+workflow_id in row for row in result['warnings'])
        assert not client.get('/projects/other/results').json()['warnings']
        assert client.get('/projects/demo/results').json()==result
    assert {path:path.read_bytes() for path in before}==before


def test_terminal_workflow_takes_precedence_over_checkpoint(host, fake_dispatch):
    finished = executor(host).execute(validate(host, deg(), pca()))
    browser = ResultManager(host[0], 'demo')
    assert len(browser.refresh())==2
    assert not any('WORKFLOW_NOT_FINALIZED' in text for text in browser.diagnostics)
    assert browser.collections[0]['id']==finished.workflow_id


@pytest.mark.parametrize('change', [{'workflow_id':'foreign'}, {'status':'succeeded'}])
def test_invalid_checkpoint_is_diagnostic_not_a_result(host, change):
    root = host[0].get_project_context('demo').output_root/'workflows'/'test'
    root.mkdir(parents=True)
    path = root/'workflow_state.json'
    path.write_text(json.dumps({'workflow_id':'test','status':'running',**change}))
    before = path.read_bytes()
    browser = ResultManager(host[0],'demo')
    assert browser.refresh()==[]
    assert len(browser.diagnostics)==1 and 'checkpoint' in browser.diagnostics[0]
    assert path.read_bytes()==before
