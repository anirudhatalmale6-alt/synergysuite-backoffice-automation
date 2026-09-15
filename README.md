# Back-office automation for a two-site restaurant group

Closes End of Night, builds ADP payroll files, checks rosters and posts
sales and staffing summaries to Telegram - all against SynergySuite.

Everything business-specific lives in `settings.py`, which is not in this
repository. See section 9.

---



Everything here runs on your VPS. It belongs to you: your server, your billing,
your SynergySuite login, your Telegram bot. Nothing depends on me continuing to
be involved.

---

## 1. The server

| | |
|---|---|
| Address | `<your-server-ip>` |
| Login | `ssh root@<your-server-ip>` using an SSH key |
| Provider | Hostinger, KVM 2 - 2 vCPU, 8 GB RAM, 100 GB disk |
| OS | Ubuntu 24.04 LTS |
| Timezone | America/Chicago, so every schedule below is your local time |
| Everything lives in | `/opt/tsc` |

SSH password login is switched off - key only, and root cannot log in with a
password. If you ever need to let somebody else in, add their public key to
`/root/.ssh/authorized_keys`.

Log files sit in `/opt/tsc/logs`, one per run, deleted automatically after 30
days by `/etc/cron.daily/tsc-logrotate`.

## 2. What runs, and when

| Job | When (Central) | What it does |
|---|---|---|
| `live_sales` | 7:30am Mon-Fri, 8:30am Sat-Sun, then 10am, 1pm, 4pm, 7pm, 10pm | Net sales and labor % per cafe plus a combined figure |
| `next_day` | 7pm daily | Who is scheduled tomorrow at both cafes |
| `birthdays` | 7am daily | Birthdays today, plus a heads-up for tomorrow. Silent when there are none |
| `end_of_night` | 1am, every other Monday | Closes the previous 14 days and reports timeclock variances |
| `schedule_check` | 8am Sunday | Audits the week you have built. Read-only, never changes a roster |
| `payroll` | 1am, every other Tuesday | Checks the period is finished, then builds the payroll files |

The two fortnightly jobs are fired weekly by cron and work out for themselves
whether this is their week, from the anchor dates in the scripts. On an off week
they exit quietly.

To run any of them by hand:

    /opt/tsc/venv/bin/python /opt/tsc/run_job.py live_sales

## 3. Failure alerts

Every job runs through `run_job.py`. If one fails you get a Telegram message with
the tail of its log. This was tested by deliberately breaking a job - the point
being that a job dying silently is worse than no automation, because an absent
message looks the same as a quiet day.

## 4. The rules built in, and why

These exist because of things found while building. Do not remove them casually.

**Never types a date into End of Night.** It navigates by URL. A two-digit year in
that box resolves to the wrong century - 8/5/26 becomes September 1926 - while the
box still displays what you typed.

**Refuses to save unless the page heading names the date it meant to close.**

**Never edits a confirmed clock time.** It reads them before and after and aborts
if any moved. The single exception is a zero-length punch (4:58pm-4:58pm), where
the end moves one minute so the night can close - and every such change is listed
in the report.

**Never unlocks a closed night.** It skips it and says so.

**Leaves a night open if anyone but you, an employee or an employee clocked out after
midnight.** A 2:15am punch is the till closing a shift nobody clocked out of.
Closing the night would lock those hours in as paid, and fixing it afterwards
needs an unlock this software will not do.

**Leaves a night open if somebody was scheduled and never clocked in at all.**
Whether they were absent or worked without clocking decides their pay, so it is
your decision.

**Resolves only the NO SECTION warning**, by setting Crew Member. Any other
warning leaves that night alone, and the run carries on with the rest.

**Builds no payroll file while any night in the period is open.** A file short of
a night's hours does not look wrong, it looks like a quiet week.

## 4b. The payroll files

Built by `payroll_build.py`, normally through the Tuesday job. To run a period by
hand:

    /opt/tsc/venv/bin/python /opt/tsc/payroll_build.py 2026-08-31 2026-09-13

It writes into a folder named for the period, alongside a copy of the source
report as `source_hours-and-tips.xlsx` so the file can always be traced back.

