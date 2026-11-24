# Isolated execution prototype (Phase 9.6)

This is an **operator-only development harness**, not an arbitrary code sandbox,
Skill, LLM tool, API endpoint, or Streamlit feature. Existing Controlled
Customization stays on its current audited parameter/annotation path.

Only five fixed task types exist: `smoke`, `test_timeout`, `test_oom`,
`test_failure`, `test_bad_output`. The last four are deliberate harmless test
fixtures. Parameters are an empty closed model. No task can supply executable
code, command, image, flags, mount paths, or daemon address. Calling the host
launcher is an operator approval boundary, not authorization inferred from an
LLM-generated JSON document.

## Build and review

From the repository root, with Docker Desktop in Linux mode:

```powershell
docker image inspect --format '{{.Id}}' bulk-rnaseq:phase9-5
docker build --pull=false -f container/isolated/Dockerfile -t bulk-rnaseq:phase9-6-worker .
docker image inspect --format '{{.Id}}' bulk-rnaseq:phase9-6-worker
```

Before building, compare the base ID with `PHASE9_DUAL_SERVICE_REPORT.md`. Review
the fixed worker source and build output. The derived image adds only this source;
R/Bioconductor is not reinstalled. The launcher accepts a host-approved immutable
local image ID; a hash is an identity, **not a security certification or signature**.
Only the trusted operator may configure that value. Future image promotion needs
signed builds and an independently managed allowlist.

## Run explicit validation

Use an isolated copy of an existing demo project with valid DEG provenance and
portable references. Do not copy private research data just to test infrastructure.
The source workspace and staging roots must be separate. Staging must not exist
under the source workspace (or contain it). The launcher generates per-task
directories with exclusive creation; reused execution IDs are rejected.

```powershell
python -m container.isolated.validate_runtime --workspace outputs/phase9_6/source_workspace --staging outputs/phase9_6/new_staging --project Dual_UI_Demo --source-run <verified-DEG-run-id> --image-digest <reviewed-sha256-image-id>
python -m pytest -q tests/test_isolated_execution.py
```

The integration command executes real containers, including a bounded 128 MiB OOM
test and a 10-second timeout. It is not collected by default pytest. No DeepSeek
calls are made. Cancellation is a trusted host `threading.Event`, not a request
parameter or public control endpoint.

Input comes from `ProjectHistory.artifact`, which verifies the canonical
SkillResult and its stored checksum. Only the bounded source CSV and validated
request are copied into read-only `/input`. `/output` is a task-specific bind
directory; `/tmp` is a 16 MiB tmpfs. `/workspace` is an empty read-only tmpfs to
override the base image's inherited VOLUME; it is never the application's volume.
Logs and execution manifests live on the trusted host outside worker write mounts.
`staging/tmp` is reserved host metadata space, not a second writable host mount.

Output validation requires exactly one regular, non-linked `receipt.json`, at most
16 KiB, the exact identity/input hash/input size, expected checks and resource
limits. Exit 0 alone is insufficient. The receipt is infrastructure evidence, not
a scientific SkillResult. No worker output enters ResultManager or its index.

Statuses: CREATED → VALIDATED → RUNNING → SUCCEEDED/FAILED/TIMED_OUT/CANCELLED.
Validation can fail before starting, and cancellation can occur before or during
execution. Success is published only after output validation and container cleanup.
Failed outputs remain quarantined in staging for inspection, with no published
output checksum/artifact. Cleanup errors are explicit and require operator action.

## Limits

Docker Engine access gives host-level control. Never mount its socket in the
application or worker, expose this launcher as a web handler, or accept remote
Docker configuration from a user request. The launcher uses the operator's local
Docker CLI context. No Docker daemon service, queue, Compose or scheduler is added.

Read-only root, no network, no capabilities, no-new-privileges and default seccomp
reduce exposure but cannot certify safety for arbitrary hostile code. The current
output bind has **no storage quota**; output size is checked after the fixed trusted
task stops. This design is not sufficient for untrusted output flooding. Host-side
symlink/hash checks also do not eliminate races with a malicious concurrent host
writer. Windows bind permission behavior must be measured on each target host.
Crash/power loss can leave stopped or running containers; this prototype has no
durable reaper. Manually inspect the `org.rnaseq.isolated=phase96` label and manifest
before recovery. Normal timeout, cancellation and failure cleanup are tested.

See `PHASE9_ISOLATED_EXECUTION_REPORT.md` for observed evidence and threat model.
