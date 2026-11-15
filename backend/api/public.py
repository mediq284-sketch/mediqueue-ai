from flask import Blueprint, jsonify, request

from .. import APIError
from ..db import get_db, rows, one, get_setting
from ..services import booking

bp = Blueprint("public", __name__, url_prefix="/api")

DOCTOR_FIELDS = """d.id, d.name, d.department_id, d.specialization, d.consultation_duration_average, d.working_days,
                   d.start_time, d.end_time, dep.name AS department_name, dep.prefix"""


@bp.get("/departments")
def departments():
    return jsonify(departments=rows(get_db().execute(
        """SELECT dep.id, dep.name, dep.prefix, dep.description,
                  (SELECT COUNT(*) FROM doctors d WHERE d.department_id=dep.id AND d.active=1) AS doctor_count
           FROM departments dep WHERE dep.active=1 ORDER BY dep.name""")))


@bp.get("/doctors")
def doctors():
    dep = request.args.get("department_id", type=int)
    q = f"SELECT {DOCTOR_FIELDS} FROM doctors d JOIN departments dep ON dep.id=d.department_id WHERE d.active=1 AND dep.active=1"
    args = []
    if dep:
        q += " AND d.department_id=?"
        args.append(dep)
    return jsonify(doctors=rows(get_db().execute(q + " ORDER BY dep.name, d.name", args)))


@bp.get("/doctors/<int:doctor_id>/slots")
def slots(doctor_id):
    date = request.args.get("date")
    if not date:
        raise APIError("Please choose a date.", 400, "missing_date")
    return jsonify(booking.available_slots(get_db(), doctor_id, date))


@bp.get("/public/info")
def info():
    db = get_db()
    return jsonify(hospital_name=get_setting(db, "hospital_name"), demo_mode=get_setting(db, "demo_mode") == "1",
                   slot_minutes=int(get_setting(db, "slot_minutes", "15")))