Where the numbers come from:

- Hours and rates: the "Consolidated Employee Tips and Hours" report, run for the
  14 days and downloaded as xlsx.
- Tips: one read-only call per cafe per week, four in total. The Distribute Tips
  screen is never opened, because Save on that screen actually distributes tips.

What it does to the data:

- A person listed once per section is merged into one row, hours summed. If their
  rate differs between sections it refuses to merge them and says so, because
  summing hours across two rates would underpay somebody quietly.
- Owners are left out entirely - an employee, an employee, an employee and Jay - and every
  exclusion is named in the run summary.
- Extra hours that exist outside the timeclock live in `ADJUSTMENTS` in that
  script. an employee currently carries 4 hours of travelling time. These are
  written into the cell as a visible sum, `=38.25+4`, never folded into a total,
  so anyone opening the file can see the adjustment.

What it will not do: upload anything to ADP. It builds the files, you check them
and you upload.

## 5. Timeclock flagging

Two rules, both settable in `build/variance.py`:

1. A clock-out at or after 10:00pm, whatever was scheduled (`LATE_CLOCK_OUT`).
2. More than 35 minutes between scheduled and clocked, at either end of the
   shift, in either direction (`GAP_MINUTES`).

## 6. Files

    /opt/tsc/
      CREDENTIALS.txt        SynergySuite login, Telegram token and chat id. chmod 600.
      session.json           cached SynergySuite session - safe to delete, it re-logs in
      availability_template.xlsx   staff availability and school holidays
      birthdays.json         cached names and dates of birth, chmod 600
      run_job.py             wrapper - every scheduled job goes through this
      crontab.txt            the schedule. Apply changes with: crontab /opt/tsc/crontab.txt
      tsc_bot.py             live sales
      roster_report.py       tomorrow's shifts
      birthdays.py           birthday reminders
      eon_biweekly.py        end of night
      payroll.py             payroll checks and build
      payroll_gate.py        the "is this period finished" check
      schedule_check.py      weekly schedule audit
      availability.py        reads the availability workbook
      build/variance.py      the timeclock flagging rules
      build/report.py        formats the end of night summary
      logs/                  one file per run, kept 30 days

## 7. What you maintain, and where

**In SynergySuite:** time-off requests as they come in; date of birth on a new
starter; the section on a shift when you create it; an end date when somebody
leaves; and keep the duplicate records in step for anyone who works at both cafes.

**In `availability_template.xlsx` on the server:** each person's hours per day,
whether their hours are hard or a preference, and the school holiday dates. During
a school holiday the weekday rule for under-18s switches off automatically.

**By editing a script:** the shift patterns (`schedule_check.py`, `PATTERN`), the
35-minute threshold and the 10pm ceiling (`build/variance.py`), the fortnightly
anchor dates (`eon_biweekly.py`, `payroll.py`), the send times (`crontab.txt`),
the owners exempt from the 2:15am rule (`eon_biweekly.py`, `OWNERS_OK_AT_2AM`),
and the birthday conflict mutes (`birthdays.py`, `CONFLICT_MUTED`).

Rule of thumb: if it is about a person, it belongs in SynergySuite or the
workbook. If it is about a rule, it is in a script.

## 8. If something breaks

Check `/opt/tsc/logs` for the most recent run of that job. A stale SynergySuite
session fixes itself - the scripts log in again on their own. If the password
changes, edit `CREDENTIALS.txt`. If Telegram messages stop, confirm the bot token
in that file still works:

    curl "https://api.telegram.org/bot<TOKEN>/getMe"

## 9. Setting this up somewhere else

1. `cp settings.example.py settings.py` and fill in the tenant URL, cafe ids,
   payroll file settings, owners and cycle anchor dates.
2. Put the two ADP templates next to the scripts.
3. Create `CREDENTIALS.txt` in the format shown in `CREDENTIALS.txt.example`.
4. Run `install.sh` as root.

`settings.py`, the credentials, the availability workbook and the ADP templates
are all gitignored, because they hold either secrets or people's personal details.
