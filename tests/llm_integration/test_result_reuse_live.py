"""Explicit paid semantic routing checks; never execute R or synthesize model decisions."""

from pathlib import Path
from uuid import uuid4
import pytest
from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.workspace.chat_router import ChatRouter
from bulk_rnaseq.run_io import write_json

pytestmark = pytest.mark.llm


@pytest.mark.parametrize("user,skill,selection,treatment,control,subset", [
    ("把刚才的DEG画成火山图，不要重新运行差异分析。", "rnaseq.volcano", "recent", None, None, "all"),
    ("用最新的OT vs T DEG对上调基因做KEGG。", "rnaseq.kegg", "latest", "OT", "T", "up"),
    ("用T vs OT的已有DEG对下调基因做GO。", "rnaseq.go", "unspecified", "T", "OT", "down"),
    ("用已有OT vs T差异结果绘制火山图。", "rnaseq.volcano", "unspecified", "OT", "T", "all"),
])
def test_reuse_semantics(user, skill, selection, treatment, control, subset, request, monkeypatch):
    registry = create_default_registry()
    monkeypatch.setattr(registry, "execute", lambda *a, **kw: pytest.fail("Routing must never execute"))
    client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
    route, response = ChatRouter(client, registry).route(user)
    expected = dict(request_type="result_reuse", target_skill=skill, source_reference=selection,
                    treatment=treatment, control=control, gene_subset=subset)
    passed = all(getattr(route, key) == value for key, value in expected.items())
    folder = Path(request.config.getoption("--llm-report-dir")) / "phase_7_8_1" / uuid4().hex
    folder.mkdir(parents=True)
    write_json(folder / "route_test.json", client.sanitize_for_audit({"user": user, "expected": expected,
        "route": route.model_dump(), "response": response.to_dict(), "passed": passed}))
    assert passed, route
