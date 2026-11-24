"""Read the synthetic project. CI only compares with the reviewed, versioned reference."""
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from bulk_rnaseq.result_schema import SkillResult

FILES = ("counts.csv", "metadata.csv", "go.tsv", "kegg.tsv")


def read(path):
    return json.loads(path.read_bytes())


def numeric_rows(path, key, columns):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    values = {row[key]: {c: float(row[c]) if row[c] else None for c in columns} for row in rows}
    assert len(rows) == len(values), f"Duplicate ID in {path.name}"
    return values


def observations(root: Path, inputs: Path) -> dict:
    report = read(root / "reports/container_smoke.json")
    assert report["status"] == "passed" and report["workflow"]["status"] == "succeeded"
    workflow = report["workflow"]["step_results"]
    steps = [row["result"] for row in report["steps"]]
    env = None
    for step in [*workflow.values(), *steps]:
        assert SkillResult.from_dict(step).status == "succeeded"
        for relative in step["outputs"].values():
            p = (root / relative).resolve()
            assert p.is_relative_to(root) and p.is_file()
            if p.suffix == ".png":
                with p.open("rb") as handle:
                    assert handle.read(8) == b"\x89PNG\r\n\x1a\n"
        prov = read(root / step["outputs"]["summary_json"])["metadata"]["provenance"]
        if env is None:
            env = prov["environment"]
        assert prov["environment"] == env
        if step["skill_name"] not in {"rnaseq.pca", "rnaseq.deg"}:
            assert report["snapshot"][step["run_id"]]["parents"] == [report["deg_run_id"]]
    deg, pca = workflow["deg"], steps[0]
    result = {
        "input_sha256": {name: hashlib.sha256((inputs / name).read_bytes()).hexdigest() for name in FILES},
        "environment": env, "comparison": deg["inputs"]["parameters"],
        "deg_counts": deg["summary"], "volcano": workflow["volcano"]["summary"],
        "heatmap": workflow["heatmap"]["summary"], "pca_summary": pca["summary"],
        "deg_table": numeric_rows(root / deg["outputs"]["deg_csv"], "gene_id", ("log2FoldChange","pvalue","padj")),
        "coordinates": numeric_rows(root / pca["outputs"]["coordinates"], "sample", ("PC1","PC2")),
        "enrichment": {},
    }
    for step, check in zip(steps[1:], report["enrichment_checks"], strict=True):
        kind = step["skill_name"].split(".")[-1]
        assert check["skill"] == step["skill_name"] and check["source_run_id"] == deg["run_id"]
        assert all(check[k] for k in ("subset_verified", "background_verified", "term_names_verified"))
        prov = read(root / step["outputs"]["summary_json"])["metadata"]["provenance"]
        assert (prov["engine"], prov["function"], prov["pAdjustMethod"]) == ("clusterProfiler", "enricher", "BH")
        table = root / step["outputs"]["all_results"]
        # Independent BH check on the actual positive-overlap family.
        subprocess.run(["Rscript", "--vanilla", "-e",
                        't<-read.csv(commandArgs(TRUE)[1]);stopifnot(isTRUE(all.equal(t$padj,'
                        'p.adjust(t$pvalue,"BH"),tolerance=1e-10)))', str(table)], check=True)
        result["enrichment"][f"{kind}_{check['subset']}"] = {
            "metrics": step["summary"], "terms": numeric_rows(table, "go_id" if kind == "go" else "pathway_id",
                                                            ("overlap_count","pvalue","padj"))}
    assert set(result["enrichment"]) == {f"{k}_{s}" for k in ("go","kegg") for s in ("all","up","down")}
    return result


def equal(actual, expected, field=""):
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), f"Keys changed at {field}"
        for key, value in expected.items():
            equal(actual[key], value, key)
    elif isinstance(expected, float):
        assert math.isclose(actual, expected, rel_tol=1e-8, abs_tol=0 if field in {"pvalue","padj"} else 1e-10), field
    else:
        assert actual == expected, field


def verify(project: Path, fixture: Path, baseline: Path) -> None:
    expected = read(baseline)
    actual = observations(project.resolve(), fixture)
    for pc in ("PC1", "PC2"):
        if sum(row[pc] * expected["coordinates"][sample][pc] for sample, row in actual["coordinates"].items()) < 0:
            for row in actual["coordinates"].values():
                row[pc] *= -1
    equal(actual, expected)
    sys.path.insert(0, "/opt/rnaseq/container")
    from verify_runtime import verify as inventory
    equal(inventory(Path("/opt/rnaseq")), expected["environment"])


if __name__ == "__main__":
    verify(*map(Path, sys.argv[1:]))

