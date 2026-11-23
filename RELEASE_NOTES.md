# 0.8.0rc1 · Local RNA-seq workspace

Release candidate for a trusted user on Windows Docker Desktop with Linux containers. Publication, final version and tag await approval.

- Start from raw counts and sample metadata. Run two-group DESeq2, explore VST-based PCA, and plot Volcano and Heatmap results. Use local mappings for clusterProfiler GO and KEGG enrichment.
- DeepSeek can propose one tool or a multi-step plan. Local validation, the Skill Registry and fixed Python/R programs determine execution. Offline requests and the public demo require no provider key.
- Trace results through project-scoped run IDs, input fingerprints and artifact records. The resolver rejects stale automatic DEG reuse; a permitted explicit selection retains historical scope. Controlled plot customization saves a separate child result.
- Streamlit and FastAPI use the same backend and persistent volume. The default Compose ports are published on loopback. Scientific, fast and Docker CI gates share local entry points and locked language environments.

## Installation and upgrade

Follow the [quick start](README.md#quick-start-on-windows) and [synthetic demonstration](examples/demo/README.md). The wheel installs the Python package, console entry point, schemas and R scripts. Supply the required R environment separately for a host installation. Compose supplies both services and the analysis runtime.

Before upgrading, finish running jobs, stop writers and [back up and test restoration of the workspace](container/README.md). Rebuild and recreate both services with the existing volume. Inspect project bindings, health and prior results before resuming. Retain the previous image and backup for rollback. Older results lacking verifiable input identity can require a fresh DEG. Automatic migration of arbitrary Windows absolute paths is outside the supported upgrade path.

## Release boundaries

Public multi-user hosting and execution of arbitrary model-generated code are unsupported. The worker prototype accepts fixed trusted tasks. Some per-run environment details and upstream annotation history still need separate records. Scientific baselines use public synthetic fixtures. Review dependency advisories and build-network requirements before installation. Images can be tagged locally with `RNASEQ_IMAGE=bulk-rnaseq:0.8.0rc1`; no registry distribution is implied.
