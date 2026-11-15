"""End-to-end and unit tests for MediQueue AI (run: python -m pytest -q)."""
import json
import sqlite3
from datetime import date, timedelta

from backend.services.features import FEATURES, TARGET
from tests.conftest import login

TODAY = date.today().isoformat()
VIKRAM = 2  # Dr. Vikram Menon, General Medicine, works every day 14:00-18:00
KAVYA = 7   # Dr. Kavya Reddy, Orthopaedics, works every day


def free_slot(client, doctor_id, day=TODAY):
    r = client.get(f"/api/doctors/{doctor_id}/slots?date={day}")
    assert r.status_code == 200, r.json
    return next(s["time"] for s in r.json["slots"] if s["available"])


def book(client, doctor_id=VIKRAM, day=TODAY, time=None):
    time = time or free_slot(client, doctor_id, day)
    return client.post("/api/patient/appointments", json={"doctor_id": doctor_id, "date": day, "time": time})


# ---------------------------------------------------------------- authentication
def test_register_login_logout(app):
    c = app.test_client()
    r = c.post("/api/auth/register", json={"full_name": "Test Person", "email": "t@x.com", "password": "abc12345"})
    assert r.status_code == 201 and r.json["user"]["role"] == "PATIENT"
    assert "password_hash" not in r.json["user"]
    assert c.get("/api/auth/me").json["user"]["email"] == "t@x.com"
    c.post("/api/auth/logout", json={})
    assert c.get("/api/auth/me").json["user"] is None
    assert c.post("/api/auth/login", json={"email": "t@x.com", "password": "wrong999"}).status_code == 401
    login(c, "t@x.com", "abc12345")


def test_register_cannot_escalate_role(app):
    c = app.test_client()
    r = c.post("/api/auth/register", json={"full_name": "Sneaky", "email": "s@x.com", "password": "abc12345", "role": "ADMIN"})
    assert r.json["user"]["role"] == "PATIENT"
    assert c.get("/api/admin/dashboard").status_code == 403


def test_register_validation(app):
    c = app.test_client()
    assert c.post("/api/auth/register", json={"full_name": "A B", "email": "bad", "password": "abc12345"}).status_code == 400
    assert c.post("/api/auth/register", json={"full_name": "A B", "email": "a@b.com", "password": "short"}).status_code == 400
    assert c.post("/api/auth/register", json={"full_name": "A B", "email": "patient@mediqueue.demo", "password": "abc12345"}).status_code == 409


def test_role_restrictions(app, patient, staff, gm_staff):
    anon = app.test_client()
    assert anon.get("/api/patient/appointments").status_code == 401
    assert patient.get("/api/staff/dashboard").status_code == 403
    assert patient.get("/api/admin/models").status_code == 403
    assert patient.post("/api/admin/models/train", json={}).status_code == 403
    assert staff.get("/api/staff/dashboard").status_code == 200
    assert staff.post("/api/admin/models/train", json={}).status_code == 403
    assert staff.put("/api/admin/settings", json={"demo_mode": 0}).status_code == 403
    assert staff.post("/api/admin/departments", json={"name": "X", "prefix": "XX"}).status_code == 403
    # department-scoped staff cannot manage another department's queue
    assert gm_staff.get(f"/api/staff/queues/{KAVYA}").status_code == 403
    assert gm_staff.get(f"/api/staff/queues/{VIKRAM}").status_code == 200


def test_non_json_writes_rejected(patient):
    assert patient.post("/api/patient/appointments", data="doctor_id=2").status_code == 415


# ---------------------------------------------------------------- appointments
def test_valid_booking_generates_queue_number(patient, app):
    r = book(patient)
    assert r.status_code == 201, r.json
    a = r.json["appointment"]
    assert a["status"] == "CONFIRMED" and a["queue_number"].startswith("GM-")
    q = patient.get("/api/patient/queue").json["queue"]
    assert q["queue_entry"]["queue_number"] == a["queue_number"]
    assert q["queue_entry"]["joined_at"] is not None


