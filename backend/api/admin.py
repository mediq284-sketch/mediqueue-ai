"""Admin-only endpoints. Every route is protected server-side with @login_required('ADMIN')."""
import csv
import io
import json
import re

from flask import Blueprint, jsonify, request, current_app, Response

from .. import APIError, clock
from ..auth import login_required, hash_password, PUBLIC_USER_FIELDS
from ..db import get_db, rows, one, tx, DEFAULT_SETTINGS
from ..ml import pipeline as ML
from ..services import analytics
from .auth_api import validate_profile, validate_password

bp = Blueprint("admin", __name__, url_prefix="/api/admin")
ADMIN = "ADMIN"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def body():
    return request.get_json(silent=True) or {}


@bp.get("/dashboard")
@login_required(ADMIN)
def dashboard():
    db = get_db()
    today = clock.now().strftime("%Y-%m-%d")
    c = lambda q, a=(): db.execute(q, a).fetchone()[0]
    active = one(db.execute("SELECT id, model_name, version, mae, rmse, r2_score, training_date FROM ml_models WHERE active=1"))
    return jsonify(
        departments=c("SELECT COUNT(*) FROM departments WHERE active=1"),
        doctors=c("SELECT COUNT(*) FROM doctors WHERE active=1"),
        staff=c("SELECT COUNT(*) FROM users WHERE role='STAFF' AND active=1"),
        patients=c("SELECT COUNT(*) FROM users WHERE role='PATIENT'"),
        appointments_today=c("SELECT COUNT(*) FROM appointments WHERE appointment_date=?", (today,)),
        waiting_now=c("SELECT COUNT(*) FROM queue_entries WHERE queue_date=? AND status IN ('WAITING','CALLED')", (today,)),
        serving_now=c("SELECT COUNT(*) FROM queue_entries WHERE queue_date=? AND status='SERVING'", (today,)),
        completed_today=c("SELECT COUNT(*) FROM queue_entries WHERE queue_date=? AND status='COMPLETED'", (today,)),
        history_records=c("SELECT COUNT(*) FROM queue_history"),
        live_history_records=c("SELECT COUNT(*) FROM queue_history WHERE source='LIVE'"),
        model_versions=c("SELECT COUNT(*) FROM ml_models"),
        active_model=active,
        monitoring=analytics.prediction_monitoring(db, None, 8),
    )


# ------------------------------------------------------------ departments
@bp.get("/departments")
@login_required(ADMIN)
def list_departments():
    return jsonify(departments=rows(get_db().execute(
        """SELECT dep.*, (SELECT COUNT(*) FROM doctors d WHERE d.department_id=dep.id) AS doctor_count
           FROM departments dep ORDER BY dep.name""")))


def _dept_payload(b, existing=None):
    name = (b.get("name") if "name" in b else (existing or {}).get("name") or "").strip()
    prefix = (b.get("prefix") if "prefix" in b else (existing or {}).get("prefix") or "").strip().upper()
    if len(name) < 2:
        raise APIError("Department name is required.", 400, "invalid")
    if not re.match(r"^[A-Z]{2,4}$", prefix):
        raise APIError("Queue prefix must be 2-4 letters (e.g. GM).", 400, "invalid")
    desc = b.get("description", (existing or {}).get("description"))
    active = 1 if b.get("active", (existing or {}).get("active", 1)) in (1, True, "1", "true") else 0
    return name, prefix, desc, active


@bp.post("/departments")
@login_required(ADMIN)
def create_department():
    db = get_db()
    name, prefix, desc, active = _dept_payload(body())
    try:
        cur = db.execute("INSERT INTO departments (name, prefix, description, active, created_at) VALUES (?,?,?,?,?)",
                         (name, prefix, desc, active, clock.ts()))
    except Exception:
        raise APIError("A department with that name or prefix already exists.", 409, "duplicate")
    return jsonify(department=one(db.execute("SELECT * FROM departments WHERE id=?", (cur.lastrowid,)))), 201


