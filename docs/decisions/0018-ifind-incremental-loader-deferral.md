# ADR 0018 — iFind Incremental Loader Deferral

- **Status**: Accepted
- **Date**: 2026-05-17
- **Deciders**: Project lead
- **Supersedes (partial)**: ADR 0005 §1 "iFind 补漏" deliverable
- **Phase**: R3 (post-v1.0 hardening)

## Context

ADR 0005 §1 declared two iFind code paths as Phase 2 deliverables:

- `HistoricalIPOLoader._upsert_from_ifind` — backfill IPOs newer than the
  SQLite snapshot cutoff
- `ComparablePoolBuilder._ingest` — fetch industry comparable pool from iFind

The 2026-05-17 full-codebase review (docs/PLAN_post_v1.0.md §1) found
both methods were `return (0, 0)` / `return 0` stubs that emit only a
`log.warning`. They were nonetheless marked DONE in ADR 0005 §Progress,
in violation of CLAUDE.md «严禁跳过测试» and «严禁输出无 citation 的
Finding»-style hard constraints applied to data pipelines.

The reason the stubs persisted is structural, not implementation
neglect:

1. **iFindPy SDK is not on PyPI**. Building real upsert paths requires
   thinkive-provisioned credentials and SDK bundle, neither of which
   the CI runner has.
2. **iFind response shapes are not public**. The actual JSON / dict
   structure of `THS_DR("ipo_dynamic")` and similar endpoints is only
   available via vendor docs that the project doesn't track.
3. **Tests would have to mock the SDK entirely** — which provides
   coverage of "our parsing logic" but not "we actually parse iFind
   output correctly". The latter requires a live integration test
   environment that doesn't exist in repo.

The Phase 2 PG seed from `scripts/migrate_sqlite_to_pg.py` (399 IPOs,
1,314 cornerstone records as of 2026-05) covers the canonical historical
window. The incremental loader is needed only for IPOs newer than the
snapshot cutoff — a small, schedulable batch.

## Decision

**Defer the iFind incremental loader to a dedicated future phase
(R-iFind, scheduled post-v1.1).** Until then:

- Both stub methods `raise NotImplementedError` with a pointer to this
  ADR. Calling them is now loud-fail.
- ADR 0005 §Progress Phase 2 item "iFind 仅作补漏路径" is reclassified
  from ✅ DONE to ⚠️ DEFERRED.
- The audit-only path (`HistoricalIPOLoader(ifind=None)` and
  `ComparablePoolBuilder(ifind=None)`) remains the supported usage:
  it returns counts of existing PG rows without attempting to fetch.
- New IPOs that arrive between the SQLite snapshot cutoff and the
  R-iFind phase must be hand-loaded via SQL inserts or by re-running
  `scripts/migrate_sqlite_to_pg.py` against an updated source DB.

## Consequences

### Positive

- No more silent "0 new IPOs" returned by a caller that thought they
  wired iFind. The failure surface is named and explicit.
- The reclassification (DONE → DEFERRED) in ADR 0005 is now honest
  about where the project actually stands.
- Future implementation can land cleanly: tests are already written
  against the new NotImplementedError contract; the only change
  needed is the parse implementation + replacement tests.

### Negative

- Phase 7.5 scheduler (`scripts/run_high_freq_state_check.py`) cannot
  call into `HistoricalIPOLoader.load_listed_between(ifind=...)` until
  R-iFind lands. Practically: incremental IPO discovery between
  snapshot cutoff and now must be manual.
- Comparable-pool freshness for Phase 4 valuation depends on whatever
  the SQLite snapshot contained; new industries / new comparables
  won't appear until R-iFind.

### Neutral

- The structural reason for the deferral (SDK not on PyPI + no public
  response shape) is unchanged; this ADR documents it explicitly so
  future contributors don't try to "just implement it" without first
  obtaining vendor SDK + credentials + sample responses.

## Verification

- `tests/unit/data/test_builders_ifind_stub.py` pins the
  NotImplementedError contract.
- `docs/PLAN_post_v1.0.md` §16 Progress board tracks R-iFind as a
  future phase (no R3 task slot — that's this ADR's whole point).

## Links

- Supersedes (partial): [ADR 0005](0005-nacs-legacy-asset-migration.md) §1
- Plan: [docs/PLAN_post_v1.0.md](../PLAN_post_v1.0.md) §5 R3-1
- Implementation pointers:
  - `src/hk_ipo_agent/data/builders/historical_ipo_loader.py:_upsert_from_ifind`
  - `src/hk_ipo_agent/data/builders/comparable_pool_builder.py:_ingest`
