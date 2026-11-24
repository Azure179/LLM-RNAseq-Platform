"""Local Docker CLI lifecycle: one image, two independent entry points, one volume.

Never deletes volumes or adopts containers created outside this script.
"""

import argparse
import json
import logging
from pathlib import Path
import re
import subprocess

LABEL = 'org.rnaseq.dual-workspace'
LOGGER = logging.getLogger('rnaseq.dual')


def state_label(state: dict | None) -> str:
    """Report observed container state, never treat a start request as readiness."""
    if state is None:
        return 'absent'
    observed = state['State']
    if not observed['Running']:
        return observed.get('Status', 'stopped')
    return 'running / ' + observed.get('Health', {}).get('Status', 'health unavailable')


def docker(*args: str, missing_ok: bool = False) -> str:
    result = subprocess.run(['docker', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        if missing_ok and 'No such' in result.stderr:
            return ''
        # Command/env-file contents are deliberately not echoed.
        raise RuntimeError(f'Docker {args[0]} failed (exit {result.returncode}); check Docker and resource configuration.')
    return result.stdout


def specification(service: str, prefix: str, image: str, volume: str, env_file: Path | None) -> list[str]:
    port = 8000 if service == 'api' else 8501
    args = ['run','-d','--name',prefix+'-'+service,'--label',LABEL+'='+prefix,
            '-p',f'127.0.0.1:{port}:{port}','--mount',f'type=volume,source={volume},target=/workspace',
            '-e','RNASEQ_WORKSPACE_ROOT=/workspace','-e','RNASEQ_RSCRIPT=Rscript']
    if env_file:
        args += ['--env-file', str(env_file.resolve())]
    if service == 'ui':
        args += ['--health-cmd',"python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).close()\"",
                 '-e','STREAMLIT_BROWSER_GATHER_USAGE_STATS=false']
    args += [image]
    if service == 'ui':
        args += ['python','-m','streamlit','run','app/main.py','--server.address=0.0.0.0',
                 '--server.port=8501','--server.headless=true']
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start','stop','status'])
    parser.add_argument('--image', default='bulk-rnaseq:phase9-5')
    parser.add_argument('--prefix', default='rnaseq-phase95')
    parser.add_argument('--volume', default='rnaseq-phase95-workspace')
    parser.add_argument('--env-file', type=Path, help='Optional runtime configuration; never a build argument')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value) for value in (args.prefix,args.volume)):
        parser.error('Prefix and volume must be portable Docker names')
    if args.env_file and not args.env_file.is_file():
        parser.error('Runtime env file does not exist')
    try:
        image_id = json.loads(docker('image','inspect',args.image))[0]['Id'] if args.action=='start' else None
        # Preflight both names before changing anything; never commandeer another container.
        states = {}
        for service in ('api','ui'):
            raw = docker('container','inspect',args.prefix+'-'+service,missing_ok=True)
            state = json.loads(raw)[0] if raw else None
            if state:
                if state['Config'].get('Labels',{}).get(LABEL)!=args.prefix:
                    raise RuntimeError('Container name belongs to an unmanaged resource; choose another prefix.')
                if args.action=='start' and (state['Image']!=image_id or not any(
                        m.get('Name')==args.volume and m['Destination']=='/workspace' for m in state['Mounts'])):
                    raise RuntimeError('Existing deployment differs from requested image/volume; stop and inspect it explicitly.')
            states[service]=state
        if args.action=='start':
            docker('volume','create',args.volume)
        for service,state in states.items():
            name=args.prefix+'-'+service
            if args.action=='start':
                if state is None:
                    docker(*specification(service,args.prefix,args.image,args.volume,args.env_file))
                elif not state['State']['Running']:
                    docker('start',name)
            elif args.action=='stop' and state and state['State']['Running']:
                docker('stop','--time','30',name)
            if args.action != 'status':
                raw = docker('container','inspect',name,missing_ok=True)
                state = json.loads(raw)[0] if raw else None
            LOGGER.info('%s: %s',name,state_label(state))
        if args.action in {'start','status'}:
            LOGGER.info('API http://127.0.0.1:8000 | UI http://127.0.0.1:8501')
        return 0
    except (RuntimeError,OSError,ValueError) as error:
        LOGGER.error('%s',error)
        return 1


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
