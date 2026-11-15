"""Feature engineering shared by (1) QueueHistory creation, (2) the synthetic
simulator and (3) live prediction, so training and serving use identical logic.

Leakage rule: every feature is computable *before* the patient is served.
The target `actual_waiting_time` and the patient's own `consultation_duration`
are never inputs.
"""
from datetime import datetime

CATEGORICAL_FEATURES = ["department_id", "doctor_id"]
NUMERIC_FEATURES = [
    "day_of_week",
    "appointment_hour",
    "appointment_minute",
    "patients_ahead",
    "queue_length",
    "consultation_duration_average",
    "peak_hour",
    "previous_average_wait",
    "doctor_average_wait",
    "department_average_wait",
    "patient_arrival_delay",
]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES
TARGET = "actual_waiting_time"
FORBIDDEN_FEATURES = {TARGET, "consultation_duration", "patient_id", "full_name", "email", "phone"}
assert not (set(FEATURES) & FORBIDDEN_FEATURES), "data leakage / PII in feature list"

DEFAULT_WAIT = 15.0          # cold-start fallback when no history exists yet (minutes)
MAX_VALID_WAIT = 480.0       # waits above 8h are treated as invalid records
DELAY_UNKNOWN_BEYOND = 120.0 # |arrival delay| > 2h => schedule mismatch, treated as 0
VALID_WAIT_SQL = "actual_waiting_time IS NOT NULL AND actual_waiting_time >= 0 AND actual_waiting_time <= 480 AND source != 'SYNTHETIC_INVALID'"


def peak_hours(db):
    row = db.execute("SELECT value FROM settings WHERE key='peak_hours'").fetchone()
    raw = row["value"] if row else "10,11,12,17"
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}


def arrival_delay(scheduled_dt, joined_dt):
    if not scheduled_dt or not joined_dt:
        return 0.0
    d = (joined_dt - scheduled_dt).total_seconds() / 60.0
    return 0.0 if abs(d) > DELAY_UNKNOWN_BEYOND else round(d, 2)


def historical_averages(db, doctor_id, department_id, before_date):
    """Doctor / department / global average wait from history strictly BEFORE the given date."""
    q = f"SELECT AVG(actual_waiting_time) a FROM queue_history WHERE {VALID_WAIT_SQL} AND date < ?"
    glob = db.execute(q, (before_date,)).fetchone()["a"]
    doc = db.execute(q + " AND doctor_id = ?", (before_date, doctor_id)).fetchone()["a"]
    dep = db.execute(q + " AND department_id = ?", (before_date, department_id)).fetchone()["a"]
    fallback = glob if glob is not None else DEFAULT_WAIT
    return (round(doc if doc is not None else (dep if dep is not None else fallback), 2),
            round(dep if dep is not None else fallback, 2))


def previous_average_wait(db, doctor_id, date, before_ts, fallback, n=5):
    """Mean wait of the doctor's last n patients already served today (before `before_ts`)."""
    r = db.execute(
        """SELECT AVG(w) a, COUNT(*) c FROM (
               SELECT actual_waiting_time w FROM queue_entries
               WHERE doctor_id = ? AND queue_date = ? AND actual_waiting_time IS NOT NULL
                 AND service_started_at < ?
               ORDER BY service_started_at DESC LIMIT ?)""",
        (doctor_id, date, before_ts, n)).fetchone()
    return round(r["a"], 2) if r["c"] else fallback


def build_features(*, department_id, doctor_id, date, appointment_time, patients_ahead, queue_length,
                   consultation_duration_average, peak_set, previous_avg, doctor_avg, department_avg, delay):
    d = datetime.strptime(date, "%Y-%m-%d")
    hh, mm = (int(x) for x in appointment_time.split(":")[:2])
    return {
        "department_id": int(department_id),
        "doctor_id": int(doctor_id),
        "day_of_week": d.weekday(),
        "appointment_hour": hh,
        "appointment_minute": mm,
        "patients_ahead": int(patients_ahead),
        "queue_length": int(queue_length),
        "consultation_duration_average": float(consultation_duration_average),
        "peak_hour": 1 if hh in peak_set else 0,
        "previous_average_wait": float(previous_avg),
        "doctor_average_wait": float(doctor_avg),
        "department_average_wait": float(department_avg),
        "patient_arrival_delay": float(delay),
    }


def baseline_estimate(features):
    """NON-ML baseline used only for academic comparison:
    patients ahead x doctor's average consultation duration."""
    return round(max(0.0, features["patients_ahead"] * features["consultation_duration_average"]), 1)
