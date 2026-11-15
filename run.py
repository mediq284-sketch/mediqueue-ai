"""Start MediQueue AI.

    python run.py            # seeds the demo database on first run, then serves http://127.0.0.1:5000
    python run.py --reseed   # wipe and regenerate all synthetic demo data + model v1
"""
import os
import sys

from backend import create_app, BASE_DIR


def main():
    db = os.environ.get("MEDIQUEUE_DB", os.path.join(BASE_DIR, "instance", "mediqueue.db"))
    if "--reseed" in sys.argv or not os.path.exists(db):
        from backend.seed import seed
        print("Generating synthetic demo data and training model v1 …")
        seed()
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  MediQueue AI running at http://127.0.0.1:{port}\n  Demo password for all accounts: Demo@1234\n")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