@bp.put("/departments/<int:dep_id>")
@login_required(ADMIN)
def update_department(dep_id):
    db = get_db()
    ex = one(db.execute("SELECT * FROM departments WHERE id=?", (dep_id,)))
    if not ex:
        raise APIError("Department not found.", 404, "not_found")
    name, prefix, desc, active = _dept_payload(body(), ex)
    try:
        db.execute("UPDATE departments SET name=?, prefix=?, description=?, active=? WHERE id=?", (name, prefix, desc, active, dep_id))
    except Exception:
        raise APIError("A department with that name or prefix already exists.", 409, "duplicate")
    return jsonify(department=one(db.execute("SELECT * FROM departments WHERE id=?", (dep_id,))))


# ------------------------------------------------------------ doctors
@bp.get("/doctors")
@login_required(ADMIN)
def list_doctors():
    return jsonify(doctors=rows(get_db().execute(
        "SELECT d.*, dep.name AS department_name FROM doctors d JOIN departments dep ON dep.id=d.department_id ORDER BY dep.name, d.name")))


def _doctor_payload(db, b, ex=None):
    ex = ex or {}
    g = lambda k, default=None: b.get(k, ex.get(k, default))
    name = (g("name") or "").strip()
    dep = g("department_id")
    if len(name) < 3:
        raise APIError("Doctor name is required.", 400, "invalid")
    if not dep or not db.execute("SELECT 1 FROM departments WHERE id=?", (dep,)).fetchone():
        raise APIError("Please choose a valid department.", 400, "invalid")
    try:
        dur = float(g("consultation_duration_average", 12))
    except (TypeError, ValueError):
        dur = -1
    if not 1 <= dur <= 120:
        raise APIError("Average consultation duration must be between 1 and 120 minutes.", 400, "invalid")
    days = g("working_days", "0,1,2,3,4,5")
    if isinstance(days, list):
        days = ",".join(str(int(x)) for x in days)
    if not re.match(r"^[0-6](,[0-6])*$", days or ""):
        raise APIError("Please choose at least one working day.", 400, "invalid")
    st, en = g("start_time", "09:00"), g("end_time", "13:00")
    if not (TIME_RE.match(st or "") and TIME_RE.match(en or "")) or st >= en:
        raise APIError("Please enter a valid schedule (start before end, HH:MM).", 400, "invalid")
    active = 1 if g("active", 1) in (1, True, "1", "true") else 0
    return (name, int(dep), g("specialization"), dur, days, st, en, active)


@bp.post("/doctors")
@login_required(ADMIN)
def create_doctor():
    db = get_db()
    p = _doctor_payload(db, body())
    cur = db.execute("""INSERT INTO doctors (name, department_id, specialization, consultation_duration_average, working_days,
                        start_time, end_time, active, created_at) VALUES (?,?,?,?,?,?,?,?,?)""", p + (clock.ts(),))
    return jsonify(doctor=one(db.execute("SELECT * FROM doctors WHERE id=?", (cur.lastrowid,)))), 201


@bp.put("/doctors/<int:doc_id>")
@login_required(ADMIN)
def update_doctor(doc_id):
    db = get_db()
    ex = one(db.execute("SELECT * FROM doctors WHERE id=?", (doc_id,)))
    if not ex:
        raise APIError("Doctor not found.", 404, "not_found")
    p = _doctor_payload(db, body(), ex)
    db.execute("""UPDATE doctors SET name=?, department_id=?, specialization=?, consultation_duration_average=?,
                  working_days=?, start_time=?, end_time=?, active=? WHERE id=?""", p + (doc_id,))
    return jsonify(doctor=one(db.execute("SELECT * FROM doctors WHERE id=?", (doc_id,))))


# ------------------------------------------------------------ staff
@bp.get("/staff")
@login_required(ADMIN)
def list_staff():
    return jsonify(staff=rows(get_db().execute(
        f"""SELECT {', '.join('u.' + f.strip() for f in PUBLIC_USER_FIELDS.split(','))}, dep.name AS department_name
            FROM users u LEFT JOIN departments dep ON dep.id=u.department_id
            WHERE u.role IN ('STAFF','ADMIN') ORDER BY u.role, u.full_name""")))


