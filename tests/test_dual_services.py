"""Deployment specification tests, not claims about Docker runtime success."""
from pathlib import Path
import pytest
from container.dual_services import specification, state_label
from container.context_audit import inventory


@pytest.mark.parametrize('service,port',[('api',8000),('ui',8501)])
def test_shared_local_mount_and_image(service,port):
    command=specification(service,'demo','shared:image','demo-volume',None)
    assert f'127.0.0.1:{port}:{port}' in command
    assert 'type=volume,source=demo-volume,target=/workspace' in command
    assert 'shared:image' in command
    assert '--env-file' not in command
    assert not any('enableCORS=false' in v or 'enableXsrfProtection=false' in v for v in command)
    if service=='ui':
        assert 'app/main.py' in command and '--server.headless=true' in command
        assert '_stcore/health' in command[command.index('--health-cmd')+1]


def test_runtime_secret_file_is_not_build_argument(tmp_path):
    command=specification('api','demo','shared:image','volume',tmp_path/'private.env')
    assert command[command.index('--env-file')+1]==str((tmp_path/'private.env').resolve())
    assert '--build-arg' not in command


def test_context_contains_ui_but_not_workspace():
    root=Path(__file__).resolve().parents[1]
    paths={row['path'] for row in inventory(root)['files']}
    assert 'app/main.py' in paths
    assert not any(p.startswith(('workspace/','outputs/','.env')) for p in paths)


@pytest.mark.parametrize('state,expected', [
    (None, 'absent'),
    ({'State': {'Running': False, 'Status': 'exited'}}, 'exited'),
    ({'State': {'Running': True, 'Health': {'Status': 'starting'}}}, 'running / starting'),
    ({'State': {'Running': True, 'Health': {'Status': 'unhealthy'}}}, 'running / unhealthy'),
    ({'State': {'Running': True, 'Health': {'Status': 'healthy'}}}, 'running / healthy'),
])
def test_status_reports_observation_not_requested_action(state, expected):
    assert state_label(state) == expected
