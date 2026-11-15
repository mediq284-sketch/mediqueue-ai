# MediQueue AI — Build Progress Log

| Phase | Status | What works |
|---|---|---|
| 1 Foundation | ✅ | Flask app factory, SQLite, env config (`MEDIQUEUE_DB`, `MEDIQUEUE_SECRET_KEY`, `PORT`), session auth, SPA served by Flask |
| 2 Database + roles | ✅ | All 8 entities + settings; constraints; `login_required(role)` on every route |
| 3 Appointments | ✅ | Slots from schedules, server validation, transactional booking, `PREFIX-NNN` numbers, cancellation |
| 4 Queue engine | ✅ | State machine, positions, check-in, call-next, completion → history + prediction scoring |
| 5 Staff dashboard | ✅ | Dashboard, today's appointments, live queue console, queue management, predictions, analytics |
| 6 Historical data | ✅ | Discrete-event OPD simulator (45 days) + LIVE records from real completions |
| 7 ML pipeline | ✅ | Validation, features, chronological split, RandomForest, MAE/RMSE/R², time-series CV, baseline, joblib versions |
| 8 Prediction service | ✅ | Active-model serving, re-prediction on queue change, stored with inputs |
| 9 Patient AI dashboard | ✅ | Live queue page (5 s polling), estimate with model version + timestamp + disclaimer |
| 10 Admin ML dashboard | ✅ | Overview, dataset, features, metrics, charts, train, versions, activate, monitoring |
| 11 Analytics | ✅ | 8 analytics from SQL (department/hour waits, served/day, queue length, status, rates, predicted vs actual) |
| 12 Security + testing | ✅ | 24 pytest tests pass; headless jsdom smoke test of every route for every role, no JS errors |
| 13 UI polish | ✅ | Healthcare SaaS styling, responsive sidebar, badges, loading/error states, disabled buttons during actions |

**Known limitations:** see PROJECT_DOCUMENTATION.md §10.
**Next steps if continued:** time-based evaluation split, background training job, WebSocket push, notifications.

## Audit pass — 7 Oct 2026
- Evaluation switched from random 80/20 to a chronological date-boundary split; CV switched to TimeSeriesSplit; v1 retrained (MAE 8.21, RMSE 12.08, R² 0.644).
- New validation rule: consultations < 1 min excluded (demo click-throughs were entering training data).
- Fixed: CV loop variable shadowed the baseline metrics (caught when training failed safely); settings accepted demo_mode values other than 0/1; admin appointment filters did not stack on mobile; Doctors-page "Book" did not preselect the doctor; hard-coded "(80%)/(20%)" labels replaced with real split counts and periods; ambiguous "Position" label.
- Added: 4 tests (28 total), `scripts/audit_live.py` (55 live checks), `docs/AUDIT_REPORT.md`.