def test_invalid_bookings(patient):
    past = (date.today() - timedelta(days=1)).isoformat()
    assert book(patient, day=past, time="14:00").status_code == 400
    assert book(patient, time="03:00").status_code == 400                       # outside schedule
    # Suresh Iyer (id 4) works Mon/Wed/Fri only: find a day he doesn't work
    d = next(date.today() + timedelta(days=i) for i in range(1, 8) if (date.today() + timedelta(days=i)).weekday() in (1, 3))
    assert book(patient, doctor_id=4, day=d.isoformat(), time="10:00").status_code == 409
    assert book(patient, doctor_id=9999, time="10:00").status_code == 404


def test_double_booking_prevented(patient, patient2):
    t = free_slot(patient, VIKRAM)
    assert book(patient, time=t).status_code == 201
    assert book(patient2, time=t).status_code == 409                               # slot taken
    assert book(patient).status_code == 409                                       # same patient same doctor same day


def test_inactive_doctor_not_bookable(admin, patient):
    admin.put(f"/api/admin/doctors/{VIKRAM}", json={"active": 0})
    assert book(patient, time="14:00").status_code in (404, 409)


def test_cancellation(patient):
    a = book(patient).json["appointment"]
    r = patient.post(f"/api/patient/appointments/{a['id']}/cancel", json={})
    assert r.status_code == 200 and r.json["appointment"]["status"] == "CANCELLED"
    assert patient.post(f"/api/patient/appointments/{a['id']}/cancel", json={}).status_code == 409


def test_queue_numbers_unique_per_department_day(app, patient, patient2):
    a1 = book(patient, doctor_id=1 if date.today().weekday() < 6 else VIKRAM).json.get("appointment")
    a2 = book(patient2).json["appointment"]
    con = sqlite3.connect(app.config["DATABASE"])
    dup = con.execute("""SELECT department_id, queue_date, queue_number, COUNT(*) FROM queue_entries
                         GROUP BY 1,2,3 HAVING COUNT(*) > 1""").fetchall()
    assert dup == []
    if a1:
        assert a1["queue_number"] != a2["queue_number"]


# ---------------------------------------------------------------- security: data isolation
def test_patient_cannot_access_other_patient(patient, patient2):
    a = book(patient).json["appointment"]
    assert patient2.get(f"/api/patient/appointments/{a['id']}").status_code == 404
    assert patient2.post(f"/api/patient/appointments/{a['id']}/cancel", json={}).status_code == 404
    ids = [x["id"] for x in patient2.get("/api/patient/appointments").json["appointments"]]
    assert a["id"] not in ids
    q2 = patient2.get("/api/patient/queue").json["queue"]
    assert q2 is None


# ---------------------------------------------------------------- queue engine
def _my_entry(patient):
    return patient.get("/api/patient/queue").json["queue"]


def _drain_called_serving(staff, doctor_id):
    for e in staff.get(f"/api/staff/queues/{doctor_id}").json["entries"]:
        if e["status"] == "SERVING":
            assert staff.post(f"/api/staff/entries/{e['id']}/complete", json={}).status_code == 200
        if e["status"] == "CALLED":
            staff.post(f"/api/staff/entries/{e['id']}/start", json={})
            staff.post(f"/api/staff/entries/{e['id']}/complete", json={})


