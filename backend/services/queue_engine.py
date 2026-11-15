"""Queue state machine.

    WAITING -> CALLED -> SERVING -> COMPLETED
    WAITING -> NO_SHOW | CANCELLED
    CALLED  -> NO_SHOW | CANCELLED     (patient did not answer the call)

Ordering: token order (queue_seq). Only checked-in patients (joined_at set) can be called.
On completion the actual wait / consult time are computed, a QueueHistory row
(the ML training data) is created, and every prediction made for that
appointment is scored against the real outcome.
"""
import json
from datetime import datetime

from .. import APIError, clock
from ..db import one, rows, tx, get_setting
from . import features as F

ACTIVE = ("WAITING", "CALLED", "SERVING")
TRANSITIONS = {
    "WAITING": {"CALLED", "NO_SHOW", "CANCELLED"},
    "CALLED": {"SERVING", "NO_SHOW", "CANCELLED"},
    "SERVING": {"COMPLETED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "NO_SHOW": set(),
}
APPT_STATUS_FOR = {"COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED", "NO_SHOW": "NO_SHOW"}


def check_transition(cur, new):
    if new not in TRANSITIONS.get(cur, set()):
        raise APIError(f"Invalid queue transition: {cur} \u2192 {new}.", 409, "invalid_transition")


def recompute_positions(db, doctor_id, date):
    entries = rows(db.execute(
        "SELECT id, status FROM queue_entries WHERE doctor_id=? AND queue_date=? ORDER BY queue_seq",
        (doctor_id, date)))
    pos = 0
    for e in entries:
        if e["status"] == "WAITING":
            pos += 1
            p = pos
        elif e["status"] in ("CALLED", "SERVING"):
            p = 0
        else:
            p = None
        db.execute("UPDATE queue_entries SET position=? WHERE id=?", (p, e["id"]))


def queue_snapshot(db, entry):
    """patients_ahead / queue_length for an entry, from current DB state."""
    ahead = db.execute(
        """SELECT COUNT(*) c FROM queue_entries WHERE doctor_id=? AND queue_date=? AND id != ?
           AND (status IN ('CALLED','SERVING') OR (status='WAITING' AND queue_seq < ?))""",
        (entry["doctor_id"], entry["queue_date"], entry["id"], entry["queue_seq"])).fetchone()["c"]
    length = db.execute(
        "SELECT COUNT(*) c FROM queue_entries WHERE doctor_id=? AND queue_date=? AND status IN ('WAITING','CALLED','SERVING')",
        (entry["doctor_id"], entry["queue_date"])).fetchone()["c"]
    return ahead, length


def mark_joined(db, entry_id):
    e = one(db.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,)))
    if e["joined_at"]:
        return e
    if e["queue_date"] != clock.now().strftime("%Y-%m-%d"):
        raise APIError("Check-in is only possible on the day of the appointment.", 409, "not_today")
    if e["status"] != "WAITING":
        raise APIError("This queue entry is no longer active.", 409, "inactive")
    ahead, length = queue_snapshot(db, e)
    db.execute("UPDATE queue_entries SET joined_at=?, patients_ahead_at_join=?, queue_length_at_join=? WHERE id=?",
               (clock.ts(), ahead, length, entry_id))
    return one(db.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,)))


def _get(db, entry_id):
    e = one(db.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,)))
    if not e:
        raise APIError("Queue entry not found.", 404, "not_found")
    return e


def call_next(db, doctor_id, date):
    with tx(db):
        busy = db.execute("SELECT queue_number FROM queue_entries WHERE doctor_id=? AND queue_date=? AND status='CALLED'",
                          (doctor_id, date)).fetchone()
        if busy:
            raise APIError(f"{busy['queue_number']} has already been called. Start or close that consultation first.", 409, "already_called")
        nxt = one(db.execute(
            """SELECT * FROM queue_entries WHERE doctor_id=? AND queue_date=? AND status='WAITING' AND joined_at IS NOT NULL
               ORDER BY queue_seq LIMIT 1""", (doctor_id, date)))
        if not nxt:
            raise APIError("No checked-in patients are waiting in this queue.", 409, "queue_empty")
        db.execute("UPDATE queue_entries SET status='CALLED', called_at=? WHERE id=?", (clock.ts(), nxt["id"]))
        recompute_positions(db, doctor_id, date)
    return _get(db, nxt["id"])


