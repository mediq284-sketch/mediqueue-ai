"""Synthetic demo data generator (NO real patient data).

Historical waiting times are not drawn from a formula: they come out of a
discrete-event simulation of each doctor's OPD session (patients arrive
early/late around their slot, consultation times are gamma-distributed around
the doctor's average, slower in peak hours, with occasional interruptions and a
variable doctor start delay). The wait is whatever the simulated queue produces,
so the data is realistic, noisy and not deterministic.

Timeline produced:
  * days -45 .. -3 : history only (training data for model v1)
  * model v1 is TRAINED on that history with the real pipeline and ACTIVATED
  * days -2, -1 and the earlier part of today: "post-deployment" - v1 makes a
    prediction at each patient's arrival, then the simulated outcome is
    recorded, so prediction monitoring shows genuine out-of-sample errors.
  * today: live queues (completed / serving / waiting) for doctors working today.
A handful of deliberately corrupted + duplicate rows are injected (flagged
SYNTHETIC_INVALID) so the data-validation step has something to reject.

Run:  python -m backend.seed   (wipes and recreates the database)
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta

import numpy as np

from . import clock, create_app
from .auth import hash_password
from .db import connect, init_schema, get_setting
from .services import features as F
from .ml import pipeline as ML, prediction as P

DEMO_PASSWORD = "Demo@1234"

DEPARTMENTS = [
    ("General Medicine", "GM", "Fevers, infections, chronic condition follow-ups and general consultations."),
    ("Cardiology", "CAR", "Heart and blood-pressure outpatient consultations."),
    ("Orthopaedics", "ORT", "Bone, joint, spine and sports-injury consultations."),
    ("Paediatrics", "PED", "Outpatient care for infants, children and adolescents."),
    ("Dermatology", "DER", "Skin, hair and nail consultations."),
    ("ENT", "ENT", "Ear, nose and throat consultations."),
]
ALL = "0,1,2,3,4,5,6"
DOCTORS = [  # name, dept prefix, specialization, avg consult, days, start, end
    ("Dr. Ananya Rao", "GM", "Internal Medicine", 9, "0,1,2,3,4,5", "09:00", "13:00"),
    ("Dr. Vikram Menon", "GM", "Family Medicine", 11, ALL, "14:00", "18:00"),
    ("Dr. Farhan Ali", "GM", "General Physician", 10, "0,1,2,3,4,5", "09:00", "12:00"),
    ("Dr. Suresh Iyer", "CAR", "Interventional Cardiology", 16, "0,2,4", "10:00", "14:00"),
    ("Dr. Meera Krishnan", "CAR", "Clinical Cardiology", 14, "1,3,5", "09:00", "13:00"),
    ("Dr. Rahul Varma", "ORT", "Joint Replacement", 13, "0,1,2,3,4", "09:00", "13:00"),
    ("Dr. Kavya Reddy", "ORT", "Sports Medicine", 12, ALL, "09:00", "13:00"),
    ("Dr. Lakshmi Nair", "PED", "General Paediatrics", 10, "0,1,2,3,4,5", "09:00", "13:00"),
    ("Dr. Arjun Shetty", "DER", "Clinical Dermatology", 8, "0,1,2,3,4", "15:00", "18:00"),
    ("Dr. Priya Das", "ENT", "Otology", 10, "0,1,2,3,4,5", "10:00", "14:00"),
    ("Dr. Nikhil Joshi", "ENT", "Rhinology", 11, "1,2,3,4,5,6", "09:00", "13:00"),
]
FIRST = ["Aarav", "Vivaan", "Aditya", "Sai", "Arjun", "Reyansh", "Krishna", "Ishaan", "Rohan", "Kiran", "Ananya", "Diya",
         "Saanvi", "Aadhya", "Pooja", "Lakshmi", "Meghana", "Sneha", "Divya", "Harika", "Ravi", "Suresh", "Manoj", "Teja",
         "Bhavya", "Keerthi", "Nandini", "Varun", "Gopal", "Srinivas"]
LAST = ["Reddy", "Rao", "Naidu", "Sharma", "Varma", "Kumar", "Chowdary", "Patel", "Iyer", "Menon", "Gupta", "Prasad", "Murthy", "Das"]

HISTORY_DAYS = 45
DEPLOY_DAYS = 2           # days -2, -1 are simulated after model v1 is deployed


def _mins(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def simulate_session(rng, doc, slots, weekday, peak_set, n_today=None):
    """Discrete-event simulation of one doctor session. Times are minutes since midnight."""
    patients = []
    for i, s in enumerate(slots):
        hour = s // 60
        p = 0.80 + (0.10 if weekday == 0 else 0) + (0.10 if hour in peak_set else 0) - (0.15 if weekday >= 5 else 0)
        if n_today is not None:
            if i >= n_today:
                break
            p = 1.0
        if rng.random() > min(p, 0.97):
            continue
        r = rng.random()
        status = "CANCELLED" if r < 0.06 else ("NO_SHOW" if r < 0.13 else "COMPLETED")
        if n_today is not None and status == "CANCELLED":
            status = "COMPLETED"
        arrival = s + float(np.clip(rng.normal(-18, 14), -75, 35))
        patients.append({"slot": s, "token": i, "status": status, "arrival": arrival})
    served = [p for p in patients if p["status"] == "COMPLETED"]
    t = slots[0] + max(0.0, rng.normal(14, 10) + (6 if weekday == 0 else 0))
    pending = sorted(served, key=lambda p: p["token"])
    while pending:
        arrived = [p for p in pending if p["arrival"] <= t]
        if not arrived:
            t = min(p["arrival"] for p in pending)
            continue
        p = arrived[0]                      # lowest token among checked-in patients
        factor = (1.25 if int(t // 60) in peak_set else 1.1) * (1.05 if weekday == 0 else 1.0)
        dur = rng.gamma(4.0, doc["avg"] * factor / 4.0)
        if rng.random() < 0.04:
            dur += rng.uniform(8, 20)      # interruption (ward call, emergency)
        p["start"], p["end"] = t, t + dur
        t = p["end"] + max(0.0, rng.normal(0.5, 0.5))  # hand-over gap
        pending.remove(p)
    for p in served:
        A = p["arrival"]
        ahead = sum(1 for q in served if q["token"] < p["token"] and q["start"] > A)
        ahead += sum(1 for q in served if q is not p and q["start"] <= A < q["end"])
        ahead += sum(1 for q in patients if q["status"] == "NO_SHOW" and q["token"] < p["token"] and q["slot"] + 20 > A)
        length = sum(1 for q in served if q["end"] > A) + sum(1 for q in patients if q["status"] == "NO_SHOW" and q["slot"] + 20 > A)
        p["ahead"], p["length"] = ahead, max(length, ahead + 1)
        p["wait"] = round(p["start"] - A, 2)
        p["dur"] = round(p["end"] - p["start"], 2)
        prior = sorted((q for q in served if q["start"] < A), key=lambda q: q["start"])[-5:]
        p["prev"] = round(sum(q["wait"] if "wait" in q else q["start"] - q["arrival"] for q in prior) / len(prior), 2) if prior else None
    return patients


def seed(db_path=None, model_dir=None, today=None, verbose=True):
    app = create_app({"DATABASE": db_path} if db_path else None)
    db_path = app.config["DATABASE"]
    model_dir = model_dir or app.config["MODEL_DIR"]
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)
    for f in os.listdir(model_dir):
        if f.endswith(".joblib"):
            os.remove(os.path.join(model_dir, f))
    db = connect(db_path)
    init_schema(db)
    rng = np.random.RandomState(7)
    random.seed(7)
    real_now = clock.now()
    today = today or real_now.date()
    log = (lambda *a: print(*a)) if verbose else (lambda *a: None)

    db.execute("BEGIN")
    dep_ids = {}
    for name, prefix, desc in DEPARTMENTS:
        dep_ids[prefix] = db.execute("INSERT INTO departments (name, prefix, description) VALUES (?,?,?)", (name, prefix, desc)).lastrowid
    docs = []
    for name, pref, spec, avg, days, st, en in DOCTORS:
        did = db.execute("""INSERT INTO doctors (name, department_id, specialization, consultation_duration_average, working_days,
                            start_time, end_time) VALUES (?,?,?,?,?,?,?)""", (name, dep_ids[pref], spec, avg, days, st, en)).lastrowid
        docs.append({"id": did, "dep": dep_ids[pref], "avg": float(avg), "days": {int(x) for x in days.split(",")}, "start": st, "end": en})

    pw = hash_password(DEMO_PASSWORD)
    U = lambda n, e, ph, role, dep=None: db.execute(
        "INSERT INTO users (full_name, email, phone, role, password_hash, department_id) VALUES (?,?,?,?,?,?)", (n, e, ph, role, pw, dep)).lastrowid
    U("Admin User", "admin@mediqueue.demo", "+91 90000 00001", "ADMIN")
    U("Front Desk (All Departments)", "staff@mediqueue.demo", "+91 90000 00002", "STAFF")
    U("GM Reception", "gm.staff@mediqueue.demo", "+91 90000 00003", "STAFF", dep_ids["GM"])
    demo_patient = U("Ravi Kumar (Demo Patient)", "patient@mediqueue.demo", "+91 90000 00010", "PATIENT")
    U("Sneha Reddy (Demo Patient 2)", "patient2@mediqueue.demo", "+91 90000 00011", "PATIENT")
    pool = [U(f"{random.choice(FIRST)} {random.choice(LAST)}", f"synthetic.patient{i:03d}@mediqueue.demo", None, "PATIENT") for i in range(80)]
    db.execute("COMMIT")

    peak_set = F.peak_hours(db)
    slot_minutes = int(get_setting(db, "slot_minutes", "15"))
    seq_counter = {}

    def doc_slots(d):
        a, b = _mins(d["start"]), _mins(d["end"])
        return list(range(a, b - slot_minutes + 1, slot_minutes))

    def write_session(d, date, sim, model_meta=None, cut_now=None, real_base=None):
        """Persist one simulated session. If model_meta is given, a prediction is
        made with the deployed model at each patient's arrival (before outcome)."""
        date_s = date.isoformat()
        day0 = datetime.combine(date, datetime.min.time())
        to_dt = (lambda m: real_base + timedelta(minutes=m)) if real_base else (lambda m: day0 + timedelta(minutes=m))
        doc_avg, dep_avg = F.historical_averages(db, d["id"], d["dep"], date_s)
        dep_prefix = next(p for p, i in dep_ids.items() if i == d["dep"])
        used_today = set()
        for p in sorted(sim, key=lambda x: x["token"]):
            pid = random.choice(pool)
            while pid in used_today:
                pid = random.choice(pool)
            used_today.add(pid)
            key = (d["dep"], date_s)
            seq_counter[key] = seq_counter.get(key, 0) + 1
            seq = seq_counter[key]
            qn = f"{dep_prefix}-{seq:03d}"
            slot_t = f"{p['slot'] // 60:02d}:{p['slot'] % 60:02d}"
            status = p["status"]
            q_status, appt_status = status, status
            if cut_now is not None and status == "COMPLETED":
                if to_dt(p["end"]) <= cut_now:
                    pass
                elif to_dt(p["start"]) <= cut_now:
                    q_status, appt_status = "SERVING", "CONFIRMED"
                else:
                    q_status, appt_status = "WAITING", "CONFIRMED"
            if cut_now is not None and status == "NO_SHOW" and to_dt(p["slot"] + 20) > cut_now:
                q_status, appt_status = "WAITING", "CONFIRMED"
            booked = day0 - timedelta(days=int(rng.randint(1, 8)), minutes=int(rng.randint(0, 600)))
            if real_base:
                booked = real_base - timedelta(hours=int(rng.randint(2, 48)))
            aid = db.execute("""INSERT INTO appointments (patient_id, doctor_id, department_id, appointment_date, appointment_time,
                                booking_timestamp, status, queue_number, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                             (pid, d["id"], d["dep"], date_s, slot_t, clock.ts(booked), appt_status, qn, clock.ts(booked), clock.ts(booked))).lastrowid
            joined = called = started = completed = None
            wait = dur = None
            if status == "COMPLETED":
                arr = to_dt(p["arrival"])
                joined = clock.ts(min(arr, cut_now - timedelta(minutes=1)) if cut_now else arr)
                if q_status in ("COMPLETED", "SERVING"):
                    called = clock.ts(to_dt(p["start"]) - timedelta(minutes=1))
                    started = clock.ts(to_dt(p["start"]))
                    wait = p["wait"]
                if q_status == "COMPLETED":
                    completed = clock.ts(to_dt(p["end"]))
                    dur = p["dur"]
            elif q_status == "WAITING":   # no-show not yet marked: still in the list, checked in? no
                joined = None
            db.execute("""INSERT INTO queue_entries (appointment_id, patient_id, doctor_id, department_id, queue_date, queue_number,
                          queue_seq, status, joined_at, called_at, service_started_at, completed_at, actual_waiting_time,
                          consultation_duration, patients_ahead_at_join, queue_length_at_join) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (aid, pid, d["id"], d["dep"], date_s, qn, seq, q_status, joined, called, started, completed, wait, dur,
                        p.get("ahead"), p.get("length")))
            if status != "COMPLETED":
                continue
            feats = F.build_features(department_id=d["dep"], doctor_id=d["id"], date=date_s, appointment_time=slot_t,
                                     patients_ahead=p["ahead"], queue_length=p["length"], consultation_duration_average=d["avg"],
                                     peak_set=peak_set, previous_avg=p["prev"] if p["prev"] is not None else doc_avg,
                                     doctor_avg=doc_avg, department_avg=dep_avg, delay=round(p["arrival"] - p["slot"], 2))
            arrived_before_now = cut_now is None or to_dt(p["arrival"]) <= cut_now
            if model_meta and arrived_before_now:
                pred = P.predict_features(model_dir, model_meta, feats)
                P.store_prediction(db, aid, model_meta["id"], feats, pred, when=clock.ts(to_dt(p["arrival"])))
            if q_status == "COMPLETED":
                db.execute("""INSERT INTO queue_history (appointment_id, doctor_id, department_id, date, day_of_week, appointment_hour,
                              appointment_minute, patients_ahead, queue_length, consultation_duration, consultation_duration_average,
                              actual_waiting_time, peak_hour, previous_average_wait, doctor_average_wait, department_average_wait,
                              patient_arrival_delay, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'SYNTHETIC',?)""",
                           (aid, d["id"], d["dep"], date_s, feats["day_of_week"], feats["appointment_hour"], feats["appointment_minute"],
                            feats["patients_ahead"], feats["queue_length"], p["dur"], d["avg"], p["wait"], feats["peak_hour"],
                            feats["previous_average_wait"], feats["doctor_average_wait"], feats["department_average_wait"],
                            feats["patient_arrival_delay"], completed))
                if model_meta:
                    db.execute("""UPDATE ml_predictions SET actual_waiting_time=?, absolute_error=ROUND(ABS(predicted_waiting_time-?),2)
                                  WHERE appointment_id=?""", (p["wait"], p["wait"], aid))

    def run_day(date, model_meta=None):
        db.execute("BEGIN")
        for d in docs:
            if date.weekday() in d["days"]:
                write_session(d, date, simulate_session(rng, d, doc_slots(d), date.weekday(), peak_set), model_meta)
        db.execute("COMMIT")

    # 1) pre-deployment history
    for k in range(HISTORY_DAYS, DEPLOY_DAYS, -1):
        run_day(today - timedelta(days=k))
    _inject_invalid(db, rng)
    log("history rows:", db.execute("SELECT COUNT(*) FROM queue_history").fetchone()[0])

    # 2) train + activate model v1 on data before the deployment date (real pipeline)
    cutoff = (today - timedelta(days=DEPLOY_DAYS)).isoformat()
    clock.set_now(datetime.combine(today - timedelta(days=DEPLOY_DAYS), datetime.min.time()) + timedelta(hours=7))
    m = ML.train(db, model_dir, max_date=cutoff)
    clock.set_now(None)
    if m["training_status"] != "TRAINED":
        raise SystemExit(f"Initial training failed: {m['model_metadata']}")
    ML.activate(db, m["id"])
    meta = P.active_model(db)
    log(f"model v{m['version']} trained: MAE={m['mae']} RMSE={m['rmse']} R2={m['r2_score']}")

    # 3) post-deployment days: predictions made at arrival, scored on completion
    for k in range(DEPLOY_DAYS, 0, -1):
        run_day(today - timedelta(days=k), meta)

    # 4) today's live queues
    now = real_now if today == real_now.date() else datetime.combine(today, datetime.min.time()) + timedelta(hours=11)
    db.execute("BEGIN")
    for d in docs:
        if today.weekday() not in d["days"]:
            continue
        slots = doc_slots(d)
        n = min(len(slots) - 4, int(rng.randint(7, 11)))
        sim = simulate_session(rng, d, slots, today.weekday(), peak_set, n_today=n)
        span = min([p["start"] for p in sim if "start" in p] or [slots[0]])
        # real-clock base so that roughly half the session has already happened
        elapsed = rng.uniform(45, 85)
        base = now - timedelta(minutes=span + elapsed)
        write_session(d, today, sim, meta, cut_now=now, real_base=base)
    for d in docs:
        from .services.queue_engine import recompute_positions
        recompute_positions(db, d["id"], today.isoformat())
    # demo patient's past visits (for the appointments page)
    db.execute("""UPDATE appointments SET patient_id=? WHERE id IN (
                    SELECT id FROM appointments WHERE status IN ('COMPLETED','CANCELLED') AND appointment_date < ? ORDER BY RANDOM() LIMIT 4)""",
               (demo_patient, today.isoformat()))
    db.execute("UPDATE queue_entries SET patient_id=(SELECT patient_id FROM appointments a WHERE a.id=queue_entries.appointment_id)")
    db.execute("COMMIT")
    stats = {k: db.execute(q).fetchone()[0] for k, q in {
        "appointments": "SELECT COUNT(*) FROM appointments", "history": "SELECT COUNT(*) FROM queue_history",
        "predictions": "SELECT COUNT(*) FROM ml_predictions", "scored_predictions": "SELECT COUNT(*) FROM ml_predictions WHERE actual_waiting_time IS NOT NULL",
        "today_waiting": f"SELECT COUNT(*) FROM queue_entries WHERE queue_date='{today.isoformat()}' AND status='WAITING'"}.items()}
    log(stats)
    db.close()
    return stats


def _inject_invalid(db, rng):
    """Deliberately bad rows so the validation step demonstrably rejects them."""
    db.execute("BEGIN")
    base = [dict(r) for r in db.execute("SELECT * FROM queue_history ORDER BY RANDOM() LIMIT 14")]
    cols = [c for c in base[0].keys() if c != "id"]
    corrupt = [("actual_waiting_time", -12.0), ("actual_waiting_time", 900.0), ("patients_ahead", None),
               ("actual_waiting_time", None), ("appointment_hour", 27), ("queue_length", 0),
               ("doctor_average_wait", None), ("patients_ahead", -3), ("consultation_duration_average", 0.0), ("actual_waiting_time", -1.5)]
    for row, (col, val) in zip(base[:10], corrupt):
        r = dict(row)
        r[col], r["source"] = val, "SYNTHETIC_INVALID"
        db.execute(f"INSERT INTO queue_history ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
    for row in base[10:14]:                     # exact duplicates
        r = dict(row)
        r["source"] = "SYNTHETIC_INVALID"
        db.execute(f"INSERT INTO queue_history ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
    db.execute("COMMIT")


if __name__ == "__main__":
    seed()