def test_full_queue_flow_creates_history_and_scores_prediction(app, patient, staff):
    a = book(patient).json["appointment"]
    q = _my_entry(patient)
    assert q["prediction"]["status"] == "ok"
    first_pred = q["prediction"]["prediction"]["predicted_waiting_time"]
    assert first_pred >= 0
    ahead0 = q["patients_ahead"]
    hist_before = sqlite3.connect(app.config["DATABASE"]).execute("SELECT COUNT(*) FROM queue_history WHERE source='LIVE'").fetchone()[0]

    served = 0
    while True:
        _drain_called_serving(staff, VIKRAM)
        r = staff.post(f"/api/staff/queues/{VIKRAM}/call-next", json={})
        if r.status_code != 200:
            break
        e = r.json["entry"]
        if e["appointment_id"] == a["id"]:
            assert _my_entry(patient)["queue_entry"]["status"] == "CALLED"
            assert staff.post(f"/api/staff/entries/{e['id']}/complete", json={}).status_code == 409   # invalid transition
            assert staff.post(f"/api/staff/entries/{e['id']}/start", json={}).status_code == 200
            assert _my_entry(patient)["queue_entry"]["status"] == "SERVING"
            assert staff.post(f"/api/staff/entries/{e['id']}/complete", json={}).status_code == 200
            break
        served += 1
        # positions must move up for our patient
        assert _my_entry(patient)["patients_ahead"] <= ahead0
    con = sqlite3.connect(app.config["DATABASE"])
    con.row_factory = sqlite3.Row
    qe = con.execute("SELECT * FROM queue_entries WHERE appointment_id=?", (a["id"],)).fetchone()
    assert qe["status"] == "COMPLETED" and qe["actual_waiting_time"] is not None and qe["consultation_duration"] is not None
    assert con.execute("SELECT status FROM appointments WHERE id=?", (a["id"],)).fetchone()[0] == "COMPLETED"
    hist = con.execute("SELECT * FROM queue_history WHERE appointment_id=? AND source='LIVE'", (a["id"],)).fetchone()
    assert hist is not None and hist["actual_waiting_time"] == qe["actual_waiting_time"]
    assert con.execute("SELECT COUNT(*) FROM queue_history WHERE source='LIVE'").fetchone()[0] >= hist_before + 1 + served
    p = con.execute("SELECT * FROM ml_predictions WHERE appointment_id=? ORDER BY id DESC", (a["id"],)).fetchone()
    assert p["actual_waiting_time"] == qe["actual_waiting_time"]
    assert abs(p["absolute_error"] - abs(p["predicted_waiting_time"] - p["actual_waiting_time"])) < 0.01


def test_no_show_and_cancel_transitions(patient, patient2, staff):
    a1 = book(patient).json["appointment"]
    a2 = book(patient2).json["appointment"]
    entries = {e["appointment_id"]: e for e in staff.get(f"/api/staff/queues/{VIKRAM}").json["entries"]}
    e1, e2 = entries[a1["id"]], entries[a2["id"]]
    assert staff.post(f"/api/staff/entries/{e1['id']}/no-show", json={}).json["entry"]["status"] == "NO_SHOW"
    assert staff.post(f"/api/staff/entries/{e2['id']}/cancel", json={}).json["entry"]["status"] == "CANCELLED"
    assert staff.post(f"/api/staff/entries/{e1['id']}/start", json={}).status_code == 409     # terminal state
    assert patient.get(f"/api/patient/appointments/{a1['id']}").json["appointment"]["status"] == "NO_SHOW"
    assert staff.post(f"/api/staff/entries/{e1['id']}/bogus", json={}).status_code == 400


def test_positions_update_after_removal(patient, patient2, staff):
    a1 = book(patient).json["appointment"]
    a2 = book(patient2).json["appointment"]
    before = _my_entry(patient2)["patients_ahead"]
    e1 = next(e for e in staff.get(f"/api/staff/queues/{VIKRAM}").json["entries"] if e["appointment_id"] == a1["id"])
    staff.post(f"/api/staff/entries/{e1['id']}/cancel", json={})
    assert _my_entry(patient2)["patients_ahead"] == before - 1


# ---------------------------------------------------------------- ML
def test_data_quality_rejects_bad_rows(admin):
    dq = admin.get("/api/admin/data-quality").json
    assert dq["total_records"] > dq["valid_records"] >= 50
    assert dq["missing_values"] > 0 and dq["invalid_values"] > 0 and dq["duplicate_records"] > 0
    assert dq["features"] == FEATURES and TARGET not in dq["features"]


