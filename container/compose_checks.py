"""Pure checks for resolved Compose documents; never print environment values.

Runtime callers obtain the document with `docker compose config --format json`.
Default unit tests supply in-memory documents and never require Docker.
"""
from typing import Any


def validate_spec(document: dict[str, Any]) -> dict[str, Any]:
    services = document['services']
    assert set(services) == {'api', 'streamlit'}, 'Only the two application services are allowed'
    images, mounts = set(), []
    for name, service in services.items():
        images.add(service['image'])
        assert service['user'] == '10001:10001'
        assert not service.get('privileged') and not service.get('depends_on')
        assert service.get('network_mode') != 'host'
        assert service['restart'] == 'unless-stopped'
        assert service['init'] is True
        assert service['cap_drop'] == ['ALL']
        assert any(v in ('no-new-privileges:true', 'no-new-privileges') for v in service['security_opt'])
        assert len(service['ports']) == 1
        port = service['ports'][0]
        assert port['host_ip'] == '127.0.0.1'
        assert port['target'] == (8000 if name == 'api' else 8501)
        assert len(service['volumes']) == 1
        mount = service['volumes'][0]
        assert mount['target'] == '/workspace' and mount['type'] in ('volume', 'bind')
        if mount['type'] == 'bind':
            assert mount['bind']['create_host_path'] is False
        mounts.append((mount['type'], mount['source']))
        assert service['environment']['RNASEQ_WORKSPACE_ROOT'] == '/workspace'
        assert service['environment']['RNASEQ_RSCRIPT'] == 'Rscript'
        health = ' '.join(service['healthcheck']['test'])
        assert ('/health' if name == 'api' else '/_stcore/health') in health
        assert all(word not in health for word in ('DeepSeek', 'Rscript', 'registry.execute'))
        assert set(service['build'].get('args', {})) <= {'RNASEQ_CACHED_IMAGE'}, 'Secrets/config are not build arguments'
        assert service['logging']['driver'] == 'local'
        assert int(service['logging']['options']['max-file']) >= 2
    assert len(images) == 1, 'Both services must use the same image'
    assert mounts[0] == mounts[1], 'Both services must share exactly the same workspace'
    return {'services': sorted(services), 'image': next(iter(images)),
            'mount_type': mounts[0][0], 'status': 'PASS'}


def validate_runtime(container: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted output only: docker inspect contains runtime credentials."""
    config, host = container['Config'], container['HostConfig']
    assert config['User'] == '10001:10001' and not host['Privileged']
    assert host['CapDrop'] == ['ALL']
    assert any('no-new-privileges' in v for v in host['SecurityOpt'])
    assert len(container['Mounts']) == 1 and container['Mounts'][0]['Destination'] == '/workspace'
    assert all(binding['HostIp'] == '127.0.0.1'
               for bindings in (host['PortBindings'] or {}).values() for binding in bindings)
    return {'id': container['Id'], 'image': container['Image'], 'user': config['User'],
            'mount_type': container['Mounts'][0]['Type'],
            'status': container['State']['Status'],
            'health': container['State'].get('Health', {}).get('Status'),
            'restart_count': container['RestartCount']}
