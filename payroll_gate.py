"""Is a pay period actually ready for payroll?

The tips screen will not open until End of Night is complete, so building a
payroll file over a period with open nights produces a file with hours missing
and no warning on its face. This is the check that stops that happening.
"""
from datetime import timedelta

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings                                              # noqa: E402

BASE = settings.BASE
CAFES = settings.CAFES


def open_nights(ctx, start, end):
    """Every (cafe, date) in the period that has not been closed."""
    out = []
    for cafe, cid in CAFES:
        locked = {}
        anchor = start
        while anchor <= end:                     # one call per week, not per day
            week_end = anchor - timedelta(days=anchor.weekday()) + timedelta(days=6)
            d = ctx.request.get(BASE + "/rest/hrm/rosterEditor/roster/%s?date=%s"
                                % (cid, week_end.isoformat())).json()
            for day in d.get("days", []):
                locked[day["date"]] = day.get("locked", False)
            anchor = week_end + timedelta(days=1)
        day = start
        while day <= end:
            if not locked.get(day.isoformat(), False):
                out.append((cafe, day))
            day += timedelta(days=1)
    return out


def describe(gaps):
    if not gaps:
        return "Every night in the period is closed - payroll can run."
    lines = ["PAYROLL CANNOT RUN YET - %d night(s) still open:" % len(gaps)]
    for cafe, day in gaps:
        lines.append("  %s %s" % (cafe, day.strftime("%a %-d %b")))
    lines.append("Close these and the payroll file can be built.")
    return "\n".join(lines)
