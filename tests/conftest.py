import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import create_app  # noqa: E402
from backend.seed import seed, DEMO_PASSWORD  # noqa: E402


@pytest.fixture(scope="session")
def seeded(tmp_path_factory):
    d = tmp_path_factory.mktemp("seed")
    db, models = str(d / "seed.db"), str(d / "models")
    os.makedirs(models)
    seed(db_path=db, model_dir=models, verbose=False)
    return db, models


@pytest.fixture()
def app(seeded, tmp_path):
    db, models = seeded
    tdb, tmodels = str(tmp_path / "t.db"), str(tmp_path / "models")
    shutil.copy(db, tdb)
    shutil.copytree(models, tmodels)
    return create_app({"DATABASE": tdb, "MODEL_DIR": tmodels, "TESTING": True})


def login(client, email, pw=DEMO_PASSWORD):
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.json
    return r.json["user"]


@pytest.fixture()
def patient(app):
    c = app.test_client()
    login(c, "patient@mediqueue.demo")
    return c


@pytest.fixture()
def patient2(app):
    c = app.test_client()
    login(c, "patient2@mediqueue.demo")
    return c


@pytest.fixture()
def staff(app):
    c = app.test_client()
    login(c, "staff@mediqueue.demo")
    return c


@pytest.fixture()
def gm_staff(app):
    c = app.test_client()
    login(c, "gm.staff@mediqueue.demo")
    return c


@pytest.fixture()
def admin(app):
    c = app.test_client()
    login(c, "admin@mediqueue.demo")
    return c