@bp.post("/staff")
@login_required(ADMIN)
def create_staff():
    b, db = body(), get_db()
    full_name, email, phone = (b.get("full_name") or "").strip(), (b.get("email") or "").strip().lower(), (b.get("phone") or "").strip()
    validate_profile(full_name, email, phone)
    validate_password(b.get("password"))
    role = b.get("role", "STAFF")
    if role not in ("STAFF", "ADMIN"):
        raise APIError("Role must be STAFF or ADMIN.", 400, "invalid")
    dep = b.get("department_id") or None
    try:
        cur = db.execute("""INSERT INTO users (full_name, email, phone, role, password_hash, department_id, created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?,?)""", (full_name, email, phone or None, role, hash_password(b["password"]), dep, clock.ts(), clock.ts()))
    except Exception:
        raise APIError("An account with this email already exists.", 409, "email_taken")
    return jsonify(user=one(db.execute(f"SELECT {PUBLIC_USER_FIELDS} FROM users WHERE id=?", (cur.lastrowid,)))), 201


@bp.put("/staff/<int:user_id>")
@login_required(ADMIN)
def update_staff(user_id):
    b, db = body(), get_db()
    u = one(db.execute("SELECT * FROM users WHERE id=? AND role IN ('STAFF','ADMIN')", (user_id,)))
    if not u:
        raise APIError("Staff member not found.", 404, "not_found")
    from ..auth import current_user
    active = 1 if b.get("active", u["active"]) in (1, True, "1", "true") else 0
    if user_id == current_user()["id"] and not active:
        raise APIError("You cannot deactivate your own account.", 409, "self")
    dep = b.get("department_id", u["department_id"]) or None
    db.execute("UPDATE users SET department_id=?, active=?, updated_at=? WHERE id=?", (dep, active, clock.ts(), user_id))
    return jsonify(user=one(db.execute(f"SELECT {PUBLIC_USER_FIELDS} FROM users WHERE id=?", (user_id,))))


# ------------------------------------------------------------ appointments / queues / history
@bp.get("/appointments")
@login_required(ADMIN)
def all_appointments():
    a = request.args
    where, args = ["1=1"], []
    for col, key in (("a.appointment_date", "date"), ("a.status", "status"), ("a.department_id", "department_id"), ("a.doctor_id", "doctor_id")):
        if a.get(key):
            where.append(f"{col}=?")
            args.append(a.get(key))
    limit = min(a.get("limit", 200, type=int), 1000)
    q = f"""SELECT a.id, a.appointment_date, a.appointment_time, a.status, a.queue_number, a.booking_timestamp,
                   u.full_name AS patient_name, d.name AS doctor_name, dep.name AS department_name
            FROM appointments a JOIN users u ON u.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id
            JOIN departments dep ON dep.id=a.department_id WHERE {' AND '.join(where)}
            ORDER BY a.appointment_date DESC, a.appointment_time DESC LIMIT ?"""
    total = get_db().execute(f"SELECT COUNT(*) FROM appointments a WHERE {' AND '.join(where)}", args).fetchone()[0]
    return jsonify(total=total, appointments=rows(get_db().execute(q, args + [limit])))


@bp.get("/history")
@login_required(ADMIN)
def history():
    page = max(1, request.args.get("page", 1, type=int))
    size = 50
    db = get_db()
    src = request.args.get("source")
    w, a = ("WHERE h.source=?", [src]) if src else ("", [])
    total = db.execute(f"SELECT COUNT(*) FROM queue_history h {w}", a).fetchone()[0]
    data = rows(db.execute(f"""SELECT h.*, d.name AS doctor_name, dep.name AS department_name FROM queue_history h
                               LEFT JOIN doctors d ON d.id=h.doctor_id LEFT JOIN departments dep ON dep.id=h.department_id
                               {w} ORDER BY h.date DESC, h.id DESC LIMIT ? OFFSET ?""", a + [size, (page - 1) * size]))
    return jsonify(total=total, page=page, page_size=size, records=data)


@bp.get("/history/export.csv")
@login_required(ADMIN)
def export_history():
    data = rows(get_db().execute("SELECT * FROM queue_history ORDER BY id"))
    buf = io.StringIO()
    if data:
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=mediqueue_queue_history.csv"})


