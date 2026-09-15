"""Builds the two ADP insert files for a pay period.

Sources, all read-only:
  hours  - "Consolidated Employee Tips and Hours" report, run for the 14 days
  tips   - /rest/tips/tipShare/{cafeId}/config?startDate&endDate, one call per
           week per cafe, so four in total. Never opens the Tip Distribution
           screen, because Save on that screen actually distributes the tips.

The report lists a person once per section they worked. Those were previously
merged by hand, e.g. a cell reading =29.35+30.33+9, so this sums them per person.
"""
import os, sys, shutil, warnings
from datetime import date, timedelta
import openpyxl
from playwright.sync_api import sync_playwright

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import settings                                              # noqa: E402

BASE = settings.BASE

CAFE = settings.CAFE_PAYROLL
BATCH_ID, TEMP_DEPT, EARNINGS_CODE, TAX_MULT = (
    settings.BATCH_ID, settings.TEMP_DEPT, settings.EARNINGS_CODE, settings.TAX_MULT)

OWNERS_EXCLUDED = settings.OWNERS

ADJUSTMENTS = settings.ADJUSTMENTS
FIRST_ROW = 6 + 2          # headers on row 6, row 7 blank, data from row 8


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def read_report(path):
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    store, rows = None, []
    for r in range(1, ws.max_row + 1):
        joined = " ".join(str(ws.cell(r, c).value) for c in range(1, 5)
                          if ws.cell(r, c).value is not None)
        if "Store :" in joined:
            store = joined.split("Store :")[1].strip().split()[0]
            continue
        name, reg = ws.cell(r, 2).value, num(ws.cell(r, 7).value)
        if name and reg is not None:
            rows.append({"store": store, "name": str(name).strip(),
                         "payroll_no": num(ws.cell(r, 3).value),
                         "section": ws.cell(r, 4).value,
                         "rate": num(ws.cell(r, 6).value), "reg": reg,
                         "ot": num(ws.cell(r, 8).value) or 0.0})
    return rows


def norm_name(name):
    import re
    return " ".join(re.sub(r"[^a-z ]", "", (name or "").lower()).split())


def merge(rows):
    """One row per person. Flags a rate that differs between their sections -
    summing hours across two different rates would silently underpay someone."""
    people, order, warn = {}, [], []
    skipped = []
    for r in rows:
        if norm_name(r["name"]) in OWNERS_EXCLUDED:
            skipped.append("%s %s (%.2f h) left out - owner"
                           % (r["store"], r["name"], r["reg"]))
            continue
        key = (r["store"], r["name"])
        if key not in people:
            people[key] = dict(r)
            order.append(key)
        else:
            p = people[key]
            if p["rate"] != r["rate"]:
                warn.append("%s %s has two rates in this period, %s and %s - not merged"
                            % (r["store"], r["name"], p["rate"], r["rate"]))
            p["reg"] += r["reg"]
            p["ot"] += r["ot"]
    return [people[k] for k in order], warn + skipped


def tips(ctx, cafe_id, start, end):
    """Two weekly figures. Tips distribute weekly, so a fortnight is two lookups."""
    out = []
    wk = start
    while wk < end:
        wk_end = wk + timedelta(days=6)
        d = ctx.request.get(BASE + "/rest/tips/tipShare/%s/config?endDate=%s&startDate=%s"
                            % (cafe_id, wk_end.isoformat(), wk.isoformat())).json()
        out.append(round(d.get("totalTipsToDistribute") or 0.0, 2))
        wk = wk_end + timedelta(days=1)
    return out


def write_file(cafe, people, tip_weeks, start, end, outdir):
    """Adjustments are written as a visible formula, e.g. =38.25+4, so the extra
    hours can be seen and questioned rather than hiding inside a total."""
    cfg = CAFE[cafe]
    dest = os.path.join(outdir, cfg["out"])
    shutil.copy(os.path.join(HERE, cfg["template"]), dest)
    wb = openpyxl.load_workbook(dest)
    ws = wb.worksheets[0]
    vc = ws.cell(1, cfg["value_col"]).column_letter

    # clear whatever the template came with, then write this period
    for r in range(FIRST_ROW, ws.max_row + 1):
        for c in range(1, 14):
            ws.cell(r, c).value = None

    for i, p in enumerate(people):
        r = FIRST_ROW + i
        ws.cell(r, 1, p["name"])
        ws.cell(r, 2, cfg["co"])
        ws.cell(r, 3, BATCH_ID)
        ws.cell(r, 4, p["payroll_no"])
        ws.cell(r, 5, p["rate"])
        adj = ADJUSTMENTS.get((cafe, norm_name(p["name"])))
        if adj:
            ws.cell(r, 6, "=%s+%s" % (round(p["reg"], 2), adj[0]))
        else:
            ws.cell(r, 6, round(p["reg"], 2))
        ws.cell(r, 7, round(p["ot"], 2))
        ws.cell(r, 8, TEMP_DEPT)
        ws.cell(r, 9, EARNINGS_CODE)
        ws.cell(r, 10, "=(F{0}+G{0})*${1}$3".format(r, vc))
        ws.cell(r, 12, "=E{0}*F{0}".format(r))
        ws.cell(r, 13, "=E{0}*1.5*G{0}".format(r))

    last = FIRST_ROW + len(people) - 1
    lc, v = cfg["label_col"], cfg["value_col"]
    for row, label, formula in (
            (1, "total hours", "=sum(F5:G5)"),
            (2, "total tips", "=" + "+".join("%.2f" % t for t in tip_weeks)),
            (3, "tips per hour", "={0}2/{0}1".format(vc)),
            (4, "gross payment", "=L5+M5+J5"),
            (5, "total after tax", "={0}4*{1}".format(vc, TAX_MULT))):
        ws.cell(row, lc, label)
        ws.cell(row, v, formula)
    for col in ("E", "F", "G", "J", "L", "M"):
        ws.cell(5, openpyxl.utils.column_index_from_string(col),
                "=sum({0}{1}:{0}{2})".format(col, FIRST_ROW, cfg["last_row"]))
    wb.save(dest)
    return dest, last


def build(start, end, outdir=None):
    outdir = outdir or os.path.join(HERE, "payroll_%s_%s" % (start, end))
    os.makedirs(outdir, exist_ok=True)
    import run_report
    report = run_report.run(start.isoformat(), end.isoformat(),
                            dump=os.path.join(outdir, "source_hours-and-tips.xlsx"))
    rows = read_report(report)
    made, warnings_ = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(storage_state=os.path.join(HERE, "session.json"))
        for cafe in CAFE:
            mine, warn = merge([r for r in rows if r["store"] == cafe])
            warnings_ += warn
            tw = tips(ctx, CAFE[cafe]["id"], start, end)
            for pp in mine:
                a = ADJUSTMENTS.get((cafe, norm_name(pp["name"])))
                if a:
                    warnings_.append("%s %s: %.2f h from the clock plus %s h %s"
                                     % (cafe, pp["name"], pp["reg"], a[0], a[1]))
            path, last = write_file(cafe, mine, tw, start, end, outdir)
            made.append((cafe, path, len(mine), tw))
        b.close()
    return made, warnings_, outdir


if __name__ == "__main__":
    s = date.fromisoformat(sys.argv[1])
    e = date.fromisoformat(sys.argv[2])
    made, warn, outdir = build(s, e)
    for cafe, path, n, tw in made:
        print("%s -> %s  (%d people, tips %s)" % (cafe, os.path.basename(path), n, tw))
    for w in warn:
        print("WARNING:", w)
    print("folder:", outdir)
