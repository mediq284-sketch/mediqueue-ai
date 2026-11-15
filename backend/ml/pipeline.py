"""Waiting-time ML pipeline (scikit-learn Random Forest Regressor).

QueueHistory -> validation -> feature selection -> one-hot encoding ->
chronological train/test split -> RandomForestRegressor -> MAE / RMSE / R2 (+time-series CV)
-> baseline comparison -> joblib artifact + ml_models version row.

A failed run is stored as FAILED and never touches the active model.
"""
import json
import os
import traceback

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .. import APIError, clock
from ..db import one, rows, tx
from ..services.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, FEATURES, TARGET, MAX_VALID_WAIT

MODEL_NAME = "WaitingTimeModel"
ALGORITHM = "RandomForestRegressor"
MIN_RECORDS = 50
MIN_CONSULT_MINUTES = 1.0
TEST_SIZE = 0.20
RANDOM_STATE = 42
RF_PARAMS = dict(n_estimators=200, min_samples_leaf=3, max_features=0.6, random_state=RANDOM_STATE, n_jobs=-1)


# ----------------------------------------------------------------- data
def load_history(db):
    return pd.DataFrame(rows(db.execute("SELECT * FROM queue_history ORDER BY id")))


def validate(df):
    """Returns (clean_df, report). Each excluded record is counted under the first rule it breaks."""
    report = {"total_records": int(len(df)), "missing_values": 0, "invalid_values": 0,
              "duplicate_records": 0, "implausible_records": 0, "valid_records": 0, "exclusions": []}
    if df.empty:
        return df, report
    cols = FEATURES + [TARGET]
    keep = pd.Series(True, index=df.index)
    reasons = {}

    missing = df[cols].isna().any(axis=1)
    reasons["Missing feature or target value"] = missing
    num = df[cols].apply(pd.to_numeric, errors="coerce")
    invalid = (~missing) & (
        (num[TARGET] < 0) | (num[TARGET] > MAX_VALID_WAIT) |
        (num["patients_ahead"] < 0) | (num["queue_length"] < 1) |
        (num["patients_ahead"] >= num["queue_length"] + 1) |
        (num["appointment_hour"] < 0) | (num["appointment_hour"] > 23) |
        (num["appointment_minute"] < 0) | (num["appointment_minute"] > 59) |
        (num["consultation_duration_average"] <= 0) | (num["day_of_week"] < 0) | (num["day_of_week"] > 6)
    )
    # A consultation completed in under 1 minute is not a real consultation (accidental or
    # test click-through); its waiting time is not representative, so the record is excluded.
    implausible = (~missing) & (~invalid) & (pd.to_numeric(df["consultation_duration"], errors="coerce") < MIN_CONSULT_MINUTES)
    reasons[f"Invalid value (negative/over {int(MAX_VALID_WAIT)} min wait, impossible queue counts or time)"] = invalid
    reasons[f"Implausible consultation (< {MIN_CONSULT_MINUTES:g} min, e.g. test click-through)"] = implausible
    dup_key = ["appointment_id"] + cols
    dup = (~missing) & (~invalid) & (~implausible) & df.duplicated(subset=dup_key, keep="first")
    reasons["Duplicate record"] = dup

    for reason, mask in reasons.items():
        mask = mask & keep
        if mask.any():
            report["exclusions"].append({"reason": reason, "count": int(mask.sum()),
                                         "example_ids": [int(i) for i in df.loc[mask, "id"].head(5)]})
        keep &= ~mask
    report["missing_values"] = int(missing.sum())
    report["invalid_values"] = int(invalid.sum())
    report["duplicate_records"] = int(dup.sum())
    report["implausible_records"] = int(implausible.sum())
    clean = df.loc[keep].copy()
    clean[NUMERIC_FEATURES + [TARGET]] = clean[NUMERIC_FEATURES + [TARGET]].astype(float)
    clean[CATEGORICAL_FEATURES] = clean[CATEGORICAL_FEATURES].astype(int).astype(str)
    report["valid_records"] = int(len(clean))
    return clean, report


def prepare_input(features_list):
    X = pd.DataFrame(features_list)[FEATURES].copy()
    X[CATEGORICAL_FEATURES] = X[CATEGORICAL_FEATURES].astype(int).astype(str)
    X[NUMERIC_FEATURES] = X[NUMERIC_FEATURES].astype(float)
    return X


