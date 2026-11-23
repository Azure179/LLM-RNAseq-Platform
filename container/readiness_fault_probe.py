"""Real disposable-container health failures; no R analysis or provider access."""

import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

IMAGE = 'bulk-rnaseq:phase9-4-final-20261028'
OUT = Path(__file__).resolve().parents[1]/'outputs'/'phase9_4'


def main() -> int:
    report = {'status':'failed','commands':[],'cases':[]}

    def command(*args: str) -> str:
        result = subprocess.run(['docker',*args],capture_output=True,text=True,encoding='utf-8',errors='replace')
        report['commands'].append({'args':list(args),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        assert result.returncode==0, result.stderr
        return result.stdout

    try:
        for case in ('missing_r','readonly_workspace','startup_permission'):
            name = 'rnaseq-phase94-fault-'+case
            extra = ['-e','RNASEQ_RSCRIPT=/missing/Rscript'] if case=='missing_r' else []
            mount = ['--tmpfs','/workspace:uid=10001,gid=10001']
            if case=='readonly_workspace':
                mount = ['--mount','type=volume,source=rnaseq-phase94-20261027,target=/workspace,readonly']
            if case=='startup_permission':
                extra = ['-e','RNASEQ_WORKSPACE_ROOT=/app/unwritable']
            command('run','-d','--name',name,'-p','127.0.0.1:18005:8000',*mount,*extra,
                    '--health-interval=1s','--health-start-period=1s','--health-retries=2',IMAGE)
            try:
                observation = {'case':case,'health_transitions':[]}
                for _ in range(60):
                    state = json.loads(command('inspect',name))[0]['State']
                    health = state.get('Health',{}).get('Status')
                    if not observation['health_transitions'] or observation['health_transitions'][-1]!=health:
                        observation['health_transitions'].append(health)
                    if case=='startup_permission' and not state['Running']:
                        assert state['ExitCode']!=0
                        logs = command('logs',name)+report['commands'][-1]['stderr']
                        assert 'PROJECT_IO_ERROR' in logs and 'Application startup failed' in logs
                        observation.update(exit_code=state['ExitCode'],diagnostic='PROJECT_IO_ERROR')
                        break
                    if case!='startup_permission' and health=='unhealthy':
                        for endpoint,code in (('/health',200),('/ready',503)):
                            try:
                                response = urlopen('http://127.0.0.1:18005'+endpoint,timeout=3)
                            except HTTPError as error:
                                response = error
                            with response:
                                payload = json.loads(response.read())
                                assert response.status==code
                                observation[endpoint]={'code':code,'payload':payload}
                                assert '/missing' not in json.dumps(payload) and '/workspace' not in json.dumps(payload)
                        assert observation['/ready']['payload']['error']['code']=='SERVICE_NOT_READY'
                        assert observation['/ready']['payload']['error']['details']['checks'][
                            'analysis_available' if case=='missing_r' else 'workspace_writable'] is False
                        break
                    time.sleep(1)
                else:
                    raise AssertionError('Expected unhealthy/failed state not observed')
                report['cases'].append(observation)
            finally:
                command('rm','-f',name)
        report['status']='passed'
    except Exception as error:
        report.update(error_type=type(error).__name__,error=str(error))
    finally:
        (OUT/'readiness-faults.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
