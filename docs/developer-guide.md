# Developing and testing the analysis backend

Start with one request and follow it through the backend. For example, the offline demo posts a plan to `POST /workflow/run`. The public contract is available at [local OpenAPI](http://127.0.0.1:8000/openapi.json). A project must already have validated input bindings. Path selection and output directories belong to the host.

## Find the code for a change

| Change | Start here | Contract to preserve |
|---|---|---|
| Add or inspect a Skill | [Built-in bindings](../src/bulk_rnaseq/builtin_registry.py), [manifests](../src/bulk_rnaseq/manifests), [SkillResult](skill-result-schema.md) | Register an explicit executor, describe inputs/outputs, declare upstream dependencies, return the shared result. GSEA has a reserved manifest and no executor. |
| Adjust a Volcano/Heatmap option | [Display validation](../src/bulk_rnaseq/plot_options.py), [Skill wrappers](../src/bulk_rnaseq/skills), [R scripts](../src/bulk_rnaseq/r_scripts) | Keep defaults compatible, reject out-of-range values, pass validated options through the existing R invocation and record effective values. Write into a new run. |
| Offer a plot option in chat | [Customization catalog](../src/bulk_rnaseq/customization/catalog.py), [engine](../src/bulk_rnaseq/customization/engine.py), manifest input schema | Expose only reviewed business parameters. Test an invalid value, a valid rendering, unchanged source and parent lineage. |
| Alter workflow validation | [Models](../src/bulk_rnaseq/workflow/models.py), [validator](../src/bulk_rnaseq/workflow/validator.py), [executor](../src/bulk_rnaseq/workflow/executor.py) | Validate required inputs, bindings, skill names and DAG before execution. Bind actual upstream outputs and propagate failed/skipped states per branch. |
| Diagnose wrong or missing source | [ResultResolver](../src/bulk_rnaseq/workspace/result_resolver.py), [DEG reuse guard](../src/bulk_rnaseq/workspace/deg_reuse.py), [ResultManager](../src/bulk_rnaseq/workspace/result_manager.py) | Enforce project scope, direction and input identity; preserve explicit historical scope. Inspect a child's parent IDs, workflow result and provenance. |

Python validates inputs, creates run directories, checks process termination and artifacts, and publishes structured results. R executes the statistical and plotting code. Keep parameter definitions in the manifest/schema path so the tool adapter and API inherit the same contract. Check the OpenAPI extension for allowed business parameters on `POST /skills/{skill_name}/run`. A model-supplied local path cannot substitute for an input binding.

Result index writes merge under a bounded cross-process lock and save atomically. Read a valid index without acquiring an exclusive lock. Avoid holding a lock during R execution or large-file hashing. The [contention report](../PHASE9_7_1_LOCK_CONTENTION_HOTFIX.md) covers concurrent writers and timeout recovery.

## Test a plotting change

In a development environment with the locked Windows dependencies installed, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_volcano.py tests/test_heatmap.py tests/test_customization.py
```

Use the selected environment's Python if developing in a venv. For full verification run the [three shared gates](../scripts/ci/README.md). Fast runs the default suite, package checks and lock verification. Scientific runs real R against [versioned expectations](../tests/fixtures/ci_science/reference.json). Docker tests service health, shared storage, concurrent reuse and restart. Heavy jobs are manually triggered. The default suite needs no paid model key.

## Change an environment deliberately

Use the [lock and rebuild instructions](../container/locks/README.md). Python pins are platform-specific. The R lock records exact source versions and hashes. The Dockerfile pins the base by digest. Review proposed dependency changes, rebuild in isolation, compare old and proposed numeric outputs, then run the scientific and deployment gates. Record the reason and reviewed differences before changing a reference. Ordinary test runs never regenerate the frozen baseline. See [release hardening](../PHASE10_SECURITY_RELEASE_HARDENING_REPORT.md) for supply-chain caveats.
