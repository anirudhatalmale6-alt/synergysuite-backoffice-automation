"""Scheduled vs clocked variance rules for the TSC end-of-night check.

Reads the two strings SynergySuite renders in the End of Night row, e.g.
    Scheduled Time  "2:00pm-9:30pm"
    Confirmed Time  "1:59pm-9:22pm"
and decides what gets flagged. Business day matters because a clock-out after
midnight belongs to the following calendar day.
"""

from datetime import datetime, timedelta, date

GAP_MINUTES = 35            # flag a difference of more than this, either direction
LATE_CLOCK_OUT = "22:00"    # absolute ceiling: clocking out at/after this is a red flag
MISSED_PUNCH_HOUR = 1       # a clock-out at/after 01:00 is almost certainly a forgotten punch
STRICT_930_RULE = False     # True = also flag any pre-9:00pm out on a 9:30pm shift


def _parse_one(t, business_day):
    """'9:22pm' -> datetime on business_day. '12:15am' rolls to the next day."""
    t = t.strip().lower().replace(" ", "")
    fmt = "%I:%M%p" if ":" in t else "%I%p"
    naive = datetime.strptime(t, fmt)
    stamp = datetime.combine(business_day, naive.time())
    if naive.hour < 5 or (naive.hour == 12 and naive.strftime("%p").lower() == "am"):
        stamp += timedelta(days=1)      # small hours belong to the next date
    return stamp


def parse_range(s, business_day):
    """'2:00pm-9:30pm' -> (start, end) datetimes. Returns None if blank."""
    if not s or "-" not in s:
        return None
    start_s, end_s = s.split("-", 1)
    start = _parse_one(start_s, business_day)
    end = _parse_one(end_s, business_day)
    if end < start:                      # shift crossed midnight
        end += timedelta(days=1)
    return start, end


def check_shift(employee, business_day, scheduled, confirmed):
    """Return a list of flag dicts for one employee's shift."""
    flags = []
    sched = parse_range(scheduled, business_day)
    conf = parse_range(confirmed, business_day)

    if conf is None:
        flags.append({"employee": employee, "date": business_day,
                      "type": "no clocking", "detail": "no confirmed time recorded"})
        return flags

    conf_in, conf_out = conf

    # Absolute ceiling, independent of what was scheduled. The cafes close at
    # 9 or 9:30pm, so a punch-out at/after 10pm is a red flag on its own even
    # when the gap against the schedule is inside tolerance. Checked before the
    # gap rule so it still fires when no schedule exists.
    midnight = datetime.combine(business_day, datetime.min.time())
    hh, mm = (int(x) for x in LATE_CLOCK_OUT.split(":"))
    ceiling = midnight + timedelta(hours=hh, minutes=mm)
    missed = midnight + timedelta(days=1, hours=MISSED_PUNCH_HOUR)
    if conf_out >= missed:
        flags.append({"employee": employee, "date": business_day,
                      "type": "missed punch",
                      "detail": "clocked out %s, past %02d:00 - almost certainly never punched out"
                                % (conf_out.strftime("%-I:%M%p").lower(), MISSED_PUNCH_HOUR)})
    elif conf_out >= ceiling:
        flags.append({"employee": employee, "date": business_day,
                      "type": "late clock-out",
                      "detail": "clocked out %s, at or after %s" % (
                          conf_out.strftime("%-I:%M%p").lower(), LATE_CLOCK_OUT)})

    if sched is None:
        flags.append({"employee": employee, "date": business_day,
                      "type": "unscheduled", "detail": "clocked time with no schedule"})
        return flags

    sched_in, sched_out = sched

    for label, s, c in (("clock-in", sched_in, conf_in), ("clock-out", sched_out, conf_out)):
        delta = round((c - s).total_seconds() / 60)
        if abs(delta) > GAP_MINUTES:
            flags.append({"employee": employee, "date": business_day,
                          "type": label,
                          "detail": "%s %d min %s (scheduled %s, clocked %s)" % (
                              label, abs(delta), "late" if delta > 0 else "early",
                              s.strftime("%-I:%M%p").lower(),
                              c.strftime("%-I:%M%p").lower())})

    if STRICT_930_RULE and sched_out.strftime("%H:%M") == "21:30":
        if conf_out < datetime.combine(business_day, datetime.min.time()) + timedelta(hours=21):
            if not any(f["type"] == "clock-out" for f in flags):
                flags.append({"employee": employee, "date": business_day,
                              "type": "clock-out",
                              "detail": "left before 9:00pm on a 9:30pm shift (%s)" %
                                        conf_out.strftime("%-I:%M%p").lower()})
    return flags
