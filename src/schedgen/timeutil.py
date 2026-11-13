"""Time helpers. Internally all times are integer minutes since HORIZON_START
(plant-local, no timezone); exported as ISO-8601 strings."""
from datetime import datetime, timedelta

HORIZON_START = datetime(2026, 11, 2, 0, 0)  # a Monday
FMT = "%Y-%m-%dT%H:%M:%S"
DAY = 1440


def to_iso(minutes):
    return (HORIZON_START + timedelta(minutes=minutes)).strftime(FMT)


def from_iso(s):
    d = datetime.strptime(s, FMT) - HORIZON_START
    return d.days * DAY + d.seconds // 60


def to_date(minutes):
    return (HORIZON_START + timedelta(minutes=minutes)).strftime("%Y-%m-%d")


def date_str(d):
    return d.strftime("%Y-%m-%d")


def weekday_of_day(day_index):
    """0=Monday for horizon day index."""
    return (HORIZON_START + timedelta(days=day_index)).weekday()
