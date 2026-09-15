"""Reads the availability workbook the owner maintains.

Sheet "Availability": one row per person per cafe, Mon-Sun windows in 24h.
  blank = cannot work that day, "any" = any hours.
  Column "Hard or preference": "hard weekdays" means Mon-Fri is a real constraint
  (school), not a preference - but only while school is actually in.
Sheet "School holidays": date ranges where that hard rule is switched off.
"""
import os
from datetime import date, datetime

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "availability_template.xlsx")

import settings
CAFE_NAMES = {name for name, _ in settings.CAFES}


def _time(text):
    h, m = text.strip().split(":")
    return int(h) + int(m) / 60.0


def load(path=DEFAULT):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Availability"] if "Availability" in wb.sheetnames else wb.worksheets[0]
    people = {}
    for r in range(2, ws.max_row + 1):
        cafe, name = ws.cell(r, 1).value, ws.cell(r, 2).value
        if not cafe or not name or str(cafe).strip() not in CAFE_NAMES:
            continue
        windows = {}
        for i, day in enumerate(DAYS):
            v = str(ws.cell(r, 3 + i).value or "").strip()
            if not v:
                windows[day] = None                  # cannot work
            elif v.lower() == "any":
                windows[day] = "any"
            elif "-" in v:
                a, b = v.split("-", 1)
                windows[day] = (_time(a), _time(b))
            else:
                windows[day] = None
        people[(str(cafe).strip(), str(name).strip())] = {
            "windows": windows,
            "hard_weekdays": str(ws.cell(r, 10).value or "").lower().startswith("hard"),
            "note": (ws.cell(r, 11).value or "").strip(),
        }

    holidays = []
    if "School holidays" in wb.sheetnames:
        hs = wb["School holidays"]
        for r in range(2, hs.max_row + 1):
            a, b = hs.cell(r, 1).value, hs.cell(r, 2).value
            if not a or not b:
                continue
            try:
                a = a.date() if isinstance(a, datetime) else date.fromisoformat(str(a).strip())
                b = b.date() if isinstance(b, datetime) else date.fromisoformat(str(b).strip())
            except ValueError:
                continue
            holidays.append((a, b, (hs.cell(r, 3).value or "").strip()))
    return people, holidays


def in_school_holiday(day, holidays):
    return any(a <= day <= b for a, b, _ in holidays)


def check(person, day, start_h, end_h, holidays):
    """Returns (severity, detail). severity is 'error', 'note' or None.

    A hard rule only bites while school is in - during the holidays these are
    exactly the people who can work daytime shifts.
    """
    win = person["windows"][DAYS[day.weekday()]]
    weekday = day.weekday() < 5
    hard = person["hard_weekdays"] and weekday and not in_school_holiday(day, holidays)

    if win == "any":
        return None, ""
    if win is None:
        if person["note"]:
            return None, ""                  # not rostered here by design
        return ("error" if hard else "note"), "not available that day"
    lo, hi = win
    early = max(0.0, lo - start_h)      # starting before the window opens
    late = max(0.0, end_h - hi)         # finishing after it closes
    if early <= 0 and late <= 0:
        return None, ""

    # School governs when someone can START, not when they finish - a 17 year old
    # is in class at 9am, but staying to 21:30 instead of 21:00 is nothing to do
    # with school. So a hard rule only makes an early start an error; running late
    # stays a note however it is marked.
    if hard and early > 0:
        return "error", "starts %d min before %s" % (int(round(early * 60)), _fmt(win))

    mins = int(round(max(early, late) * 60))
    if mins <= 30:
        return "minor", "%d min outside %s" % (mins, _fmt(win))
    return "note", "%d min outside %s" % (mins, _fmt(win))


def _fmt(win):
    if win in ("any", None):
        return str(win)
    return "%02d:%02d-%02d:%02d" % (int(win[0]), round((win[0] % 1) * 60),
                                    int(win[1]), round((win[1] % 1) * 60))
