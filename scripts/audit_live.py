"""Live end-to-end audit against a RUNNING MediQueue AI server (default http://127.0.0.1:5000).

    python run.py                      # terminal 1
    python scripts/audit_live.py       # terminal 2   (BASE_URL env var to override)

Performs the real patient -> staff -> admin workflow over HTTP, then inspects the
SQLite database and the persisted model artifact to verify every link in the chain.
Prints PASS/FAIL per check and exits non-zero on any failure. It writes demo data
(a booking, a completed consultation, a new model version) into the database.
"""
import json
import os
import sqlite3
import sys
import threading
from datetime import date, timedelta

import requests

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ["no_proxy"] = os.environ["NO_PROXY"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
DB = os.environ.get("MEDIQUEUE_DB", os.path.join(ROOT, "instance", "mediqueue.db"))
MODEL_DIR = os.path.join(ROOT, "ml_models")
PW = "Demo@1234"
results = []


def check(name, cond, evidence=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{evidence}]" if evidence != "" else ""))
    return cond


def session(email):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW})
    check(f"login {email}", r.status_code == 200, r.status_code)
    return s


def db():
    # same connection settings as the application (foreign_keys=ON is per-connection in SQLite)
    from backend.db import connect
    return connect(DB)


def main():
    today = date.today().isoformat()
    print(f"== Server {BASE}")
    check("frontend index loads", requests.get(BASE + "/").status_code == 200)
    check("static JS loads", requests.get(BASE + "/js/core.js").status_code == 200)

    pat, pat2, staff, gm, admin = (session(e) for e in (
        "patient@mediqueue.demo", "patient2@mediqueue.demo", "staff@mediqueue.demo", "gm.staff@mediqueue.demo", "admin@mediqueue.demo"))
    for s, u in ((pat, "/api/patient/dashboard"), (staff, "/api/staff/dashboard"), (admin, "/api/admin/dashboard")):
        check(f"dashboard {u}", s.get(BASE + u).status_code == 200)

    print("\n== Patient booking flow")
    deps = pat.get(BASE + "/api/departments").json()["departments"]
    gm_dep = next(d for d in deps if d["prefix"] == "GM")
    docs = pat.get(f"{BASE}/api/doctors?department_id={gm_dep['id']}").json()["doctors"]
    doc = next(d for d in docs if d["name"] == "Dr. Vikram Menon")
    slots = pat.get(f"{BASE}/api/doctors/{doc['id']}/slots?date={today}").json()["slots"]
    slot = next(s["time"] for s in slots if s["available"])
    # cancel any leftover active booking of the demo patient with this doctor today (re-runs)
    for a in pat.get(BASE + "/api/patient/appointments").json()["appointments"]:
        if a["appointment_date"] == today and a["doctor_id"] == doc["id"] and a["status"] in ("BOOKED", "CONFIRMED") and a["queue_status"] == "WAITING":
            pat.post(f"{BASE}/api/patient/appointments/{a['id']}/cancel", json={})
    r = pat.post(BASE + "/api/patient/appointments", json={"doctor_id": doc["id"], "date": today, "time": slot})
    check("book appointment", r.status_code == 201, r.json())
    appt = r.json()["appointment"]
    check("queue number generated (GM-NNN)", appt["queue_number"].startswith("GM-") and len(appt["queue_number"]) == 6, appt["queue_number"])
    q = pat.get(BASE + "/api/patient/queue").json()["queue"]
    check("patient sees own queue entry", q["queue_entry"]["queue_number"] == appt["queue_number"], f"ahead={q['patients_ahead']} length={q['queue_length']}")
    pred = q["prediction"]
    check("ML prediction returned", pred["status"] == "ok", pred.get("prediction"))
    first_pred_id = pred["prediction"]["id"]

    print("\n== Prediction provenance (not hard-coded / random / LLM)")
    import joblib
    import numpy as np
    from backend.ml.pipeline import prepare_input
    from backend.services.features import FEATURES
    c = db()
    prow = c.execute("SELECT p.*, m.artifact_path, m.version FROM ml_predictions p JOIN ml_models m ON m.id=p.model_id WHERE p.id=?", (first_pred_id,)).fetchone()
    feats = json.loads(prow["input_features"])
    art = joblib.load(os.path.join(MODEL_DIR, prow["artifact_path"]))
    model = art["model"]
    check("artifact is sklearn Pipeline(OneHot+RandomForestRegressor)", type(model.named_steps["rf"]).__name__ == "RandomForestRegressor", type(model.named_steps["rf"]).__name__)
    recomputed = round(float(max(0.0, model.predict(prepare_input([feats]))[0])), 1)
    check("stored prediction == active artifact.predict(stored features)", abs(recomputed - prow["predicted_waiting_time"]) < 0.05, f"{recomputed} vs {prow['predicted_waiting_time']}")
    again = round(float(model.predict(prepare_input([feats]))[0]), 3)
    check("deterministic (same input -> same output)", again == round(float(model.predict(prepare_input([feats]))[0]), 3))
    f2 = dict(feats, patients_ahead=feats["patients_ahead"] + 6, queue_length=feats["queue_length"] + 6)
    check("responds to inputs (6 more patients ahead -> longer wait)", model.predict(prepare_input([f2]))[0] > model.predict(prepare_input([feats]))[0],
          f"{model.predict(prepare_input([feats]))[0]:.1f} -> {model.predict(prepare_input([f2]))[0]:.1f}")
    check("no PII / target in stored inputs", set(feats) == set(FEATURES), sorted(feats))

    print("\n== Staff queue flow")
    check("dept-scoped staff blocked from other dept queue", gm.get(f"{BASE}/api/staff/queues/7").status_code == 403)
    served = 0
    target_entry = None
    for _ in range(60):
        entries = staff.get(f"{BASE}/api/staff/queues/{doc['id']}").json()["entries"]
        for e in entries:
            if e["status"] == "SERVING" and e["appointment_id"] != appt["id"]:
                staff.post(f"{BASE}/api/staff/entries/{e['id']}/complete", json={})
            if e["status"] == "CALLED" and e["appointment_id"] != appt["id"]:
                staff.post(f"{BASE}/api/staff/entries/{e['id']}/start", json={})
                staff.post(f"{BASE}/api/staff/entries/{e['id']}/complete", json={})
        r = staff.post(f"{BASE}/api/staff/queues/{doc['id']}/call-next", json={})
        if r.status_code != 200:
            print("   call-next:", r.json())
            break
        e = r.json()["entry"]
        if e["appointment_id"] == appt["id"]:
            target_entry = e
            break
        served += 1
    check("staff CALL NEXT reached our patient", target_entry is not None, f"{served} patients served before")
    check("patient sees CALLED", pat.get(BASE + "/api/patient/queue").json()["queue"]["queue_entry"]["status"] == "CALLED")
    check("invalid transition CALLED->COMPLETED rejected", staff.post(f"{BASE}/api/staff/entries/{target_entry['id']}/complete", json={}).status_code == 409)
    check("start service", staff.post(f"{BASE}/api/staff/entries/{target_entry['id']}/start", json={}).status_code == 200)
    check("patient sees SERVING", pat.get(BASE + "/api/patient/queue").json()["queue"]["queue_entry"]["status"] == "SERVING")
    check("complete consultation", staff.post(f"{BASE}/api/staff/entries/{target_entry['id']}/complete", json={}).status_code == 200)

    c = db()
    qe = c.execute("SELECT * FROM queue_entries WHERE appointment_id=?", (appt["id"],)).fetchone()
    check("actual waiting time = service_started_at - joined_at", qe["actual_waiting_time"] is not None, f"{qe['joined_at']} -> {qe['service_started_at']} = {qe['actual_waiting_time']} min")
    check("consultation duration calculated", qe["consultation_duration"] is not None, f"{qe['consultation_duration']} min")
    check("appointment status COMPLETED", c.execute("SELECT status FROM appointments WHERE id=?", (appt["id"],)).fetchone()[0] == "COMPLETED")
    h = c.execute("SELECT * FROM queue_history WHERE appointment_id=? AND source='LIVE'", (appt["id"],)).fetchone()
    check("QueueHistory LIVE row created", h is not None and h["actual_waiting_time"] == qe["actual_waiting_time"], dict(h) if h else None)
    preds = c.execute("SELECT * FROM ml_predictions WHERE appointment_id=?", (appt["id"],)).fetchall()
    check("every prediction linked to actual wait + absolute error",
          preds and all(p["actual_waiting_time"] == qe["actual_waiting_time"] and abs(p["absolute_error"] - abs(p["predicted_waiting_time"] - p["actual_waiting_time"])) < 0.01 for p in preds),
          [(p["predicted_waiting_time"], p["actual_waiting_time"], p["absolute_error"]) for p in preds])

    print("\n== Admin ML flow")
    dq = admin.get(BASE + "/api/admin/data-quality").json()
    check("dataset inspected", dq["valid_records"] > 50, {k: dq[k] for k in ("total_records", "missing_values", "invalid_values", "duplicate_records", "valid_records", "expected_training_records", "expected_testing_records", "test_period_starts")})
    before = admin.get(BASE + "/api/admin/models").json()["models"]
    r = admin.post(BASE + "/api/admin/models/train", json={})
    check("train model", r.status_code == 201, r.status_code)
    m = r.json()["model"]
    md = m["metadata"]
    check("new version = previous max + 1", m["version"] == max(x["version"] for x in before) + 1, f"v{m['version']}")
    check("chronological split: max(train date) < min(test date)", md["split"]["train_period"][1] < md["split"]["test_period"][0], md["split"])

    # recompute metrics independently from QueueHistory + saved artifact
    from backend.ml.pipeline import load_history, validate, chronological_split
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    clean, _ = validate(load_history(c))
    tr, te, _cut = chronological_split(clean)
    art2 = joblib.load(os.path.join(MODEL_DIR, f"WaitingTimeModel_v{m['version']}.joblib"))
    p = np.clip(art2["model"].predict(te[FEATURES]), 0, None)
    mae, rmse, r2 = mean_absolute_error(te["actual_waiting_time"], p), np.sqrt(mean_squared_error(te["actual_waiting_time"], p)), r2_score(te["actual_waiting_time"], p)
    check("MAE recomputed from artifact matches stored", abs(mae - m["mae"]) < 0.001, f"{mae:.3f} vs {m['mae']}")
    check("RMSE recomputed matches stored", abs(rmse - m["rmse"]) < 0.001, f"{rmse:.3f} vs {m['rmse']}")
    check("R2 recomputed matches stored", abs(r2 - m["r2_score"]) < 0.0001, f"{r2:.4f} vs {m['r2_score']}")
    imp = art2["model"].named_steps["rf"].feature_importances_
    check("feature importance comes from trained forest (sums to 1)", abs(imp.sum() - 1) < 1e-6 and abs(sum(f["importance"] for f in md["feature_importance"]) - 1) < 0.01)
    base = (te["patients_ahead"] * te["consultation_duration_average"]).values
    bmae = mean_absolute_error(te["actual_waiting_time"], base)
    check("baseline MAE recomputed matches stored", abs(bmae - md["baseline"]["mae"]) < 0.001, f"{bmae:.3f}")
    print(f"   v{m['version']}: train={m['training_records']} test={m['testing_records']} MAE={m['mae']} RMSE={m['rmse']} R2={m['r2_score']} "
          f"baseline MAE={md['baseline']['mae']:.3f} RMSE={md['baseline']['rmse']:.3f} improvement={md['improvement_over_baseline_pct']}%")

    check("staff cannot activate models", staff.post(f"{BASE}/api/admin/models/{m['id']}/activate", json={}).status_code == 403)
    r = admin.post(f"{BASE}/api/admin/models/{m['id']}/activate", json={})
    check("admin activates new model", r.status_code == 200 and r.json()["model"]["active"])
    actives = c.execute("SELECT COUNT(*) FROM ml_models WHERE active=1").fetchone()[0]
    check("exactly one active model", actives == 1)
    try:
        c.execute("UPDATE ml_models SET active=1 WHERE active=0")
        c.commit()
        check("DB rejects a second active model", False)
    except sqlite3.IntegrityError:
        c.rollback()
        check("DB rejects a second active model", True)

    # future prediction from new model: patient 2 books
    slots = pat2.get(f"{BASE}/api/doctors/{doc['id']}/slots?date={today}").json()["slots"]
    slot2 = next(s["time"] for s in slots if s["available"])
    a2 = pat2.post(BASE + "/api/patient/appointments", json={"doctor_id": doc["id"], "date": today, "time": slot2}).json()["appointment"]
    p2 = pat2.get(BASE + "/api/patient/queue").json()["queue"]["prediction"]["prediction"]
    check("future prediction uses newly active model", p2["model_version"].endswith(f"v{m['version']}"), p2)
    pat2.post(f"{BASE}/api/patient/appointments/{a2['id']}/cancel", json={})

    print("\n== Security")
    check("patient2 cannot read patient1 appointment", pat2.get(f"{BASE}/api/patient/appointments/{appt['id']}").status_code == 404)
    check("patient2 cannot cancel patient1 appointment", pat2.post(f"{BASE}/api/patient/appointments/{appt['id']}/cancel", json={}).status_code == 404)
    check("patient blocked from staff API", pat.get(BASE + "/api/staff/queues").status_code == 403)
    check("patient blocked from admin API", pat.get(BASE + "/api/admin/history").status_code == 403)
    check("staff blocked from admin CRUD", staff.post(BASE + "/api/admin/doctors", json={"name": "Dr. X"}).status_code == 403)
    check("anonymous blocked", requests.get(BASE + "/api/patient/queue").status_code == 401)
    check("password hash never returned", "password_hash" not in json.dumps(admin.get(BASE + "/api/admin/staff").json()))

    print("\n== Integrity")
    try:
        c.execute("INSERT INTO appointments (patient_id, doctor_id, department_id, appointment_date, appointment_time, booking_timestamp, status) VALUES (999999, 1, 1, '2030-01-01', '09:00', 'x', 'BOOKED')")
        c.commit()
        check("foreign keys enforced", False)
    except sqlite3.IntegrityError:
        c.rollback()
        check("foreign keys enforced", True)
    dup = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM queue_entries GROUP BY department_id, queue_date, queue_number HAVING COUNT(*)>1)").fetchone()[0]
    check("queue numbers unique per department/day", dup == 0)
    # concurrent double booking: 8 threads race for the same slot
    future = next(date.today() + timedelta(days=i) for i in range(1, 8))
    fs = pat.get(f"{BASE}/api/doctors/{doc['id']}/slots?date={future.isoformat()}").json()["slots"]
    race_slot = next(s["time"] for s in fs if s["available"])
    users = []
    for i in range(8):
        s = requests.Session()
        s.post(f"{BASE}/api/auth/register", json={"full_name": f"Race Tester {i}", "email": f"race{i}.{os.getpid()}@test.local", "password": "abc12345"})
        users.append(s)
    codes = []
    def go(s):
        codes.append(s.post(BASE + "/api/patient/appointments", json={"doctor_id": doc["id"], "date": future.isoformat(), "time": race_slot}).status_code)
    th = [threading.Thread(target=go, args=(s,)) for s in users]
    [t.start() for t in th]
    [t.join() for t in th]
    check("concurrent booking of one slot: exactly one succeeds", codes.count(201) == 1 and codes.count(409) == 7, sorted(codes))

    failed = [n for n, ok in results if not ok]
    print(f"\n== {len(results)} checks, {len(results) - len(failed)} passed, {len(failed)} failed")
    for n in failed:
        print("   FAILED:", n)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
