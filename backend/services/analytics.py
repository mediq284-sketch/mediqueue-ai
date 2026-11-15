"""Operational analytics computed directly from the database (nothing hard-coded)."""
from datetime import timedelta

from .. import clock
from ..db import rows, one
from .features import VALID_WAIT_SQL


def _scope(dept, alias=""):
    p = f"{alias}." if alias else ""
    return (f" AND {p}department_id = ?", [dept]) if dept else ("", [])


def compute(db, days=30, dept=None):
    since = (clock.now().date() - timedelta(days=days)).isoformat()
    s, sp = _scope(dept)
    sa, _ = _scope(dept, "h")
    out = {"period_days": days, "since": since}
    out["avg_wait_by_department"] = rows(db.execute(
        f"""SELECT d.name AS label, ROUND(AVG(h.actual_waiting_time),1) AS value, COUNT(*) AS n
            FROM queue_history h JOIN departments d ON d.id=h.department_id
            WHERE {VALID_WAIT_SQL.replace('actual_waiting_time','h.actual_waiting_time').replace('source','h.source')}
              AND h.date >= ? {sa} GROUP BY d.id ORDER BY value DESC""", [since] + sp))
    out["avg_wait_by_hour"] = rows(db.execute(
        f"""SELECT appointment_hour AS label, ROUND(AVG(actual_waiting_time),1) AS value, COUNT(*) AS n
            FROM queue_history WHERE {VALID_WAIT_SQL} AND date >= ? {s} GROUP BY appointment_hour ORDER BY appointment_hour""",
        [since] + sp))
    out["queue_length_by_hour"] = rows(db.execute(
        f"""SELECT appointment_hour AS label, ROUND(AVG(queue_length),1) AS value FROM queue_history
            WHERE {VALID_WAIT_SQL} AND date >= ? {s} GROUP BY appointment_hour ORDER BY appointment_hour""", [since] + sp))
    out["patients_served_per_day"] = rows(db.execute(
        f"""SELECT appointment_date AS label, COUNT(*) AS value FROM appointments
            WHERE status='COMPLETED' AND appointment_date >= ? {s} GROUP BY appointment_date ORDER BY appointment_date""",
        [since] + sp))
    dist = rows(db.execute(
        f"""SELECT status AS label, COUNT(*) AS value FROM appointments WHERE appointment_date >= ? {s}
            GROUP BY status ORDER BY value DESC""", [since] + sp))
    out["appointment_status_distribution"] = dist
    tot = sum(r["value"] for r in dist)
    get = lambda k: next((r["value"] for r in dist if r["label"] == k), 0)
    out["totals"] = {
        "appointments": tot,
        "completed": get("COMPLETED"),
        "no_show_rate": round(get("NO_SHOW") / tot * 100, 1) if tot else 0.0,
        "cancellation_rate": round(get("CANCELLED") / tot * 100, 1) if tot else 0.0,
        "avg_wait": one(db.execute(f"SELECT ROUND(AVG(actual_waiting_time),1) a FROM queue_history WHERE {VALID_WAIT_SQL} AND date >= ? {s}",
                                   [since] + sp))["a"],
    }
    pj, pjp = (" AND a.department_id = ?", [dept]) if dept else ("", [])
    out["predicted_vs_actual"] = rows(db.execute(
        f"""SELECT p.id, p.predicted_waiting_time AS predicted, p.actual_waiting_time AS actual, p.absolute_error,
                   p.baseline_waiting_time AS baseline
            FROM ml_predictions p JOIN appointments a ON a.id=p.appointment_id
            WHERE p.actual_waiting_time IS NOT NULL
              AND p.id IN (SELECT MAX(id) FROM ml_predictions GROUP BY appointment_id) {pj}
            ORDER BY p.id DESC LIMIT 300""", pjp))
    return out


def prediction_monitoring(db, dept=None, limit=50):
    pj, pjp = (" AND a.department_id = ?", [dept]) if dept else ("", [])
    recent = rows(db.execute(
        f"""SELECT p.id, p.appointment_id, a.queue_number, d.name AS doctor_name, dep.name AS department_name,
                   p.prediction_timestamp, p.predicted_waiting_time, p.baseline_waiting_time, p.actual_waiting_time,
                   p.absolute_error, m.version AS model_version
            FROM ml_predictions p JOIN appointments a ON a.id=p.appointment_id
            JOIN doctors d ON d.id=a.doctor_id JOIN departments dep ON dep.id=a.department_id
            JOIN ml_models m ON m.id=p.model_id
            WHERE p.id IN (SELECT MAX(id) FROM ml_predictions GROUP BY appointment_id) {pj}
            ORDER BY p.id DESC LIMIT ?""", pjp + [limit]))
    summ = one(db.execute(
        f"""SELECT COUNT(*) n, ROUND(AVG(p.absolute_error),2) live_mae,
                   ROUND(AVG(ABS(p.baseline_waiting_time - p.actual_waiting_time)),2) baseline_live_mae
            FROM ml_predictions p JOIN appointments a ON a.id=p.appointment_id
            WHERE p.actual_waiting_time IS NOT NULL
              AND p.id IN (SELECT MAX(id) FROM ml_predictions GROUP BY appointment_id) {pj}""", pjp))
    return {"recent": recent, "summary": summ}
