"""End of Night for the whole 14-day period, both cafes.

Runs 1am every other Monday. On an off week it exits quietly - cron fires weekly,
the cycle check lives here.
"""
import os, sys, json, re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "build"))
import variance, report                                    # noqa: E402
import payroll_gate                                        # noqa: E402

import settings                                              # noqa: E402

BASE = settings.BASE
CAFES = settings.CAFES
ANCHOR = date(*settings.ANCHOR_MONDAY)      # a known payroll Monday

OWNERS_OK_AT_2AM = settings.OWNERS_OK_AT_2AM


def norm_name(name):
    """Names arrive with different casing and sometimes a trailing asterisk -
    "Jane Doe", "JANE DOE", "Jane Doe*" are one person. Match on this,
    never on the raw string, or an exemption silently stops applying."""
    return " ".join(re.sub(r"[^a-z ]", "", (name or "").lower()).split())
CT = ZoneInfo("America/Chicago")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
ROW_END = re.compile(r"Total for Breaks:\s*\d+\s*m")
EON_HEAD = re.compile(r"Type of EON:\s*(?:Full|Partial|Quick)", re.I)
NAME_AT_START = re.compile(
    r"^\s*([A-Za-z][A-Za-z'\-\. ]*?)Section(?:[A-Za-z ]*?)Time(?=Confirmed Time:)")


def creds():
    c = {}
    for line in open(os.path.join(HERE, "CREDENTIALS.txt")):
        if line.startswith("username:"): c["u"] = line.split(":", 1)[1].strip()
        elif line.startswith("password:"): c["p"] = line.split(":", 1)[1].strip()
        elif line.startswith("Telegram bot token:"): c["t"] = line.split(":", 1)[1].strip()
        elif line.startswith("Telegram chat id:"): c["chat"] = line.split(":", 1)[1].split()[0].strip()
    return c


def login(ctx, c):
    pg = ctx.new_page()
    pg.goto(BASE + "/#!/login", wait_until="load", timeout=60000)
    pg.fill("#web-input-login-email", c["u"])
    pg.fill("#web-input-login-password", c["p"])
    pg.get_by_role("button", name="Sign In").first.click()
    pg.wait_for_timeout(12000)
    pg.close()


def locked_days(ctx, cafe_id, any_day_in_week):
    """Which days of that week are already closed. This is the skip check -
    the Previous End of Nights list only holds 10 rows and cannot be trusted."""
    d = ctx.request.get(BASE + "/rest/hrm/rosterEditor/roster/%s?date=%s"
                        % (cafe_id, any_day_in_week)).json()
    return {day["date"]: day.get("locked", False) for day in d.get("days", [])}


def crossed_midnight(confirmed):
    """True if a confirmed shift finished in the small hours.

    The cafes shut at 9pm, so a clock-out after midnight is never a real leaving
    time - it is the till closing a shift nobody clocked out of, usually stamped
    2:15am. Detected by the shift crossing noon-to-midnight rather than by an hour
    threshold, so 12:45am counts as well as 2:15am.
    """
    if not confirmed or "-" not in confirmed:
        return False
    a, b = [x.strip().lower() for x in confirmed.split("-", 1)]
    return a.endswith("pm") and b.endswith("am")


def parse_rows(captured):
    """Pair each confirmed time with its scheduled time and the employee name.

    inner_text() does not return input values, so times come from the inputs and
    names from the hidden summary block. Split that blob on the field that closes
    each row rather than matching one big pattern - a single regex kept gluing a
    stray letter from "0m" or "Full" onto the front of the name, and a wrong name
    on a payroll flag is worse than no name at all.
    """
    blob = captured.get("summaryText", "")
    segs = ROW_END.split(blob)
    if segs:
        head = EON_HEAD.split(segs[0])
        segs[0] = head[-1] if len(head) > 1 else segs[0]
    names = []
    for seg in segs:
        m = NAME_AT_START.match(seg)
        if m:
            names.append(m.group(1).strip())

    rows = captured.get("rows", [])
    # If the two sides disagree, every name could be against the wrong shift.
    # Drop the names rather than risk flagging the wrong person.
    aligned = len(names) == len(rows)
    if not aligned and rows:
        print("    name parse mismatch (%d names, %d shifts) - reporting without names"
              % (len(names), len(rows)))
    return [{"name": names[i] if aligned else "Shift %d" % (i + 1),
             "confirmed": r.get("confirmed") or "",
             "scheduled": r.get("scheduled") or ""}
            for i, r in enumerate(rows)]


