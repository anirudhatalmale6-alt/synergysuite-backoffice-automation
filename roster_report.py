"""The 7pm message: who works tomorrow, both cafes."""
import sys, json, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
import settings
BASE = settings.BASE
CAFES = settings.CAFES
CT = ZoneInfo("America/Chicago")


def day_roster(ctx, cafe_id, day):
    d = ctx.request.get(BASE + "/rest/hrm/rosterEditor/roster/%s?date=%s" % (cafe_id, day)).json()
    shifts = []
    for e in d.get("employees", []):
        for s in (e.get("shifts", {}).get(day) or []):
            if s.get("wasCancelled"):
                continue
            # Shifts with no start or end time belong to the owner, who comes and
            # goes as needed - so they are not useful in a "who is on tomorrow"
            # list. Note the consequence: if a crew member ever ends up with a
            # timeless shift they will not appear here either.
            if not (s.get("shiftText") or {}).get("time12Hr"):
                continue
            shifts.append({
                "name": e["displayName"],
                "time": s["shiftText"]["time12Hr"],
                "section": (s.get("section") or {}).get("name") or "",
                "start": s["startTime"]["orderableTime"],
                "hours": (s.get("grossDuration") or {}).get("decimal", 0),
            })
    shifts.sort(key=lambda s: s["start"])
    return shifts, d.get("published", False), d.get("exists", False)


def build(day, per_cafe):
    out = ["Tomorrow's shifts - " + day.strftime("%a %-d %b"), ""]
    for cafe, shifts, published, exists in per_cafe:
        if not exists:
            out += [cafe + ": no schedule created for that week", ""]
            continue
        if not shifts:
            out += [cafe + ": nobody scheduled", ""]
            continue
        total = sum(s["hours"] for s in shifts)
        out.append("%s - %d on, %.1f hours" % (cafe, len(shifts), total))
        for s in shifts:
            out.append("  %-9s %s%s" % (s["time"], s["name"],
                                        "  (" + s["section"] + ")" if s["section"] else ""))
        if not published:
            out.append("  note: this week is not published yet, so it can still change")
        out.append("")
    return "\n".join(out).rstrip()


if __name__ == "__main__":
    day = datetime.now(CT).date() + timedelta(days=1)
    if len(sys.argv) > 1 and sys.argv[1].startswith("2026-"):
        day = datetime.fromisoformat(sys.argv[1]).date()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(storage_state=os.path.join(HERE, "session.json"))
        per_cafe = [(name,) + day_roster(ctx, cid, day.isoformat()) for name, cid in CAFES]
        b.close()
    msg = build(day, per_cafe)
    print(msg)
    if "--send" in sys.argv:
        import tsc_bot
        c = tsc_bot.creds()
        print("\ntelegram ok:", tsc_bot.send(msg, c["t"], c["chat"]).get("ok"))