def chronological_split(clean, test_size=TEST_SIZE):
    """Time-based split on a DATE boundary: the oldest ~80% of records (whole days)
    train the model, the most recent ~20% of days are the unseen test set.
    No record in the training set is later than any record in the test set."""
    df = clean.sort_values(["date", "id"]).reset_index(drop=True)
    counts = df.groupby("date").size()
    cum = counts.cumsum() / len(df)
    train_dates = cum[cum <= (1 - test_size) + 1e-9].index
    if len(train_dates) == 0 or len(train_dates) == len(counts):
        # fewer than 2 usable days: fall back to ordering by record id (still chronological)
        cut = int(round(len(df) * (1 - test_size)))
        return df.iloc[:cut], df.iloc[cut:], "record_order"
    cutoff = counts.index[len(train_dates)]          # first test date
    return df[df["date"] < cutoff], df[df["date"] >= cutoff], cutoff


def build_model():
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ("num", "passthrough", NUMERIC_FEATURES),
    ])
    return Pipeline([("prep", pre), ("rf", RandomForestRegressor(**RF_PARAMS))])


def _metrics(y, p):
    return {"mae": float(mean_absolute_error(y, p)),
            "rmse": float(np.sqrt(mean_squared_error(y, p))),
            "r2": float(r2_score(y, p))}


def grouped_importance(model):
    names = model.named_steps["prep"].get_feature_names_out()
    imp = model.named_steps["rf"].feature_importances_
    agg = {f: 0.0 for f in FEATURES}
    for n, v in zip(names, imp):
        block, col = n.split("__", 1)
        base = next(c for c in CATEGORICAL_FEATURES if col.startswith(c + "_")) if block == "cat" else col
        agg[base] += float(v)
    return sorted(({"feature": k, "importance": round(v, 4)} for k, v in agg.items()), key=lambda x: -x["importance"])


# ----------------------------------------------------------------- training
def dataset_summary(db):
    df = load_history(db)
    clean, rep = validate(df)
    n = rep["valid_records"]
    tr_n = te_n = 0
    cutoff = None
    if n >= 2:
        tr, te, cutoff = chronological_split(clean)
        tr_n, te_n = len(tr), len(te)
    rep.update({"training_percentage": round(100 * tr_n / n, 1) if n else 0, "testing_percentage": round(100 * te_n / n, 1) if n else 0,
                "expected_training_records": tr_n, "expected_testing_records": te_n,
                "split_method": "chronological (oldest days train, most recent days test)",
                "test_period_starts": str(cutoff) if cutoff is not None else None,
                "feature_count": len(FEATURES), "features": FEATURES, "target": TARGET,
                "minimum_required": MIN_RECORDS, "sufficient": n >= MIN_RECORDS})
    return rep


