"""Production entry point (gunicorn wsgi:app).

Seeds the synthetic demo database and trains model v1 on first boot if no
database exists yet (on Render this normally happens during the build step).
"""
import os

from backend import create_app, BASE_DIR

_db = os.environ.get("MEDIQUEUE_DB", os.path.join(BASE_DIR, "instance", "mediqueue.db"))
if not os.path.exists(_db):
    from backend.seed import seed
    seed()

app = create_app()
