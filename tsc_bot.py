"""Fetch live sales and push the message to Telegram.
Re-logs in automatically if the saved session has expired."""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
import settings
BASE = settings.BASE
CAFE_IDS = [cid for _, cid in settings.CAFES]
U_ALL = (BASE + "/rest/salesReporting/v2/liveSales/all"
                "?direction=ASC&page=1&pageSize=25&sortBy=OUTLET")
U_WAGES = (BASE + "/rest/hrm/roster/reporting/live?"
           + "&".join("companyIds=" + c for c in CAFE_IDS)
           + "&includeManagementCosts=true")
SESSION = os.path.join(HERE, "session.json")
CT = ZoneInfo("America/Chicago")


def creds():
    c = {}
    for line in open(os.path.join(HERE, "CREDENTIALS.txt")):
        if line.startswith("username:"): c["u"] = line.split(":", 1)[1].strip()
        elif line.startswith("password:"): c["p"] = line.split(":", 1)[1].strip()
        elif line.startswith("Telegram bot token:"): c["t"] = line.split(":", 1)[1].strip()
        elif line.startswith("Telegram chat id:"): c["chat"] = line.split(":", 1)[1].strip()
    return c


def _login(ctx, c):
    pg = ctx.new_page()
    pg.goto(BASE + "/#!/login", wait_until="load", timeout=60000)
    pg.fill("#web-input-login-email", c["u"])
    pg.fill("#web-input-login-password", c["p"])
    pg.get_by_role("button", name="Sign In").first.click()
    pg.wait_for_timeout(12000)
    pg.close()


def fetch():
    c = creds()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(storage_state=SESSION if os.path.exists(SESSION) else None)
        r = ctx.request.get(U_ALL)
        if r.status != 200 or "results" not in r.text():
            _login(ctx, c)
            ctx.storage_state(path=SESSION)
            r = ctx.request.get(U_ALL)
        sales = r.json()
        wages = ctx.request.get(U_WAGES).json()
        ctx.storage_state(path=SESSION)
        b.close()

    wage = {w["objectReference"]["id"]: w["wagesFigure"]["dataTypes"] for w in wages}
    rows = []
    for r in sales["results"]:
        ls = r["liveSales"]
        w = wage.get(r["outlet"]["id"], {})
        rows.append({"cafe": r["outlet"]["display"], "date": r["saleDate"],
                     "age": r["relativeTime"], "net": ls["netSales"],
                     "labor_cost": w.get("BASE_COST", 0.0)})
    return rows


def build(rows, now):
    net = sum(r["net"] for r in rows)
    labor = sum(r["labor_cost"] for r in rows)
    pct = lambda cost, n: (100 * cost / n) if n else 0
    lines = ["TSC Live Sales - " + now.strftime("%a %-d %b, %-I:%M %p CT"),
             "Business day " + " / ".join(sorted({r["date"] for r in rows})), ""]
    for r in rows:
        lines.append("%s   Net $%s   Labor %.2f%%"
                     % (r["cafe"], format(r["net"], ",.2f"), pct(r["labor_cost"], r["net"])))
    lines += ["", "Combined   Net $%s   Labor %.2f%%"
              % (format(net, ",.2f"), pct(labor, net))]
    stale = [r for r in rows if r["age"] not in ("0 minutes ago", "1 minute ago")]
    if stale:
        lines += ["", "Till data age: " + ", ".join("%s %s" % (r["cafe"], r["age"]) for r in stale)]
    return "\n".join(lines)


def send(text, token, chat_id, attempts=4):
    """Telegram, with retries.

    A single SSL handshake timeout on 12 Sep swallowed a 4pm sales message: the
    figures had already been fetched and the message built, and the only thing
    that failed was the last hop out. One transient blip should not cost a run,
    so try again rather than dying. A retry could in theory deliver twice if the
    reply is what got lost - a duplicate sales figure is harmless, a missing one
    is not.
    """
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, data, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep((2, 5, 15)[i])
    raise last


def send_document(path, caption, token, chat_id, attempts=3):
    """Telegram file upload, built by hand so there is no extra dependency."""
    import mimetypes, uuid
    boundary = "----tsc" + uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = bytearray()
    for field, value in (("chat_id", str(chat_id)), ("caption", caption)):
        body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                 % (boundary, field, value)).encode()
    body += ("--%s\r\nContent-Disposition: form-data; name=\"document\"; filename=\"%s\"\r\n"
             "Content-Type: %s\r\n\r\n" % (boundary, name, ctype)).encode()
    body += open(path, "rb").read()
    body += ("\r\n--%s--\r\n" % boundary).encode()

    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendDocument" % token, data=bytes(body),
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep((3, 10)[i])
    raise last


if __name__ == "__main__":
    c = creds()
    msg = build(fetch(), datetime.now(CT))
    print(msg)
    if "--send" in sys.argv:
        res = send(msg, c["t"], c.get("chat"))
        print("\ntelegram ok:", res.get("ok"))
