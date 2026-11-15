"""Patient endpoints. Every query is filtered by the signed-in patient's id on the
server, so a patient can never read or change another patient's records."""
from flask import Blueprint, jsonify, request, current_app

from .. import APIError, clock
from ..auth import login_required, current_user
from ..db import get_db, rows, one, tx
from ..services import booking, queue_engine as Q
from ..ml import prediction as P

bp = Blueprint("patient", __name__, url_prefix="/api/patient")

APPT_SELECT = """SELECT a.*, d.name AS doctor_name, d.specialization, dep.name AS department_name,
                        q.id AS queue_entry_id, q.status AS queue_status, q.position, q.joined_at
                 FROM appointments a JOIN doctors d ON d.id=a.doctor_id JOIN departments dep ON dep.id=a.department_id
                 LEFT JOIN queue_entries q ON q.appointment_id=a.id"""


def _me():
    return current_user()["id"]


@bp.get("/appointments")
@login_required("PATIENT")
def my_appointments():
    return jsonify(appointments=rows(get_db().execute(
        APPT_SELECT + " WHERE a.patient_id=? ORDER BY a.appointment_date DESC, a.appointment_time DESC", (_me(),))))


@bp.get("/appointments/<int:appt_id>")
@login_required("PATIENT")
def one_appointment(appt_id):
    a = one(get_db().execute(APPT_SELECT + " WHERE a.id=? AND a.patient_id=?", (appt_id, _me())))
    if not a:
        raise APIError("Appointment not found.", 404, "not_found")
    return jsonify(appointment=a)


@bp.post("/appointments")
@login_required("PATIENT")
def book():
    b = request.get_json(silent=True) or {}
    a = booking.book(get_db(), _me(), b.get("doctor_id"), b.get("date"), b.get("time"))
    return jsonify(appointment=a), 201


@bp.post("/appointments/<int:appt_id>/cancel")
@login_required("PATIENT")
def cancel(appt_id):
    return jsonify(appointment=booking.cancel_by_patient(get_db(), _me(), appt_id))


@bp.post("/appointments/<int:appt_id>/check-in")
@login_required("PATIENT")
def check_in(appt_id):
    db = get_db()
    e = one(db.execute("SELECT * FROM queue_entries WHERE appointment_id=? AND patient_id=?", (appt_id, _me())))
    if not e:
        raise APIError("Appointment not found.", 404, "not_found")
    with tx(db):
        Q.mark_joined(db, e["id"])
        Q.recompute_positions(db, e["doctor_id"], e["queue_date"])
    return jsonify(ok=True)


def _queue_view(db, e):
    ahead, length = Q.queue_snapshot(db, e)
    board = rows(db.execute(
        """SELECT queue_number, status FROM queue_entries WHERE doctor_id=? AND queue_date=?
           AND status IN ('WAITING','CALLED','SERVING') ORDER BY queue_seq""", (e["doctor_id"], e["queue_date"])))
    served = db.execute("SELECT COUNT(*) c FROM queue_entries WHERE doctor_id=? AND queue_date=? AND status='COMPLETED'",
                        (e["doctor_id"], e["queue_date"])).fetchone()["c"]
    now_serving = next((r["queue_number"] for r in board if r["status"] == "SERVING"), None)
    pred = P.prediction_for_entry(db, current_app.config["MODEL_DIR"], e)
    appt = one(db.execute(APPT_SELECT + " WHERE a.id=?", (e["appointment_id"],)))
    return {"appointment": appt, "queue_entry": {k: e[k] for k in ("id", "queue_number", "status", "position", "joined_at", "called_at", "service_started_at", "queue_date")},
            "patients_ahead": ahead if e["status"] == "WAITING" else 0, "queue_length": length,
            "served_today": served, "now_serving": now_serving,
            "board": [{"queue_number": r["queue_number"], "status": r["status"], "is_me": r["queue_number"] == e["queue_number"]} for r in board],
            "prediction": pred, "server_time": clock.ts()}


@bp.get("/queue")
@login_required("PATIENT")
def my_queue():
    """Today's active queue entry for this patient (with ML prediction)."""
    db = get_db()
    today = clock.now().strftime("%Y-%m-%d")
    entries = rows(db.execute(
        """SELECT * FROM queue_entries WHERE patient_id=? AND queue_date=?
           ORDER BY CASE status WHEN 'SERVING' THEN 0 WHEN 'CALLED' THEN 1 WHEN 'WAITING' THEN 2 ELSE 3 END, queue_seq""",
        (_me(), today)))
    if not entries:
        return jsonify(queue=None)
    with tx(db):
        view = _queue_view(db, entries[0])
    others = [{"appointment_id": x["appointment_id"], "queue_number": x["queue_number"], "status": x["status"]} for x in entries[1:]]
    return jsonify(queue=view, other_entries_today=others)


@bp.get("/dashboard")
@login_required("PATIENT")
def dashboard():
    db = get_db()
    today = clock.now().strftime("%Y-%m-%d")
    upcoming = rows(db.execute(APPT_SELECT + """ WHERE a.patient_id=? AND a.appointment_date >= ? AND a.status IN ('BOOKED','CONFIRMED')
                                             ORDER BY a.appointment_date, a.appointment_time LIMIT 5""", (_me(), today)))
    counts = rows(db.execute("SELECT status, COUNT(*) n FROM appointments WHERE patient_id=? GROUP BY status", (_me(),)))
    return jsonify(upcoming=upcoming, counts={r["status"]: r["n"] for r in counts}, today=today)
