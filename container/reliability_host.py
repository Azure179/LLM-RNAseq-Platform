"""Explicit Phase 9.4 controls for an already-created, isolated test container.

Never targets the normal app or a production Workspace. All Docker return codes,
sampling observations and timings are persisted; samples are not true peaks.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import subprocess
import threading
import time
from urllib.request import urlopen

NAME = 'rnaseq-phase94-20261027'
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'phase9_4'
LOGGER = logging.getLogger('phase94.host')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['run', 'finish', 'restart', 'interrupt_recover', 'health','verify_fixed'])
    stage = parser.parse_args().stage
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'stage': stage, 'status': 'failed', 'commands': [], 'samples': [],
              'started_at': datetime.now(timezone.utc).isoformat(), 'llm_requests': 0}
    stop = threading.Event()

    def call(args: list[str], *, allowed: tuple[int, ...] = (0,)) -> str:
        started = time.perf_counter()
        result = subprocess.run(['docker', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
        report['commands'].append({'command': ['docker', *args], 'exit_code': result.returncode,
            'seconds': time.perf_counter()-started, 'stdout': result.stdout, 'stderr': result.stderr})
        assert result.returncode in allowed, (args, result.returncode, result.stderr[-1000:])
        return result.stdout

    def sample() -> None:
        while not stop.is_set():
            result = subprocess.run(['docker','stats',NAME,'--no-stream','--format','{{json .}}'], capture_output=True,
                                    text=True, encoding='utf-8', errors='replace')
            if result.returncode == 0 and result.stdout.strip():
                report['samples'].append({'time':datetime.now(timezone.utc).isoformat(), **json.loads(result.stdout)})
            stop.wait(3)

    def ready() -> None:
        started = time.perf_counter()
        first_http = None
        states = []
        for _ in range(90):
            try:
                with urlopen('http://127.0.0.1:18004/ready', timeout=2) as response:
                    if response.status == 200 and first_http is None:
                        first_http = time.perf_counter()-started
            except OSError:
                pass
            state = json.loads(call(['inspect',NAME]))[0]['State']
            health = state.get('Health', {}).get('Status')
            if not states or states[-1] != health:
                states.append(health)
            if health == 'healthy' and first_http is not None:
                report.setdefault('startup', []).append({'first_ready_seconds':first_http,
                    'docker_healthy_seconds':time.perf_counter()-started,'health_transitions':states})
                return
            time.sleep(1)
        raise AssertionError('Readiness / Docker health did not become ready')

    def probe(mode: str) -> None:
        call(['exec',NAME,'python','/validation/container/reliability_probe.py',mode])

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.perf_counter()
    try:
        state = json.loads(call(['inspect',NAME]))[0]
        assert state['Name'] == '/'+NAME
        assert any(m.get('Name') == NAME and m['Destination']=='/workspace' for m in state['Mounts'])
        report['image'] = json.loads(call(['image','inspect',state['Image']]))[0]
        ready()
        if stage in {'run','finish','verify_fixed'}:
            probe(stage)
        elif stage == 'restart':
            call(['stop','--time','15',NAME])
            stopped = json.loads(call(['inspect',NAME]))[0]['State']
            assert stopped['ExitCode']==0 and not stopped['OOMKilled']
            report['normal_stop']=stopped
            call(['start',NAME])
            ready()
            probe('restart')
        elif stage == 'interrupt_recover':
            marker = OUT/'kill-ready'
            assert not marker.exists(), 'Refuse to reuse an old kill marker'
            with (OUT/'interrupt-command.log').open('w', encoding='utf-8') as handle:
                command = ['docker','exec',NAME,'python','/validation/container/reliability_probe.py','interrupt']
                process = subprocess.Popen(command,stdout=handle,stderr=subprocess.STDOUT)
                for _ in range(180):
                    if marker.exists():
                        break
                    assert process.poll() is None, 'Interruption probe exited before marker'
                    time.sleep(1)
                else:
                    raise AssertionError('Real R interruption marker not observed')
                call(['kill',NAME])
                process.wait(timeout=30)
                assert process.returncode != 0
                report['commands'].append({'command':command,'exit_code':process.returncode})
            state = json.loads(call(['inspect',NAME]))[0]['State']
            assert state['ExitCode']==137 and not state['OOMKilled']
            report['controlled_kill']=state
            call(['start',NAME])
            ready()
            probe('recover')
        report['workspace_bytes']=int(call(['exec',NAME,'du','-sb','/workspace']).split()[0])
        report['status']='passed'
    except Exception as error:
        report.update(error_type=type(error).__name__,error=str(error)[:2000])
        LOGGER.error('%s failed: %s',stage,error)
    finally:
        stop.set()
        monitor.join(timeout=10)
        logs = call(['logs',NAME],allowed=(0,1))
        (OUT/('container-'+stage+'.log')).write_text(logs+report['commands'][-1]['stderr'],encoding='utf-8')
        report['seconds']=time.perf_counter()-started
        (OUT/('host-'+stage+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
