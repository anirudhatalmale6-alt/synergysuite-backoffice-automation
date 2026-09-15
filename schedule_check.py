"""Read-only audit of a week's roster.

Checks three things the copy-forward cannot see for itself:
  1. a shift sitting on a day the person has approved time off
  2. a shift outside the hours that person is available
  3. the day's shape against the expected pattern

Writes nothing. Never publishes.
"""
import os, sys, json
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
import settings

BASE = settings.BASE
CAFES = settings.CAFES

PATTERN = settings.SHIFT_PATTERN


def hhmm(t):
    return t["hour24"] + t["minute"] / 60.0


def start_label(s):
    return (s.get("shiftText") or {}).get("time12Hr", "").split("-")[0]


def audit(d, cafe, start):
    days = [start + timedelta(days=n) for n in range(7)]
    absent, outside, nocover, unknown = [], [], [], []
    for e in d.get("employees", []):
        name = e["displayName"]
        avail = e.get("preferredShifts") or {}
        has_any_avail = any(avail.get(x.isoformat()) for x in days)
        abs_map = e.get("absences") or {}
        for day in days:
            key = day.isoformat()
            shifts = [s for s in (e.get("shifts", {}).get(key) or []) if not s.get("wasCancelled")]
            if not shifts:
                continue
            if abs_map.get(key):
                reason = (abs_map[key][0].get("reason") or "time off").strip()
                for s in shifts:
                    absent.append("%s, %s - %s but has approved time off (%s)"
                                  % (day.strftime("%a %-d %b"), name,
                                     (s.get("shiftText") or {}).get("time12Hr", "shift"), reason))
                continue
            windows = avail.get(key) or []
            if not windows:
                if has_any_avail:
                    outside.append({"mins": 999,
                                    "text": "%s, %s - %s but is marked unavailable that day"
                                            % (day.strftime("%a %-d %b"), name,
                                               (shifts[0].get("shiftText") or {}).get("time12Hr", "shift"))})
                continue
            for s in shifts:
                if not s.get("startTime") or not s.get("endTime"):
                    continue
                ss, se = hhmm(s["startTime"]), hhmm(s["endTime"])
                # how far outside the nearest window, in minutes
                best = None
                for w in windows:
                    if w.get("allDay"):
                        best = 0; break
                    over = max(0.0, hhmm(w["start"]) - ss) + max(0.0, se - hhmm(w["end"]))
                    best = over if best is None else min(best, over)
                if not best:
                    continue
                w = windows[0]
                outside.append({
                    "mins": int(round(best * 60)),
                    "text": "%s, %s - %s, available %s"
                            % (day.strftime("%a %-d %b"), name,
                               (s.get("shiftText") or {}).get("time12Hr", "shift"),
                               "all day" if w.get("allDay") else
                               "%s-%s" % (w["start"]["display12"], w["end"]["display12"]))})
        if not has_any_avail and any(e.get("shifts", {}).get(x.isoformat()) for x in days):
            unknown.append(name)

    for day in days:
        key = day.isoformat()
        starts = []
        for e in d.get("employees", []):
            for s in (e.get("shifts", {}).get(key) or []):
                if not s.get("wasCancelled") and start_label(s):
                    starts.append(start_label(s))
        want = PATTERN[cafe]["wknd" if day.weekday() >= 5 else "week"]
        if len(starts) < len(want):
            nocover.append("%s - %d scheduled, pattern has %d (%s)"
                           % (day.strftime("%a %-d %b"), len(starts), len(want), ", ".join(want)))
    return absent, outside, nocover, sorted(set(unknown))


def main(send=False):
    start = None
    for a in sys.argv[1:]:
        if a.startswith("2026-") or a.startswith("2027-"):
            start = date.fromisoformat(a)
    if start is None:
        today = date.today()
        start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    out = ["Schedule check - week of %s" % start.strftime("%a %-d %b"), ""]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(storage_state=os.path.join(HERE, "session.json"))
        for cafe, cid in CAFES:
            d = ctx.request.get(BASE + "/rest/hrm/rosterEditor/roster/%s?date=%s"
                                % (cid, (start + timedelta(days=6)).isoformat())).json()
            if not d.get("exists"):
                out += ["%s: no schedule created for that week yet" % cafe, ""]
                continue
            absent, outside, thin, unknown = audit(d, cafe, start)
            out.append("%s%s" % (cafe, "" if d.get("published") else "  (not published)"))
            if absent:
                out.append("  SCHEDULED ON APPROVED TIME OFF - these are wrong:")
                out += ["    " + a for a in absent]
            if outside:
                # SynergySuite calls this field PREFERRED and the owner means it:
                # he can schedule against a preference if he needs to. So these
                # are notes, not violations. Only approved time off is hard.
                # A 30-minute overrun on a closing shift is almost certainly the
                # window being set to 9:00 when the closer works to 9:30.
                big = sorted([o for o in outside if o["mins"] > 30],
                             key=lambda o: -o["mins"])
                small = [o for o in outside if o["mins"] <= 30]
                if big:
                    out.append("  Outside their preferred hours (his call, just flagging):")
                    out += ["    %s  (%d min)" % (o["text"], o["mins"]) for o in big]
                if small:
                    out.append("  Also %d shift(s) up to 30 min past a preferred window - "
                               "all closing shifts, likely the window set to 9:00 "
                               "rather than 9:30:" % len(small))
                    out += ["    " + o["text"] for o in small]
            if thin:
                out.append("  Days below the usual pattern:")
                out += ["    " + a for a in thin]
            if unknown:
                out.append("  Working but no preferred hours on file: " + ", ".join(unknown))
            if not (absent or outside or thin or unknown):
                out.append("  nothing to flag")
            out.append("")
        both = combined_week_hours(ctx, start)
        if both:
            out.append("Across both cafes")
            out += ["  " + x for x in both]
        b.close()
    msg = "\n".join(out).rstrip()
    print(msg)
    if send:
        import tsc_bot
        c = tsc_bot.creds()
        print("telegram ok:", tsc_bot.send(msg, c["t"], c["chat"]).get("ok"))


def combined_week_hours(ctx, start):
    """Scheduled hours per person across BOTH cafes for the week.

    Usually only one person works at both cafes, but overtime is worked
    out per store, so 25 hours at one and 20 at the other is 45 hours that neither
    store's report will ever show. Catching it here, while the schedule is still a
    draft, is better than finding it on the payroll run.
    """
    import collections
    days = [start + timedelta(days=n) for n in range(7)]
    hours = collections.defaultdict(float)
    where = collections.defaultdict(set)
    for cafe, cid in CAFES:
        d = ctx.request.get(BASE + "/rest/hrm/rosterEditor/roster/%s?date=%s"
                            % (cid, (start + timedelta(days=6)).isoformat())).json()
        for e in d.get("employees", []):
            for day in days:
                for s in (e.get("shifts", {}).get(day.isoformat()) or []):
                    if s.get("wasCancelled"):
                        continue
                    name = e["displayName"].strip()
                    hours[name] += (s.get("grossDuration") or {}).get("decimal", 0.0)
                    where[name].add(cafe)
    out = []
    for name, h in sorted(hours.items(), key=lambda kv: -kv[1]):
        if len(where[name]) > 1 and h > 40:
            out.append("%s is on %.1f hours across %s - over 40, so overtime is due "
                       "and neither store's report will show it"
                       % (name, h, " and ".join(sorted(where[name]))))
        elif len(where[name]) > 1:
            out.append("%s works both cafes this week, %.1f hours combined (under 40)"
                       % (name, h))
    return out


if __name__ == "__main__":
    main(send="--send" in sys.argv)
