"""Single source of 'now' so tests and the simulator can control time."""
from datetime import datetime

FMT = "%Y-%m-%d %H:%M:%S"
_override = None


def now():
    return _override or datetime.now().replace(microsecond=0)


def set_now(dt):
    global _override
    _override = dt


def ts(dt=None):
    return (dt or now()).strftime(FMT)


def parse(s):
    return datetime.strptime(s, FMT) if s else None


def minutes_between(a, b):
    return round((b - a).total_seconds() / 60.0, 2)
