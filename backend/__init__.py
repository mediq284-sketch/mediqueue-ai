"""MediQueue AI - Flask application factory."""
import os
import secrets
import logging
from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from .db import close_db, connect, init_schema

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


class APIError(Exception):
    """Error with a safe, user-facing message (never a stack trace)."""
    def __init__(self, message, status=400, code="bad_request"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def _secret_key(instance_dir):
    env = os.environ.get("MEDIQUEUE_SECRET_KEY")
    if env:
        return env
    path = os.path.join(instance_dir, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


def create_app(test_config=None):
    instance_dir = os.path.join(BASE_DIR, "instance")
    os.makedirs(instance_dir, exist_ok=True)
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DATABASE=os.environ.get("MEDIQUEUE_DB", os.path.join(instance_dir, "mediqueue.db")),
        MODEL_DIR=os.environ.get("MEDIQUEUE_MODEL_DIR", os.path.join(BASE_DIR, "ml_models")),
        SECRET_KEY=_secret_key(instance_dir),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["MODEL_DIR"], exist_ok=True)

    conn = connect(app.config["DATABASE"])
    init_schema(conn)
    conn.close()

    app.teardown_appcontext(close_db)

    from .api.auth_api import bp as auth_bp
    from .api.public import bp as public_bp
    from .api.patient import bp as patient_bp
    from .api.staff import bp as staff_bp
    from .api.admin import bp as admin_bp
    for bp in (auth_bp, public_bp, patient_bp, staff_bp, admin_bp):
        app.register_blueprint(bp)

    @app.before_request
    def _csrf_guard():
        # State-changing API calls must be JSON (blocks classic cross-site form posts;
        # SameSite=Lax cookies cover the rest).
        from flask import request
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not request.is_json:
                return jsonify(error="Requests must be sent as JSON.", code="bad_request"), 415

    @app.errorhandler(APIError)
    def _api_error(e):
        return jsonify(error=e.message, code=e.code), e.status

    @app.errorhandler(HTTPException)
    def _http_error(e):
        from flask import request
        if request.path.startswith("/api/"):
            return jsonify(error=e.description if e.code < 500 else "Something went wrong.", code=e.name), e.code
        return e

    @app.errorhandler(Exception)
    def _unhandled(e):
        logging.getLogger("mediqueue").exception("Unhandled error")
        return jsonify(error="Something went wrong. Please try again.", code="server_error"), 500

    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.route("/<path:path>")
    def static_files(path):
        if path.startswith("api/"):
            return jsonify(error="Not found", code="not_found"), 404
        full = os.path.join(FRONTEND_DIR, path)
        if os.path.isfile(full):
            return send_from_directory(FRONTEND_DIR, path)
        return send_from_directory(FRONTEND_DIR, "index.html")

    return app
