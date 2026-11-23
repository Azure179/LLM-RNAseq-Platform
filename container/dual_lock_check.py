"""Opt-in real cross-container lock check on a named volume or new host directory."""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from dual_validation import command


def check(first: list[str], second: list[str]) -> dict:
    name='LockCheck_'+uuid4().hex[:8]
    prelude="from pathlib import Path; from bulk_rnaseq.workspace import ProjectManager, ProjectError; m=ProjectManager(Path('/workspace')); "
    command(first+['-c',prelude+f"m.create_project('{name}')"])
    # The holder stays alive until the parent sends a release signal. No timing race.
    source=prelude+"\nwith m._write_lock():\n print('LOCKED',flush=True)\n input()\n"
    holder=subprocess.Popen(first+['-u','-c',source],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        assert holder.stdout.readline().strip()=='LOCKED'
        probe=prelude+f"\ntry:\n m.update_project_metadata('{name}',{{'description':'blocked'}})\nexcept ProjectError as e:\n print(e.code)\nelse:\n raise AssertionError('Second container bypassed shared lock')"
        assert command(second+['-c',probe]).strip()=='WORKSPACE_BUSY'
    finally:
        stdout,stderr=holder.communicate('\n',timeout=30)
        assert holder.returncode==0,stderr
    command(second+['-c',prelude+f"m.update_project_metadata('{name}',{{'description':'after release'}})"])
    verify=prelude+f"import json; p=m.open_project('{name}'); assert p.metadata.description=='after release'; print(json.dumps({{'project':p.key,'description':p.metadata.description}}))"
    a=json.loads(command(first+['-c',verify])); b=json.loads(command(second+['-c',verify]))
    assert a==b
    return {'status':'PASS','blocked_code':'WORKSPACE_BUSY','after_release':a}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',default='rnaseq-phase95')
    parser.add_argument('--image',default='bulk-rnaseq:phase9-5')
    parser.add_argument('--bind-root',type=Path,required=True,help='Must be a new, disposable directory')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    # Refuse to use an existing directory, protecting private workspaces.
    args.bind_root.mkdir(parents=True,exist_ok=False)
    named=check(['docker','exec','-i',args.prefix+'-api','python'],
                ['docker','exec','-i',args.prefix+'-ui','python'])
    mount=f'type=bind,source={args.bind_root.resolve()},target=/workspace'
    base=['docker','run','--rm','-i','--network','none','--mount',mount,args.image,'python']
    bind=check(base,base)
    args.report.write_text(json.dumps({'named_volume':named,'host_bind':bind},indent=2),encoding='utf-8')
    print(json.dumps({'named_volume':named['status'],'host_bind':bind['status']}))


if __name__=='__main__':
    main()