def run_night(ctx, cafe, cafe_id, day, dry):
    """Close one night. Returns (status, rows, note)."""
    url = "%s/app.html#/hrm/eon-v2/%s/%s/23:00/FULL" % (BASE, cafe_id, day.isoformat())
    expect = "End of Night - %s - %s %d, %d" % (cafe, MONTHS[day.month - 1], day.day, day.year)
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="load", timeout=60000)
        pg.wait_for_timeout(20000)
        body = pg.inner_text("body")
        if expect not in body:
            return "skipped", [], "page did not show %s" % expect

        before = pg.eval_on_selector_all("input#confirmedTime", "e => e.map(x => x.value)")
        captured = pg.evaluate("""() => {
            const summary = [...document.querySelectorAll('*')].map(e => e.textContent || '')
                .filter(t => t.includes('Confirmed Time:') && t.includes('Type of EON'))
                .sort((a, b) => a.length - b.length)[0] || '';
            const inputs = [...document.querySelectorAll('input')];
            const rows = [];
            inputs.forEach((el, i) => {
                if (el.id !== 'confirmedTime') return;
                let sched = null;
                for (let j = i + 1; j < inputs.length; j++) {
                    if (inputs[j].id === 'confirmedTime') break;
                    if (inputs[j].disabled) { sched = inputs[j].value; break; }
                }
                rows.push({confirmed: el.value, scheduled: sched});
            });
            return {summaryText: summary, rows: rows};
        }""")
        rows = parse_rows(captured)

        # Someone scheduled who never clocked in at all. Setting a section does
        # not resolve that - it needs a person to decide whether they were absent
        # or worked without clocking, and that decides whether they get paid.
        noclock = [r for r in rows if not r["confirmed"] and r["scheduled"]]

        fixed = 0
        for _ in range(15):
            sel = pg.locator("md-select:has-text('Select Section')")
            if sel.count() == 0:
                pg.wait_for_timeout(1500)          # let Angular settle, then re-ask
                if pg.locator("md-select:has-text('Select Section')").count() == 0:
                    break
            sel.first.click(); pg.wait_for_timeout(1200)
            menu = pg.locator(".md-select-menu-container.md-active").last
            menu.wait_for(state="visible", timeout=8000)
            menu.locator("md-option").filter(has_text="Crew Member").first.click()
            pg.wait_for_timeout(1200)
            fixed += 1

        # A punch stamped 2:15am is the till closing a shift nobody clocked out
        # of. Closing the night would lock that time in as paid hours, and fixing
        # it afterwards needs an unlock, which this job will never do. So leave
        # the night open while the punch is still wrong.
        overnight = [r for r in rows if crossed_midnight(r.get("confirmed"))]
        owners = [r for r in overnight if norm_name(r["name"]) in OWNERS_OK_AT_2AM]
        staff = [r for r in overnight if r not in owners]
        if staff:
            return "left open", rows, (
                "clock-out after midnight, needs your check before it is paid: "
                + ", ".join("%s (%s)" % (r["name"], r["confirmed"]) for r in staff))
        owner_note = ""
        if owners:
            owner_note = "owner overnight punch left as is: " + ", ".join(
                "%s (%s)" % (r["name"], r["confirmed"]) for r in owners)

        if noclock:
            who = ", ".join("%s (scheduled %s)" % (r["name"], r["scheduled"]) for r in noclock)
            return "left open", rows, "no clocking at all for %s - your call whether they were absent or worked without clocking" % who

        after = pg.eval_on_selector_all("input#confirmedTime", "e => e.map(x => x.value)")
        if after != before:
            return "left open", rows, "clock times moved unexpectedly - aborted"

        if dry:
            return "dry run", rows, "%d section fix(es) would be applied" % fixed

        # Save is disabled while anything is unresolved. Clicking a disabled
        # button just times out and killed the whole run on 14 Sep, so ask first
        # and report the night rather than dying on it.
        save = pg.get_by_role("button", name="Save").first
        if not save.is_enabled():
            warn = [l.strip() for l in pg.inner_text("body").splitlines()
                    if "warning" in l.lower() or "required" in l.lower()]
            return "left open", rows, ("save button disabled - %s"
                                       % (warn[0][:120] if warn else "unresolved warning"))
        save.click()
        pg.wait_for_timeout(12000)
        bits = [x for x in (("%d section fix(es)" % fixed) if fixed else "", owner_note) if x]
        return "closed", rows, "; ".join(bits)
    finally:
        pg.close()


