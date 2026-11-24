# Reproducing and tracing a result

To investigate a figure, start from its project and run ID. In Results inspect its parent, then open the source DEG and its effective comparison. Follow a workflow run back to its step records and resolved bindings. For a customization, open its collection and inspect the recorded source run. Retain the project volume with the input bundles and result files; a screenshot alone cannot reconstruct the analysis.

| Evidence | Current coverage |
|---|---|
| Input versions | **Implemented for project DEG reuse.** Count and metadata content fingerprints, the design/comparison and required artifacts determine eligibility. Replacing a file with different bytes under the same name can invalidate automatic reuse. Mapping or display changes alone do not invalidate DEG. |
| Current versus historical | **Implemented.** A valid current source records `current_input_snapshot`. Explicit selection of an eligible older run can record `historical_run`. A child links to its real parent. Unprovable old results cannot qualify automatically. Verify status and scope before interpreting a child as current. |
| Direction and parameters | **Implemented in part.** DEG uses treatment relative to control with a simple condition design. Effective public thresholds and comparison are recorded. Some R package defaults, actual fitting decisions and implementation details still require reading the matching code. |
| Enrichment | **Implemented for effective input.** Inspect query_used.csv, universe_used.csv and mapping_used.csv and their provenance. The measured eligible background is intersected with the effective mapping. The upstream provider's annotation release and gene-ID construction may need separate lab records. |
| Artifact integrity and lineage | **Implemented for managed results.** ResultRef, parent IDs, run metadata and recorded checksums trace managed artifacts. Paths are scoped to a project. Unresolvable legacy absolute paths require operator review. |
| Environment | **Implemented at deployment level, partial per run.** Locked Python wheels and R sources, Docker base digest and baseline support a repeatable environment. Individual historical results do not universally identify the precise Python implementation, image digest, fonts or every transitive setting. Schema version and implementation version are distinct. |
| Future work | More complete per-run environment and implementation manifests, annotation provenance, and remote CI evidence. |

**Provenance** describes what produced an existing output. **Reuse eligibility** decides whether that output answers a new request under the current inputs. The resolver's checks address the latter for DEG. They do not define a universal cache key for every possible analysis.

Scientific regression compares keyed gene statistics, explained variances, selected genes, tested terms, background and lineage. PCA axes can reverse sign together without changing sample geometry. A font, rendering device or embedded timestamp can change plot bytes while leaving the underlying numbers intact. Use file hashes to verify an existing artifact; use numerical and semantic checks to compare independent executions. The [CI guide](../scripts/ci/README.md) gives the tolerances and baseline review procedure.

Evidence: [provenance audit](../PHASE10_REPRODUCIBILITY_PROVENANCE_AUDIT.md), [input-version guard](../PHASE10_1_1_RESULT_REUSE_GUARD_REPORT.md), [dependency recovery](../PHASE10_DEPENDENCY_LOCKING_REPORT.md).
