"""Builds the Telegram summary for one biweekly end-of-night run."""

from datetime import date


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def build(run_day, period_start, period_end, cafes):
    """cafes: list of dicts with keys cafe, closed, skipped, flags, errors."""
    out = ["End of Night - %s" % run_day.strftime("%a %d %b %Y"),
           "Period %s to %s" % (period_start.strftime("%d %b"),
                                period_end.strftime("%d %b")),
           ""]

    for c in cafes:
        line = "%s: closed %s" % (c["cafe"], _plural(len(c["closed"]), "day"))
        if c["skipped"]:
            line += ", %d already done" % len(c["skipped"])
        out.append(line)
    out.append("")

    total = sum(len(c["flags"]) for c in cafes)
    if not total:
        out.append("No time variances to review.")
    else:
        out.append("%s to review:" % _plural(total, "variance"))
        for c in cafes:
            if not c["flags"]:
                continue
            out.append("")
            out.append(c["cafe"])
            for f in c["flags"]:
                out.append("  %s, %s" % (f["date"].strftime("%a %d %b"), f["employee"]))
                out.append("    %s" % f["detail"])

    problems = [e for c in cafes for e in c["errors"]]
    if problems:
        out.append("")
        out.append("Needs your attention:")
        for e in problems:
            out.append("  %s" % e)

    out.append("")
    out.append("No clock times were changed. Nothing was unlocked.")
    return "\n".join(out)