def main():
    dry = "--dry-run" in sys.argv
    today = datetime.now(CT).date()
    if "--force" not in sys.argv and (today - ANCHOR).days % 14 != 0:
        print("off week (%d days since anchor) - nothing to do" % (today - ANCHOR).days)
        return
    start, end = today - timedelta(days=14), today - timedelta(days=1)
    print("period %s to %s" % (start, end))

    c = creds()
    results = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 720},
                            storage_state=os.path.join(HERE, "session.json")
                            if os.path.exists(os.path.join(HERE, "session.json")) else None)
        if ctx.request.get(BASE + "/rest/permission/personNavigation").status != 200:
            login(ctx, c)
            ctx.storage_state(path=os.path.join(HERE, "session.json"))

        for cafe, cafe_id in CAFES:
            closed, skipped, flags, errors = [], [], [], []
            weeks = {}
            for n in range((end - start).days + 1):
                day = start + timedelta(days=n)
                wk = day - timedelta(days=day.weekday())
                if wk not in weeks:
                    weeks[wk] = locked_days(ctx, cafe_id, (wk + timedelta(days=6)).isoformat())
                if weeks[wk].get(day.isoformat()):
                    skipped.append(day); continue

                try:
                    status, rows, note = run_night(ctx, cafe, cafe_id, day, dry)
                except Exception as exc:
                    status, rows, note = "left open", [], "%s: %s" % (
                        type(exc).__name__, str(exc).splitlines()[0][:120])
                print("  %s %s -> %s %s" % (cafe, day, status, note))
                if status in ("closed", "dry run"):
                    closed.append(day)
                else:
                    errors.append("%s %s not closed - %s" % (cafe, day.strftime("%a %-d %b"), note))
                for r in rows:
                    for f in variance.check_shift(r["name"], day, r["scheduled"], r["confirmed"]):
                        flags.append(f)
            results.append({"cafe": cafe, "closed": closed, "skipped": skipped,
                            "flags": flags, "errors": errors})
        gaps = payroll_gate.open_nights(ctx, start, end)
        ctx.storage_state(path=os.path.join(HERE, "session.json"))
        b.close()

    msg = report.build(today, start, end, results)
    # Say plainly whether payroll can run. A file built over a period with open
    # nights is short of hours and looks perfectly normal, so this has to be
    # stated every run, not inferred from the list above.
    msg += "\n\n" + payroll_gate.describe(gaps)
    print("\n" + msg)
    if "--send" in sys.argv:
        import tsc_bot
        print("\ntelegram ok:", tsc_bot.send(msg, c["t"], c["chat"]).get("ok"))


if __name__ == "__main__":
    main()
