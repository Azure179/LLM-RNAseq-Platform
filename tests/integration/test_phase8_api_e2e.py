"""Opt-in API acceptance over private real inputs; no generated biological results.

R-only: pytest tests/integration/test_phase8_api_e2e.py --run-r
Full: add --run-llm. Missing local research inputs are explicitly skipped.
Every invocation has an isolated workspace under ignored outputs/api_e2e.
"""

from contextlib import contextmanager
import csv
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from time import perf_counter, sleep
from typing import Any, Iterator
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from bulk_rnaseq.api.main import create_app
from bulk_rnaseq.customization.filtering import read_annotations
from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.result_formatter import format_skill_result_for_llm
from bulk_rnaseq.result_schema import SkillResult
from bulk_rnaseq.run_io import sha256, write_json
from bulk_rnaseq.workflow.final_response import format_workflow_result_for_llm
from bulk_rnaseq.workflow.models import WorkflowPlan
from bulk_rnaseq.workspace import FileImportService, ProjectManager
from bulk_rnaseq.workspace.result_manager import ResultManager

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "Real_DATA_API_E2E"
pytestmark = pytest.mark.integration


def rows(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def annotation_mapping(source: Path, destination: Path) -> dict[str, str]:
    """Test input adapter: attach REAL descriptions using the prior explicit crosswalk.

    No term/ID inference and no statistical transformation. Keep all original KEGG
    associations/names, and combine the descriptions of mapped transcripts.
    """
    crosswalk = source / "asc_crosswalk/gene_id_mapping.tsv"
    eggnog = source / "Acanthopagrus_schlegelii.emapper.annotations.tsv"
    mapping = source / "prepared_asc/gene_to_kegg.tsv"
    identities = {row["query"]: row["gene_id"] for row in rows(crosswalk, "\t")}
    descriptions: dict[str, set[str]] = {}
    with eggnog.open(encoding="utf-8-sig", newline="") as handle:
        header = next(line for line in handle if line.startswith(("#query\t", "query\t")))
        for row in csv.DictReader(handle, delimiter="\t", fieldnames=header.lstrip("#").rstrip("\r\n").split("\t")):
            gene = identities.get(row["query"])
            if gene and row.get("Description") not in (None, "", "-"):
                descriptions.setdefault(gene, set()).add(row["Description"])
    with destination.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gene_id", "pathway_id", "pathway_name", "Description"], delimiter="\t")
        writer.writeheader()
        for row in rows(mapping, "\t"):
            writer.writerow({**row, "Description": "; ".join(sorted(descriptions.get(row["gene_id"], set())))})
    return {path.relative_to(source).as_posix(): sha256(path) for path in (crosswalk, eggnog, mapping)}


@pytest.fixture(scope="module")
def real_workspace(request: pytest.FixtureRequest) -> dict[str, Any]:
    source = ROOT / "Real_DATA_TEST/input"
    inputs = {"count_matrix": source / "prepared_asc/count_matrix.all_samples.csv",
              "metadata": source / "prepared_asc/metadata.all_samples.csv",
              "go_mapping": source / "prepared_asc/gene_to_go.tsv"}
    required = [*inputs.values(), source / "asc_crosswalk/gene_id_mapping.tsv",
                source / "Acanthopagrus_schlegelii.emapper.annotations.tsv",
                source / "prepared_asc/gene_to_kegg.tsv"]
    if not all(path.is_file() for path in required):
        pytest.skip("Private real-data inputs absent; see PHASE8_API_E2E_REPORT.md. No substitute results used.")
    destination = ROOT / "outputs/api_e2e" / uuid4().hex
    destination.mkdir(parents=True)
    manager = ProjectManager(destination / "workspace")
    manager.create_project(PROJECT, description="Phase 8.7 real API E2E; private research input")
    manager.create_project("Isolation")
    mapping = destination / "kegg_with_real_descriptions.tsv"
    hashes = annotation_mapping(source, mapping)
    inputs["kegg_mapping"] = mapping
    importer = FileImportService(manager)
    imports = {}
    for role, path in inputs.items():
        started = perf_counter()
        result = importer.import_file(PROJECT, path, role)
        assert result.status == "succeeded", result
        imports[role] = {"summary": result.summary, "seconds": perf_counter() - started, "sha256": sha256(path)}
    write_json(destination / "dataset.json", {"scope": "Real black sea bream dataset; private, not for Git",
        "crosswalk_basis": "Existing user-confirmed name crosswalk; no new ID inference", "source_hashes": hashes, "imports": imports})
    return {"manager": manager, "destination": destination,
        "rscript": request.config.getoption("--rscript"),
        "env_file": Path(request.config.getoption("--llm-env-file"))}


class Audit:
    def __init__(self, workspace: dict[str, Any], name: str) -> None:
        self.workspace = workspace
        self.path = workspace["destination"] / (name + ".json")
        self.data: dict[str, Any] = {"status": "failed", "http": [], "registry": [], "provider": [], "processes": []}

    def post(self, client: Any, endpoint: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
        started = perf_counter()
        response = client.post(endpoint, json=body)
        self.data["http"].append({"endpoint": endpoint, "request": body, "status": response.status_code,
            "seconds": perf_counter() - started, "body": response.json()})
        assert response.status_code == status, response.text
        assert str(self.workspace["manager"].root) not in response.text
        return response.json()

    @contextmanager
    def session(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
        started = perf_counter()
        workspace = self.workspace
        app = create_app(workspace_root=workspace["manager"].root, timeout=1800,
            rscript_executable=workspace["rscript"], llm_env_file=workspace["env_file"])
        real_request = DeepSeekClient._request
        real_process = subprocess.run
        def observed_process(command: Any, **kwargs: Any) -> Any:
            start = perf_counter()
            result = real_process(command, **kwargs)
            self.data["processes"].append({"program": Path(command[0]).name,
                "script": Path(command[1]).name if len(command) > 1 else None,
                "returncode": result.returncode, "seconds": perf_counter() - start,
                "stdout_chars": len(result.stdout or ""), "stderr_chars": len(result.stderr or "")})
            return result
        def observed_request(provider: DeepSeekClient, *args: Any, **kwargs: Any) -> Any:
            entry: dict[str, Any] = {"status": "requested", "user_message_chars": len(args[0]) if args else 0}
            self.data["provider"].append(entry)
            response, elapsed = real_request(provider, *args, **kwargs)
            entry.update(status="completed", seconds=elapsed, response=provider.sanitize_for_audit(response))
            return response, elapsed
        with monkeypatch.context() as patch:
            patch.setattr(DeepSeekClient, "_request", observed_request)
            patch.setattr(subprocess, "run", observed_process)
            try:
                with TestClient(app) as client:
                    execute = app.state.skill_registry.execute
                    def observe(name: str, inputs: dict[str, Any]) -> Any:
                        start = perf_counter()
                        entry: dict[str, Any] = {"skill": name}
                        self.data["registry"].append(entry)
                        if "gene_list" in inputs:
                            entry["input_gene_ids"] = [row["gene_id"] for row in rows(Path(inputs["gene_list"]))]
                        result = execute(name, inputs)  # REAL Registry and R, no substituted results.
                        entry.update(seconds=perf_counter() - start, run_id=result.run_id, status=result.status)
                        return result
                    patch.setattr(app.state.skill_registry, "execute", observe)
                    yield client
                self.data["status"] = "passed"
            finally:
                self.data["seconds"] = perf_counter() - started
                self.data["request_count"] = len(self.data["provider"])
                self.data["usage"] = {key: sum((entry.get("response", {}).get("usage") or {}).get(key, 0)
                    for entry in self.data["provider"]) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                write_json(self.path, self.data)


def run_skill(audit: Audit, client: Any, skill: str, parameters: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    body = {"project_id": PROJECT, "parameters": parameters}
    if source:
        body["source_run_id"] = source
    return audit.post(client, f"/skills/{skill}/run", body)


@pytest.fixture(scope="module")
def real_deg(real_workspace: dict[str, Any]) -> dict[str, Any]:
    audit = Audit(real_workspace, "source_deg")
    with pytest.MonkeyPatch.context() as patch, audit.session(patch) as client:
        result = run_skill(audit, client, "rnaseq.deg", {"treatment": "OT", "control": "T"})
        assert result["status"] == "succeeded", result
        assert [item["skill"] for item in audit.data["registry"]] == ["rnaseq.deg"]
        assert not audit.data["provider"]
    return result


def assert_up_genes(workspace: dict[str, Any], deg: dict[str, Any], calls: list[dict[str, Any]]) -> None:
    table = workspace["manager"].open_project(PROJECT).root / deg["outputs"]["filtered_csv"]
    expected = {row["gene_id"] for row in rows(table) if float(row["log2FoldChange"]) > 0}
    assert len(expected) == deg["summary"]["upregulated_genes"]
    assert calls and set(calls[-1]["input_gene_ids"]) == expected


def historical_plan(source: str, project: str = PROJECT) -> dict[str, Any]:
    def binding(output: str, **extras: Any) -> dict[str, Any]:
        return {"source": "project_result", "run_id": source, "output": output, **extras}
    return WorkflowPlan(project_id=project, user_request="Explicit existing DEG reuse integration test", steps=[
        {"step_id": "volcano", "skill_name": "rnaseq.volcano", "input_bindings": {"deg_result_file": binding("deg_csv")}},
        {"step_id": "kegg", "skill_name": "rnaseq.kegg", "input_bindings": {
            "gene_list": binding("filtered_csv", gene_subset="up"), "analysis_genes": binding("deg_csv")}}]).model_dump(mode="json")


def test_real_reuse_and_results(real_workspace, real_deg, monkeypatch):
    audit = Audit(real_workspace, "single_reuse")
    with audit.session(monkeypatch) as client:
        for endpoint in ("/health", "/skills", "/skills/rnaseq.deg", "/docs", "/openapi.json"):
            assert client.get(endpoint).status_code == 200
        volcano = run_skill(audit, client, "rnaseq.volcano", {}, real_deg["run_id"])
        kegg = run_skill(audit, client, "rnaseq.kegg", {"gene_subset": "up"}, real_deg["run_id"])
        assert [entry["skill"] for entry in audit.data["registry"]] == ["rnaseq.volcano", "rnaseq.kegg"]
        assert_up_genes(real_workspace, real_deg, audit.data["registry"])
        browser = ResultManager(real_workspace["manager"], PROJECT)
        assert len(browser.preview_table(real_deg["run_id"], "deg_csv").frame) == 100
        assert browser.download(real_deg["run_id"], "deg_csv")[1]
        with Image.open(BytesIO(browser.preview_image(volcano["run_id"], "plot"))) as image:
            image.verify()
        for result in (real_deg, volcano, kegg):
            assert result["status"] == "succeeded"
            detail = client.get(f"/projects/{PROJECT}/results/{result['run_id']}").json()
            assert detail["created_at"] and detail["completed_at"] and detail["workflow_id"]
            if result is not real_deg:
                assert detail["source_run_id"] == real_deg["run_id"]
                assert detail["comparison"] == "OT vs T"
            if result is kegg:
                assert detail["gene_subset"] == "up"
            assert client.get(f"/projects/Isolation/results/{result['run_id']}").status_code == 404
        assert not audit.data["provider"]


def test_real_historical_workflow(real_workspace, real_deg, monkeypatch):
    audit = Audit(real_workspace, "historical_workflow")
    with audit.session(monkeypatch) as client:
        result = audit.post(client, "/workflow/run", {"project_id": PROJECT, "plan": historical_plan(real_deg["run_id"])})
        assert result["status"] == "succeeded", result
        assert sorted(item["skill"] for item in audit.data["registry"]) == ["rnaseq.kegg", "rnaseq.volcano"]
        kegg_calls = [item for item in audit.data["registry"] if item["skill"] == "rnaseq.kegg"]
        assert_up_genes(real_workspace, real_deg, kegg_calls)
        assert all(record["source_run_id"] == real_deg["run_id"] for record in result["step_records"].values())
        before = len(audit.data["registry"])
        audit.post(client, "/workflow/run", {"project_id": "Isolation", "plan": historical_plan(real_deg["run_id"], "Isolation")}, 409)
        assert len(audit.data["registry"]) == before and not audit.data["provider"]


@pytest.mark.llm
def test_live_plan_and_real_workflow(real_workspace, real_deg, monkeypatch):
    audit = Audit(real_workspace, "planned_workflow")
    with audit.session(monkeypatch) as client:
        planned = audit.post(client, "/workflow/plan", {"project_id": PROJECT,
            "request": "以T为对照比较OT，画火山图，并对上调基因做KEGG。"})
        assert planned["status"] == "ready" and not audit.data["registry"], planned
        steps = {step["skill_name"]: step for step in planned["plan"]["steps"]}
        assert set(steps) == {"rnaseq.deg", "rnaseq.volcano", "rnaseq.kegg"}
        deg = steps["rnaseq.deg"]
        assert (deg["parameters"]["treatment"], deg["parameters"]["control"]) == ("OT", "T")
        for name in ("rnaseq.volcano", "rnaseq.kegg"):
            assert planned["dependencies"][steps[name]["step_id"]] == [deg["step_id"]]
        assert steps["rnaseq.kegg"]["input_bindings"]["gene_list"]["gene_subset"] == "up"
        calls = len(audit.data["provider"])
        result = audit.post(client, "/workflow/run", {"project_id": PROJECT, "plan": planned["plan"]})
        assert result["status"] == "succeeded", result
        assert len(audit.data["provider"]) == calls
        assert sorted(item["skill"] for item in audit.data["registry"]) == ["rnaseq.deg", "rnaseq.kegg", "rnaseq.volcano"]
        assert_up_genes(real_workspace, result["step_results"][deg["step_id"]],
            [entry for entry in audit.data["registry"] if entry["skill"] == "rnaseq.kegg"])
        assert result["step_records"][steps["rnaseq.kegg"]["step_id"]]["source_run_id"] == result["step_results"][deg["step_id"]]["run_id"]
        response = audit.post(client, "/skills/rnaseq.volcano/run", {"project_id": PROJECT, "parameters": {}}, 409)
        assert response["error"]["code"] == "AMBIGUOUS_UPSTREAM_RESULT"


@pytest.mark.llm
@pytest.mark.parametrize("text,kind,status", [
    ("以T为对照比较OT，画火山图，并对上调差异基因做KEGG富集。", "workflow", "success"),
    ("画一个PCA。", "single_skill", "success"),
    ("做差异表达并画火山图。", None, "needs_input"),
    ("做单细胞轨迹分析。", "unsupported", "unsupported"),
], ids=["workflow", "pca", "ambiguous", "unsupported"])
def test_live_assistant_closure(real_workspace, real_deg, monkeypatch, text, kind, status, request):
    audit = Audit(real_workspace, "assistant_" + request.node.callspec.id)
    with audit.session(monkeypatch) as client:
        body = {"project_id": PROJECT, "request": text, "request_id": str(uuid4())}
        answer = audit.post(client, "/assistant/analyze", body)
        assert answer["status"] == status, answer
        if kind:
            assert answer["request_type"] == kind, answer
        if status == "success":
            assert answer["final_response_status"] == "succeeded", answer
            expected = ["rnaseq.deg", "rnaseq.kegg", "rnaseq.volcano"] if kind == "workflow" else ["rnaseq.pca"]
            assert sorted(item["skill"] for item in audit.data["registry"]) == expected
            # Production grounding verifies exact facts/status/warnings, not an arbitrary substring assertion.
            assert answer["response"] and answer["artifacts"]
            project = real_workspace["manager"].open_project(PROJECT)
            if kind == "workflow":
                canonical = json.loads((project.root / "results/workflows" / answer["workflow_id"] / "workflow_result.json").read_text(encoding="utf-8"))
                formatted = format_workflow_result_for_llm(canonical)
                assert all(fact in answer["response"] for fact in formatted.facts)
                assert all(notice in answer["response"] for notice in formatted.notices)
            else:
                browser = ResultManager(real_workspace["manager"], PROJECT)
                detail = client.get(f"/projects/{PROJECT}/results/{answer['run_id']}")
                assert detail.status_code == 200 and detail.json()["workflow_id"], detail.text
                raw = browser.read_artifact(answer["run_id"], "summary_json")[1]
                formatted = format_skill_result_for_llm(SkillResult.from_dict(json.loads(raw)))
                assert all(fact in answer["response"] for fact in formatted.facts)
        else:
            assert not audit.data["registry"]
            if status == "needs_input":
                assert {"treatment", "control"} <= set(answer["required_fields"])
        before = (len(audit.data["provider"]), len(audit.data["registry"]))
        assert audit.post(client, "/assistant/analyze", body) == answer
        assert (len(audit.data["provider"]), len(audit.data["registry"])) == before


@pytest.mark.llm
@pytest.mark.parametrize("filtering", [False, True], ids=["volcano", "annotation"])
def test_live_customization(real_workspace, real_deg, monkeypatch, filtering):
    audit = Audit(real_workspace, "customization_annotation" if filtering else "customization_volcano")
    with audit.session(monkeypatch) as client:
        source = real_deg if filtering else run_skill(audit, client, "rnaseq.volcano", {}, real_deg["run_id"])
        project = real_workspace["manager"].open_project(PROJECT)
        before = {path: sha256(project.root / path) for path in source["outputs"].values()}
        text = "筛选OT vs T中与性别分化相关的上调DEG。" if filtering else "把刚才火山图上调改红色，下调蓝色，标Top20。"
        proposal = audit.post(client, "/customizations/plan", {"project_id": PROJECT,
            "source_run_id": source["run_id"], "request": text})
        assert proposal["status"] == "ready", proposal
        decision = proposal["plan"]["decision"]
        assert decision["mode"] == ("annotation_filter" if filtering else "parameter_override")
        if filtering:
            assert decision["parameters"]["gene_subset"] == "up"
        else:
            assert decision["parameters"]["top_n_labels"] == 20
            assert decision["parameters"]["color_up"].lower() in {"red", "#ff0000"}
            assert decision["parameters"]["color_down"].lower() in {"blue", "#0000ff"}
        count = len(audit.data["provider"])
        result = audit.post(client, "/customizations/run", {"project_id": PROJECT, "plan": proposal["plan"]})
        assert result["status"] == "succeeded", result
        assert result["source_run_id"] == source["run_id"] and result["customization_id"]
        assert result["result"]["customization_id"] == result["customization_id"]
        assert len(audit.data["provider"]) == count
        assert all(item["skill"] != "rnaseq.deg" for item in audit.data["registry"])
        assert all(item["script"] == "plot_volcano.R" for item in audit.data["processes"])
        assert before == {path: sha256(project.root / path) for path in before}
        if filtering:
            # Empty literal matches are valid; never add guessed genes to make this pass.
            browser = ResultManager(real_workspace["manager"], PROJECT)
            artifact = next(a for a in result["result"]["artifacts"] if a["filename"] == "filtered_deg.csv")
            selected = rows(browser.validate_result_path(project.root / artifact["relative_ref"]))
            original = {row["gene_id"]: row for row in rows(project.root / real_deg["outputs"]["filtered_csv"])}
            assert len(selected) == result["result"]["summary"]["n_matched_genes"]
            assert all(row == original[row["gene_id"]] and float(row["log2FoldChange"]) > 0 for row in selected)
            options = decision["parameters"]
            context = real_workspace["manager"].get_project_context(PROJECT)
            annotations = read_annotations(context.kegg_mapping)
            for gene, evidence in read_annotations(context.go_mapping).items():
                annotations.setdefault(gene, []).extend(evidence)
            expected = set()
            for gene, row in original.items():
                if float(row["log2FoldChange"]) <= 0:
                    continue
                evidence = [text.casefold() for category, text in annotations.get(gene, [])
                            if category in options["annotation_sources"]]
                hits = [any(word.casefold() in text for text in evidence) for word in options["keywords"]]
                if (all(hits) if options.get("match_mode", "any") == "all" else any(hits)):
                    expected.add(gene)
            assert {row["gene_id"] for row in selected} == expected
        assert client.get(f"/projects/{PROJECT}/results/{result['result']['run_id']}").status_code == 200


@pytest.mark.llm  # Network opt-in here permits loopback only; this test never invokes DeepSeek.
def test_actual_process_restart(real_workspace, real_deg):
    """Two distinct uvicorn processes, not merely two TestClient instances."""
    directory = real_workspace["destination"]
    environment = {**os.environ, "RNASEQ_WORKSPACE_ROOT": str(real_workspace["manager"].root)}
    record: dict[str, Any] = {"status": "failed", "pids": [], "results": [], "request_count": 0}
    try:
        for index in range(2):
            with socket.socket() as lease:
                lease.bind(("127.0.0.1", 0))
                port = lease.getsockname()[1]
            command = [sys.executable, "-m", "uvicorn", "bulk_rnaseq.api.main:app", "--host", "127.0.0.1",
                "--port", str(port), "--no-access-log"]
            with (directory / f"server_{index}.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                record["pids"].append(process.pid)
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1800, trust_env=False) as client:
                        for _ in range(120):
                            assert process.poll() is None, "API process exited; see isolated server log"
                            try:
                                if client.get("/health").status_code == 200:
                                    break
                            except httpx.TransportError:
                                pass
                            sleep(.25)
                        else:
                            pytest.fail("API startup timeout")
                        response = client.get(f"/projects/{PROJECT}/results")
                        assert response.status_code == 200
                        record["results"].append(response.json())
                        assert any(row["run_id"] == real_deg["run_id"] for row in response.json()["results"])
                        if index:
                            assert record["results"][0] == record["results"][1]
                            reused = client.post("/skills/rnaseq.volcano/run", json={"project_id": PROJECT,
                                "source_run_id": real_deg["run_id"], "parameters": {}})
                            record["reuse"] = reused.json()
                            assert reused.status_code == 200 and reused.json()["metadata"]["source_run_id"] == real_deg["run_id"]
                            after = client.get(f"/projects/{PROJECT}/results").json()
                            assert sum(r["skill_name"] == "rnaseq.deg" for r in after["results"]) == sum(
                                r["skill_name"] == "rnaseq.deg" for r in response.json()["results"])
                finally:
                    process.terminate()
                    process.wait(timeout=30)
        assert record["pids"][0] != record["pids"][1]
        record["status"] = "passed"
    finally:
        write_json(directory / "process_restart.json", record)