def transition(db, entry_id, new_status):
    with tx(db):
        e = _get(db, entry_id)
        check_transition(e["status"], new_status)
        now = clock.ts()
        if new_status == "CALLED":
            db.execute("UPDATE queue_entries SET status='CALLED', called_at=? WHERE id=?", (now, entry_id))
        elif new_status == "SERVING":
            serving = db.execute("SELECT queue_number FROM queue_entries WHERE doctor_id=? AND queue_date=? AND status='SERVING'",
                                 (e["doctor_id"], e["queue_date"])).fetchone()
            if serving:
                raise APIError(f"{serving['queue_number']} is still in consultation. Complete it first.", 409, "doctor_busy")
            joined = clock.parse(e["joined_at"]) or clock.parse(e["called_at"])
            wait = max(0.0, clock.minutes_between(joined, clock.now()))
            db.execute("UPDATE queue_entries SET status='SERVING', service_started_at=?, actual_waiting_time=? WHERE id=?",
                       (now, wait, entry_id))
        elif new_status == "COMPLETED":
            started = clock.parse(e["service_started_at"])
            dur = max(0.0, clock.minutes_between(started, clock.now()))
            db.execute("UPDATE queue_entries SET status='COMPLETED', completed_at=?, consultation_duration=? WHERE id=?",
                       (now, dur, entry_id))
            e = _get(db, entry_id)
            _record_history(db, e)
        else:  # NO_SHOW / CANCELLED
            db.execute("UPDATE queue_entries SET status=? WHERE id=?", (new_status, entry_id))
        if new_status in APPT_STATUS_FOR:
            db.execute("UPDATE appointments SET status=?, updated_at=? WHERE id=?",
                       (APPT_STATUS_FOR[new_status], now, e["appointment_id"]))
        recompute_positions(db, e["doctor_id"], e["queue_date"])
    return _get(db, entry_id)


def _record_history(db, e):
    """Completed consultation -> QueueHistory training record + prediction scoring."""
    appt = one(db.execute("SELECT * FROM appointments WHERE id=?", (e["appointment_id"],)))
    doc = one(db.execute("SELECT * FROM doctors WHERE id=?", (e["doctor_id"],)))
    joined = clock.parse(e["joined_at"]) or clock.parse(e["called_at"])
    doc_avg, dep_avg = F.historical_averages(db, e["doctor_id"], e["department_id"], e["queue_date"])
    prev = F.previous_average_wait(db, e["doctor_id"], e["queue_date"], e["joined_at"] or e["called_at"], doc_avg)
    sched = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    feats = F.build_features(
        department_id=e["department_id"], doctor_id=e["doctor_id"], date=e["queue_date"],
        appointment_time=appt["appointment_time"],
        patients_ahead=e["patients_ahead_at_join"] if e["patients_ahead_at_join"] is not None else 0,
        queue_length=e["queue_length_at_join"] if e["queue_length_at_join"] is not None else 1,
        consultation_duration_average=doc["consultation_duration_average"], peak_set=F.peak_hours(db),
        previous_avg=prev, doctor_avg=doc_avg, department_avg=dep_avg, delay=F.arrival_delay(sched, joined))
    db.execute(
        """INSERT INTO queue_history (appointment_id, doctor_id, department_id, date, day_of_week, appointment_hour,
             appointment_minute, patients_ahead, queue_length, consultation_duration, consultation_duration_average,
             actual_waiting_time, peak_hour, previous_average_wait, doctor_average_wait, department_average_wait,
             patient_arrival_delay, source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'LIVE',?)""",
        (e["appointment_id"], e["doctor_id"], e["department_id"], e["queue_date"], feats["day_of_week"],
         feats["appointment_hour"], feats["appointment_minute"], feats["patients_ahead"], feats["queue_length"],
         e["consultation_duration"], feats["consultation_duration_average"], e["actual_waiting_time"],
         feats["peak_hour"], feats["previous_average_wait"], feats["doctor_average_wait"],
         feats["department_average_wait"], feats["patient_arrival_delay"], clock.ts()))
    score_predictions(db, e["appointment_id"], e["actual_waiting_time"])


def score_predictions(db, appointment_id, actual):
    db.execute("""UPDATE ml_predictions SET actual_waiting_time=?, absolute_error=ROUND(ABS(predicted_waiting_time - ?), 2)
                  WHERE appointment_id=?""", (actual, actual, appointment_id))


def doctor_queue(db, doctor_id, date):
    return rows(db.execute(
        """SELECT q.*, u.full_name AS patient_name, a.appointment_time
           FROM queue_entries q JOIN users u ON u.id=q.patient_id JOIN appointments a ON a.id=q.appointment_id
           WHERE q.doctor_id=? AND q.queue_date=? ORDER BY q.queue_seq""", (doctor_id, date)))
