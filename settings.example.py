"""Everything specific to this business. Stays on the server, never published.

The code in the repository is generic; this file is what makes it yours. Copy
settings.example.py to settings.py on a new server and fill it in.
"""

# SynergySuite tenant
BASE = "https://yourcompany.synergysuite.net"
CAFES = [("CAFE1", "000000000000001"),
         ("CAFE2", "000000000000002")]

# Per-cafe payroll file setup
CAFE_PAYROLL = {
    "CAFE1": {"co": "XXX", "template": "EPIXXX01 Insert (1).xlsx",
              "out": "EPIXXX01 Insert.xlsx", "label_col": 14, "value_col": 15,
              "last_row": 198},
    "CAFE2": {"co": "YYY", "template": "EPIYYY01 Insert (2).xlsx",
              "out": "EPIYYY01 Insert.xlsx", "label_col": 15, "value_col": 16,
              "last_row": 57},
}
BATCH_ID, TEMP_DEPT, EARNINGS_CODE, TAX_MULT = 10000, 100000, "TNP", 1.0

# People who are not paid through ADP, so never appear in the payroll files.
OWNERS = {"owner one", "owner two"}

# A clock-out after midnight on one of these people does not hold a night open.
OWNERS_OK_AT_2AM = {"owner one", "owner two"}

# Hours worked outside the timeclock. (cafe, name) -> (hours, reason)
ADJUSTMENTS = {
    ("CAFE1", "employee name"): (4.0, "travelling time"),
}

# People with a staff record at each cafe carrying different dates of birth.
# Their birthday is taken from the first cafe listed in CAFES and the mismatch
# warning is suppressed until the records are corrected.
CONFLICT_MUTED = {"owner two"}

# Cycle anchors. EOD runs the Monday, payroll the Tuesday after.
ANCHOR_MONDAY = (2026, 8, 17)
ANCHOR_TUESDAY = (2026, 8, 18)

# Expected shape of a day, per cafe. An expectation, not a rule - a mismatch is
# reported, never corrected.
SHIFT_PATTERN = {
    "CAFE1": {"week": ["6:30a", "8a", "12p", "4p", "4p"],
              "wknd": ["7:30a", "9a", "12p", "4p", "4p"]},
    "CAFE2": {"week": ["6:30a", "9a", "4p", "4p"],
              "wknd": ["7:30a", "9a", "4p", "4p"]},
}
