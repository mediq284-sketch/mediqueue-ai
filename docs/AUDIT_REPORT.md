# MediQueue AI — Audit & Verification Report (7 Oct 2026)

Method: source inspection, fresh start (`run.py` on an empty `instance/`), `pytest`, live HTTP audit
(`scripts/audit_live.py`), headless jsdom render of every route for every role, and a jsdom click-through of the
patient booking + staff console.

| Feature | Implemented | Verified | Notes |
|---|---|---|---|
| Server start, DB init, seed, model v1 train | Yes | Yes | Fresh run seeds 5,056 appts / 4,392 history rows and trains v1 in ≈25 s |
| Frontend (all 32 routes, public + 3 roles) | Yes | Yes | jsdom: every route renders, 0 JS errors; patient → /admin redirected |
| Auth (register/login/logout, hashing) | Yes | Yes | pytest + live; hash never returned |
| Backend RBAC | Yes | Yes | 401/403/404 checks live; GM staff blocked from ORT queue |
| Booking + validation | Yes | Yes | past date, off-day, outside schedule, inactive doctor, duplicate rejected |
| Concurrent double booking | Yes | Yes | 8-thread race → 1×201, 7×409 |
| Queue numbers PREFIX-NNN unique | Yes | Yes | DB unique index; 0 duplicates |
| Queue state machine | Yes | Yes | CALLED→COMPLETED rejected (409); no-show/cancel terminal |
| Completion → wait, consult, QueueHistory, scored prediction | Yes | Yes | live row + absolute error checked |
| RandomForest pipeline from QueueHistory | Yes | Yes | artifact type checked; metrics recomputed from artifact match to 3 dp |
| Chronological evaluation | Yes (new) | Yes | max(train date) < min(test date) |
| Baseline separate from ML | Yes | Yes | own function; baseline MAE recomputed |
| Versioning / single active / failed run safe | Yes | Yes | DB rejects second active; FAILED v-row leaves active untouched |
| Future prediction uses new active model | Yes | Yes | prediction row carries model_id → v2 |
| Analytics from SQL | Yes | Yes | no hard-coded values found |
| Foreign keys / CHECK constraints | Yes | Yes | enforced on the app's connections |