def train(db, model_dir, max_date=None):
    """Train a new model version. `max_date` (exclusive) is used by the seeder to
    train only on data before the 'deployment' date."""
    with tx(db):
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 v FROM ml_models").fetchone()["v"]
        cur = db.execute("""INSERT INTO ml_models (model_name, model_type, version, training_date, training_status, active)
                            VALUES (?,?,?,?, 'TRAINING', 0)""", (MODEL_NAME, ALGORITHM, version, clock.ts()))
        model_id = cur.lastrowid
    try:
        df = load_history(db)
        if max_date:
            df = df[df["date"] < max_date]
        clean, report = validate(df)
        if len(clean) < MIN_RECORDS:
            raise ValueError(f"Insufficient historical data for ML prediction: {len(clean)} valid records, {MIN_RECORDS} required.")
        assert TARGET not in FEATURES
        tr, te, cutoff = chronological_split(clean)
        if len(te) < 10 or len(tr) < MIN_RECORDS * 0.8:
            raise ValueError("Insufficient historical data for a chronological train/test split.")
        X_tr, y_tr, X_te, y_te = tr[FEATURES], tr[TARGET].values, te[FEATURES], te[TARGET].values
        id_te = te["id"].values
        model = build_model().fit(X_tr, y_tr)
        p_te = np.clip(model.predict(X_te), 0, None)
        m = _metrics(y_te, p_te)
        train_m = _metrics(y_tr, model.predict(X_tr))

        base_te = (X_te["patients_ahead"] * X_te["consultation_duration_average"]).values
        b = _metrics(y_te, base_te)
        improvement = (b["mae"] - m["mae"]) / b["mae"] * 100 if b["mae"] else None

        cv = None
        if len(tr) >= 200:
            # forward-chaining CV inside the TRAINING period only (each fold trains on the past, tests on the next block)
            Xs, ys = tr[FEATURES].reset_index(drop=True), y_tr
            scores = []
            for fold_tr, fold_te in TimeSeriesSplit(n_splits=5).split(Xs):
                fold_pred = build_model().fit(Xs.iloc[fold_tr], ys[fold_tr]).predict(Xs.iloc[fold_te])
                scores.append(mean_absolute_error(ys[fold_te], fold_pred))
            scores = np.array(scores)
            cv = {"method": "TimeSeriesSplit (forward chaining) on training period", "folds": 5,
                  "mae_mean": float(scores.mean()), "mae_std": float(scores.std()),
                  "fold_mae": [round(float(s), 3) for s in scores]}

        errors = (p_te - y_te)
        hist_counts, hist_edges = np.histogram(errors, bins=12)
        rng = np.random.RandomState(0)
        idx = rng.choice(len(y_te), size=min(250, len(y_te)), replace=False)
        os.makedirs(model_dir, exist_ok=True)
        path = os.path.join(model_dir, f"{MODEL_NAME}_v{version}.joblib")
        joblib.dump({"model": model, "features": FEATURES, "version": version}, path)

        meta = {
            "algorithm_params": {k: v for k, v in RF_PARAMS.items()},
            "features": FEATURES, "categorical_features": CATEGORICAL_FEATURES,
            "numeric_features": NUMERIC_FEATURES, "target": TARGET, "target_unit": "minutes",
            "encoding": "One-hot encoding for department_id and doctor_id; numeric features passed through",
            "split": {"method": "chronological", "description": "oldest days -> training, most recent ~20% of days -> testing (unseen future)",
                      "train_period": [str(tr["date"].min()), str(tr["date"].max())],
                      "test_period": [str(te["date"].min()), str(te["date"].max())], "target_test_fraction": TEST_SIZE},
            "deployed_artifact_trained_on": "training period only (the evaluated model is exactly the deployed model)",
            "data_quality": report, "trained_on_data_before": max_date,
            "train_metrics": train_m,
            "test_metrics": m,
            "baseline": {"description": "patients_ahead x doctor's average consultation duration", **b},
            "improvement_over_baseline_pct": round(improvement, 2) if improvement is not None else None,
            "cross_validation": cv,
            "feature_importance": grouped_importance(model),
            "test_samples": [{"id": int(id_te[i]), "actual": round(float(y_te[i]), 2), "predicted": round(float(p_te[i]), 2)} for i in idx],
            "error_histogram": {"counts": [int(c) for c in hist_counts], "edges": [round(float(e), 2) for e in hist_edges]},
        }
        with tx(db):
            db.execute("""UPDATE ml_models SET dataset_size=?, training_records=?, testing_records=?, feature_count=?,
                          mae=?, rmse=?, r2_score=?, training_status='TRAINED', artifact_path=?, model_metadata=? WHERE id=?""",
                       (len(clean), len(y_tr), len(y_te), len(FEATURES), round(m["mae"], 3), round(m["rmse"], 3),
                        round(m["r2"], 4), os.path.basename(path), json.dumps(meta), model_id))
    except Exception as e:  # failed training never replaces the active model
        import logging; logging.getLogger("mediqueue").error("Training v%s failed: %s", version, traceback.format_exc())
        msg = str(e) if isinstance(e, ValueError) else "Training failed due to an internal error."
        with tx(db):
            db.execute("UPDATE ml_models SET training_status='FAILED', model_metadata=? WHERE id=?",
                       (json.dumps({"error": msg}), model_id))
    return one(db.execute("SELECT * FROM ml_models WHERE id=?", (model_id,)))


def activate(db, model_id):
    with tx(db):
        m = one(db.execute("SELECT * FROM ml_models WHERE id=?", (model_id,)))
        if not m:
            raise APIError("Model not found.", 404, "not_found")
        if m["training_status"] != "TRAINED" or m["mae"] is None:
            raise APIError("Only successfully trained and evaluated models can be activated.", 409, "not_trained")
        db.execute("UPDATE ml_models SET active=0 WHERE active=1")
        db.execute("UPDATE ml_models SET active=1 WHERE id=?", (model_id,))
    return one(db.execute("SELECT * FROM ml_models WHERE id=?", (model_id,)))
