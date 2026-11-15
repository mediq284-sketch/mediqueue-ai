import re
from flask import Blueprint, jsonify, request, session

from .. import APIError, clock
from ..auth import current_user, hash_password, verify_password, login_required, PUBLIC_USER_FIELDS
from ..db import get_db, one, tx

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+\-\s]{7,15}$")


def body():
    return request.get_json(silent=True) or {}


def validate_profile(full_name, email, phone):
    if not full_name or len(full_name.strip()) < 2:
        raise APIError("Please enter your full name.", 400, "invalid_name")
    if not email or not EMAIL_RE.match(email):
        raise APIError("Please enter a valid email address.", 400, "invalid_email")
    if phone and not PHONE_RE.match(phone):
        raise APIError("Please enter a valid phone number.", 400, "invalid_phone")


def validate_password(pw):
    if not pw or len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise APIError("Password must be at least 8 characters and contain letters and numbers.", 400, "weak_password")


@bp.post("/register")
def register():
    b = body()
    full_name, email, phone, pw = (b.get("full_name") or "").strip(), (b.get("email") or "").strip().lower(), (b.get("phone") or "").strip(), b.get("password") or ""
    validate_profile(full_name, email, phone)
    validate_password(pw)
    db = get_db()
    with tx(db):
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise APIError("An account with this email already exists.", 409, "email_taken")
        # Public registration can only ever create PATIENT accounts.
        cur = db.execute("INSERT INTO users (full_name, email, phone, role, password_hash, created_at, updated_at) VALUES (?,?,?, 'PATIENT', ?,?,?)",
                         (full_name, email, phone or None, hash_password(pw), clock.ts(), clock.ts()))
    session.clear()
    session["uid"] = cur.lastrowid
    return jsonify(user=one(db.execute(f"SELECT {PUBLIC_USER_FIELDS} FROM users WHERE id=?", (cur.lastrowid,)))), 201


@bp.post("/login")
def login():
    b = body()
    email, pw = (b.get("email") or "").strip().lower(), b.get("password") or ""
    db = get_db()
    u = one(db.execute("SELECT * FROM users WHERE email=?", (email,)))
    if not u or not verify_password(u["password_hash"], pw):
        raise APIError("Incorrect email or password.", 401, "invalid_credentials")
    if not u["active"]:
        raise APIError("This account has been deactivated.", 403, "inactive")
    session.clear()
    session["uid"] = u["id"]
    return jsonify(user={k: u[k] for k in PUBLIC_USER_FIELDS.replace(" ", "").split(",")})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
def me():
    return jsonify(user=current_user())


@bp.put("/profile")
@login_required()
def update_profile():
    b, u, db = body(), current_user(), get_db()
    full_name = (b.get("full_name") or u["full_name"]).strip()
    phone = (b.get("phone") if "phone" in b else u["phone"]) or ""
    validate_profile(full_name, u["email"], phone.strip())
    db.execute("UPDATE users SET full_name=?, phone=?, updated_at=? WHERE id=?", (full_name, phone.strip() or None, clock.ts(), u["id"]))
    return jsonify(user=one(db.execute(f"SELECT {PUBLIC_USER_FIELDS} FROM users WHERE id=?", (u["id"],))))


@bp.put("/password")
@login_required()
def change_password():
    b, u, db = body(), current_user(), get_db()
    row = one(db.execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)))
    if not verify_password(row["password_hash"], b.get("current_password") or ""):
        raise APIError("Current password is incorrect.", 400, "invalid_credentials")
    validate_password(b.get("new_password"))
    db.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (hash_password(b["new_password"]), clock.ts(), u["id"]))
    return jsonify(ok=True)
