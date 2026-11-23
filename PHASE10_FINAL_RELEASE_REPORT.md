# Phase 10.6A · Final release candidate acceptance

2026-11-20 · Windows Docker Desktop, Linux amd64, local trusted-user scope. This document is the Part A review record and publication decision point.

## 1–2. Project and acceptance matrix

The two local applications share a Python/R backend and persistent workspace. DeepSeek can propose registered operations; local validation and execution produce the scientific results. The release covers bulk count matrices, metadata and mapped enrichment. The public demonstration uses synthetic inputs.

| Stage | Accepted evidence | Boundary retained |
|---|---|---|
| 7 and 7.8.1 | Local workflow, customization, post-workflow reuse and lineage passed after the hotfix | The earlier preview's NOT READY verdict applies to that historical snapshot |
| 8 | FastAPI contract, error handling, execution and project-scoped results passed | Local API |
| 9.1–9.8 | Phase 9 overall PASS, including dual services and bounded concurrent index writes after 9.7.1 | Windows Docker Desktop, non-root containers, no public multi-user deployment |
| 10.1 / 10.1.1 | Provenance audit completed; stale DEG auto-reuse defect fixed and tested | Historical runs have a separate scope; per-run provenance remains partial |
| 10.2 | Locked runtime recovery and scientific baseline passed | Upstream availability and system package repositories still matter |
| 10.3 | Local fast, scientific and deployment gates passed | Remote execution pending |
| 10.4 | Local release hardening passed | Dependency advisories and their deployment-specific mitigations remain documented |
| 10.5 | Documentation, real synthetic-data demos A–E and browser download passed | New paid provider tests were outside acceptance |

## 3–4. Version, branch and proposed scope

The proposed review version is **0.8.0rc1**, following the repository's 0.7.0 preview. The package, CLI, API and README identify the same candidate. The version for a final tag still needs a user decision. A later change to the package version requires rebuilding the artifacts and rechecking versions, tests and digests.

Local checkout: `master`, HEAD `ac69487846b308ee2657519341d1d91dde981397`, tracking `origin/master`. At the initial inventory, 48 tracked files had working-tree edits and 156 files were untracked. These include application code, tests, Docker and CI configuration. The index had no staged changes. The review also adds candidate metadata and this report.

