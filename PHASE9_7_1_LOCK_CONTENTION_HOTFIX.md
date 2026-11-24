# Phase 9.7.1 workspace lock contention hotfix

Valid result-index reads avoid write locks. Repair rechecks under a bounded lock and saves atomically. Independent child runs preserve the same parent.

Public edition. The detailed historical evidence is retained in the local audit archive. See the [public demonstration](examples/demo/README.md) and the [release acceptance matrix](PHASE10_FINAL_RELEASE_REPORT.md).
