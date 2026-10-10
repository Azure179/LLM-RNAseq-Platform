"""Opt-in real API/R through Streamlit AppTest; demo inputs are explicitly synthetic."""

import json
from pathlib import Path
from uuid import uuid4
import pytest
from streamlit.testing.v1 import AppTest
from app.state import STATE_KEY, set_current_project
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.run_io import write_json, sha256
from bulk_rnaseq.workspace import ProjectManager, FileImportService
from bulk_rnaseq.workspace.chat_controller import ChatController
from bulk_rnaseq.workspace.chat_router import ChatRouter
from bulk_rnaseq.workspace.result_manager import ResultManager

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.llm
CASES = [
    ("以T为对照比较OT，画火山图，并对上调基因做KEGG。", "workflow", None),
    ("画PCA，同时以T为对照比较OT。", "workflow", None),
    ("把刚才火山图改成红蓝配色，标Top20。", "customization", "rnaseq.volcano"),
    ("筛选性别分化相关的上调DEG。", "customization", "rnaseq.deg"),
    ("帮我画PCA。", "single_skill", None),
    ("帮我做单细胞Seurat聚类。", "unsupported", None),
]


@pytest.mark.parametrize("user,kind,target", CASES)
def test_live_chat_route(user, kind, target, request, monkeypatch):
    registry = create_default_registry()
    def forbidden(*args, **kwargs):
        pytest.fail("Routing must not execute Skills")
    monkeypatch.setattr(registry, "execute", forbidden)
    client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
    directory = Path(request.config.getoption("--llm-report-dir")) / "phase_7_7" / uuid4().hex
    directory.mkdir(parents=True)
    route, response = ChatRouter(client, registry).route(user)
    passed = route.request_type == kind and (target is None or route.target_skill == target)
    write_json(directory / "route_test.json", client.sanitize_for_audit({"user": user, "route": route.model_dump(),
        "response": response.to_dict(), "passed": passed}))
    assert passed, route


@pytest.mark.integration
def test_real_streamlit_workflow_customization(request, monkeypatch):
    directory = (Path(request.config.getoption("--llm-report-dir")) / "phase_7_7" / uuid4().hex).resolve()
    manager = ProjectManager(directory / "workspace")
    manager.create_project("UI_Workflow_Demo")
    service = FileImportService(manager)
    for role, filename in (("count_matrix", "counts.csv"), ("metadata", "metadata.csv")):
        assert service.import_file("UI_Workflow_Demo", ROOT / "examples/data" / filename, role).status == "succeeded"
    # Synthetic annotation fixture, not a claim about real gene functions.
    annotation = directory / "synthetic_eggnog.tsv"
    annotation.write_text("#query\tDescription\tGOs\tKEGG_Pathway\n" + "".join(
        f"gene_{i:03d}\tsynthetic fixture sex differentiation\tGO:0008150\tmap01100\n" for i in range(1, 31)), encoding="utf-8")
    assert service.import_eggnog("UI_Workflow_Demo", annotation).status == "succeeded"
    registry = create_default_registry()
    client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
    api, calls, results = [], [], []
    real_chat, real_execute = client.chat, registry.execute
    def observe_chat(*args, **kwargs):
        response = real_chat(*args, **kwargs)
        api.append(response.to_dict())
        return response
    def observe_execute(name, inputs):
        calls.append(name)
        return real_execute(name, inputs)
    monkeypatch.setattr(client, "chat", observe_chat)
    monkeypatch.setattr(registry, "execute", observe_execute)
    controller = ChatController(manager, Path("unused"), runner_factory=lambda: SingleRequestRunner(client, registry))
    monkeypatch.setattr("app.views.assistant.ChatController", lambda *args: controller)
    monkeypatch.setenv("RNASEQ_WORKSPACE_ROOT", str(manager.root))
    users = ["以control为对照比较treatment，画火山图，并对上调基因做KEGG。",
             "把刚才火山图上调改成红色，下调蓝色，标Top20。",
             "筛选注释中明确包含英文短语 sex differentiation 的上调DEG。"]
    passed = False
    try:
        app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=300).run()
        set_current_project(app.session_state[STATE_KEY], service, "UI_Workflow_Demo")
        app.run()
        for user in users:
            before = len(api)
            app.chat_input[0].set_value(user).run(timeout=300)
            assert not app.exception
            result = app.session_state[STATE_KEY].chat_messages[-1]["result"]
            results.append(result)
            assert result["status"] == "succeeded", result
            assert len(api) > before
            count = len(api), len(calls)
            app.run(timeout=300)
            assert count == (len(api), len(calls))
        assert results[0]["final_response_status"] == "succeeded"
        assert calls.count("rnaseq.deg") == 1 and calls.count("rnaseq.kegg") == 1 and calls.count("rnaseq.volcano") == 2
        browser = ResultManager(manager, "UI_Workflow_Demo")
        assert len(browser.list_collections("Workflow")) == 1 and len(browser.list_collections("Customization")) == 2
        for result in results:
            for artifact in result["artifacts"]:
                assert (manager.root / "UI_Workflow_Demo" / artifact["path"]).is_file()
        restarted = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=300).run()
        set_current_project(restarted.session_state[STATE_KEY], service, "UI_Workflow_Demo")
        restarted.run()
        assert len(restarted.chat_message) == 6
        next(b for b in restarted.button if b.label == "View Results").click().run()
        assert not restarted.exception and restarted.session_state[STATE_KEY].selected_tab == "Results"
        passed = True
    finally:
        usage = {k: sum((r.get("usage") or {}).get(k, 0) for r in api) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        write_json(directory / "ui_e2e_test.json", client.sanitize_for_audit({"surface": "Streamlit AppTest, not graphical browser",
            "data": "30-gene synthetic demo; genuine DESeq2/KEGG/R rendering; synthetic annotations",
            "passed": passed, "skill_calls": calls, "chat_results": results, "api_responses": api,
            "request_count": len(api), "usage": usage}))