@bp.get("/data-quality")
@login_required(ADMIN)
def data_quality():
    return jsonify(ML.dataset_summary(get_db()))


# ------------------------------------------------------------ ML
def _model_view(m, full=False):
    meta = json.loads(m["model_metadata"]) if m.get("model_metadata") else {}
    out = {k: m[k] for k in ("id", "model_name", "model_type", "version", "training_date", "dataset_size", "training_records",
                             "testing_records", "feature_count", "mae", "rmse", "r2_score", "training_status", "active")}
    out["active"] = bool(out["active"])
    out["error"] = meta.get("error")
    out["baseline_mae"] = meta.get("baseline", {}).get("mae")
    out["improvement_over_baseline_pct"] = meta.get("improvement_over_baseline_pct")
    out["cv_mae"] = (meta.get("cross_validation") or {}).get("mae_mean")
    if full:
        out["metadata"] = meta
    return out


@bp.get("/models")
@login_required(ADMIN)
def models():
    ms = rows(get_db().execute("SELECT * FROM ml_models ORDER BY version DESC"))
    return jsonify(models=[_model_view(m) for m in ms])


@bp.get("/models/<int:model_id>")
@login_required(ADMIN)
def model_detail(model_id):
    m = one(get_db().execute("SELECT * FROM ml_models WHERE id=?", (model_id,)))
    if not m:
        raise APIError("Model not found.", 404, "not_found")
    return jsonify(model=_model_view(m, full=True))


@bp.post("/models/train")
@login_required(ADMIN)
def train():
    db = get_db()
    if db.execute("SELECT 1 FROM ml_models WHERE training_status='TRAINING'").fetchone():
        raise APIError("A training run is already in progress.", 409, "busy")
    m = ML.train(db, current_app.config["MODEL_DIR"])
    status = 201 if m["training_status"] == "TRAINED" else 422
    return jsonify(model=_model_view(m, full=True)), status


@bp.post("/models/<int:model_id>/activate")
@login_required(ADMIN)
def activate(model_id):
    return jsonify(model=_model_view(ML.activate(get_db(), model_id)))


@bp.get("/predictions")
@login_required(ADMIN)
def predictions():
    return jsonify(analytics.prediction_monitoring(get_db(), None, request.args.get("limit", 100, type=int)))


@bp.get("/analytics")
@login_required(ADMIN)
def admin_analytics():
    return jsonify(analytics.compute(get_db(), request.args.get("days", 30, type=int), request.args.get("department_id", type=int)))


# ------------------------------------------------------------ settings
EDITABLE = {"hospital_name": str, "slot_minutes": int, "peak_hours": str, "demo_mode": int,
            "cancellation_cutoff_minutes": int, "repredict_minutes": int, "max_booking_days_ahead": int}


@bp.get("/settings")
@login_required(ADMIN)
def get_settings():
    return jsonify(settings={r["key"]: r["value"] for r in get_db().execute("SELECT * FROM settings")})


@bp.put("/settings")
@login_required(ADMIN)
def put_settings():
    b, db = body(), get_db()
    with tx(db):
        for k, v in b.items():
            if k not in EDITABLE:
                raise APIError(f"Unknown setting: {k}", 400, "invalid")
            if EDITABLE[k] is int:
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    raise APIError(f"{k} must be a number.", 400, "invalid")
                if k == "demo_mode" and iv not in (0, 1):
                    raise APIError("Demo mode must be 0 (off) or 1 (on).", 400, "invalid")
                if k == "slot_minutes" and iv not in (5, 10, 15, 20, 30):
                    raise APIError("Slot length must be 5, 10, 15, 20 or 30 minutes.", 400, "invalid")
                if iv < 0:
                    raise APIError(f"{k} cannot be negative.", 400, "invalid")
                v = str(iv)
            if k == "peak_hours" and not re.match(r"^\s*(\d{1,2}\s*,\s*)*\d{1,2}\s*$", str(v)):
                raise APIError("Peak hours must be a comma-separated list of hours, e.g. 10,11,12.", 400, "invalid")
            db.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    return get_settings()
