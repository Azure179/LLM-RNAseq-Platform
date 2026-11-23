"""Explicit offline HTTP E2E on public synthetic demo; never calls an LLM.

Run only in the dedicated Phase 9.4 container. Production services are unchanged;
fault flags are consumed only by the separate test Rscript wrapper.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator
from bulk_rnaseq.run_io import write_json
from bulk_rnaseq.workspace import ProjectManager, FileImportService
from bulk_rnaseq.workflow.models import WorkflowPlan, WorkflowResult
from bulk_rnaseq.workflow.validator import PlanValidator
from bulk_rnaseq.workflow.history import ProjectHistory
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.customization_service import PreparedCustomization
from bulk_rnaseq.workspace.chat_store import ChatStore
from bulk_rnaseq.customization.models import CustomizationDecision
from bulk_rnaseq.llm.client import ChatResponse
from runtime_checks import check_enrichment, result_snapshot, check_previews

ROOT = Path('/workspace')
EVIDENCE = Path('/evidence')
PROJECT = 'Phase94_Demo'
LOGGER = logging.getLogger('phase94')


def deg(step: str = 'deg') -> dict:
    return dict(step_id=step, skill_name='rnaseq.deg', parameters={'treatment': 'treatment', 'control': 'control'})


def source(output: str = 'deg_csv', *, run_id: str | None = None, subset: str | None = None) -> dict:
    value = {'source': 'project_result', 'run_id': run_id} if run_id else {'source': 'workflow_step', 'step_id': 'deg'}
    return {**value, 'output': output, **({'gene_subset': subset} if subset else {})}


def plot(kind: str, run_id: str | None = None) -> dict:
    return dict(step_id=kind, skill_name='rnaseq.'+kind,
                parameters={'group_column': 'condition', 'top_n': 10} if kind == 'heatmap' else {},
                input_bindings={'deg_result_file': source(run_id=run_id)})


def enrichment(kind: str, subset: str, run_id: str | None = None) -> dict:
    return dict(step_id=kind+'_'+subset, skill_name='rnaseq.'+kind,
                parameters={'ontology': 'BP'} if kind == 'go' else {}, input_bindings={
                    'gene_list': source('filtered_csv', run_id=run_id, subset=subset),
                    'analysis_genes': source(run_id=run_id)})


class Probe:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.manager = ProjectManager(ROOT)
        self.client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=600, trust_env=False)
        self.report = {'mode': mode, 'status': 'failed', 'llm_requests': 0, 'http': []}
        self.document = self.client.get('/openapi.json').json()

    def request(self, method: str, endpoint: str, body: dict | None = None,
                expected: tuple[int, ...] = (200,), schema_path: str | None = None) -> dict:
        started = perf_counter()
        response = self.client.request(method, endpoint, json=body) if body is not None else self.client.request(method, endpoint)
        data = response.json()
        self.report['http'].append({'method': method, 'endpoint': endpoint, 'status': response.status_code,
                                    'request_id': response.headers.get('x-request-id'), 'seconds': perf_counter()-started})
        assert response.status_code in expected, (endpoint, response.status_code, data)
        assert response.headers.get('x-request-id')
        assert not any(value in response.text for value in ('/workspace/', '/usr/local/', 'Traceback', 'secret-sentinel'))
        if schema_path:
            schemas = self.document['paths'][schema_path][method.lower()]['responses']
            key = str(response.status_code)
            if key in schemas and 'content' in schemas[key]:
                schema = schemas[key]['content']['application/json']['schema']
                Draft202012Validator({**schema, 'components': self.document['components']}).validate(data)
        return data

    def workflow(self, steps: list[dict], *, expected: str = 'succeeded', project: str = PROJECT) -> dict:
        plan = WorkflowPlan(project_id=project, user_request='Explicit synthetic offline Phase 9.4 test plan', steps=steps)
        valid = PlanValidator(create_default_registry()).validate_project(plan, self.manager.get_project_context(project),
                                                                          history=ProjectHistory(self.manager, project))
        assert valid.valid, valid.errors
        data = self.request('POST', '/workflow/run', {'project_id': project, 'plan': plan.model_dump(mode='json')},
                            schema_path='/workflow/run')
        WorkflowResult.model_validate(data)
        assert data['status'] == expected, data
        context = self.manager.get_project_context(project)
        saved = context.restore_paths(json.loads((context.output_root/'workflows'/plan.workflow_id/'workflow_result.json').read_text()))
        assert saved['status'] == data['status'] and saved['execution_order'] == data['execution_order']
        for name, result in data['step_results'].items():
            assert saved['step_results'][name]['summary'] == result['summary']
            assert saved['step_results'][name]['run_id'] == result['run_id']
            for relative in result['outputs'].values():
                assert context.resolve_path(relative, must_exist=True).is_file()
            self.request('GET', f'/projects/{project}/results/{result["run_id"]}',
                         schema_path='/projects/{project_id}/results/{run_id}')
        self.report.setdefault('workflows', []).append(data)
        self.save()
        return data

    def save(self) -> None:
        write_json(EVIDENCE/(self.mode+'.json'), self.report)

    def setup(self) -> None:
        assert os.geteuid() == 10001 and not os.environ.get('DEEPSEEK_API_KEY')
        fixtures = ROOT/'fixtures'
        fixtures.mkdir(exist_ok=False)
        # Explicit synthetic annotations for the existing 30-gene public demo.
        for kind in ('go', 'kegg'):
            header = 'gene_id\tgo_id\tgo_name\tontology\n' if kind == 'go' else 'gene_id\tpathway_id\tpathway_name\n'
            rows = []
            for i in range(1, 31):
                ident = ('GO:0008150' if kind == 'go' else 'map01100')
                rows.append(f'gene_{i:03d}\t{ident}\tSynthetic test universe'+ ('\tBP' if kind == 'go' else '')+'\n')
            (fixtures/(kind+'.tsv')).write_text(header+''.join(rows), encoding='utf-8')
        importer = FileImportService(self.manager)
        for name in (PROJECT, 'Phase94_Other', 'Phase94_NoAnnotation', 'Phase94_Bad'):
            self.manager.create_project(name, description='Isolated synthetic Phase 9.4 fixture, not biological findings')
            for role, filename in [('count_matrix', Path('/fixtures/counts.csv')), ('metadata', Path('/fixtures/metadata.csv'))]:
                assert importer.import_file(name, filename, role).status == 'succeeded'
            if name != 'Phase94_NoAnnotation':
                for role, filename in [('go_mapping', fixtures/'go.tsv'), ('kegg_mapping', fixtures/'kegg.tsv')]:
                    assert importer.import_file(name, filename, role).status == 'succeeded'
        self.report['dataset'] = {'kind': 'existing synthetic demo with synthetic universe annotations', 'genes': 30, 'samples': 6}

    def run(self) -> None:
        for endpoint in ('/health', '/ready', '/skills'):
            self.request('GET', endpoint, schema_path=endpoint)
        a = self.workflow([deg(), {'step_id':'pca', 'skill_name':'rnaseq.pca', 'parameters':{'group_column':'condition'}}, plot('volcano'), plot('heatmap')])
        assert not a['step_records']['pca']['depends_on'] and not a['step_records']['deg']['depends_on']
        b = self.workflow([deg(), *[enrichment(kind, subset) for subset in ('all','up','down') for kind in ('go','kegg')]])
        identity = b['step_results']['deg']['run_id']
        self.report['enrichment'] = [check_enrichment(self.manager, PROJECT, b['step_results'][kind+'_'+subset], identity, subset)
                                     for subset in ('all','up','down') for kind in ('go','kegg')]
        before = result_snapshot(self.manager, PROJECT)
        c = self.workflow([plot('volcano', identity), enrichment('kegg', 'up', identity)])
        assert all(row['source_run_id'] == identity for row in c['step_records'].values())
        after = result_snapshot(self.manager, PROJECT)
        assert all(after[key] == value for key, value in before.items())
        assert sum(r['skill']=='rnaseq.deg' for r in after.values()) == 2
        self.report.update(source=identity, snapshot=after, reuse_no_deg=True)
        self.errors(identity)
        self.concurrency(identity)
        self.customization(identity)
        self.report['snapshot'] = result_snapshot(self.manager, PROJECT)
        self.report['previews'] = check_previews(self.manager, PROJECT)

    def errors(self, identity: str) -> None:
        importer = FileImportService(self.manager)
        bad = ROOT/'fixtures/bad.csv'
        before = self.manager.open_project('Phase94_Bad').metadata.files.model_dump()
        for role, text in [('count_matrix', 'gene_id,a,b\ng1,1.5,-2\n'), ('metadata', 'sample_id,condition\nwrong,control\n')]:
            bad.write_text(text)
            result = importer.import_file('Phase94_Bad', bad, role, replace=True)
            assert result.status == 'failed', result
            self.report.setdefault('import_errors', []).append({'role':role, 'error_code':result.error_code})
            assert self.manager.open_project('Phase94_Bad').metadata.files.model_dump() == before
        self.request('POST','/skills/rnaseq.deg/run', {'project_id':PROJECT,'parameters':{'treatment':'unknown','control':'control'}}, (422,409))
        self.request('POST','/skills/rnaseq.volcano/run', {'project_id':PROJECT,'parameters':{},'source_run_id':'absent'}, (404,409))
        self.request('POST','/skills/rnaseq.go/run', {'project_id':'Phase94_NoAnnotation','parameters':{}}, (409,422))
        self.request('POST','/skills/rnaseq.deg/run', {'project_id':PROJECT,'parameters':{'output_dir':'/etc/secret-sentinel'}}, (422,))
        self.request('GET',f'/projects/Phase94_Other/results/{identity}', expected=(404,))
        # These real formal routes must fail closed without contacting a provider.
        for endpoint in ('/workflow/plan','/assistant/analyze','/customizations/plan'):
            self.request('POST',endpoint, {'project_id':PROJECT,'request':'Draw PCA'}, (503,))
        flag = ROOT/'.phase94-fail-volcano'
        flag.touch(exist_ok=False)
        try:
            renderer = {**plot('volcano', identity), 'step_id':'a_volcano'}
            failed = self.workflow([renderer, enrichment('kegg', 'up', identity)], expected='partial')
            assert failed['execution_order']==['a_volcano','kegg_up']
            assert failed['failed_steps'] == ['a_volcano'] and failed['completed_steps'] == ['kegg_up']
        finally:
            flag.unlink()
        # Controlled subprocess failure after real validation, not a fake Skill result.
        root_flag = ROOT/'.phase94-fail-deg'
        root_flag.touch(exist_ok=False)
        try:
            failed = self.workflow([deg(), plot('volcano'), enrichment('kegg','up')], project='Phase94_Bad', expected='failed')
        finally:
            root_flag.unlink()
        assert failed['failed_steps'] == ['deg'] and set(failed['skipped_steps']) == {'volcano','kegg_up'}
        assert not failed['step_results']['deg']['outputs']
        self.workflow([deg()], project='Phase94_Bad')

    def concurrency(self, identity: str) -> None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            reads = list(pool.map(lambda _: self.request('GET',f'/projects/{PROJECT}/results'), range(3)))
        assert all(row['project_id'] == PROJECT for row in reads)
        before = result_snapshot(self.manager, PROJECT)
        body = {'project_id':PROJECT,'source_run_id':identity,'parameters':{}}
        with ThreadPoolExecutor(max_workers=2) as pool:
            writes = list(pool.map(lambda _: self.request('POST','/skills/rnaseq.volcano/run',body,(200,409)), range(2)))
        success = [row for row in writes if row.get('status') == 'succeeded']
        assert success and all(row.get('status') == 'succeeded' or row['error']['code']=='WORKSPACE_BUSY' for row in writes)
        after = result_snapshot(self.manager, PROJECT)
        assert len(after)==len(before)+len(success) and all(after[key]==value for key,value in before.items())
        assert all(after[row['run_id']]['parents']==[identity] for row in success)
        for path in ROOT.rglob('*.json'):
            json.loads(path.read_text(encoding='utf-8'))
        self.report['concurrency'] = {'readers':3,'writers':2,'successful_writes':len(success),'safe_busy':2-len(success),'json_integrity':True}

    def customization(self, identity: str) -> None:
        # Approved-plan fixture explicitly labeled NOT_RUN, not a claimed model response.
        ticket = PreparedCustomization(plan_id=uuid4().hex, project_id=PROJECT, source_run_id=identity,
            request='Deterministic test: change labels only', decision=CustomizationDecision(
                mode='parameter_override', target_skill='rnaseq.volcano', parameters={'top_n_labels':3}))
        store = ChatStore(self.manager, PROJECT)
        with store.lock():
            ticket_path = store.path('customization_plans/'+ticket.plan_id+'.json')
            ticket_path.parent.mkdir(exist_ok=True)
            write_json(ticket_path, {'status':'planned',
                'plan':ticket.model_dump(mode='json'), 'inputs_snapshot':self.manager.open_project(PROJECT).metadata.files.model_dump(),
                'provider_response':ChatResponse('explicit-test-fixture','NOT_RUN','','stop',None,0).to_dict()})
        body = {'project_id':PROJECT,'plan':ticket.model_dump(mode='json')}
        first = self.request('POST','/customizations/run',body,schema_path='/customizations/run')
        assert first['status']=='succeeded' and first['source_run_id']==identity
        assert self.request('POST','/customizations/run',body) == first
        self.report['customization'] = first

    def finish(self) -> None:
        """Resume test assertions after fixing a harness bug; never edit result files."""
        previous = json.loads((EVIDENCE/'run.json').read_text())
        assert len(previous['workflows']) >= 3
        assert all(row['status']=='succeeded' for row in previous['workflows'][:3])
        self.report.update(source=previous['source'], prior_core_report='run.json')
        self.errors(previous['source'])
        self.concurrency(previous['source'])
        self.customization(previous['source'])
        self.report['snapshot'] = result_snapshot(self.manager, PROJECT)
        self.report['previews'] = check_previews(self.manager, PROJECT)

    def restart(self) -> None:
        previous = json.loads((EVIDENCE/('finish.json' if (EVIDENCE/'finish.json').exists() else 'run.json')).read_text())
        assert previous['status']=='passed'
        before = result_snapshot(self.manager, PROJECT)
        assert before == previous['snapshot']
        self.workflow([plot('volcano',previous['source'])])
        after = result_snapshot(self.manager, PROJECT)
        assert all(after[key]==value for key,value in before.items())
        assert sum(r['skill']=='rnaseq.deg' for r in after.values())==2
        self.report['snapshot'] = after

    def interrupt(self) -> None:
        plan = WorkflowPlan(project_id=PROJECT, user_request='Controlled crash during real Skill execution', steps=[
            deg('a_deg'), {'step_id':'z_pca','skill_name':'rnaseq.pca','parameters':{'group_column':'condition'}}])
        write_json(EVIDENCE/'interrupt-plan.json', plan.model_dump(mode='json'))
        (ROOT/'.phase94-hold-pca').touch(exist_ok=False)
        # Host kills the whole dedicated container only after the wrapper marker.
        self.request('POST','/workflow/run',{'project_id':PROJECT,'plan':plan.model_dump(mode='json')})
        raise AssertionError('Interruption did not occur')

    def recover(self) -> None:
        (ROOT/'.phase94-hold-pca').unlink()
        plan = json.loads((EVIDENCE/'interrupt-plan.json').read_text())
        context = self.manager.get_project_context(PROJECT)
        folder = context.output_root/'workflows'/plan['workflow_id']
        state = context.restore_paths(json.loads((folder/'workflow_state.json').read_text()))
        assert not (folder/'workflow_result.json').exists()
        assert state['status']=='running' and state['steps']['a_deg']['status']=='succeeded' and state['steps']['z_pca']['status']=='running'
        previous = json.loads((EVIDENCE/'restart.json').read_text())['snapshot']
        now = result_snapshot(self.manager, PROJECT)
        assert all(now[key]==value for key,value in previous.items())
        assert len(now)==len(previous)+1  # Completed DEG preserved; no PCA success invented.
        identity = state['steps']['a_deg']['run_id']
        assert identity in now
        self.request('POST','/workflow/run',{'project_id':PROJECT,'plan':plan}, (409,))
        self.workflow([plot('volcano',identity)])
        self.report.update(interrupted_workflow=plan['workflow_id'], checkpoint=state,
            classification='Incomplete checkpoint, no terminal WorkflowResult; not resumed or marked successful',
            preserved_complete_run=identity, snapshot=result_snapshot(self.manager,PROJECT))
        self.report['api_results'] = self.request('GET',f'/projects/{PROJECT}/results')

    def verify_fixed(self) -> None:
        previous = json.loads((EVIDENCE/'recover.json').read_text())
        assert previous['status']=='passed'
        before = result_snapshot(self.manager, PROJECT)
        assert before==previous['snapshot']
        response = self.request('GET',f'/projects/{PROJECT}/results',schema_path='/projects/{project_id}/results')
        assert any('WORKFLOW_NOT_FINALIZED: '+previous['interrupted_workflow'] in row for row in response['warnings'])
        self.request('GET','/ready',schema_path='/ready')
        self.workflow([plot('volcano', previous['preserved_complete_run'])])
        after = result_snapshot(self.manager, PROJECT)
        assert all(after[key]==value for key,value in before.items())
        assert len(after)==len(before)+1
        self.report.update(snapshot=after, warnings=response['warnings'], previews=check_previews(self.manager, PROJECT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['setup','run','finish','restart','interrupt','recover','verify_fixed'])
    mode = parser.parse_args().mode
    logging.basicConfig(level=logging.INFO)
    probe = Probe(mode)
    started = perf_counter()
    try:
        getattr(probe,mode)()
        probe.report['status']='passed'
        return 0
    except Exception as error:
        probe.report.update(error_type=type(error).__name__, error=str(error)[:1500])
        LOGGER.exception('%s failed: %s',mode,type(error).__name__)
        return 1
    finally:
        probe.report['seconds']=perf_counter()-started
        probe.save()
        probe.client.close()


if __name__=='__main__':
    raise SystemExit(main())
