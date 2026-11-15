# MediQueue AI

**AI-Based Smart Hospital Queue Management and Waiting-Time Prediction System**
*Smart Queue Management. Predictable Waiting.* — B.Tech AIML mini project

Outpatient appointment booking, a digital queue engine, and a genuine scikit-learn
**Random Forest Regressor** that predicts each patient's waiting time, with model
training, evaluation (MAE / RMSE / R²), versioning, activation and live monitoring.

> This academic demonstration system uses synthetic data and is not intended for storing real patient medical records.

## Quick start

Requires **Python 3.10+** (tested on 3.12). No Node/npm needed.

```bash
cd mediqueue
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open **http://127.0.0.1:5000**. The first run generates the synthetic dataset and
trains + activates `WaitingTimeModel v1` (≈15 s). `python run.py --reseed` resets everything.

| Role | Email | Password |
|---|---|---|
| Patient (no appointment today — book one) | patient@mediqueue.demo | Demo@1234 |
| Patient 2 | patient2@mediqueue.demo | Demo@1234 |
| Staff (all departments) | staff@mediqueue.demo | Demo@1234 |
| Staff (General Medicine only) | gm.staff@mediqueue.demo | Demo@1234 |
| Admin | admin@mediqueue.demo | Demo@1234 |

Run the test suite: `python -m pytest -q` (28 workflow, security and ML tests).
Live end-to-end audit (with the server running): `python scripts/audit_live.py` (55 checks; writes demo data). Set `BASE_URL` if not on port 5000.

## Viva demo script (≈5 minutes)

1. **Patient** logs in → *Book Appointment* → General Medicine → Dr. Vikram Menon (works every day) → today → any slot → *Confirm*. A queue number such as `GM-028` is issued.
2. *My Queue* shows position, patients ahead, now-serving token and the **AI Estimated Waiting Time** with model version + timestamp. Keep this tab open (auto-refreshes every 5 s).
3. In a second browser / private window, **Staff** logs in → *Active Queue* → Dr. Vikram Menon → **CALL NEXT** / **Start service** / **Complete**. Watch the patient tab update and the estimate recalculate when the queue changes.
4. When the patient is completed, the actual wait is recorded, a `QueueHistory` row (source `LIVE`) is created and the prediction's absolute error is stored.
5. **Admin** → *ML Model*: overview, dataset, real MAE/RMSE/R² on the most recent unseen days (chronological split), baseline comparison, actual-vs-predicted, error distribution and feature importance → **TRAIN MODEL** → v2 is trained and evaluated → **ACTIVATE MODEL**.
6. The patient's next prediction shows `WaitingTimeModel v2`. *ML Model → Prediction monitoring* and *Staff → Predictions* compare predicted vs actual.

See `docs/PROJECT_DOCUMENTATION.md` for architecture, schema, ML methodology and limitations, and `docs/PROGRESS.md` for the phase log.

## Deploying to Render (free tier)

The repo includes `render.yaml` and `wsgi.py`.

1. Push the project to a GitHub repository.
2. On render.com: New → Blueprint (or Web Service) → pick the repo.
3. Render runs `pip install -r requirements.txt && python -m backend.seed` (creates the demo DB and trains model v1), then starts `gunicorn wsgi:app`.

Notes: the free tier sleeps after ~15 minutes idle (first request then takes ~1 minute) and its disk is not persistent, so the demo data resets to the freshly seeded state on every restart or redeploy. `TZ=Asia/Kolkata` keeps queue times in IST.
