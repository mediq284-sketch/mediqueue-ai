"""Server-side prediction service. Uses ONLY the active trained model version.
Every prediction is stored in ml_predictions with its exact input features,
the non-ML baseline for comparison, and later the actual wait + absolute error."""
import json
import os
import threading
from datetime import datetime

import joblib
import numpy as np

from .. import clock
from ..db import one, get_setting
from ..services import features as F
from ..services import queue_engine as Q
from .pipeline import prepare_input

_cache = {}
_lock = threading.Lock()


def active_model(db):
    return one(db.execute("SELECT * FROM ml_models WHERE active=1 AND training_status='TRAINED'"))


def load_artifact(model_dir, meta):
    key = (meta["id"], meta["artifact_path"])
    with _lock:
        if key not in _cache:
            _cache.clear()
            _cache[key] = joblib.load(os.path.join(model_dir, meta["artifact_path"]))
        return _cache[key]


def live_features(db, entry):
    appt = one(db.execute("SELECT * FROM appointments WHERE id=?", (entry["appointment_id"],)))
    doc = one(db.execute("SELECT * FROM doctors WHERE id=?", (entry["doctor_id"],)))
    ahead, length = Q.queue_snapshot(db, entry)
    doc_avg, dep_avg = F.historical_averages(db, entry["doctor_id"], entry["department_id"], entry["queue_date"])
    prev = F.previous_average_wait(db, entry["doctor_id"], entry["queue_date"], clock.ts(), doc_avg)
    sched = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    return F.build_features(
        department_id=entry["department_id"], doctor_id=entry["doctor_id"], date=entry["queue_date"],
        appointment_time=appt["appointment_time"], patients_ahead=ahead, queue_length=length,
        consultation_duration_average=doc["consultation_duration_average"], peak_set=F.peak_hours(db),
        previous_avg=prev, doctor_avg=doc_avg, department_avg=dep_avg,
        delay=F.arrival_delay(sched, clock.parse(entry["joined_at"])))


def predict_features(model_dir, meta, feats):
    art = load_artifact(model_dir, meta)
    return round(float(max(0.0, art["model"].predict(prepare_input([feats]))[0])), 1)


def store_prediction(db, appointment_id, model_id, feats, predicted, when=None):
    base = F.baseline_estimate(feats)
    cur = db.execute(
        """INSERT INTO ml_predictions (appointment_id, model_id, prediction_timestamp, predicted_waiting_time,
               baseline_waiting_time, prediction_difference, input_features) VALUES (?,?,?,?,?,?,?)""",
        (appointment_id, model_id, when or clock.ts(), predicted, base, round(predicted - base, 1), json.dumps(feats)))
    return one(db.execute("SELECT * FROM ml_predictions WHERE id=?", (cur.lastrowid,)))


def prediction_for_entry(db, model_dir, entry, force=False):
    """Returns {'status': ..., 'prediction': {...}|None, 'message': str|None}.
    Re-predicts when the queue changed (patients ahead / queue length) or the
    last prediction is older than `repredict_minutes`, or the active model changed."""
    if entry["status"] != "WAITING":
        msg = {"CALLED": "You have been called. Please proceed to the consultation room.",
               "SERVING": "Your consultation is in progress."}.get(entry["status"], "This queue entry is closed.")
        return {"status": "not_waiting", "prediction": None, "message": msg}
    if entry["queue_date"] != clock.now().strftime("%Y-%m-%d"):
        return {"status": "not_today", "prediction": None,
                "message": "Waiting-time prediction becomes available on the day of your appointment."}
    meta = active_model(db)
    if not meta:
        return {"status": "no_model", "prediction": None, "message": "Insufficient historical data for ML prediction."}
    feats = live_features(db, entry)
    last = one(db.execute("SELECT * FROM ml_predictions WHERE appointment_id=? ORDER BY id DESC LIMIT 1",
                          (entry["appointment_id"],)))
    if last and not force:
        lf = json.loads(last["input_features"])
        age = clock.minutes_between(clock.parse(last["prediction_timestamp"]), clock.now())
        fresh = age < float(get_setting(db, "repredict_minutes", "10"))
        same = lf.get("patients_ahead") == feats["patients_ahead"] and lf.get("queue_length") == feats["queue_length"]
        if last["model_id"] == meta["id"] and fresh and same:
            return {"status": "ok", "prediction": _view(last, meta), "message": None}
    try:
        predicted = predict_features(os.path.join(model_dir), meta, feats)
    except Exception:
        return {"status": "error", "prediction": None, "message": "Prediction could not be generated."}
    p = store_prediction(db, entry["appointment_id"], meta["id"], feats, predicted)
    return {"status": "ok", "prediction": _view(p, meta), "message": None}


def _view(p, meta):
    return {"id": p["id"], "predicted_waiting_time": p["predicted_waiting_time"],
            "prediction_timestamp": p["prediction_timestamp"], "model_id": meta["id"],
            "model_version": f"{meta['model_name']} v{meta['version']}",
            "patients_ahead": json.loads(p["input_features"])["patients_ahead"]}
