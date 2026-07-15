# ADR 0001 — Deduplicate readiness-probe failure logs

- **Status:** Proposed
- **Date:** 2026-07-15

## Context

The readiness probe (`fastapi_health_probes/router.py`) checks every configured
**Service Dependency** and logs a failure on every probe whenever a dependency is
unhealthy. Kubernetes calls readiness frequently (seconds), so a sustained
dependency outage floods the logs with near-identical lines — three log calls per
failed probe today, multiplied by probe frequency and outage length. This drowns
out real signal and inflates log-storage and alerting noise.

Successful `/health/*` access logs are already silenced by an access-log filter.
The remaining spam is the explicit failure logs inside the readiness handler.

## Decision

Introduce a small in-memory state machine that deduplicates readiness failure
logs per **(Service Dependency, Error Signature)**:

1. **First Occurrence** of a signature for a dependency is logged once at
   `ERROR` with full detail.
2. Subsequent probes with the **same signature** are **Suppressed** (counted,
   not logged).
3. A **Heartbeat** summary is emitted at `WARNING` every 10 minutes of
   continuous failure, so a long Outage never goes invisible.
4. A **Recovery Summary** is emitted at `INFO` when the dependency flips back to
   healthy.
5. When the **Error Signature changes** while still failing, a `WARNING`
   transition is logged and a new First Occurrence begins.

Supporting decisions:

- **Error Signature** form is `http:{status}` for non-200 responses and
  `{ExceptionClassName}` for transport errors. Raw volatile message text is not
  part of the key.
- The readiness loop now checks **all** dependencies each probe and returns 503
  at the end if any failed. (Previously it raised on the first failure,
  short-circuiting, which hid additional failing dependencies.)
- The router is migrated to **structlog** (`get_logger`), consistent with the
  rest of the library. This also fixes pre-existing stdlib `%`-format misuse.
- State is per-process in-memory, guarded by an `asyncio.Lock`; the heartbeat is
  evaluated lazily on each probe (no background task). Heartbeat cadence is a
  function parameter (`heartbeat_interval_s`, default 600s).

## Consequences

- **Positive:** Log volume during outages drops from O(probes × failures) to
  O(first occurrence + heartbeats + recovery). Per-dependency visibility is
  preserved (and improved, because all deps are now checked each probe).
- **Negative:** State is per-process, so each pod deduplicates independently;
  there is no cross-pod roll-up from this library. Restart resets state, which is
  acceptable (a restart is itself a new state of the world).
- **Risk:** Operators building alerts on the *count* of readiness failure log
  lines must now key off First Occurrence + Heartbeat rather than raw frequency.
