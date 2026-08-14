# Legacy HomeFit → BuiltHere household migration

This is a one-time cutover, not a two-way synchronization design. V1 (CT115)
and V2 (CT120) must never write to the same SQLite database.

## Baseline audit — 2026-08-14

Both databases have the same 26 application tables and the same three user IDs.
The comparison used SQLite read-only connections.

| Profile | Dataset | V1 | V2 | Delta |
|---|---|---:|---:|---:|
| Brent | workouts | 26 | 26 | 0 |
| Brent | weights | 68 | 68 | 0 |
| Brent | daily metrics | 540 | 540 | 0 |
| Brent | external workouts | 103 | 103 | 0 |
| Brent | food entries | 187 | 187 | 0 |
| Brent | sleep entries | 59 | 59 | 0 |
| Brent | weekly plans | 1 | 1 | 0 |
| Shay | workouts | 31 | 31 | 0 |
| Shay | weights | 26 | 26 | 0 |
| Shay | daily metrics | 536 | 528 | +8 in V1 |
| Shay | external workouts | 34 | 34 | 0 |
| Shay | food entries | 186 | 185 | +1 in V1 |
| Shay | sleep entries | 0 | 0 | 0 |
| Shay | weekly plans | 1 | 1 | 0 |
| Kelsie | workouts | 1 | 1 | 0 |
| Kelsie | weights | 1 | 1 | 0 |
| Kelsie | daily metrics | 0 | 0 | 0 |
| Kelsie | external workouts | 0 | 0 | 0 |
| Kelsie | food entries | 0 | 0 | 0 |
| Kelsie | sleep entries | 0 | 0 | 0 |
| Kelsie | weekly plans | 0 | 0 | 0 |

Global counts also match for chat, Coach memory/plans/digests, API keys, APNs
tokens, nutrition goals, readiness, rest overrides, and pauses. V1 is ahead only
for the nine Shay records above.

## Pre-cutover acceptance

Before freezing V1, each person should open V2 and verify:

- their name/PIN and Profile values;
- current/goal weight, fitness level, limitations, and equipment;
- weekly plan and today's Start behavior;
- workout and weight history;
- Health and Food history;
- theme choice and any integration expected to remain enabled.

Use a disposable completed workout to validate the full write path for each test
profile. Verify it appears in Health/History, then remove the disposable record
before the final snapshot. Do not use a real workout as migration test data.

## Final cutover sequence

1. Announce a short V1 maintenance window and stop new workouts/food entries.
2. Take an online V1 SQLite backup, then stop V1 to freeze further writes.
3. Take a separate V2 SQLite backup before replacing anything.
4. Copy the frozen V1 snapshot to a staging path on CT120; never copy over the
   live V2 database directly.
5. Run `PRAGMA integrity_check`, confirm schema version, and reproduce this row
   count comparison against the staged snapshot.
6. Stop `homefit.service` on CT120, atomically replace its database from the
   validated stage file, set `homefit:homefit` ownership and mode `0640`, and
   start the service.
7. Require `/healthz` to return HTTP 200 and `status=ok`.
8. Re-run per-profile row counts and perform a browser login/dashboard/history
   smoke test for all three profiles.
9. Keep V1 stopped but intact during the rollback window. Do not delete CT115.

V2 secrets remain V2-owned. Do not copy V1's Flask session secret or environment
file. Database-held device/API tokens should be deliberately retained or rotated
as part of the cutover decision.

## Rollback

If V2 fails integrity, readiness, login, or history verification:

1. stop CT120's `homefit.service`;
2. restore the pre-cutover V2 backup;
3. start the service and verify `/healthz`;
4. restart V1 and direct the household back to V1;
5. preserve the failed staged database for diagnosis.

Rollback is safe only while V2 has not accepted real post-cutover writes. Once
real data is entered in V2, decide explicitly how to reconcile it before moving
backward.
