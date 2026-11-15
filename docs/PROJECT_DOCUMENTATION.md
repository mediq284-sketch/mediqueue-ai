# MediQueue AI — Project Documentation

*AI-Based Smart Hospital Queue Management and Waiting-Time Prediction System* · B.Tech AIML mini project.
This document describes only what is actually implemented in this repository.

## 1. Objective
Reduce uncertainty in hospital outpatient (OPD) waiting by (a) digital appointment booking with queue tokens,
(b) a server-side queue engine that staff operate, and (c) a trained regression model that predicts each
waiting patient's waiting time from the hospital's own historical queue data.

## 2. Architecture

```
Browser (HTML/CSS/vanilla JS SPA, hash routing, 5 s polling)
        │  JSON over HTTP, HttpOnly session cookie
Flask REST API  ── auth / public / patient / staff / admin blueprints, role checks on every route
        │
Services ── booking.py (slots, validation, queue numbers) · queue_engine.py (state machine, history)
        │    features.py (shared feature engineering) · analytics.py
ML ──────── ml/pipeline.py (validate → split → RandomForest → metrics → version) · ml/prediction.py (serving)
        │
SQLite (instance/mediqueue.db)          joblib model artifacts (ml_models/WaitingTimeModel_vN.joblib)
```

| Layer | Technology | Folder |
|---|---|---|
| Frontend | HTML5, CSS, vanilla JavaScript (no build step), hand-written SVG charts | `frontend/` |
| Backend | Python 3, Flask 3 | `backend/`, `backend/api/`, `backend/services/` |
| Database | SQLite (relational, foreign keys, CHECK + unique constraints) | `backend/db.py` |
| ML | scikit-learn RandomForestRegressor, pandas, NumPy, joblib | `backend/ml/` |
| Tests | pytest + Flask test client | `tests/` |

Everything is portable source code with no proprietary builder. SQLite can be swapped for PostgreSQL by
porting `db.py` (SQL is standard apart from partial indexes, which PostgreSQL also supports).

## 3. Database schema (`backend/db.py`)
- **users** (id, full_name, email UNIQUE, phone, role PATIENT/STAFF/ADMIN, password_hash, department_id [staff scope], active, created_at, updated_at)
- **departments** (id, name, prefix UNIQUE e.g. GM, description, active, created_at)
- **doctors** (id, name, department_id, specialization, consultation_duration_average, working_days "0..6", start_time, end_time, active, created_at)
- **appointments** (id, patient_id, doctor_id, department_id, appointment_date, appointment_time, booking_timestamp, status BOOKED/CONFIRMED/COMPLETED/CANCELLED/NO_SHOW, queue_number, created_at, updated_at)
  - partial UNIQUE (doctor, date, time) for active bookings → no double booking
  - partial UNIQUE (patient, doctor, date) for active bookings → no duplicate booking
- **queue_entries** (id, appointment_id, patient_id, doctor_id, department_id, queue_date, queue_number, queue_seq, position, status WAITING/CALLED/SERVING/COMPLETED/CANCELLED/NO_SHOW, joined_at, called_at, service_started_at, completed_at, actual_waiting_time, consultation_duration, patients_ahead_at_join, queue_length_at_join)
  - UNIQUE (department_id, queue_date, queue_number)
- **queue_history** — ML dataset (all spec fields + `consultation_duration_average` + `source` LIVE/SYNTHETIC/SYNTHETIC_INVALID)
- **ml_models** (id, model_name, model_type, version UNIQUE, training_date, dataset_size, training_records, testing_records, feature_count, mae, rmse, r2_score, training_status TRAINING/TRAINED/FAILED, active, artifact_path, model_metadata JSON) — partial UNIQUE index guarantees **only one active model**
- **ml_predictions** (id, appointment_id, model_id, prediction_timestamp, predicted_waiting_time, baseline_waiting_time, prediction_difference, input_features JSON, actual_waiting_time, absolute_error)
- **settings** (key, value): slot length, peak hours, demo mode, re-prediction interval, booking horizon, cancellation cut-off

## 4. Appointments and queue numbers
Department → doctor → date → available slot → confirm. Server validates: doctor and department active, date
not in the past and within the booking horizon, doctor works that weekday, slot inside the schedule and free,
no duplicate booking. The appointment, queue number and queue entry are created inside one
`BEGIN IMMEDIATE` transaction; the DB unique indexes are the final guard against races.
Queue number = `PREFIX-NNN`, sequence per department per day (GM-001, CAR-004).
**Demo mode** (Settings) keeps earlier slots of *today* bookable so the live workflow can be shown at any hour.

