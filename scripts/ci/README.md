# Running the quality gates

For a release review, use the [local deployment security checklist](../../PHASE10_SECURITY_RELEASE_HARDENING_REPORT.md). The installed CLI loads its packaged TOML by default. Use `bulk-rnaseq --config <file>` to select a development configuration. Compose publishes on loopback, stores project data in the shared workspace volume, and accepts the optional provider credential at runtime. Use the aggregate gate summary when sharing diagnostics.

Run these commands from the repository root with 64-bit Python **3.13.2**. On Windows, `py -3.13` can replace `python` if it selects that patch version. Each command creates a temporary virtual environment and installs the reviewed platform lock with wheel hash checking. pip can reuse its download cache.

| Command | Runs | Environment |
|---|---|---|
| `python scripts/ci/run.py fast` | Workflow lint, Git/build-context scan, manifest and dependency checks, build and import the wheel, full default pytest | Windows x64. No Docker, R, DeepSeek key or research dataset required. |
| `python scripts/ci/run.py scientific` | Build the current Dockerfile, start a temporary API service, run six real Skills, check dependency receipt, numeric baseline and lineage | Linux/amd64 Docker Engine and Compose v2. Plan for a source build on the first run. |
| `python scripts/ci/run.py docker` | Build, start both services, validate runtime security, race two requests against one real DEG, take down and recreate services, compare the result index, parents and artifact hashes | Same Docker prerequisites. Two loopback ports are assigned dynamically. |

The two Docker jobs generate unique Compose project, image and volume names. They supply an empty Compose env file, copy only the public fixtures into the scientific container, and remove their own disposable resources on exit. The application containers run as UID 10001. The host orchestrates Docker. The read-only token used by Actions stays outside the containers.

For targeted debugging in the Windows development environment, run `python -m pytest -q tests/test_api_contract_freeze.py tests/test_workflow_validator.py tests/test_workflow_executor.py tests/test_result_reuse.py tests/test_deg_reuse_guard.py`. Individual tests bearing the integration or llm marker retain their opt-in requirements. A targeted run does not certify the full gate.

## Runs and evidence

Pushes and pull requests run Fast CI on `windows-2025`. Both heavy jobs are manual dispatches restricted to the repository default branch. Before enabling them, provision an **ephemeral dedicated Linux x64 runner** with label `rnaseq-ci`, Git, Docker/Compose, enough space for the R source build and access to the reviewed dependency repositories. Restrict its runner group to the two reviewed workflows on the default branch. Keep credentials, private workspaces and research data off this runner. Use hosted workers for untrusted PRs. Repository administrators can require the Fast CI check in branch protection after its first remote run.

Each gate writes `outputs/ci/<level>/summary.json` and exits nonzero on failure. Actions uploads only this aggregate JSON for seven days. It includes the job, trigger, interpreter, command, duration, check outcomes and pytest counts when pytest ran. Full install/build diagnostics, pytest details and working data remain local. An unavailable required engine/interpreter is BLOCKED and returns nonzero. A missing fixture, unexpected skip, failed assertion or missing required test suite fails its gate. Untriggered workflows are NOT RUN. Paid DeepSeek tests require separate authorization and are outside all three gates.

Fast CI downloads and verifies actionlint 1.7.12 for Windows on first use. An offline machine can prepopulate `outputs/ci/tools/actionlint.exe` with the same reviewed executable. The code checks its SHA-256 before use. Updating a tool or action requires reviewing its upstream release and immutable digest. [actionlint releases](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)

## Scientific baseline review

`examples/data/counts.csv` and `metadata.csv` are the existing 30-gene synthetic example. The two small TSVs in `tests/fixtures/ci_science` associate these synthetic gene IDs with test sets. The sets exercise term names, positive-overlap BH correction, valid empty significant sets, and positive enrichment. Their labels have no biological interpretation. The reviewed reference records the four input hashes, dependency fingerprints, DEG counts and every gene's LFC/pvalue/padj, six samples' PCA coordinates, plotting summaries and enrichment results keyed by term and subset.

The reference was measured in the accepted Phase 10.2 image. The DEG result and PCA measurements were independently cross-checked with the earlier accepted demo. The private Phase 10.2 real-data workspace is unnecessary for running these checks. Relative tolerance is 1e-8. Absolute tolerance is zero for probabilities and 1e-10 for other floating-point values. Each PCA axis may flip sign once for all samples. Integer counts, sample/term/gene IDs, names in the effective mapping and lineage relationships must match. Real PNGs must exist and be readable; renderer byte differences are allowed.

CI has no baseline-write mode. For a proposed change, preserve the old reference and input hashes, execute old and proposed environments separately, inspect differences by gene/sample/term, and document the reason, affected Skills, dependency or script versions, largest numeric differences, comparison direction, gene universe and reviewer decision. Change the reference only after scientific review. A changed language lock also requires this review, even if the reported numbers agree.

