# Local Linux runtime: FastAPI and Streamlit

This is a local, unauthenticated FastAPI runtime, **not** an Internet deployment.
The shared image includes both entry points. Its default command starts FastAPI;
Streamlit is a separate container command. No model key, research data or populated
workspace is included in the image. Compose now manages the two independent services;
the older CLI instructions below remain available for existing deployments.

## Docker Compose: Windows Docker Desktop

For the Windows WSL 2 backend, enable virtualization and WSL 2, start Docker Desktop, and confirm `docker info --format '{{.OSType}}'` returns `linux`. The [public demo](../examples/demo/README.md) starts an isolated workspace with its own loopback ports. The commands below start the normal persistent workspace.

Prerequisites: Docker Desktop running **Linux containers**, Docker Compose v2+
(final acceptance used Compose v5.7.0 and Engine 29.9.3), enough disk/memory for R/Bioconductor, and network access to Docker
Hub, Debian package mirrors, CRAN, Bioconductor and PyPI for a first source build. Work from the repository root
in PowerShell; quote the folder path if it contains spaces. Do not run another
application on the chosen published ports.

1. Copy `.env.example` to `.env` **only if you do not already have `.env`**.
   Put your own DeepSeek key there for Chat. Existing local settings need not be
   overwritten. `container/compose.env.example` lists deployment overrides;
   `--env-file "path/to/private.env"` selects a different file. Empty credentials
   are supported for offline Skill API execution and result browsing; AI requests
   return a missing-key error until configured.
2. Validate without disclosing expanded credentials:

   ```powershell
   docker compose config -q
   ```

3. Build/start both services:

   ```powershell
   docker compose up -d --build
   docker compose ps
   ```