def test_no_leakage_in_features():
    assert TARGET not in FEATURES
    assert "consultation_duration" not in FEATURES
    for pii in ("patient_id", "full_name", "email", "phone"):
        assert pii not in FEATURES


def test_train_evaluate_version_activate(admin, app):
    before = admin.get("/api/admin/models").json["models"]
    r = admin.post("/api/admin/models/train", json={})
    assert r.status_code == 201, r.json
    m = r.json["model"]
    assert m["training_status"] == "TRAINED" and not m["active"]
    assert m["version"] == max(x["version"] for x in before) + 1
    assert m["mae"] > 0 and m["rmse"] >= m["mae"] and m["r2_score"] <= 1
    assert m["training_records"] + m["testing_records"] == m["dataset_size"]
    assert 0.1 < m["testing_records"] / m["dataset_size"] < 0.3
    md = m["metadata"]
    assert md["baseline"]["mae"] > 0 and md["cross_validation"]["folds"] == 5
    assert abs(sum(f["importance"] for f in md["feature_importance"]) - 1) < 0.01
    # metrics are reproducible from the persisted artifact
    import joblib, numpy as np
    art = joblib.load(f"{app.config['MODEL_DIR']}/WaitingTimeModel_v{m['version']}.joblib")
    assert art["features"] == FEATURES
    r = admin.post(f"/api/admin/models/{m['id']}/activate", json={})
    assert r.status_code == 200 and r.json["model"]["active"]
    actives = [x for x in admin.get("/api/admin/models").json["models"] if x["active"]]
    assert len(actives) == 1 and actives[0]["id"] == m["id"]


def test_future_predictions_use_new_active_model(admin, patient):
    book(patient)
    v_old = _my_entry(patient)["prediction"]["prediction"]["model_version"]
    m = admin.post("/api/admin/models/train", json={}).json["model"]
    admin.post(f"/api/admin/models/{m['id']}/activate", json={})
    v_new = _my_entry(patient)["prediction"]["prediction"]["model_version"]
    assert v_old != v_new and v_new.endswith(f"v{m['version']}")


def test_failed_training_keeps_active_model(admin, app):
    active_before = next(x for x in admin.get("/api/admin/models").json["models"] if x["active"])
    con = sqlite3.connect(app.config["DATABASE"])
    con.execute("DELETE FROM queue_history")
    con.commit()
    r = admin.post("/api/admin/models/train", json={})
    assert r.status_code == 422 and r.json["model"]["training_status"] == "FAILED"
    assert "Insufficient historical data" in r.json["model"]["error"]
    active_after = next(x for x in admin.get("/api/admin/models").json["models"] if x["active"])
    assert active_after["id"] == active_before["id"]
    assert admin.post(f"/api/admin/models/{r.json['model']['id']}/activate", json={}).status_code == 409


def test_prediction_history_and_monitoring(admin, staff):
    mon = admin.get("/api/admin/predictions").json
    assert mon["summary"]["n"] > 0 and mon["summary"]["live_mae"] is not None
    scored = [p for p in mon["recent"] if p["actual_waiting_time"] is not None]
    for p in scored:
        assert abs(p["absolute_error"] - abs(p["predicted_waiting_time"] - p["actual_waiting_time"])) < 0.01
    assert staff.get("/api/staff/predictions").status_code == 200


def test_no_active_model_message(admin, patient, app):
    con = sqlite3.connect(app.config["DATABASE"])
    con.execute("UPDATE ml_models SET active=0")
    con.commit()
    book(patient)
    p = _my_entry(patient)["prediction"]
    assert p["status"] == "no_model" and p["prediction"] is None


# ---------------------------------------------------------------- analytics + admin
def test_analytics_from_db(admin):
    a = admin.get("/api/admin/analytics").json
    assert a["avg_wait_by_department"] and a["avg_wait_by_hour"] and a["patients_served_per_day"]
    assert 0 <= a["totals"]["no_show_rate"] <= 100
    assert len(a["predicted_vs_actual"]) > 0


