"""Authentication + server-side role-based authorization."""
from functools import wraps
from flask import session, g
from werkzeug.security import generate_password_hash, check_password_hash

from .db import get_db, one
from . import APIError

PUBLIC_USER_FIELDS = "id, full_name, email, phone, role, department_id, active, created_at, updated_at"


def hash_password(pw):
    return generate_password_hash(pw)


def verify_password(hashval, pw):
    return check_password_hash(hashval, pw)


def current_user():
    if "user" in g:
        return g.user
    uid = session.get("uid")
    g.user = None
    if uid:
        u = one(get_db().execute(f"SELECT {PUBLIC_USER_FIELDS} FROM users WHERE id = ?", (uid,)))
        if u and u["active"]:
            g.user = u
        else:
            session.clear()
    return g.user


def login_required(*roles):
    """@login_required() any signed-in user; @login_required('ADMIN') role-restricted."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                raise APIError("Please sign in to continue.", 401, "unauthorized")
            if roles and u["role"] not in roles:
                raise APIError("You do not have permission to do that.", 403, "forbidden")
            return fn(*a, **kw)
        return wrapper
    return deco