4. Open [Streamlit](http://127.0.0.1:8501/) or
   [FastAPI documentation](http://127.0.0.1:8000/docs).
   Create a project, upload counts, enter sample groups (or import metadata), and
   submit a request. Results previews/downloads are in Results. Neither entry point
   depends on the other process; both use the same installed Python backend.

Every command below uses the **same** `-p`, `--env-file` and `-f` options if you
used overrides when starting:

```powershell
docker compose logs --tail=100
docker compose restart
docker compose stop
docker compose start
docker compose down
docker compose up -d
docker compose up -d --force-recreate
```

`down` removes containers/network, **not the workspace volume**. Do not add `-v`.
Restart does not reload environment values: use `up -d --force-recreate` after
configuration changes. Finish running analyses first. An interrupted statistical
task is **not automatically resumed**; inspect its failed/interrupted history
before starting a new run. The restart policy restarts exited service processes;
an `unhealthy` status alone does not trigger a Docker restart.

Health checks use FastAPI `/health` plus `/ready` and Streamlit `/_stcore/health`.
They do not execute Skills or contact DeepSeek. `healthy` is service readiness,
not a claim that every analysis or UI action has been tested.

### Named Volume (default) and host Bind Mount

The default named volume is `rnaseq-workspace` at `/workspace` in both containers.
Set `RNASEQ_VOLUME_NAME` to a **new unique name** for a separate deployment;
changing only Compose's `-p` does not change this explicit volume name. Named
volumes live in Docker Desktop's Linux storage and usually offer the simplest
permissions and locking. Keep using the same name to rediscover projects.

To inspect files directly in Windows, create a dedicated host folder first:

```powershell
New-Item -ItemType Directory -Force "C:/RNAseq Workspace"
$env:RNASEQ_WORKSPACE_HOST = "C:/RNAseq Workspace"
docker compose -f compose.yaml -f compose.bind.yaml config -q
docker compose -f compose.yaml -f compose.bind.yaml up -d --build
```

The override refuses a missing host directory instead of creating a typo silently.
Use a local drive shared with Docker Desktop; avoid OneDrive synchronization,
network/NFS/SMB storage and concurrent editors of runtime JSON. On Linux the host
folder must be writable by UID/GID `10001:10001`; the application will not chmod
or chown your dataset. Docker Desktop emulates permissions differently, so test
your actual mount rather than assuming native Linux ownership behavior.

Switching volume modes **does not migrate files**. Stop all writers, back up, copy
the whole workspace (including hidden metadata), verify checksums, then start
with the new mount. Keep the old copy. Both services must always use the same mode.
Persistent content includes project.json, inputs, results, runtime/chat records,
Workflow manifests, lineage/index, customization and reports. `/tmp` is disposable.
New project-relative references resolve under the selected project after moving the whole workspace.
On Linux, a Windows absolute reference that cannot be verified returns
`LEGACY_PATH_UNAVAILABLE`. Keep its original Windows workspace and backup for inspection.

### Backup and lock recovery

Stop both services and wait for them to exit before a consistent backup. A simple
named-volume backup, using only the already installed application image:

```powershell
docker compose stop
$backup = (New-Item -ItemType Directory -Force "./outputs/workspace-backup").FullName
docker compose run --rm --no-deps -v "${backup}:/backup" api python -c "import shutil; shutil.make_archive('/backup/workspace', 'gztar', '/workspace')"
docker compose start
```

Use a new backup directory/name for each snapshot; verify the archive and restore
to a **new** workspace before relying on it. Restrict backup permissions because it
contains research data. Never publish workspace archives or private `.env` files.

Cross-process writes use filesystem exclusive-create locks and atomic replacement,
not `threading.Lock`. Workspace metadata writers wait up to five seconds for exclusive creation, polling every 125 ms. An occupied lock at timeout returns `WORKSPACE_BUSY`. Valid result indexes can be queried while a writer holds this lock. A forced process kill
can leave `.workspace.lock` (or a project-specific lock) behind. There is no
automatic stale-lock deletion: stop **every process sharing that workspace**,
confirm no active analysis, make a backup, then remove only the confirmed orphan
lock. For the workspace-level lock only, after that inspection:

```powershell
docker compose stop
docker compose run --rm --no-deps api python -c "from pathlib import Path; p=Path('/workspace/.workspace.lock'); assert p.is_file(); p.unlink()"
docker compose start
```

Do not run a recursive lock-file deletion, and do not remove locks while a writer
is active. Interrupted result directories remain evidence, not successful results.

### Configuration and secrets

The existing configuration remains authoritative: process environment overrides
the local DeepSeek dotenv loader. Compose passes only the allowlisted DeepSeek
settings and `RNASEQ_LOG_LEVEL`; workspace is fixed to `/workspace`, Rscript is
`Rscript`, application listeners are `0.0.0.0:8000/8501` **inside the containers**.
`RNASEQ_API_PUBLISHED_PORT` / `RNASEQ_UI_PUBLISHED_PORT` change only host ports;
host IP is fixed to `127.0.0.1`. No custom command or path is supplied by an LLM.

Compose Secrets was evaluated: the current loader reads `DEEPSEEK_API_KEY`, not
`DEEPSEEK_API_KEY_FILE`. Declaring a secret mount alone would not configure Chat.
This phase keeps compatible **runtime environment injection**, never build args
or COPY. Docker administrators can read container environment; this is not secret
encryption. Do not share full `docker inspect`, `docker compose config` output or
the deployment env file. Future file-backed secret support should be implemented
in the existing loader, not a second configuration subsystem.

The image build context excludes `.env`, workspace, outputs, logs and private
datasets. Logs rotate through Docker's local driver. Backend run logs persist
under each project's results/logs. No main service mounts the Docker socket;
Phase 9.6 remains a separate trusted **host-only** launcher, not a Compose service.

### Cached-image fallback and common errors

When Docker Hub is unavailable but the previously audited complete application
image exists locally, explicit packaging can reuse it:

```powershell
docker compose -f compose.yaml -f compose.cached.yaml up -d --build
```

Historical fallback default (predates the lock hotfix): `bulk-rnaseq:phase9-5`; set `RNASEQ_CACHED_IMAGE` to another
explicitly audited local application image if appropriate. This copies deployment
documentation only. It does **not rebuild source/dependencies**. Do not use it to
deploy changed application code; use the normal Dockerfile. The Phase 9.7 report records the successful source build after the Docker Hub connectivity issue was resolved. A fallback image must include the Phase 9.7.1 lock fix for final deployment parity.

| Symptom | Action |
|---|---|
| Docker daemon unavailable | Start Docker Desktop, select Linux containers, run `docker info` |
| Hub token/registry timeout | Repair network/proxy access; do not disable TLS checks; use the explicit cached option only with an audited local image |
| A build fails after the base image was pulled | Identify the failing host in the build log: Debian packages, CRAN/Bioconductor and PyPI have separate download steps. A successful Docker Hub pull checks only the registry path. Keep any proxy credentials out of build files and published logs. |
| Port already allocated | Stop the competing deployment deliberately, or set new published ports in your private env file |
| Mount missing | Create/verify the dedicated directory; use forward slashes in Windows paths |
| `/ready` unhealthy / workspace not writable | Inspect logs and actual mount permissions; check Rscript availability |
| Chat missing credentials | Configure your runtime env file and recreate containers; never add a key to the Dockerfile |
| Project/result not visible | Check both mount sources, project selection and Refresh from disk / Refresh Results |
| `WORKSPACE_BUSY` after a crash | Follow the inspected orphan-lock recovery procedure above |

This remains a local unauthenticated MVP. No public hosting, user isolation across
untrusted users, TLS endpoint, task queue, or arbitrary Python/R execution API is
provided. Resource samples are not measured peaks or production capacity claims.

For the final deployment matrix, environment, scientific checks and limitations, see [Phase 9 acceptance](../PHASE9_FINAL_ACCEPTANCE_REPORT.md).

See [Phase 9.7 report](../PHASE9_COMPOSE_DEPLOYMENT_REPORT.md) for actual test results.

## Build and start (PowerShell, repository root)

Prerequisite: Docker Desktop installed and running in **Linux containers** mode.
The first build compiles R and R packages; allow substantial time and disk space.

```powershell
docker version
docker info --format '{{.OSType}}'
python container/context_audit.py --report outputs/phase9_runtime/build_context.json
docker build --progress=plain -t bulk-rnaseq:phase9.2 .
docker volume create rnaseq-phase9-workspace
docker run -d --name rnaseq-phase9 -p 127.0.0.1:8000:8000 --mount type=volume,source=rnaseq-phase9-workspace,target=/workspace bulk-rnaseq:phase9.2
curl.exe --fail http://127.0.0.1:8000/health
curl.exe --fail http://127.0.0.1:8000/skills
```

No API key is needed for health/metadata or explicit Skill calls. To use the LLM
features later, add `--env-file .env` **at run time only**. That ignored file can
contain DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL. Never pass keys as
build arguments, paste them into Dockerfile, or commit them. This phase's smoke
does not call DeepSeek.

The normal build restores pinned Python wheels and R source archives. The base
manifest digest and the R interpreter source checksum are pinned in Dockerfile.
For development installs, lock updates, integrity checks and scientific acceptance,
see [dependency locks](locks/README.md). Debian development libraries still use
the Bookworm repositories. The image records both runtime inventories.

`.dockerignore` denies everything except explicit build inputs. The context audit
measures those source files and rejects likely credentials; record Docker's own
`transferring context` bytes from the actual build separately.

## Real container smoke and recreation

Use a **new** named volume/project; never mount an existing Windows workspace as
if it had been migrated. The following uses the already prepared private dataset,
read-only. It is mounted at run time, not sent to the build daemon.

First stop and remove only the disposable container above (not its named volume),
then start with the read-only input mount:

```powershell
docker stop rnaseq-phase9
docker rm rnaseq-phase9
$phase9Input = (Resolve-Path 'Real_DATA_TEST/input/prepared_asc').Path
docker run -d --name rnaseq-phase9 -p 127.0.0.1:8000:8000 --mount type=volume,source=rnaseq-phase9-workspace,target=/workspace --mount "type=bind,source=$phase9Input,target=/smoke-input,readonly" bulk-rnaseq:phase9.2
docker exec rnaseq-phase9 python /opt/rnaseq/container/smoke_runtime.py run
```

Before each `docker exec` smoke command, wait until the service responds at
`http://127.0.0.1:8000/health` (or Docker health is `healthy`). Starting a container
does not mean its application has finished initialization.

The smoke creates a project through ProjectManager and imports real files through
FileImportService. PCA and a DEG/Volcano/Heatmap workflow execute via actual HTTP
handlers and Registry, followed by GO and KEGG on each exact all/up/down subset.
The workflow plan is an explicit test fixture, not a simulated DeepSeek decision.
It checks non-root Linux, Python version, R executables/packages, engine provenance,
subset/background membership, TERM2NAME, portable references and output existence.
It also runs controlled Volcano customization with explicit test parameters (no
model call), checks immutable sources, bounded previews/downloads and isolation. Default
filenames/groups are from this project's prepared dataset; command options allow
other mounted files. It never fabricates a mapping or analysis result.

Recreate the application container using the same volume, without the input mount:

```powershell
docker stop rnaseq-phase9
docker rm rnaseq-phase9
docker run -d --name rnaseq-phase9 -p 127.0.0.1:8000:8000 --mount type=volume,source=rnaseq-phase9-workspace,target=/workspace bulk-rnaseq:phase9.2
docker exec rnaseq-phase9 python /opt/rnaseq/container/smoke_runtime.py verify-reuse
```

The second smoke checks persisted artifact hashes, workflow and customization
lineage, resolves the exact DEG by run_id, then requests a new Volcano. It does
not rerun DEG. Reports live in the project's
`reports/container_smoke.json` / `container_restart.json` inside the volume.
Inspect exit codes and reports; never infer success merely from file creation.

## Persistence boundary

Container projects live at `/workspace/<project>`. The backend writes versioned
project-relative persistence for new project-owned JSON records, including Skill
summaries, Workflow provenance and Customization. Runtime readers resolve these
references against the current ProjectContext. Local copy/reopen tests cover
mount-root relocation. The [final Docker acceptance](../PHASE9_FINAL_ACCEPTANCE_REPORT.md)
records the separate two-container persistence and image-replacement tests.

The deployment mount remains `/workspace`. Preserve the immutable project directory
name. Old absolute-path histories are not automatically migrated or rewritten.
See [Workspace configuration](../PHASE9_WORKSPACE_CONFIG_REPORT.md) for the reference
format, legacy compatibility, shared-writer limitations and the dated runtime evidence.

The service runs as UID/GID 10001. A new named volume inherits the image directory's
ownership; bind mounts must already be writable by that identity. Do not solve a
permission failure by running the API as root or making all files world-writable.

## Windows bind-mount round trip (disposable workspace only)

`workspace_roundtrip.py` exercises a real Windows DESeq2 result using the existing
public synthetic demo; these are portability fixtures, not biological findings.
Use a new directory. The probe refuses to overwrite an existing project.

```powershell
python container/workspace_roundtrip.py prepare-windows --workspace outputs/phase9_docker/workspace
$phase9Workspace = (Resolve-Path 'outputs/phase9_docker/workspace').Path
# Use this instead of the named-volume mount in the run commands above:
# --mount "type=bind,source=$phase9Workspace,target=/workspace"
docker exec rnaseq-phase9 python /opt/rnaseq/container/workspace_roundtrip.py reuse-linux --workspace /workspace
python container/workspace_roundtrip.py verify-windows --workspace outputs/phase9_docker/workspace
```

The Linux stage only runs Volcano from the verified Windows-origin DEG. The final
Windows stage verifies the Linux result is discoverable with identical hashes and
lineage. This does not migrate legacy absolute-path records. Run the real-data
smoke in a separate project in that same disposable mounted workspace.

If Docker Hub authentication times out while a local HTTP proxy is already
configured, the Docker CLI may also need process-local `HTTP_PROXY` / `HTTPS_PROXY`
settings. Use the actual local proxy endpoint; do not embed it in Dockerfile or
public application configuration, and do not disable TLS verification.

## Acceptance

Static tests and Windows R tests are necessary but insufficient. A real image
build, Linux smoke, HTTP checks, six real Skills, container recreation and exact
result reuse are required. This environment passed those gates on 2026-10-19–20;
see [recorded evidence and limitations](../PHASE9_DOCKER_INTEGRATION_VALIDATION.md).
Rebuilding on another machine still requires its own runtime checks. This does
not certify the whole of Phase 9 or remove the documented bind-mount latency.
# Phase 9.4 reliability probes

The current health contract separates `/health` (process liveness) from `/ready`
(startup complete, analysis executable discoverable, Workspace writable). Docker
HEALTHCHECK uses `/ready`; unavailable prerequisites return the standard error
envelope with `SERVICE_NOT_READY` and safe boolean checks. Neither endpoint runs
an analysis or contacts DeepSeek. Readiness does not certify every R package or
the validity of a particular dataset.

See `../PHASE9_HEALTH_E2E_REPORT.md` for actual container tests, failure history,
resource samples and the final verdict. Raw evidence is local and ignored under
`outputs/phase9_4/`. The `reliability_*` scripts are explicit test harnesses, not
application launchers. Their named volume/container must be dedicated to testing;
never point them at a research workspace. Initial setup refuses existing projects.
Use new names and a new evidence directory for another full experiment.

`reliability_fault_rscript.sh` is test-only fault injection. Production images use
the real Rscript directly; do not configure this wrapper for normal analysis.
The kill test starts and suspends an actual R child before killing only its test
container. It tests durable checkpoints, not automatic recovery. Results warnings
identify checkpoints without terminal results as `WORKFLOW_NOT_FINALIZED`; the
task could be active or interrupted. Operators must inspect it rather than assume
success, blindly replay an ID, or remove locks belonging to an active process.

## Phase 9.5: two independent services, one named volume

Both entry points call the same Python backend directly. Streamlit does **not**
call FastAPI over HTTP. Use a dedicated new volume for deployment validation.
The Dockerfile default remains FastAPI; the script overrides only the UI command
and healthcheck. Both containers run as UID/GID 10001 and bind host localhost.
Do not publish these unauthenticated services on the Internet.

```powershell
python container/context_audit.py --report outputs/phase9_5/context.json
docker build --progress=plain -t bulk-rnaseq:phase9-5 .
python container/dual_services.py start
python container/dual_services.py status
python container/dual_services.py stop
```

UI: http://127.0.0.1:8501/ · API: http://127.0.0.1:8000/health

Defaults: `rnaseq-phase95-api`, `rnaseq-phase95-ui`, shared named volume
`rnaseq-phase95-workspace` mounted at `/workspace`. The script never removes a
volume, never adopts unlabelled containers, and refuses image/volume mismatches.
Repeated start/stop is safe. A running/starting state is not a health PASS; wait
for healthy and verify actual operations. Ports 8000/8501 must be free.

For real DeepSeek calls, provide `--env-file .env` on **initial creation**, with
only runtime settings. Existing containers keep their original environment:
stop/start does not reload that file. To change environment or image, inspect and
deliberately recreate only these containers, preserving the volume. The script
supplies the default workspace and Rscript settings; do not include conflicting
workspace settings in the env file. No credentials are build arguments. Do not
print Docker's full Config.Env during troubleshooting. Streamlit CORS/XSRF
protections remain enabled.

### Explicit validation (no LLM calls from these commands)

First create `Dual_UI_Demo` and import the public `examples/data/counts.csv` and
`metadata.csv` through Files. Use the Assistant to run a single DEG, then a
PCA+DEG+Volcano workflow and an existing Volcano customization. These UI operations
use real DeepSeek and R; they are not part of default pytest. Use an explicitly
approved API budget. This phase's recorded run used 8 requests.

```powershell
python container/dual_validation.py reuse --report outputs/phase9_5/cross-entry.json
python container/dual_validation.py concurrent --report outputs/phase9_5/concurrent.json
python container/dual_validation.py snapshot --report outputs/phase9_5/snapshot.json
python container/dual_lock_check.py --bind-root outputs/phase9_5/new_bind_check --report outputs/phase9_5/locks.json
```

`reuse` calls the real API Volcano using the persisted UI DEG identity. `concurrent`
runs two real Volcano Skills (API HTTP and backend in the UI container), then checks
both result identities, artifact hashes, lineage, previews and JSON readability.
It does not simulate simultaneous clicks in two browsers. `snapshot` only reads.
The lock check requires a **new** bind directory; it tests held-lock rejection and
post-release writes across two actual Linux containers on both a named volume and
the supplied host mount. It does not delete test projects or volumes.

The lock is a cooperating filesystem exclusive-create lock, not a distributed
queue. Workspace lock acquisition has a five-second deadline. An occupied lock at timeout returns `WORKSPACE_BUSY`. Analysis requests are never replayed. Inspect the holder before resolving a stale lock.
This evidence is specific to the tested Docker Desktop host and filesystem,
not a guarantee for NFS/SMB or other deployment platforms.

Use sidebar **Refresh from disk** and Results **Refresh Results** after changes
from the other entry point. UI filters and selected project are per-session;
project bindings, chats, results, workflow lineage and customization are on the
shared volume. Reopening a session does not replay past requests.

See `../PHASE9_DUAL_SERVICE_REPORT.md` for evidence, limits and test results.