def test_admin_crud_and_settings(admin):
    r = admin.post("/api/admin/departments", json={"name": "Neurology", "prefix": "NEU"})
    assert r.status_code == 201
    dep = r.json["department"]["id"]
    assert admin.post("/api/admin/departments", json={"name": "Neuro 2", "prefix": "NEU"}).status_code == 409
    r = admin.post("/api/admin/doctors", json={"name": "Dr. Test", "department_id": dep, "consultation_duration_average": 10,
                                              "working_days": [0, 1, 2, 3, 4, 5, 6], "start_time": "09:00", "end_time": "10:00"})
    assert r.status_code == 201
    assert admin.post("/api/admin/doctors", json={"name": "Dr. Bad", "department_id": dep, "start_time": "11:00", "end_time": "10:00"}).status_code == 400
    r = admin.post("/api/admin/staff", json={"full_name": "New Staff", "email": "ns@x.com", "password": "abc12345", "department_id": dep})
    assert r.status_code == 201 and r.json["user"]["role"] == "STAFF"
    assert admin.put("/api/admin/settings", json={"slot_minutes": 7}).status_code == 400
    assert admin.put("/api/admin/settings", json={"repredict_minutes": 5}).json["settings"]["repredict_minutes"] == "5"
    csv = admin.get("/api/admin/history/export.csv")
    assert csv.status_code == 200 and csv.data.startswith(b"id,")


# ---------------------------------------------------------------- audit additions
def test_chronological_split_has_no_future_leakage(admin, app):
    from backend.ml.pipeline import load_history, validate, chronological_split
    con = sqlite3.connect(app.config["DATABASE"])
    con.row_factory = sqlite3.Row
    clean, _ = validate(load_history(con))
    tr, te, cutoff = chronological_split(clean)
    assert tr["date"].max() < te["date"].min()
    assert 0.1 < len(te) / len(clean) < 0.3
    m = admin.post("/api/admin/models/train", json={}).json["model"]
    sp = m["metadata"]["split"]
    assert sp["method"] == "chronological" and sp["train_period"][1] < sp["test_period"][0]
    assert m["testing_records"] == len(te) and m["training_records"] == len(tr)


def test_metrics_reproducible_from_artifact(admin, app):
    import joblib
    import numpy as np
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from backend.ml.pipeline import load_history, validate, chronological_split
    m = admin.post("/api/admin/models/train", json={}).json["model"]
    con = sqlite3.connect(app.config["DATABASE"])
    con.row_factory = sqlite3.Row
    tr, te, _ = chronological_split(validate(load_history(con))[0])
    model = joblib.load(f"{app.config['MODEL_DIR']}/WaitingTimeModel_v{m['version']}.joblib")["model"]
    assert type(model.named_steps["rf"]).__name__ == "RandomForestRegressor"
    p = np.clip(model.predict(te[FEATURES]), 0, None)
    assert abs(mean_absolute_error(te[TARGET], p) - m["mae"]) < 1e-3
    assert abs(np.sqrt(mean_squared_error(te[TARGET], p)) - m["rmse"]) < 1e-3
    assert abs(r2_score(te[TARGET], p) - m["r2_score"]) < 1e-4


def test_implausible_live_records_excluded(app):
    from backend.ml.pipeline import load_history, validate
    con = sqlite3.connect(app.config["DATABASE"])
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM queue_history WHERE source='SYNTHETIC' LIMIT 1").fetchone())
    row.pop("id")
    row.update(consultation_duration=0.0, source="LIVE", appointment_id=None)
    con.execute(f"INSERT INTO queue_history ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    con.commit()
    new_id = con.execute("SELECT MAX(id) FROM queue_history").fetchone()[0]
    clean, rep = validate(load_history(con))
    assert new_id not in set(clean["id"]) and rep["implausible_records"] >= 1


def test_settings_reject_bad_demo_mode(admin):
    assert admin.put("/api/admin/settings", json={"demo_mode": 5}).status_code == 400
