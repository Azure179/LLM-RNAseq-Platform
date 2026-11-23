# Rebuilding the accepted dependency environment

The Linux x86-64 runtime uses CPython 3.13.2 and R 4.6.1. Start in the repository root. Use an independent test volume for validation. Package installation happens only at build time.

## Install

```powershell
docker build --progress=plain -t bulk-rnaseq:locked .
$env:RNASEQ_IMAGE = 'bulk-rnaseq:locked'
docker compose up -d --no-build --wait
```

For a native Windows x86-64 development environment, use Python 3.13.2. The Windows lock includes pytest and its dependencies.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes --only-binary=:all: --no-deps -r container/locks/python-windows-dev.txt
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m pytest -q
```

The project test configuration loads sources from the checkout. For the installed CLI, build the project wheel using the pinned setuptools in a separate build environment, then install that wheel with `--no-deps`. Windows R packages are installed separately. The source-restore contract below targets Linux; a native Windows R installation can differ in libraries, BLAS, devices and fonts.

## How the normal build consumes the locks

- `pyproject.toml` declares supported Python dependency ranges. `container/python-constraints.txt` is the complete pip **installation** lock for Linux. Each wheel is pinned by version and SHA-256. The Dockerfile installs it with `-r`, verifies installed metadata with `pip check`, then installs the local wheel without resolving dependencies. The extracted application version is independent of the dependency lock. See [pip hash-checking mode](https://pip.pypa.io/en/stable/topics/secure-installs/).
- `build-requirements.txt` pins the isolated package build backend. pip comes from the base interpreter. The application code is copied after the runtime dependency layers.
- `r-linux.tsv` lists base/recommended packages from the SHA-256-verified R interpreter source, plus the exact CRAN, Bioconductor software and annotation source archives. `r_sources.py` downloads each specific release (including Archive when necessary), checks its SHA-256 and prepares a local source repository. The R installer accepts a fresh R library containing only the bundled packages, installs from this local repository, checks all versions, and loads the analysis engines. An unavailable archive or unexpected digest stops the build. See [R package installation](https://stat.ethz.ch/R-manual/R-devel/library/utils/html/install.packages.html).
- The normal application user cannot write the image's installed package directory. After validating both inventories, the build creates a receipt in that directory. Newly published Skill summaries include it under `metadata.provenance.environment`. It describes the verified image build. Custom interpreters, writable overlays or a user-modified library require separate verification. Old summaries keep their original provenance.
- Inspect the **completed** local image with `docker image inspect bulk-rnaseq:locked --format '{{.Id}} {{json .RepoDigests}}'`. Record the ID/digest alongside the acceptance run. The base manifest digest in Dockerfile identifies the upstream base. Embedding the finished image's own digest into that same image would change its digest.
- Replay uses the base manifest digest, R interpreter checksum and language locks. Debian apt packages, compiler system dependencies and timestamps can still vary. Preserve the accepted image and its inspected ID for deployment. A dated Debian Snapshot plus an explicit security-update policy would further constrain cold OS rebuilds.

## Update deliberately

1. In a disposable **candidate** Linux x86-64 environment, change the intended direct dependencies and resolve them once; verify the candidate imports and R namespaces. Keep Linux runtime and Windows test environments separate. Add a direct import to both `requirements.txt` and `pyproject.toml` when appropriate.
2. Inside the candidate Linux environment, with the checkout mounted at `/repo`, generate new locks into a scratch directory. Snapshot uses the installed Python dependency graph, active platform markers and exact wheel bytes. For R, use only a dedicated reference library with the intended analysis packages. Extra packages in a developer's personal library would enter the snapshot.

```sh
python /repo/container/locks/snapshot_python.py /repo/pyproject.toml --wheel-hashes > /tmp/python-linux.new
Rscript --vanilla /repo/container/locks/snapshot_r.R > /tmp/r-versions.tsv
python /repo/container/locks/r_sources.py /tmp/r-versions.tsv /tmp/reviewed-archives --snapshot > /tmp/r-linux.new
```

3. Generate the Windows lock **on Windows** from the reviewed Python 3.13.2 development environment with `python container/locks/snapshot_python.py pyproject.toml --dev --wheel-hashes`. Capture stdout to a temporary UTF-8 file. Keep diagnostics on stderr. Review package additions, removals, sources, interpreter versions and archive hashes, then replace the appropriate lock files. Use LF for checked-in lock bytes. Never redirect over a working lock until generation has exited successfully.
4. Build a new candidate image from the normal Dockerfile. Preserve the preceding image and baseline workspace. A clean language restore starts at the verified R interpreter plus OS libraries, before application R/Python dependencies exist. A repeat build reporting a cached dependency layer tests cache reuse only.
5. Run the same fixed input through `container/smoke_runtime.py run` in a new project using the candidate API service. Use the same comparison, project files and thresholds as the baseline. Run `compare_runs.py /baseline/Phase98_Final /candidate/YourTestProject` inside Linux with both workspace volumes mounted read-only. It checks input identity, tables by ID, PCA scores up to per-axis sign, enrichment, schemas, artifact availability and correct parent runs. Keep the per-build image ID next to the comparison evidence. Run the default pytest suite and the two-service `tests/integration/check_deg_guard_containers.py` before accepting a lock update.