## 5. Queue logic (`services/queue_engine.py`)
```
WAITING → CALLED → SERVING → COMPLETED
WAITING / CALLED → NO_SHOW | CANCELLED
```
Any other transition returns HTTP 409. A doctor can have at most one CALLED and one SERVING patient.
Queue order is token order (queue_seq); only checked-in patients (joined_at set) can be called — same-day
bookings join immediately, future-day bookings check in on arrival (patient button or staff "Mark arrived").
On completion: waiting time = service_started_at − joined_at; consultation = completed_at − service_started_at;
positions recomputed; a `queue_history` row (source LIVE) is written; every prediction for that appointment is
updated with the actual wait and absolute error.
"Real-time" is implemented by polling (patient 5 s, staff 6 s); no WebSocket server.

## 6. Machine-learning methodology
**Task:** supervised regression. **Target:** `actual_waiting_time` (minutes, check-in → consultation start).
**Algorithm:** `RandomForestRegressor(n_estimators=200, min_samples_leaf=3, max_features=0.6, random_state=42)`.

**Features (13)** — computed by the same code (`features.py`) for history records and live predictions:
`department_id`, `doctor_id` (one-hot encoded), `day_of_week`, `appointment_hour`, `appointment_minute`,
`patients_ahead`, `queue_length`, `consultation_duration_average`, `peak_hour`, `previous_average_wait`
(doctor's last 5 waits today), `doctor_average_wait`, `department_average_wait` (from history strictly before
that date), `patient_arrival_delay` (check-in − slot time, ±2 h treated as unknown = 0).

**Leakage prevention:** the target and the patient's own `consultation_duration` are never inputs (asserted in
code and tests); historical averages only use earlier dates; no personal data (name, email, phone, patient id)
is ever a feature.

**Exact architecture (as implemented)**
```
Pipeline([
  ("prep", ColumnTransformer([
       ("cat", OneHotEncoder(handle_unknown="ignore"), ["department_id", "doctor_id"]),
       ("num", "passthrough", [11 numeric features])])),
  ("rf",   RandomForestRegressor(n_estimators=200, min_samples_leaf=3, max_features=0.6, random_state=42))
])   -> persisted with joblib as ml_models/WaitingTimeModel_v{N}.joblib
```

**Pipeline** (`ml/pipeline.py`):
1. Load every `queue_history` row.
2. Validate; each excluded record is counted under the first rule it breaks: missing feature/target; invalid value
   (negative or >480 min wait, impossible counts, out-of-range hour/minute/day, non-positive average consult);
   implausible consultation (< 1 min — accidental or test click-through completions); exact duplicate.
3. **Chronological split on a date boundary**: records sorted by date; the oldest whole days holding ≈80% of the
   records are the training set, the most recent days (≈20%) are the unseen test set. Every training record is
   strictly earlier than every test record.
4. Fit the pipeline on the training period only. The evaluated model is exactly the model that is saved and deployed.
5. Predict the test period → MAE, RMSE, R² (scikit-learn metrics on real predictions; predictions clipped at 0).
6. Time-series cross-validation inside the training period (`TimeSeriesSplit`, 5 forward-chaining folds).
7. Non-ML baseline on the same test rows; improvement = (baseline MAE − model MAE) / baseline MAE.
8. Feature importance from the trained forest's `feature_importances_`, one-hot columns summed per feature.
9. Save artifact, write the `ml_models` row (version, counts, metrics, metadata incl. split periods).
Failures are stored as FAILED and never change the active model; only TRAINED models can be activated.

*Why chronological?* A random split lets the model train on days that come after some test days, which
over-states accuracy for a system that always predicts the future. The time-based split answers the real question:
"trained on the past, how well does it predict days it has never seen?" It produced slightly worse but honest
metrics (seeded v1: MAE 8.21 vs 7.98 with the old random split; R² 0.644 vs 0.758). Only the chronological metrics
are stored and displayed.

**Baseline:** `patients_ahead × doctor's average consultation duration`, computed separately in
`features.baseline_estimate` and stored next to each prediction for comparison only. It is never shown to patients.

**Serving** (`ml/prediction.py`): loads the artifact of the single active `ml_models` row, builds the same 13
features from current DB state, calls `predict`, stores the prediction with its input JSON, baseline and model id.
A new prediction is made when patients-ahead or queue length changes, when the active model changes, or after
`repredict_minutes`. No randomness, no LLM, no hard-coded values.

