# Phase 10.5 · Documentation and demonstration

Local acceptance, 2026-11-20. Public synthetic inputs only. Supported environment: Windows Docker Desktop with Linux containers, trusted local user. Phase 10.4 is the last complete release-hardening gate. GitHub-hosted and self-hosted Actions remain unverified.

## 1. Documentation inventory

| Document | Audience | Existing content | Missing content | Action |
|---|---|---|---|---|
| Root README | First-time users, reviewers | Chinese phase history, CLI examples, detailed API contracts | Short current entry point and demonstrated results | Replaced with an English overview, visual result, quick start and Chinese navigation. Module references retain detailed contracts. |
| container/README.md | Local operators | Compose deployment, ports, troubleshooting, backups and restore | Clear WSL 2 and isolated demo entry | Updated prerequisites and linked the demonstration. |
| docs/ | Researchers, contributors | Phase-specific contracts, import formats, sample form, Results and customization notes | User path, editable architecture and development map | Added task-oriented guides linking existing references. |
| pyproject, manifests, live Registry and OpenAPI | Developers | Package metadata, callable entries, validated request schemas | Explain declaration vs runnable capability | Checked the six executors, reserved GSEA and API operations against the local backend. |
| Phase 9/10 reports, CI and locks | Maintainers | Detailed dated validation and version records | Translate evidence into practical reproducibility limits | Linked canonical locks, CI entry points, guard and audit. |
| Public fixtures, container smoke | Demo users | Small public counts, metadata, mappings and deterministic real-R probes | Single replay command, version transition, public outputs | Reused the existing data and smoke; added a dedicated orchestration script and version demonstration. |

## 2–5. Homepage, user guide, developer guide and diagrams

The [homepage](README.md) introduces downstream analysis at the count-matrix boundary. The [Chinese guide](docs/user-guide.zh.md) walks through data entry, group direction, interpretation, downloads and reusing runs. The [developer guide](docs/developer-guide.md) follows a plot change from its schema through Python/R, lineage and tests. The [architecture source](docs/architecture.md) contains five diagrams: entry points, validated DAG execution, source eligibility, the deployment and the trusted execution boundary. All five rendered as SVG with Mermaid 11.12.0 in a local Edge session.

## 6–7. Reproducibility and deployment

The [reproducibility guide](docs/reproducibility.md) distinguishes implemented input checks, partial effective-parameter and per-run implementation records, and future work. The [container guide](container/README.md) is the single detailed operations reference. The normal deployment uses 127.0.0.1:8000 and 127.0.0.1:8501. Demo runs use distinct ports and volumes to avoid collisions.

## 8–10. Data, workflows and demonstration artifacts

The four input hashes matched the frozen scientific reference. Counts have 30 synthetic gene IDs; metadata has three control and three treatment samples. Demo term names explicitly identify synthetic annotations. The [demo guide](examples/demo/README.md) includes a four-stop presentation script, input links, a real application screenshot, the original Volcano figure and [structured observed evidence](examples/demo/observed.json).

| Case | Result | Observed |
|---|---|---|
| A | PASS | PCA returned 98.4012% / 1.1159%; the DEG workflow returned 9 up and 9 down with 18 usable heatmap genes. Volcano and Heatmap use the same DEG parent. |
| B | PASS | Real clusterProfiler GO and KEGG ran all/up/down. Effective universe 30; significant counts 0/0/1 for each Skill. Assertions verified mapping, targets and background. |
| C | PASS | One additional Volcano, one original DEG, verified parent and unchanged source checksum before replacing inputs. |
| D | PASS | The same filename with one changed count invalidated automatic reuse. HTTP 409, explicit historical child of the original DEG, then successful independent DEG and current child of the replacement run. |
| E | PASS | Existing customization engine applied title and top-five labels; new collection and source checksum verified. The request used fixed local parameters. |

The report's observed snapshot used the actual fixture and live backend. The final demo helper also sets the project description to explicitly identify the synthetic data; that metadata update was checked in the demo project. No research workspace was opened for the demonstration.

## 11–13. Validation, privacy and evidence

- Ran `py -3.13 examples/demo/run.py --keep-running` against a new Compose stack. Live OpenAPI exposed 12 paths including health/readiness; the catalog distinguishes callable Skills from the reserved manifest. Queried the actual workflow, Skill and customization route definitions.
- In an isolated browser session, opened PublicDemo, selected Results and downloaded its current Volcano PNG (51,338 bytes, valid PNG signature). The screenshot is genuine. The separate README plot is the original workflow artifact, before input replacement.
- Targeted default pytest for Volcano, Heatmap, customization, frozen API contracts, workspace UI, input-version guard and release hardening: **155 passed, 0 failed, 0 skipped, 2 warnings**. Full Fast CI was not repeated for this documentation change; prior full evidence is in the [hardening report](PHASE10_SECURITY_RELEASE_HARDENING_REPORT.md).
- Real DeepSeek prompt replay: **NOT RUN**. No provider key was supplied to the demo. The sample prompts illustrate supported contracts and carry that qualification in the user guide.
- The published PNGs, JSON and guides use the synthetic project only. Local diagnostics and workspace data remain outside the public demo.
- Replayed the final helper with `py -3.13 examples/demo/run.py` in a second isolated volume. Both modes exited 0. DEG counts and all six enrichment comparisons matched, and the second execution produced independent run IDs. The non-interactive replay stopped its services and retained its volume.
- Ran the homepage commands with an isolated Compose project/volume and the documented default ports. Config validation and image build passed, both services became healthy, and HTTP checks of /docs, /ready, the Streamlit root and /_stcore/health all returned 200. Stopped this quick-start stack with `docker compose down`.
- Checked 181 relative file links in 70 publishable Markdown files and the linked README heading. No broken targets. Secret and private-file checks passed for release candidates. The two public PNGs contain no text metadata. The demonstration screenshot was inspected for identifying content.

## 14. Changed files

| Files | Purpose |
|---|---|
| README.md, container/README.md | Portfolio entry, quick start, deployment navigation |
| docs/user-guide.zh.md, docs/developer-guide.md | Researcher tutorial and contributor reference |
| docs/architecture.md, docs/reproducibility.md | Five editable diagrams and evidence boundaries |
| examples/demo/run.py, examples/demo/version_guard.py | Isolated Compose runner and scoped version-change demonstration |
| examples/demo/README.md, examples/demo/observed.json | Replay instructions, walkthrough and actual run references |
| docs/demo-volcano.png, docs/demo-workspace.png | Public R-generated figure and verified UI screenshot |
| This report | Coverage matrix, evidence and verdict |

## 15. Remaining P0 / P1 / P2

No known open P0/P1 in the documentation/demo scope. P2: recheck prompt wording on an authorized provider run and verify remote CI during the release phase. A replay produces fresh run IDs and a new volume. The UI sidebar still labels the original chat mode and the header carries a historical UI phase label; the Results table and run records provide the authoritative execution evidence.

## 16. Phase 10.5 verdict

**PASS** for local documentation and demonstration. The deterministic A–E run is reproducible with public files. Local evidence supports proceeding to Phase 10.6 when authorized. Remote CI and formal release remain pending.
