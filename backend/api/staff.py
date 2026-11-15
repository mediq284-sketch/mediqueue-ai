"""Staff (and admin) operational endpoints. Staff assigned to a department are
restricted server-side to that department's doctors and queues."""
from flask import Blueprint, jsonify, request, current_app

from .. import APIError, clock
from ..auth import login_required, current_user
from ..db import get_db, rows, one
from ..services import queue_engine as Q, analytics
from ..ml import prediction as P

bp = Blueprint("staff", __name__, url_prefix="/api/staff")
OPS = ("STAFF", "ADMIN")


def scope_dept():
    u = current_user()
    return u["department_id"] if u["role"] == "STAFF" else request.args.get("department_id", type=int)


def _authorize_doctor(db, doctor_id):
    d = one(db.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)))
    if not d:
        raise APIError("Doctor not found.", 404, "not_found")
    u = current_user()
    if u["role"] == "STAFF" and u["department_id"] and d["department_id"] != u["department_id"]:
        raise APIError("You do not have permission to manage this queue.", 403, "forbidden")
    return d


def _authorize_entry(db, entry_id):
    e = one(db.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,)))
    if not e:
        raise APIError("Queue entry not found.", 404, "not_found")
    _authorize_doctor(db, e["doctor_id"])
    return e


def _today():
    return request.args.get("date") or clock.now().strftime("%Y-%m-%d")


@bp.get("/dashboard")
@login_required(*OPS)
def dashboard():
    db, dept, today = get_db(), scope_dept(), _today()
    s, sp = (" AND department_id=?", [dept]) if dept else ("", [])
    counts = {r["status"]: r["n"] for r in db.execute(
        f"SELECT status, COUNT(*) n FROM queue_entries WHERE queue_date=? {s} GROUP BY status", [today] + sp)}
    appts = db.execute(f"SELECT COUNT(*) c FROM appointments WHERE appointment_date=? {s}", [today] + sp).fetchone()["c"]
    avg = db.execute(f"SELECT ROUND(AVG(actual_waiting_time),1) a FROM queue_entries WHERE queue_date=? AND actual_waiting_time IS NOT NULL {s}",
                     [today] + sp).fetchone()["a"]
    return jsonify(date=today, appointments=appts, waiting=counts.get("WAITING", 0) + counts.get("CALLED", 0),
                   serving=counts.get("SERVING", 0), completed=counts.get("COMPLETED", 0),
                   no_shows=counts.get("NO_SHOW", 0), cancelled=counts.get("CANCELLED", 0), avg_wait_today=avg,
                   queues=_queue_summaries(db, dept, today))


def _queue_summaries(db, dept, date):
    s, sp = (" AND d.department_id=?", [dept]) if dept else ("", [])
    return rows(db.execute(
        f"""SELECT d.id AS doctor_id, d.name AS doctor_name, dep.name AS department_name, dep.prefix,
                   SUM(q.status='WAITING') AS waiting, SUM(q.status='CALLED') AS called, SUM(q.status='SERVING') AS serving,
                   SUM(q.status='COMPLETED') AS completed, COUNT(q.id) AS total,
                   (SELECT queue_number FROM queue_entries WHERE doctor_id=d.id AND queue_date=? AND status='SERVING') AS now_serving
            FROM doctors d JOIN departments dep ON dep.id=d.department_id
            LEFT JOIN queue_entries q ON q.doctor_id=d.id AND q.queue_date=?
            WHERE d.active=1 {s} GROUP BY d.id HAVING total > 0 ORDER BY dep.name, d.name""", [date, date] + sp))


@bp.get("/queues")
@login_required(*OPS)
def queues():
    return jsonify(date=_today(), queues=_queue_summaries(get_db(), scope_dept(), _today()))


@bp.get("/queues/<int:doctor_id>")
@login_required(*OPS)
def queue_detail(doctor_id):
    db = get_db()
    d = _authorize_doctor(db, doctor_id)
    date = _today()
    entries = Q.doctor_queue(db, doctor_id, date)
    latest = {r["appointment_id"]: r for r in rows(db.execute(
        """SELECT appointment_id, predicted_waiting_time, prediction_timestamp FROM ml_predictions
           WHERE id IN (SELECT MAX(id) FROM ml_predictions GROUP BY appointment_id)
             AND appointment_id IN (SELECT appointment_id FROM queue_entries WHERE doctor_id=? AND queue_date=?)""",
        (doctor_id, date)))}
    for e in entries:
        p = latest.get(e["appointment_id"])
        e["predicted_waiting_time"] = p["predicted_waiting_time"] if p else None
    dep = one(db.execute("SELECT name, prefix FROM departments WHERE id=?", (d["department_id"],)))
    return jsonify(doctor={"id": d["id"], "name": d["name"], "department_name": dep["name"]}, date=date, entries=entries)


@bp.post("/queues/<int:doctor_id>/call-next")
@login_required(*OPS)
def call_next(doctor_id):
    db = get_db()
    _authorize_doctor(db, doctor_id)
    return jsonify(entry=Q.call_next(db, doctor_id, clock.now().strftime("%Y-%m-%d")))


ACTIONS = {"call": "CALLED", "start": "SERVING", "complete": "COMPLETED", "no-show": "NO_SHOW", "cancel": "CANCELLED"}


@bp.post("/entries/<int:entry_id>/<action>")
@login_required(*OPS)
def entry_action(entry_id, action):
    if action == "check-in":
        db = get_db()
        e = _authorize_entry(db, entry_id)
        from ..db import tx
        with tx(db):
            Q.mark_joined(db, entry_id)
            Q.recompute_positions(db, e["doctor_id"], e["queue_date"])
        return jsonify(entry=one(db.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,))))
    if action not in ACTIONS:
        raise APIError("Unknown action.", 400, "bad_action")
    db = get_db()
    _authorize_entry(db, entry_id)
    if action == "call":
        e = one(db.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,)))
        if not e["joined_at"]:
            raise APIError("This patient has not checked in yet.", 409, "not_checked_in")
        if db.execute("SELECT 1 FROM queue_entries WHERE doctor_id=? AND queue_date=? AND status='CALLED'",
                      (e["doctor_id"], e["queue_date"])).fetchone():
            raise APIError("Another patient is already called for this doctor.", 409, "already_called")
    return jsonify(entry=Q.transition(db, entry_id, ACTIONS[action]))


@bp.get("/appointments")
@login_required(*OPS)
def today_appointments():
    db, dept, date = get_db(), scope_dept(), _today()
    s, sp = (" AND a.department_id=?", [dept]) if dept else ("", [])
    return jsonify(date=date, appointments=rows(db.execute(
        f"""SELECT a.id, a.appointment_date, a.appointment_time, a.status, a.queue_number, u.full_name AS patient_name,
                   d.name AS doctor_name, dep.name AS department_name, q.id AS queue_entry_id, q.status AS queue_status, q.joined_at
            FROM appointments a JOIN users u ON u.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id
            JOIN departments dep ON dep.id=a.department_id LEFT JOIN queue_entries q ON q.appointment_id=a.id
            WHERE a.appointment_date=? {s} ORDER BY a.appointment_time, a.queue_number""", [date] + sp)))


@bp.get("/predictions")
@login_required(*OPS)
def predictions():
    return jsonify(analytics.prediction_monitoring(get_db(), scope_dept(), request.args.get("limit", 50, type=int)))


@bp.get("/analytics")
@login_required(*OPS)
def ops_analytics():
    return jsonify(analytics.compute(get_db(), request.args.get("days", 30, type=int), scope_dept()))
