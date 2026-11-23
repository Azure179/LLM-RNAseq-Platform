"""Explicit real backend checks in a dedicated Compose deployment, no LLM calls.

Never imported by the application. Copy into /tmp and invoke with docker exec.
Uses existing public demo data and the existing services/Registry/R implementation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import httpx

sys.path.insert(0, '/opt/rnaseq/container')
from runtime_checks import result_snapshot, check_previews, check_path_boundaries
from bulk_rnaseq.workspace import ProjectManager, FileImportService
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workflow.models import WorkflowPlan
from bulk_rnaseq.run_io import write_json


def snapshot(project: str) -> dict:
    manager = ProjectManager(Path('/workspace'))
    root = manager.get_project_context(project).project_root
    # Listing may backfill the canonical index; hash AFTER discovery.
    result = result_snapshot(manager, project)
    files = {}
    for path in root.rglob('*'):
        if path.is_file() and path.suffix not in ('.log', '.lock'):
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    browser=ResultManager(manager,project)
    previews={'images':0,'tables':0,'downloads':0}
    # A DEG-only project legitimately has tables but no PNG yet.
    for run in browser.list_runs(status='succeeded'):
        for artifact in run.artifacts:
            assert artifact.error_code is None
            if artifact.kind=='IMAGE':
                assert browser.preview_image(run.run_id,artifact.artifact_id).startswith(b'\x89PNG')
                previews['images']+=1
            elif artifact.kind=='TABLE':
                assert len(browser.preview_table(run.run_id,artifact.artifact_id,limit=100).frame)<=100
                previews['tables']+=1
            else:
                continue
            _,data,_=browser.download(run.run_id,artifact.artifact_id)
            assert len(data)==artifact.size_bytes
            previews['downloads']+=1
    return {'results': result, 'files':files, 'previews':previews,
            'boundaries':check_path_boundaries(manager,project)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['seed','workflow','finish','snapshot','reuse'])
    parser.add_argument('--project',default='Compose_Backend_Demo')
    args = parser.parse_args()
    manager = ProjectManager(Path('/workspace'))
    report = {'mode':args.mode,'status':'failed','llm_requests':0}
    started = perf_counter()
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=600, trust_env=False) as client:
        def post(path: str, body: dict) -> dict:
            response = client.post(path,json=body)
            response.raise_for_status()
            value=response.json()
            assert value['status']=='succeeded', value
            return value
        if args.mode == 'seed':
            assert os.geteuid()==10001
            manager.create_project(args.project, description='Existing public demo; explicit offline Compose integration test')
            manager.create_project(args.project+'_Other')
            importer=FileImportService(manager)
            for role, path in [('count_matrix','counts.csv'),('metadata','metadata.csv')]:
                result=importer.import_file(args.project,Path('/tmp/compose-demo')/path,role)
                assert result.status=='succeeded'
            # Reuse the existing Phase 9.4 synthetic universe annotations unchanged.
            for role, path in [('go_mapping','go.tsv'),('kegg_mapping','kegg.tsv')]:
                result=importer.import_file(args.project,Path('/tmp/compose-demo')/path,role)
                assert result.status=='succeeded'
            report['project']=args.project
        elif args.mode in ('workflow','finish'):
            plan=WorkflowPlan(project_id=args.project, user_request='Explicit offline Compose test: DEG with two independent branches', steps=[
                {'step_id':'deg','skill_name':'rnaseq.deg','parameters':{'treatment':'treatment','control':'control'}},
                {'step_id':'volcano','skill_name':'rnaseq.volcano','input_bindings':{
                    'deg_result_file':{'source':'workflow_step','step_id':'deg','output':'deg_csv'}}},
                {'step_id':'kegg','skill_name':'rnaseq.kegg','input_bindings':{
                    'gene_list':{'source':'workflow_step','step_id':'deg','output':'filtered_csv','gene_subset':'up'},
                    'analysis_genes':{'source':'workflow_step','step_id':'deg','output':'deg_csv'}}}])
            root=manager.get_project_context(args.project).project_root
            if args.mode == 'finish':
                completed=list((root/'results/workflows').glob('*/workflow_result.json'))
                assert len(completed)==1, 'Select the exact completed workflow; never guess among several'
                result=root_result=json.loads(completed[0].read_text())
                assert root_result['status']=='succeeded'
            else:
                result=post('/workflow/run',{'project_id':args.project,'plan':plan.model_dump(mode='json')})
            report['workflow']=result
            source=result['step_results']['deg']['run_id']
            report['go']=post('/skills/rnaseq.go/run',{'project_id':args.project,'source_run_id':source,
                                                    'parameters':{'ontology':'BP','gene_subset':'up'}})
            report['source_run_id']=source
            write_json(root/'reports/compose_source.json',{'source_run_id':source,'workflow_id':result['workflow_id']})
            report['snapshot']=snapshot(args.project)
        elif args.mode == 'reuse':
            root=manager.get_project_context(args.project).project_root
            source=json.loads((root/'reports/compose_source.json').read_text())['source_run_id']
            before=result_snapshot(manager,args.project)
            report['volcano']=post('/skills/rnaseq.volcano/run',{'project_id':args.project,'source_run_id':source,'parameters':{}})
            assert report['volcano']['metadata']['source_run_id']==source
            after=result_snapshot(manager,args.project)
            assert {k for k,v in before.items() if v['skill']=='rnaseq.deg'}=={k for k,v in after.items() if v['skill']=='rnaseq.deg'}
            assert all(after[k]==v for k,v in before.items())
            report['snapshot']=snapshot(args.project)
        else:
            report['snapshot']=snapshot(args.project)
    report.update(status='PASS',seconds=perf_counter()-started)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
