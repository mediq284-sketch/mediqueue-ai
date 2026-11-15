"""Appointment slots, booking, cancellation and queue-number generation.
All validation is repeated here on the server regardless of what the UI checked."""
import sqlite3
from datetime import datetime, timedelta

from .. import APIError, clock
from ..db import one, rows, tx, get_setting
from . import queue_engine as Q


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise APIError("Please choose a valid date.", 400, "invalid_date")


def schedule_slots(db, doctor):
    step = int(get_setting(db, "slot_minutes", "15"))
    start = datetime.strptime(doctor["start_time"], "%H:%M")
    end = datetime.strptime(doctor["end_time"], "%H:%M")
    out, t = [], start
    while t + timedelta(minutes=step) <= end:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=step)
    return out


def _bookable_doctor(db, doctor_id):
    doc = one(db.execute(
        """SELECT d.*, dep.active AS dep_active, dep.prefix, dep.name AS department_name
           FROM doctors d JOIN departments dep ON dep.id=d.department_id WHERE d.id=?""", (doctor_id,)))
    if not doc:
        raise APIError("Doctor not found.", 404, "not_found")
    if not doc["active"] or not doc["dep_active"]:
        raise APIError("This doctor or department is not accepting appointments.", 409, "inactive")
    return doc


def _validate_date(db, doc, d):
    today = clock.now().date()
    if d < today:
        raise APIError("Appointments cannot be booked for a past date.", 400, "past_date")
    if d > today + timedelta(days=int(get_setting(db, "max_booking_days_ahead", "30"))):
        raise APIError("That date is too far ahead to book.", 400, "too_far")
    days = {int(x) for x in doc["working_days"].split(",") if x.strip() != ""}
    if d.weekday() not in days:
        raise APIError(f"{doc['name']} does not consult on that day.", 409, "not_working_day")


def available_slots(db, doctor_id, date_str):
    doc = _bookable_doctor(db, doctor_id)
    d = _parse_date(date_str)
    _validate_date(db, doc, d)
    taken = {r["appointment_time"] for r in db.execute(
        "SELECT appointment_time FROM appointments WHERE doctor_id=? AND appointment_date=? AND status IN ('BOOKED','CONFIRMED')",
        (doctor_id, date_str))}
    demo = get_setting(db, "demo_mode", "0") == "1"
    now = clock.now()
    out = []
    for s in schedule_slots(db, doc):
        past = d == now.date() and not demo and s <= now.strftime("%H:%M")
        out.append({"time": s, "available": s not in taken and not past})
    return {"doctor_id": doctor_id, "date": date_str, "slots": out, "demo_mode": demo}


def next_queue_number(db, department_id, prefix, date_str):
    seq = db.execute("SELECT COALESCE(MAX(queue_seq),0)+1 n FROM queue_entries WHERE department_id=? AND queue_date=?",
                     (department_id, date_str)).fetchone()["n"]
    return seq, f"{prefix}-{seq:03d}"


def book(db, patient_id, doctor_id, date_str, time_str):
    if not all([doctor_id, date_str, time_str]):
        raise APIError("Department, doctor, date and time are required.", 400, "missing_fields")
    try:
        with tx(db):
            doc = _bookable_doctor(db, doctor_id)
            d = _parse_date(date_str)
            _validate_date(db, doc, d)
            slots = available_slots(db, doctor_id, date_str)["slots"]
            slot = next((s for s in slots if s["time"] == time_str), None)
            if not slot:
                raise APIError("That time is outside the doctor's schedule.", 400, "invalid_slot")
            if not slot["available"]:
                raise APIError("Unable to book appointment. Please try another time slot.", 409, "slot_taken")
            dup = db.execute("""SELECT 1 FROM appointments WHERE patient_id=? AND doctor_id=? AND appointment_date=?
                                AND status IN ('BOOKED','CONFIRMED')""", (patient_id, doctor_id, date_str)).fetchone()
            if dup:
                raise APIError("You already have an appointment with this doctor on that day.", 409, "duplicate_booking")
            now = clock.ts()
            cur = db.execute(
                """INSERT INTO appointments (patient_id, doctor_id, department_id, appointment_date, appointment_time,
                       booking_timestamp, status, created_at, updated_at) VALUES (?,?,?,?,?,?,'BOOKED',?,?)""",
                (patient_id, doctor_id, doc["department_id"], date_str, time_str, now, now, now))
            appt_id = cur.lastrowid
            seq, qn = next_queue_number(db, doc["department_id"], doc["prefix"], date_str)
            cur = db.execute(
                """INSERT INTO queue_entries (appointment_id, patient_id, doctor_id, department_id, queue_date,
                       queue_number, queue_seq, status) VALUES (?,?,?,?,?,?,?,'WAITING')""",
                (appt_id, patient_id, doctor_id, doc["department_id"], date_str, qn, seq))
            entry_id = cur.lastrowid
            db.execute("UPDATE appointments SET status='CONFIRMED', queue_number=? WHERE id=?", (qn, appt_id))
            if date_str == clock.now().strftime("%Y-%m-%d"):
                Q.mark_joined(db, entry_id)          # same-day booking = patient joins the queue now
            Q.recompute_positions(db, doctor_id, date_str)
    except sqlite3.IntegrityError:
        # DB-level unique indexes are the final guard against races / double booking.
        raise APIError("Unable to book appointment. Please try another time slot.", 409, "slot_taken")
    return one(db.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)))


def cancel_by_patient(db, patient_id, appt_id):
    with tx(db):
        a = one(db.execute("SELECT * FROM appointments WHERE id=? AND patient_id=?", (appt_id, patient_id)))
        if not a:
            raise APIError("Appointment not found.", 404, "not_found")
        if a["status"] not in ("BOOKED", "CONFIRMED"):
            raise APIError("This appointment can no longer be cancelled.", 409, "not_cancellable")
        e = one(db.execute("SELECT * FROM queue_entries WHERE appointment_id=?", (appt_id,)))
        if e and e["status"] != "WAITING":
            raise APIError("You have already been called. Please speak to the front desk.", 409, "not_cancellable")
        sched = datetime.strptime(f"{a['appointment_date']} {a['appointment_time']}", "%Y-%m-%d %H:%M")
        cutoff = int(get_setting(db, "cancellation_cutoff_minutes", "0"))
        if cutoff and clock.now() > sched - timedelta(minutes=cutoff):
            raise APIError("The cancellation window for this appointment has passed.", 409, "cutoff")
        now = clock.ts()
        db.execute("UPDATE appointments SET status='CANCELLED', updated_at=? WHERE id=?", (now, appt_id))
        if e:
            db.execute("UPDATE queue_entries SET status='CANCELLED' WHERE id=?", (e["id"],))
            Q.recompute_positions(db, e["doctor_id"], e["queue_date"])
    return one(db.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)))
