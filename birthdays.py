"""Staff birthday notifications.

Date of birth only exists on /rest/hrm/employees/{id}?view=FULL. That record also
carries bank account numbers, tax file numbers, home addresses, medical notes and
emergency contacts, so this pulls it, keeps ONLY the name and date of birth, and
lets the rest fall on the floor. Nothing else is ever written down.
"""
import os, sys, json
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
import settings                                              # noqa: E402

BASE = settings.BASE
CAFES = settings.CAFES
CACHE = os.path.join(HERE, "birthdays.json")
CT = ZoneInfo("America/Chicago")

BIRTHDAY_CORRECT = settings.BIRTHDAY_CORRECT


def refresh(ctx):
    """Name + date of birth per cafe. Deliberately discards everything else."""
    people = []
    seen = set()
    for cafe, cid in CAFES:
        lst = ctx.request.get(BASE + "/rest/hrm/employees/active/%s" % cid).json()
        for e in lst:
            eid = e["id"]
            if eid in seen:          # people who work at both cafes appear twice
                continue
            seen.add(eid)
            full = ctx.request.get(BASE + "/rest/hrm/employees/%s?view=FULL" % eid).json()
            dob = full.get("dateOfBirth")
            if not dob:
                continue
            people.append({"name": (e.get("knownAs")
                                    or "%s %s" % (e.get("firstName", ""), e.get("lastName", ""))).strip(),
                           "cafe": cafe, "dob": dob})
    return people


def load(ctx=None, max_age_hours=20):
    fresh = True
    if os.path.exists(CACHE):
        age = (datetime.now().timestamp() - os.path.getmtime(CACHE)) / 3600
        fresh = age < max_age_hours
        if fresh:
            return json.load(open(CACHE))
    people = refresh(ctx)
    json.dump(people, open(CACHE, "w"), indent=1)
    os.chmod(CACHE, 0o600)
    return people


def norm_name(name):
    import re as _re
    return " ".join(_re.sub(r"[^a-z ]", "", (name or "").lower()).split())


def dedupe(people):
    """Someone who works at both cafes has a record at each. Merge them by name,
    and if the two records disagree on the date of birth, keep both and say so -
    picking one silently would mean greeting somebody on the wrong day."""
    by = {}
    for p in people:
        by.setdefault(p["name"].strip().lower(), []).append(p)
    out = []
    for group in by.values():
        dobs = sorted({g["dob"] for g in group})
        cafes = "/".join(sorted({g["cafe"] for g in group}))
        if len(dobs) == 1:
            out.append({"name": group[0]["name"], "cafe": cafes, "dob": dobs[0]})
        elif norm_name(group[0]["name"]) in BIRTHDAY_CORRECT:
            # the owner has told us which of the two records is right
            out.append({"name": group[0]["name"], "cafe": cafes,
                        "dob": BIRTHDAY_CORRECT[norm_name(group[0]["name"])]})
        else:
            for g in group:
                out.append({"name": group[0]["name"], "cafe": g["cafe"],
                            "dob": g["dob"], "conflict": dobs})
    return out


def on_day(people, day):
    out = []
    for p in people:
        y, m, d = (int(x) for x in p["dob"].split("-"))
        if (m, d) == (day.month, day.day):
            out.append({**p, "turning": day.year - y})
        # someone born on 29 Feb gets greeted on 1 Mar in a non-leap year
        elif (m, d) == (2, 29) and (day.month, day.day) == (3, 1):
            try:
                date(day.year, 2, 29)
            except ValueError:
                out.append({**p, "turning": day.year - y})
    return out


def build(today_list, tomorrow_list, today):
    lines = []
    if today_list:
        lines.append("Birthday today - %s" % today.strftime("%a %-d %b"))
        for p in today_list:
            lines.append("  %s (%s) turns %d" % (p["name"], p["cafe"], p["turning"]))
            if p.get("conflict"):
                lines.append("    note: %s has two staff records with different "
                             "dates of birth (%s) - one of them is wrong"
                             % (p["name"], " and ".join(p["conflict"])))
            if p["turning"] == 18:
                lines.append("    turns 18 today - the under-18 hour limits no longer apply")
    if tomorrow_list:
        if lines:
            lines.append("")
        lines.append("Tomorrow:")
        for p in tomorrow_list:
            lines.append("  %s (%s) turns %d" % (p["name"], p["cafe"], p["turning"]))
    return "\n".join(lines)


if __name__ == "__main__":
    today = datetime.now(CT).date()
    if len(sys.argv) > 1 and sys.argv[1].startswith("2026-"):
        today = date.fromisoformat(sys.argv[1])
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(storage_state=os.path.join(HERE, "session.json"))
        people = dedupe(load(ctx))
        b.close()
    print("%d staff with a date of birth on file" % len(people))
    msg = build(on_day(people, today), on_day(people, today + timedelta(days=1)), today)
    print(msg or "(nobody today or tomorrow)")
    if msg and "--send" in sys.argv:
        import tsc_bot
        c = tsc_bot.creds()
        print("telegram ok:", tsc_bot.send(msg, c["t"], c["chat"]).get("ok"))
