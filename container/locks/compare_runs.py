"""Compare two successful smoke projects mounted read-only. Prints aggregate evidence only."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_in(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing or out-of-project comparison artifact")
    return path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def table(root: Path, step: dict, output: str, key: str) -> pd.DataFrame:
    path = file_in(root, step["outputs"][output])
    frame = pd.read_csv(path, dtype={key: str}, keep_default_na=False).set_index(key)
    if not frame.index.is_unique:
        raise ValueError(f"Duplicate {key} in {output}")
    return frame.sort_index()


def compare_columns(frames, columns, *, signed=False):
    left, right = frames
    pd.testing.assert_index_equal(left.index, right.index)
    for col in columns:
        a, b = (pd.to_numeric(frame[col].replace("", np.nan)).to_numpy(dtype=float)
                for frame in frames)
        atol = 0.0 if col in {"pvalue", "padj"} else 1e-10
        if signed and np.dot(a, b) < 0:
            b = -b
        np.testing.assert_allclose(a, b, rtol=1e-8, atol=atol, equal_nan=True, err_msg=col)


def compare(old_root: Path, new_root: Path) -> dict:
    roots = (old_root, new_root)
    reports = [document(root / "reports/container_smoke.json") for root in roots]
    for report in reports:
        assert report["status"] == "passed" and report["workflow"]["status"] == "succeeded"
    inputs = [document(root / "project.json")["files"] for root in roots]
    for role in ("count_matrix", "metadata", "go_mapping", "kegg_mapping"):
        assert len({digest(file_in(root, files[role])) for root, files in zip(roots, inputs)}) == 1, role

    runs = [list(r["workflow"]["step_results"].values()) + [s["result"] for s in r["steps"]]
            for r in reports]
    assert len(runs[0]) == len(runs[1])
    tested = []
    for before, after in zip(*runs, strict=True):
        name = after["skill_name"]
        assert before["skill_name"] == name
        assert before["schema_version"] == after["schema_version"] == "2.0"
        assert before["status"] == after["status"] == "succeeded"
        assert before["inputs"]["parameters"] == after["inputs"]["parameters"]
        assert set(before["outputs"]) == set(after["outputs"])
        for step, root, report in zip((before, after), roots, reports):
            if name not in {"rnaseq.pca", "rnaseq.deg"}:
                assert report["snapshot"][step["run_id"]]["parents"] == [report["deg_run_id"]]
            for output in step["outputs"].values():
                file_in(root, output)
        summary = document(file_in(new_root, after["outputs"]["summary_json"]))
        env = summary["metadata"]["provenance"]["environment"]
        assert env["scope"] == "image_build_verified"
        if name == "rnaseq.pca":
            pair = [table(root, step, "coordinates", "sample")
                    for root, step in zip(roots, (before, after))]
            compare_columns(pair, ("PC1", "PC2"), signed=True)
            pd.testing.assert_series_equal(pair[0]["group"], pair[1]["group"])
            for key, value in before["summary"].items():
                np.testing.assert_allclose(value, after["summary"][key], rtol=1e-8, atol=1e-10)
        else:
            assert before["summary"] == after["summary"], name
        if name == "rnaseq.deg":
            assert after["inputs"]["parameters"]["treatment"] == "OT"
            assert after["inputs"]["parameters"]["control"] == "T"
            for artifact in ("deg_csv", "filtered_csv"):
                compare_columns([table(root, step, artifact, "gene_id")
                                 for root, step in zip(roots, (before, after))],
                                ("log2FoldChange", "pvalue", "padj"))
        if name in {"rnaseq.go", "rnaseq.kegg"}:
            key = "go_id" if name == "rnaseq.go" else "pathway_id"
            for artifact in ("all_results", "enrichment"):
                compare_columns([table(root, step, artifact, key)
                                 for root, step in zip(roots, (before, after))],
                                ("overlap_count", "universe_size", "pvalue", "padj"))
            for artifact in ("universe", "mapping", "query"):
                assert digest(file_in(old_root, before["outputs"][artifact])) == digest(
                    file_in(new_root, after["outputs"][artifact])), artifact
        tested.append(name)
    assert set(tested) == {"rnaseq.deg", "rnaseq.pca", "rnaseq.volcano", "rnaseq.heatmap",
                           "rnaseq.go", "rnaseq.kegg"}
    return {"status": "passed", "runs": tested, "matched_input_roles": sorted(inputs[0]),
            "deg": reports[1]["workflow"]["step_results"]["deg"]["summary"],
            "pca": reports[1]["steps"][0]["result"]["summary"],
            "enrichment": [{k: v for k, v in row.items() if k != "source_run_id"}
                           for row in reports[1]["enrichment_checks"]],
            "environment": env}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.baseline, args.candidate), indent=2))
