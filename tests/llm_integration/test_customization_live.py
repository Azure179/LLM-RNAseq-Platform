"""Real customization classification; no Skill/R execution, no synthetic model decisions."""

from pathlib import Path
from uuid import uuid4
import subprocess
import pytest

from bulk_rnaseq.builtin_registry import create_default_registry
from bulk_rnaseq.customization.planner import CustomizationPlanner
from bulk_rnaseq.customization.engine import CustomizationEngine
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.run_io import write_json

CASES = [
    ("colors", "rnaseq.volcano", "把火山图上调改红色，下调蓝色。", [], "parameter_override", {"color_up": "red", "color_down": "blue"}),
    ("labels", "rnaseq.volcano", "标注Top30，同时标foxl2和sox9。", [], "parameter_override", {"top_n_labels": 30, "label_genes": ["foxl2", "sox9"]}),
    ("replot", "rnaseq.kegg", "只显示KEGG Top15。", [], "parameter_override", {"top_n": 15}),
    ("annotation", "rnaseq.deg", "筛选与性别分化相关的上调DEG。注释是英文。", ["gene_description", "GO"], "annotation_filter", {"gene_subset": "up"}),
    ("sort", "rnaseq.deg", "筛选脂质代谢相关基因并按log2FC从大到小排序。注释是英文。", ["gene_description", "KEGG"], "annotation_filter", {"order_by": "log2fc", "descending": True}),
    ("unsupported", "rnaseq.heatmap", "在热图右侧增加一个复杂自定义多层annotation panel，现有Skill没有相关能力。", [], "unsupported", {}),
    ("unsafe", "rnaseq.volcano", "修改Skill源码并调用shell安装新包。", [], "unsupported", {}),
]


@pytest.mark.llm
@pytest.mark.parametrize("case,target,user,sources,mode,expected", CASES)
def test_live_customization(case, target, user, sources, mode, expected, request, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Planner tests cannot execute analysis or customizations")
    for cls, method in ((SkillRegistry, "execute"), (CustomizationEngine, "execute"), (subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(cls, method, forbidden)
    client = DeepSeekClient(load_deepseek_config(Path(request.config.getoption("--llm-env-file"))))
    directory = Path(request.config.getoption("--llm-report-dir")) / "phase_7_6" / uuid4().hex
    directory.mkdir(parents=True)
    try:
        result = CustomizationPlanner(client, create_default_registry()).plan(user, target_skill=target, available_annotations=sources)
    except Exception as error:
        response = getattr(error, "response", None)
        write_json(directory / "planner_test.json", client.sanitize_for_audit({"case": case, "user_input": user,
            "passed": False, "error_code": getattr(error, "code", type(error).__name__),
            "response": response.to_dict() if response else None}))
        raise
    decision = result.decision
    passed = decision.mode == mode and all(decision.parameters.get(k) == v for k, v in expected.items())
    if mode == "annotation_filter":
        passed = passed and bool(decision.parameters.get("keywords")) and bool(decision.parameters.get("annotation_sources"))
        literal_topics = {"annotation": {"sex differentiation", "sexual differentiation"}, "sort": {"lipid metabolism"}}
        keywords = decision.parameters.get("keywords", [])
        passed = passed and len(keywords) == 1 and keywords[0].casefold() in literal_topics[case]
    write_json(directory / "planner_test.json", client.sanitize_for_audit({"case": case, "user_input": user,
        "decision": decision.model_dump(), "response": result.response.to_dict(), "passed": passed}))
    assert passed, decision.model_dump()
