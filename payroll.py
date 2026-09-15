"""Tuesday 1am: check the period is genuinely finished, then build payroll.

Runs the day after End of Night so anything End of Night left open has had a day
to be fixed. If the checks fail it builds nothing - a payroll file that is short
a night's hours looks like a quiet week, not like an error.
"""
import os, sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import payroll_gate                                   # noqa: E402
from eon_biweekly import (crossed_midnight, OWNERS_OK_AT_2AM, norm_name,  # noqa: E402
                          CAFES, BASE, creds, login)

ANCHOR_TUE = date(2026, 8, 18)      # the Tuesday after a payroll Monday
CT = ZoneInfo("America/Chicago")


def overnight_punches(ctx, start, end):
    """DO NOT USE AS A GATE - kept only because it explains why there isn't one.

    The raw clock records keep the 02:15 stamp forever, even after a manager has
    corrected the confirmed time: Xaria's 25 Aug shift still reads 02:15:01 with a
    raw duration of 15h53m, while the hours she was actually paid were 6h55m. So
    this cannot tell a fixed punch from an unfixed one, and using it to block
    payroll would block it permanently.

    The real guard is upstream: End of Night now refuses to close a night that has
    a non-owner clock-out after midnight, so a closed night already means there
    isn't one. "Every night closed" is the only gate needed.
    """
    hits = []
    for cafe, cid in CAFES:
        day = start
        seen_weeks = set()
        while day <= end:
            week_end = day - timedelta(days=day.weekday()) + timedelta(days=6)
            if week_end not in seen_weeks:
                seen_weeks.add(week_end)
                d = ctx.request.get(BASE + "/rest/hrm/clocking/clockSchedule/%s/%s"
                                    % (cid, week_end.isoformat())).json()
                for sec in d:
                    for p in (sec.get("clockedSchedulePerson") or []):
                        name = (p.get("name") or "").strip()
                        for dd in p.get("clockedScheduleDay") or []:
                            for c in dd.get("clockedScheduleClock") or []:
                                out = (c.get("clockOutText") or "")
                                if not out:
                                    continue
                                hh = int(out.split(":")[0])
                                if 0 <= hh <= 5 and norm_name(name) not in OWNERS_OK_AT_2AM:
                                    hits.append("%s %s %s clocked out %s"
                                                % (cafe, dd.get("date"), name, out))
            day = week_end + timedelta(days=1)
    return sorted(set(hits))


def main():
    today = datetime.now(CT).date()
    if "--force" not in sys.argv and (today - ANCHOR_TUE).days % 14 != 0:
        print("off week (%d days since anchor Tuesday) - nothing to do"
              % (today - ANCHOR_TUE).days)
        return
    monday = today - timedelta(days=today.weekday())    # the Monday of this week
    start, end = monday - timedelta(days=14), monday - timedelta(days=1)
    print("period %s to %s" % (start, end))

    c = creds()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        sess = os.path.join(HERE, "session.json")
        ctx = b.new_context(storage_state=sess if os.path.exists(sess) else None)
        if ctx.request.get(BASE + "/rest/permission/personNavigation").status != 200:
            login(ctx, c)
            ctx.storage_state(path=sess)
        gaps = payroll_gate.open_nights(ctx, start, end)
        ctx.storage_state(path=sess)
        b.close()

    lines = ["Payroll check - %s to %s" % (start.strftime("%-d %b"), end.strftime("%-d %b")), ""]
    if gaps:
        lines.append(payroll_gate.describe(gaps))
    built = []
    if not gaps:
        lines.append("Every night is closed, and a night only closes if it has no "
                     "small-hours punch left on anyone but the owners. Checks pass.")
        import payroll_build
        made, notes, outdir = payroll_build.build(start, end)
        lines.append("")
        for cafe, path, n, tw in made:
            lines.append("%s - %d people, tips %s" % (cafe, n, " + ".join("%.2f" % t for t in tw)))
            built.append(path)
        for note in notes:
            lines.append("  " + note)
        lines.append("")
        lines.append("Both files attached. Owners are left out. Check them before "
                     "you upload - nothing here goes near ADP.")
    else:
        lines.append("")
        lines.append("No payroll file was built. Fix the above and tell me.")

    msg = "\n".join(lines)
    print(msg)
    if "--send" in sys.argv:
        import tsc_bot
        print("telegram ok:", tsc_bot.send(msg, c["t"], c["chat"]).get("ok"))
        for path in built:
            r = tsc_bot.send_document(path, os.path.basename(path), c["t"], c["chat"])
            print("sent %s ok=%s" % (os.path.basename(path), r.get("ok")))


if __name__ == "__main__":
    main()
