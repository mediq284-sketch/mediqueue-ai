"""SQLite data layer for MediQueue AI.

A single relational schema (SQLite, file-based, portable). Integrity rules that
matter for correctness are enforced here, in the database, not in the frontend:
  * unique email per user
  * one active booking per doctor/date/time slot (partial unique index)
  * one active booking per patient/doctor/date (partial unique index)
  * unique queue number per department/date
  * only one active ML model (partial unique index)
  * CHECK constraints on every status column
"""
import sqlite3
from flask import current_app, g

SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    role TEXT NOT NULL CHECK (role IN ('PATIENT','STAFF','ADMIN')),
    password_hash TEXT NOT NULL,
    department_id INTEGER REFERENCES departments(id),   -- staff scope (NULL = all departments)
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL UNIQUE,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    specialization TEXT,
    consultation_duration_average REAL NOT NULL DEFAULT 12 CHECK (consultation_duration_average > 0),
    working_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5',   -- 0=Mon ... 6=Sun
    start_time TEXT NOT NULL DEFAULT '09:00',
    end_time TEXT NOT NULL DEFAULT '13:00',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    doctor_id INTEGER NOT NULL REFERENCES doctors(id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    booking_timestamp TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('BOOKED','CONFIRMED','COMPLETED','CANCELLED','NO_SHOW')),
    queue_number TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_appt_slot ON appointments(doctor_id, appointment_date, appointment_time)
    WHERE status IN ('BOOKED','CONFIRMED');
CREATE UNIQUE INDEX IF NOT EXISTS ux_appt_patient_day ON appointments(patient_id, doctor_id, appointment_date)
    WHERE status IN ('BOOKED','CONFIRMED');
CREATE INDEX IF NOT EXISTS ix_appt_patient ON appointments(patient_id);
CREATE INDEX IF NOT EXISTS ix_appt_date ON appointments(appointment_date);

CREATE TABLE IF NOT EXISTS queue_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL UNIQUE REFERENCES appointments(id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    doctor_id INTEGER NOT NULL REFERENCES doctors(id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    queue_date TEXT NOT NULL,
    queue_number TEXT NOT NULL,
    queue_seq INTEGER NOT NULL,
    position INTEGER,
    status TEXT NOT NULL CHECK (status IN ('WAITING','CALLED','SERVING','COMPLETED','CANCELLED','NO_SHOW')),
    joined_at TEXT,                 -- check-in / arrival time (start of the wait)
    called_at TEXT,
    service_started_at TEXT,
    completed_at TEXT,
    actual_waiting_time REAL,       -- minutes, joined_at -> service_started_at
    consultation_duration REAL,     -- minutes, service_started_at -> completed_at
    patients_ahead_at_join INTEGER,
    queue_length_at_join INTEGER,
    UNIQUE (department_id, queue_date, queue_number)
);
CREATE INDEX IF NOT EXISTS ix_qe_doc_date ON queue_entries(doctor_id, queue_date);

CREATE TABLE IF NOT EXISTS queue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER REFERENCES appointments(id),
    doctor_id INTEGER,
    department_id INTEGER,
    date TEXT,
    day_of_week INTEGER,
    appointment_hour INTEGER,
    appointment_minute INTEGER,
    patients_ahead INTEGER,
    queue_length INTEGER,
    consultation_duration REAL,              -- this patient's actual consult (NOT a model feature: unknown at prediction time)
    consultation_duration_average REAL,      -- doctor's average consult length at the time (model feature)
    actual_waiting_time REAL,                -- TARGET (minutes)
    peak_hour INTEGER,
    previous_average_wait REAL,
    doctor_average_wait REAL,
    department_average_wait REAL,
    patient_arrival_delay REAL,
    source TEXT NOT NULL DEFAULT 'LIVE' CHECK (source IN ('LIVE','SYNTHETIC','SYNTHETIC_INVALID')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_qh_doc ON queue_history(doctor_id, date);

CREATE TABLE IF NOT EXISTS ml_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    version INTEGER NOT NULL UNIQUE,
    training_date TEXT NOT NULL,
    dataset_size INTEGER,
    training_records INTEGER,
    testing_records INTEGER,
    feature_count INTEGER,
    mae REAL,
    rmse REAL,
    r2_score REAL,
    training_status TEXT NOT NULL CHECK (training_status IN ('TRAINING','TRAINED','FAILED')),
    active INTEGER NOT NULL DEFAULT 0,
    artifact_path TEXT,
    model_metadata TEXT              -- JSON: features, importances, CV, baseline, test samples
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_model ON ml_models(active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS ml_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id),
    model_id INTEGER NOT NULL REFERENCES ml_models(id),
    prediction_timestamp TEXT NOT NULL,
    predicted_waiting_time REAL NOT NULL,
    baseline_waiting_time REAL,
    prediction_difference REAL,      -- predicted - baseline
    input_features TEXT NOT NULL,    -- JSON snapshot of model inputs
    actual_waiting_time REAL,
    absolute_error REAL
);
CREATE INDEX IF NOT EXISTS ix_pred_appt ON ml_predictions(appointment_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "hospital_name": "MediQueue General Hospital (Demo)",
    "slot_minutes": "15",
    "peak_hours": "10,11,12,17",
    "demo_mode": "1",                     # allows booking earlier slots of *today* for live demos
    "cancellation_cutoff_minutes": "0",
    "repredict_minutes": "10",
    "max_booking_days_ahead": "30",
}


def connect(path):
    conn = sqlite3.connect(path, detect_types=0, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema(conn):
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))


def get_setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


class tx:
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK. Serialises writers so queue numbers
    and slot checks cannot race."""
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.db.execute("COMMIT")
        else:
            self.db.execute("ROLLBACK")
        return False