### Results of the seeded model (run on 7 Oct 2026; values regenerate on reseed)
| WaitingTimeModel v1 (trained on records before the deployment date) | MAE (min) | RMSE (min) | R² |
|---|---|---|---|
| Random Forest — chronological test set (884 records, 25 Sep → 4 Oct) | 8.21 | 12.08 | 0.644 |
| Baseline — same test set | 10.79 | 16.37 | — |
| Random Forest — training set (3,224 records, 23 Aug → 24 Sep) | 4.64 | — | — |

Improvement over baseline 23.9 % · time-series CV MAE 8.90 ± 0.73 · 4,108 valid records (16 excluded).
Top features: patients_ahead 0.44, queue_length 0.11, patient_arrival_delay 0.10, previous_average_wait 0.07.
A v2 retrained during the live audit (4,376 records, test 28 Sep → 7 Oct): MAE 8.71, RMSE 12.39, R² 0.636,
baseline MAE 11.33 / RMSE 16.99, improvement 23.2 %.

## 7. Synthetic data (`backend/seed.py`)
6 departments, 11 doctors, 2 staff + 1 admin, 82 synthetic patients. 45 days of OPD sessions are produced by a
**discrete-event simulation**: slot bookings (busier on Mondays and peak hours), cancellations ≈6 %, no-shows ≈7 %,
arrival offsets ~N(−18, 14) min, doctor start delay, gamma-distributed consultation times slower at peak hours,
4 % interruptions. Waiting times emerge from the simulated queue rather than a formula.
Waiting time is never written by a formula: it is `service start − arrival` in the simulated queue. Evidence
from the audit: patients with the same doctor and the same number of patients ahead still differ by a median
standard deviation of ≈16 min; a linear model reaches R² 0.50 and a mean predictor MAE 17.0 on the test period,
while the Random Forest reaches R² 0.64 / MAE 8.2 — real, partly non-linear structure plus irreducible noise.
Model v1 is trained on days −45…−3 only; days −2, −1 and the morning of "today" are simulated with v1 predicting at
each arrival, so monitoring data is genuinely out-of-sample. Ten corrupted rows and four duplicates
(source SYNTHETIC_INVALID) are injected to demonstrate validation. No real patient data is used.

## 8. Security
Passwords hashed (Werkzeug PBKDF2/scrypt); HttpOnly, SameSite=Lax signed session cookie; secret key from
`MEDIQUEUE_SECRET_KEY` or a generated `instance/secret_key` (never sent to the browser). Every API route
declares its allowed roles server-side; patient queries are always filtered by the session's user id (another
patient's appointment returns 404); department-scoped staff get 403 on other departments; public registration
can only create PATIENT accounts. State-changing calls must be JSON (blocks cross-site form posts). Errors return
safe messages; stack traces are logged server-side only.

## 9. Testing
- `python -m pytest -q` — **28 tests** (`tests/test_workflows.py`): auth, role escalation, role restrictions, valid /
  invalid / duplicate / double booking, inactive doctor, cancellation, queue-number uniqueness, cross-patient
  isolation, full call → start → complete flow (history + prediction scoring), invalid transitions, no-show/cancel,
  position updates, data-quality rejection, implausible-record exclusion, leakage guard, chronological split without
  future leakage, metrics recomputed from the saved artifact, training/versioning/activation, new model used for
  future predictions, failed training keeps the active model, monitoring, no-active-model message, analytics,
  admin CRUD/settings validation, CSV export.
- `python scripts/audit_live.py` — **55 live checks** against a running server (start with `python run.py`): real
  HTTP workflow patient → staff → admin, prediction re-computed from the artifact, MAE/RMSE/R² re-computed from
  QueueHistory + artifact, single-active-model DB constraint, foreign keys, backend authorization, and an 8-thread
  race for one slot (exactly one booking succeeds). Note: it writes demo data into the database.

## 10. Limitations
- SQLite + Flask development server: suitable for a demo / single hospital, not production load.
- Real-time updates are polling, not push.
- Training runs synchronously in the request (≈5–10 s for ~4k rows); a job queue would be needed at scale.
- The deployed model is trained on the training period only (so its metrics are exact); it does not also learn from the most recent ~20% of days until the next retrain.
- Synthetic data: model accuracy reflects the simulator, not a real hospital.
- Live demo completions done in seconds give near-zero waits; consultations under 1 minute are excluded from training, but such predictions still count in live monitoring MAE.
- No email/SMS notifications; no password reset flow.

## 11. Future scope
SMS/WhatsApp notifications, mobile app, multi-branch support, staff allocation prediction, arrival forecasting,
queue optimisation, HIS/EMR integration, time-series forecasting, gradient-boosting comparison and hyper-parameter search.
