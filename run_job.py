"""Wrapper every scheduled job runs through.

Its whole purpose is that a job which dies must say so. A silent failure looks
exactly like a quiet trading day, and you would not notice for a fortnight.
"""
import os, sys, traceback, subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
CT = ZoneInfo("America/Chicago")

JOBS = {
    "live_sales":  ["tsc_bot.py", "--send"],
    "next_day":    ["roster_report.py", "--send"],
    "end_of_night": ["eon_biweekly.py", "--send"],
    "birthdays":   ["birthdays.py", "--send"],
    "payroll":     ["payroll.py", "--send"],
    "schedule_check": ["schedule_check.py", "--send"],
}


def creds():
    c = {}
    for line in open(os.path.join(HERE, "CREDENTIALS.txt")):
        if line.startswith("Telegram bot token:"): c["t"] = line.split(":", 1)[1].strip()
        elif line.startswith("Telegram chat id:"): c["chat"] = line.split(":", 1)[1].split()[0].strip()
    return c


def alert(text):
    """Same retries as the jobs themselves - the alert is the one message that
    absolutely must not be lost to a transient network blip."""
    import time, urllib.request, urllib.parse
    try:
        c = creds()
    except Exception:
        return
    data = urllib.parse.urlencode({"chat_id": c["chat"], "text": text}).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % c["t"]
    for i in range(4):
        try:
            urllib.request.urlopen(url, data, timeout=30).read()
            return
        except Exception:
            if i < 3:
                time.sleep((2, 5, 15)[i])
    # nothing left to do if even the alert cannot go out


def main():
    job = sys.argv[1]
    if job not in JOBS:
        sys.exit("unknown job %r, expected one of %s" % (job, ", ".join(JOBS)))
    os.makedirs(LOGS, exist_ok=True)
    stamp = datetime.now(CT)
    log = os.path.join(LOGS, "%s-%s.log" % (job, stamp.strftime("%Y%m%d-%H%M")))

    cmd = [sys.executable] + [os.path.join(HERE, JOBS[job][0])] + JOBS[job][1:]
    with open(log, "w") as fh:
        fh.write("%s starting %s\n\n" % (stamp.isoformat(), job))
        fh.flush()
        try:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                               timeout=3600, cwd=HERE)
            code = r.returncode
        except subprocess.TimeoutExpired:
            fh.write("\nTIMED OUT after 60 minutes\n")
            code = -1
        except Exception:
            fh.write("\n" + traceback.format_exc())
            code = -2

    if code != 0:
        tail = ""
        try:
            tail = "".join(open(log).readlines()[-12:])
        except Exception:
            pass
        alert("%s FAILED at %s CT (exit %s)\n\n%s\n\nLog: %s"
              % (job, stamp.strftime("%a %-d %b %-I:%M %p"), code, tail[-900:], log))
    sys.exit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
