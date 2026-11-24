# Phase 9.2 — Enrichment engine and Linux runtime

## Statistical migration

GO and KEGG share one `clusterProfiler::enricher()` adapter. It receives unique
gene IDs, unique `(term, gene)` pairs and unique `(term, name)` pairs from the
existing validated custom mappings. No `enrichGO`, `enrichKEGG`, online lookup,
ancestor expansion, organism substitution or new gene-ID conversion is used.
GO.db is an imported package dependency, **not** the source of this analysis's
gene-to-GO associations. Missing supplied names remain IDs.

Background still means declared analysis-eligible genes intersected with the
selected annotation. A full DEG table excludes NA padj/log2FC as before. Query
genes outside the declared background are rejected; unmapped genes are excluded
with counts/warnings. Empty significant tables succeed; no mapped query fails.
Workflow `all/up/down` continues to derive lists from checksum-verified DEG output.

| Parameter | Explicit policy |
|---|---|
| `universe` | Actual mapped, eligible background, never omitted |
| `pAdjustMethod` | BH |
| `minGSSize` | 1, retaining small custom sets |
| `maxGSSize` | Mapped universe size, retaining sets above 500 |
| `pvalueCutoff` | 1 inside enricher; public threshold applied after export |
| `qvalueCutoff` | 1; use unfiltered `@result`, do not filter by q-value |
| Public significance | pvalue <= configured threshold AND padj <= configured threshold AND overlap > 0 |

This is **not** clusterProfiler's default filtering policy. Using the result slot
avoids losing otherwise valid rows when q-value estimation is unavailable for a
small test family. Native pvalue and p.adjust are copied, never recomputed by the
application. Unused q-values are not added to the public CSV schema.

The verified 4.20.2 / enrichit 0.2.6 implementation tests positive-overlap terms.
The former base-R implementation included zero-overlap terms in BH. Therefore
raw probabilities should agree (within floating-point tolerance), but adjusted
values can decrease and significant counts can increase. `*_all_results.csv`
now means the **native tested family**, not every annotated background term.
`n_terms_annotated` records the latter; `n_terms_tested` records the former.
Old outputs are not rewritten or silently relabeled.

The adapter preserves all existing CSV columns, sorted order, inclusive thresholds,
semicolon-delimited original gene IDs and base-R PNG rendering. In particular it
does not split clusterProfiler's slash-delimited geneID to reconstruct IDs: an ID
may itself contain a slash. Native counts/ratios must match known memberships.
Python independently checks memberships, metadata, ratios, probabilities, engine
versions, PNGs and threshold filtering before publishing success.

Provenance records engine/function, clusterProfiler/enrichit/R/stats versions,
all engine options above, declared and mapped query/background counts and hashes.
The package version is not the same as the statistical engine version.

## Tests

```powershell
python -m pytest -q
python -m pytest tests/integration/test_real_go.py tests/integration/test_real_kegg.py tests/integration/test_clusterprofiler_migration.py --run-r -q
python -m pytest tests/integration/test_phase9_real_enrichment.py --run-r -q
```

Default tests do not run R or an LLM. The last command requires the local private
`workspace/Real_DATA_PHASE7_E2E` project and selects its latest verified OT/T DEG;
it skips when absent. New UUID Workflow directories hold all six GO/KEGG ×
all/up/down results. Audit data stays under ignored `outputs/phase9_enrichment/`.
The old Fisher implementation exists only as a migration-test reference in
`tests/fixtures/legacy_ora_reference.R`; it is not imported or packaged by Skills.

## Runtime status

Container build, smoke and restart/reuse evidence are reported separately in
`PHASE9_RUNTIME_REPORT.md`. Local Windows tests are not Linux-container evidence.
No paid API call is needed for this phase's statistical and runtime checks.