Configured origin: [Azure179/LLM-RNAseq-Platform](https://github.com/Azure179/LLM-RNAseq-Platform). A read-only Git transport query hit a connection reset. GitHub's read-only repository/ref/compare APIs confirmed public visibility, default branch master, head `e83af7f74992bd74af506cb783dbc146e1f1d0d7`, two commits ahead of local HEAD, aggregate diff confined to README.md. The only tag returned was v0.7.0-preview. No write or visibility change was requested from GitHub.

The proposed tree contains src (including R scripts, manifests, schemas, registry, API and workflow), app, packaging, Docker/Compose, locks, workflow YAML, tests, reviewed scripts/config, documentation, synthetic demo and the repository's existing MIT license. Real_DATA_TEST contains harness/configuration only, with a public-edition report. Private results and input tables, .env, local projects, venvs, cache, logs, credentials, downloaded tools and Docker storage are excluded.

**Use the reviewed candidate tree for a future commit.** Its 30 historical documentation editions omit private validation content. The working checkout retains the original evidence. An indiscriminate add of the working tree would propose different content. The local review bundle includes an exact path/hash manifest, list of public editions and SHA256SUMS.

## 5–8. Environment, regression, science and deployment

CPython 3.13.2, R 4.6.1, Bioconductor 3.23, DESeq2 1.52.0 and clusterProfiler 4.20.2. Docker Engine 29.9.3 and Compose v5.7.0. The RC changes no scientific implementation or dependency lock. The source build checked the pinned base and the R/Python environment with the existing gates. The local quick-start image was tagged bulk-rnaseq:0.8.0rc1, ID sha256:13faac04f37f14c70ec6105e4be12bce91833cdef931146f2fd739c0fb8d. No registry image upload.

Each gate ran from an independent, publishable-source checkout without private inputs or provider credentials. Machine summaries are retained locally under outputs/phase10_6/candidate/outputs/ci. The public demo and default-port quick start each received a dedicated Compose project and volume.

| Command run | Evidence in this round |
|---|---|
| python scripts/ci/run.py fast | Exit 0, 5/5, 605.80 s. Full default pytest: 1,643 passed, 0 failed, 126 skipped, 2 warnings in 590.46 s. Required suites and skip reasons checked, actionlint passed, locked packages and isolated wheel import verified. |
| python scripts/ci/run.py scientific | Exit 0, 5/5, 335.39 s. Six real Skills; numerical baseline, mapping, background, artifact and lineage checks. |
| python scripts/ci/run.py docker | Exit 0, 10/10, 244.96 s. Both services healthy, non-root, shared volume, concurrent children of one DEG, index consistency, private URL handling and restart persistence. |
| python examples/demo/run.py | Exit 0. A–E exercised real DESeq2/PCA/plots/enrichment, explicit local customization, current reuse, stale refusal and historical selection. |
| docker compose config -q; docker compose up -d --build --wait; docker compose ps; docker compose down | Exit 0. Both default host ports worked using an isolated project. API health reported the RC version. API /ready, /docs, UI / and /_stcore/health all returned 200. |

## 9. Secret, privacy and history review

The scan used the repository's path and credential rules, exact comparison to locally configured DeepSeek key material without displaying it, and checks for personal Windows paths and known private-data markers. Safe test sentinels and the example dotenv file are retained. History inspection covered two locally reachable commits, 257 distinct path/blob pairs, and both subsequent remote README revisions. These checks have finite pattern and history coverage.

**Historical disclosure:** earlier tracked Markdown, including the real-data report and README history, contains aggregate experimental results. The reachable public history still exposes earlier editions. No credential match was found in the inspected data. The RC public editions remove the identified private result material from the proposed tip. This does not remove past objects, copies or tags. Repository-owner review of the existing exposure is a separate publication prerequisite. No history rewrite or deletion took place.

## 10–12. Documentation, demo and remote CI

The export contains 414 files, including 72 Markdown documents. Four legacy links referenced local-only test output; their labels now identify the evidence as local. All 156 remaining relative file references were checked against the actual export inventory, including the README section anchor. The final edits after gate startup were documentation only. The three numerical comparisons (original DEG summary, enrichment counts and comparison in the demo) use freshly generated runs; the result IDs are independent of the published example.

Public example: 30 synthetic genes, 6 samples, 9 up and 9 down in the original DEG. Both enrichment Skills returned 0/0/1 significant terms for all/up/down. Replacing a count under the same filename returned STALE_DEG_RESULT for automatic reuse. The permitted historical plot retained its original parent; a new DEG produced a separate current parent. The explicit demo did not call DeepSeek. The guides describe deployment, backup, environment rebuild, analysis, result reuse and the unsupported public/multi-user case.

| Execution | State |
|---|---|
| Local Scientific CI | PASS |
| GitHub-hosted Fast CI | NOT RUN · authorization pending |
| GitHub self-hosted Scientific / Docker | NOT RUN · isolated runner provisioning and authorization pending |
| Live DeepSeek | NOT RUN · zero paid requests this round |

## 13. Local release artifacts

Built with locked setuptools 80.9.0. The wheel installed into a fresh environment with hash-checked Windows dependencies, passed pip check, launched the CLI and loaded the Registry and FastAPI app. The sdist rebuilt offline to a wheel with identical contents for all 144 members, including 10 R scripts. All regular archive members were checked. The full project source archive additionally contains the UI, Compose deployment and developer/demo files. Complete digests for distribution artifacts are supplied in the local SHA256SUMS file.

| File | SHA-256 |
|---|---|
| bulk_rnaseq_skill-0.8.0rc1-py3-none-any.whl | 3cdfdd3237e99e1c82af34b4a22abae2cb6a2a0dd8dd1161f1d00618d9ed140a |
| bulk_rnaseq_skill-0.8.0rc1.tar.gz | 3934c1db24d6b7026cf05beef0d88faf99f515908e250c2ee2298e8e4966c5c5 |

## 14–16. Risks and authorization checkpoints

- No open P0/P1 identified in the scoped local runtime or reviewed release tree. Publication gates remain open: historical disclosure, remote divergence, version, license, visibility and remote CI. Local functional tests cannot settle these decisions.
- P2: dependency advisory follow-up (certifi, Pillow, OpenTelemetry, setuptools), partial per-run implementation provenance, legacy paths, runner provisioning and network availability. The current worker accepts fixed trusted tasks. General untrusted execution and public multi-user SaaS are unsupported.
- **Checkpoint B:** obtain approval for the exact reviewed source, proposed candidate number, existing MIT license and public visibility. Resolve the owner's decision regarding earlier public aggregate results. Integrate the remote README changes against the latest remote head, then commit/push only to the agreed review branch and verify hosted CI. Self-hosted jobs require a separate trusted ephemeral runner without private research data, privileged PR execution or an over-permissioned token. Recheck branch and tag state before any write.
- **Checkpoint C:** after required remote validation, obtain separate explicit authorization for the final version/tag, target commit, GitHub Release and assets. A pre-release package and final package have different version identities and hashes. No PyPI, Docker Hub, tag or Release authorization is inferred.

## 17. Final release status

**Phase 10.6A: LOCAL RC PASS. RELEASE CANDIDATE READY / PUBLICATION PENDING.**

Remote hosted CI: NOT RUN. Remote self-hosted CI: NOT RUN. Final release: PENDING. The existing public preview is a separate version. No commit, push, tag, release, permission change or remote workflow dispatch occurred in this round.
