"""Synchronized Windows/spawn and POSIX index/lock regressions."""
import hashlib
import json
import multiprocessing
from pathlib import Path
from time import monotonic
from unittest.mock import patch

import pytest

from bulk_rnaseq.workspace import ProjectError, ProjectManager
from bulk_rnaseq.workspace import manager as locks
from bulk_rnaseq.workspace.result_manager import ResultManager
from bulk_rnaseq.workspace.result_resolver import ResultResolver
from test_workflow_executor import host, fake_dispatch, deg, validate, executor


def query(root, project, source, barrier, results):
    manager = ProjectManager(root)
    scan = ResultManager._scan_refs
    save = ResultManager._save_ref
    scans = writes = 0

    def synchronized(browser):
        nonlocal scans
        answer = scan(browser)
        scans += 1
        if scans == 1:
            barrier.wait(15)
        return answer

    def record(browser, ref):
        nonlocal writes
        writes += 1
        save(browser, ref)

    with patch.object(ResultManager, "_scan_refs", synchronized), patch.object(ResultManager, "_save_ref", record):
        found = ResultResolver(manager, project).find_compatible_result(skill_name="rnaseq.deg", run_id=source)
        results.put((found.status, writes))


@pytest.mark.parametrize("mode", ["read", "repair", "other_project"])
def test_simultaneous_queries(host, fake_dispatch, mode):
    manager, _ = host
    workflow = executor(host).execute(validate(host, deg()))
    source = workflow.step_results["deg"]["run_id"]
    browser = ResultManager(manager, "demo")
    browser.list_result_refs()
    path = browser.root / "result_index" / (hashlib.sha256(source.encode()).hexdigest() + ".json")
    original = json.loads(path.read_bytes())
    manager.create_project("Other")
    if mode == "repair":
        path.unlink()
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    projects = ["demo", "Other" if mode == "other_project" else "demo"]
    workers = [ctx.Process(target=query, args=(manager.root, project, source, barrier, results)) for project in projects]
    from contextlib import nullcontext
    # Valid reads must even succeed while a different process owns the writer lock.
    with manager._write_lock() if mode == "read" else nullcontext():
        for w in workers:
            w.start()
        for w in workers:
            w.join(30)
            assert w.exitcode == 0
    observations = [results.get(timeout=2) for _ in workers]
    assert sorted(s for s, _ in observations) == (["not_found", "resolved"] if mode == "other_project" else ["resolved"] * 2)
    assert sum(n for _, n in observations) == int(mode == "repair")
    assert json.loads(path.read_bytes()) == original


def hold(root, ready, release):
    with ProjectManager(root)._write_lock():
        ready.set()
        assert release.wait(15)


def test_wait_after_confirmed_collision(tmp_path, monkeypatch):
    manager = ProjectManager(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=hold, args=(tmp_path, ready, release))
    process.start()
    try:
        assert ready.wait(10)
        sleep = locks.sleep
        def on_collision(seconds):
            release.set()
            sleep(seconds)
        monkeypatch.setattr(locks, "sleep", on_collision)
        with manager._write_lock():
            assert release.is_set()
    finally:
        release.set()
        process.join(10)
    assert process.exitcode == 0


def test_orphan_timeout_preserves_owner(tmp_path, monkeypatch):
    manager = ProjectManager(tmp_path)
    path = tmp_path / ".workspace.lock"
    path.write_text("previous owner")
    monkeypatch.setattr(locks, "LOCK_WAIT_SECONDS", 0.25)
    started = monotonic()
    with pytest.raises(ProjectError) as error, manager._write_lock():
        pytest.fail("orphan lock was stolen")
    assert error.value.code == "WORKSPACE_BUSY"
    assert 0.24 <= monotonic() - started < 2
    assert path.read_text() == "previous owner"

def test_invalid_index_is_preserved(host, fake_dispatch):
    manager, _ = host
    source = executor(host).execute(validate(host, deg())).step_results["deg"]["run_id"]
    browser = ResultManager(manager, "demo")
    browser.list_result_refs()
    path = browser.root / "result_index" / (hashlib.sha256(source.encode()).hexdigest() + ".json")
    path.write_bytes(b'{"incomplete":')
    with pytest.raises(ProjectError) as error:
        browser.list_result_refs()
    assert error.value.code == "RESULT_JSON_INVALID"
    assert path.read_bytes() == b'{"incomplete":'
