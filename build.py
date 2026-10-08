"""
Rebuilds the Vishwas Performance Dashboard from the live Google Sheet.

Usage:  python build.py
Output: C:\\Users\\RPFC 1\\Documents\\Claude\\vishwas_dashboard\\output.html

Emits one record per real establishment case (DATA.cases) plus the officer
roster (DATA.roster). All rollups (by officer, by category, by DA) and the
drill-down lists are computed client-side in the template's JS from this
single source, so the dashboard's summary numbers and its "click a box to
see the underlying cases" feature can never drift apart.

After running this, publish/update the artifact with the Artifact tool using
url="https://claude.ai/code/artifact/b4e1c98a-b8c6-4efb-bdea-7677fe2f0e33"
so it updates the existing dashboard link instead of minting a new one.
"""
import csv
import datetime
import io
import json
import os
import re
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = "1siQzaeOVOwwPWGJlZPTg850sluGhoNFHyY1efvdww_Q"
GIDS = {
    "cat1": "749666687",
    "cat2": "913415292",
    "cat3": "96316066",
    "cat4": "507110994",
    "roster": "1133137544",
    "outreach": "1221557974",
}
# Confirmed sample/test rows to exclude from application-workflow figures
# (base case data on these rows is real and IS kept; only the Vishwas
# application/workflow fields on them are treated as non-real).
SAMPLE_ESTT_CODES = {"CBSLM0000131000", "CBSLM0004900000"}


def fetch_csv(gid):
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(data)))


def fetch_csv_by_name(sheet_name):
    """Fetch a tab by its name rather than a numeric gid, for tabs that
    don't exist yet (so we don't need to know a gid in advance). Google's
    gviz endpoint silently falls back to a DIFFERENT sheet if the name
    isn't found instead of erroring, so the caller must sanity-check the
    header row before trusting the result."""
    import urllib.parse
    q = urllib.parse.quote(sheet_name)
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={q}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read().decode("utf-8-sig")
        return list(csv.reader(io.StringIO(data)))
    except Exception:
        return None


def num(x):
    x = (x or "").replace(",", "").strip()
    if x in ("", "-", "_"):
        return 0.0
    try:
        return float(x)
    except ValueError:
        return 0.0


# Each case field -> header text fragments that identify its column (first
# match wins). Columns are looked up by header text rather than fixed
# position because the office keeps inserting columns into these tabs, and
# fixed indices silently shifted onto the wrong data every time they did.
CASE_COLUMNS = {
    "da": ["da concerned"],
    "eo": ["name of the eo"],
    "estt": ["estt code"],
    "esttName": ["estt name"],
    "legalForum": ["legal forum"],
    "caseNo": ["case no"],
    "assessed": ["14b assessed amount"],
    "remit14b": ["14b remitted amount"],
    "status7q": ["7q (fully"],
    "email": ["date of email sent"],
    "sms": ["date of sms sent"],
    "appDate": ["application date"],
    "daDate": ["submission by da", "receipt at da"],
    "ssDate": ["submission by ss", "receipt at ss"],
    "apfcDate": ["submission by apfc", "receipt at apfc"],
    "approval": ["approval date"],
    "withdrawal": ["withdr"],
}


def parse_category(rows, cat_id):
    """One case record per row whose DIVISION cell is a number. Fields a
    tab doesn't have (e.g. Legal Forum outside Category I) come out blank."""
    header_idx = next((i for i in range(min(5, len(rows)))
                       if "division" in [c.strip().lower() for c in rows[i]]), None)
    if header_idx is None:
        return []
    header = [c.strip().lower() for c in rows[header_idx]]
    c_div = header.index("division")
    cols = {field: next((i for n in needles for i, h in enumerate(header) if n in h), None)
            for field, needles in CASE_COLUMNS.items()}

    def get(r, field):
        i = cols[field]
        return r[i].strip() if i is not None and len(r) > i else ""

    out = []
    for r in rows[header_idx + 1:]:
        if len(r) <= c_div or not r[c_div].strip().isdigit():
            continue
        estt = get(r, "estt")
        app_date = "" if estt in SAMPLE_ESTT_CODES else get(r, "appDate")
        out.append({
            "category": cat_id, "division": r[c_div].strip(), "eo": get(r, "eo"),
            "estt": estt, "esttName": get(r, "esttName"),
            "legalForum": get(r, "legalForum"), "caseNo": get(r, "caseNo"),
            "da": get(r, "da"),
            "email": bool(get(r, "email")), "sms": bool(get(r, "sms")),
            "applicationDate": fmt_app_date(app_date),
            "applicationStage": application_stage(app_date, get(r, "daDate"), get(r, "ssDate"),
                                                  get(r, "apfcDate"), get(r, "approval")),
            "withdrawalDate": get(r, "withdrawal"),
            "status7q": get(r, "status7q"),
            "assessed": num(get(r, "assessed")), "remit14b": num(get(r, "remit14b")),
        })
    return out


def fmt_app_date(raw):
    """Application date is stamped as a 12-digit YYMMDDHHMMSS string
    (e.g. '260722122608' -> 22-07-2026 12:26). Falls back to the raw
    text unchanged for any other format (e.g. a plain DD-MM-YYYY date
    entered by hand)."""
    raw = (raw or "").strip()
    if len(raw) == 12 and raw.isdigit():
        yy, mo, dd, hh, mi = raw[0:2], raw[2:4], raw[4:6], raw[6:8], raw[8:10]
        return f"{dd}-{mo}-20{yy} {hh}:{mi}"
    return raw


def application_stage(app_date, da_date, ss_date, apfc_date, approval_date):
    """Derive where an application currently sits from the four receipt-
    stamp columns the office added. An application only exists at all if
    app_date is set; otherwise there's nothing to stage."""
    if not app_date:
        return ""
    if approval_date:
        return "Approved"
    if apfc_date:
        return "Pending at APFC"
    if ss_date:
        return "Pending at SS"
    if da_date:
        return "Pending at DA"
    return "Pending at DA"  # received but not yet logged at any level


def read_outreach():
    """Reads the "Outreach" tab (added by the office). Layout: a title row
    ("OUTREACH PROGRAM CONDUCTED") sits above the real header row (Division,
    Name, OUTREACH DETAILS, DATE) - so the header row is NOT always row 0
    and must be located by scanning. Below the header is a sub-row listing
    one calendar date per column (15 Jul, 16 Jul, ... 31 Jul); the actual
    counts for each metric are logged per-day in those columns, so a
    metric's total is the SUM across all of that row's day-columns, not a
    single fixed "value" column. Division/Name are only filled on the first
    row of each division's block and blank on the rows below it, so we
    track the "current" division while scanning down. Officers holding an
    additional-charge division have BOTH division numbers merged into one
    cell, e.g. "506 & 507" - kept as-is as the "division" key rather than
    split, since we can't tell how to divide their totals between the two.

    Fetched by gid (not by tab name via gviz): gviz's by-name lookup does
    NOT reliably return values from merged cells like "506 & 507" (they
    come back blank), while the direct CSV export by gid does.

    Returns [] if the tab doesn't exist yet or doesn't look like this shape.
    """
    rows = fetch_csv(GIDS["outreach"])
    if not rows or len(rows) < 2:
        return []

    header_row_idx = None
    header = None
    for hr in range(min(5, len(rows))):
        candidate = [c.strip().lower() for c in rows[hr]]
        if any("division" in h for h in candidate) and any("outreach details" in h for h in candidate):
            header_row_idx = hr
            header = candidate
            break
    if header_row_idx is None:
        return []  # gviz fell back to some other tab, or the layout changed again

    def col(*names):
        for i, h in enumerate(header):
            for n in names:
                if n in h:
                    return i
        return None

    c_div = col("division")
    c_name = col("name")
    c_detail = col("outreach details")
    c_date = col("date")
    # Day-columns start wherever the sub-row right below the header first
    # has a value (that row is nothing but day-by-day date labels, e.g.
    # "15-Jul-2026", one per column) - detecting it this way instead of
    # assuming a fixed offset from the "DATE" header text survives the
    # sheet being reshuffled (the exact offset has already changed once).
    date_subrow = rows[header_row_idx + 1] if len(rows) > header_row_idx + 1 else []
    value_start = next((i for i, c in enumerate(date_subrow) if c.strip()),
                        (c_date if c_date is not None else 5))

    LABEL_MAP = {
        "venue": "venue",
        "employers participated": "employersParticipated",
        "employees participated": "employeesParticipated",
        "establishments identified": "establishmentsIdentified",
        "emails sent": "emails",
        "sms sent": "sms",
        "contacted over phone": "employersContacted",
    }

    by_div = {}
    order = []
    cur_div, cur_name = None, None
    for r in rows[header_row_idx + 1:]:
        div_cell = r[c_div].strip() if c_div is not None and len(r) > c_div else ""
        name_cell = r[c_name].strip() if c_name is not None and len(r) > c_name else ""
        if re.search(r"\d", div_cell):
            # Accepts both a plain division number ("501") and a combined
            # additional-charge cell ("506 & 507") - either way, any digit
            # present means this row starts a new division block.
            cur_div, cur_name = div_cell, name_cell
        elif name_cell and name_cell != cur_name:
            # A new officer's block started but its Division cell is blank
            # (a data-entry gap in the sheet) - stop attributing rows to
            # whatever division came before, since that would silently
            # corrupt an unrelated division's totals. Skip this block
            # entirely until a row with a real division number appears.
            cur_div, cur_name = None, name_cell
        if not cur_div:
            continue
        detail = (r[c_detail].strip().lower() if c_detail is not None and len(r) > c_detail else "")
        if cur_div not in by_div:
            by_div[cur_div] = {"division": cur_div, "name": cur_name, "venue": "",
                                "emails": 0, "sms": 0, "seminars": 0, "webinars": 0,
                                "employersParticipated": 0, "employeesParticipated": 0,
                                "employersContacted": 0, "establishmentsIdentified": 0}
            order.append(cur_div)
        if "mode of outreach" in detail:
            # This row logs the word SEMINAR or WEBINAR per day (text, not
            # a count) - so a metric's total here is how many days say
            # each word, not a numeric sum.
            for c in r[value_start:]:
                v = c.strip().lower()
                if "seminar" in v:
                    by_div[cur_div]["seminars"] += 1
                elif "webinar" in v:
                    by_div[cur_div]["webinars"] += 1
            continue
        for label, key in LABEL_MAP.items():
            if label in detail:
                if key == "venue":
                    venue_text = next((c.strip() for c in r[value_start:] if c.strip()), "")
                    by_div[cur_div]["venue"] = venue_text
                else:
                    by_div[cur_div][key] = sum(num(c) for c in r[value_start:])
                break
    return [by_div[d] for d in order]


def main():
    roster_rows = fetch_csv(GIDS["roster"])[1:]
    roster = [
        {"division": r[0].strip(), "name": r[1].strip()}
        for r in roster_rows if len(r) >= 2 and r[0].strip()
    ]

    cases = []
    for cat_id in ("cat1", "cat2", "cat3", "cat4"):
        cases.extend(parse_category(fetch_csv(GIDS[cat_id]), cat_id))

    outreach = read_outreach()

    data = {
        "asOf": datetime.date.today().strftime("%d %b %Y"),
        "roster": roster,
        "cases": cases,
        "outreach": outreach,
    }

    with open(os.path.join(BASE, "template.html"), encoding="utf-8") as f:
        html = f.read()

    html = html.replace("__DATA_JSON__", json.dumps(data))

    for key, fname in [
        ("__SANS400__", "sans400"), ("__SANS600__", "sans600"), ("__SANS700__", "sans700"),
        ("__MONO500__", "mono500"), ("__MONO600__", "mono600"),
    ]:
        with open(os.path.join(BASE, "fonts", fname + ".b64"), encoding="ascii") as f:
            html = html.replace(key, f.read().strip())

    out_path = os.path.join(BASE, "output.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    eligible = sum(1 for c in cases if c["status7q"] == "Fully Remitted")
    stages = {}
    for c in cases:
        if c["applicationStage"]:
            stages[c["applicationStage"]] = stages.get(c["applicationStage"], 0) + 1
    print(f"Built {out_path} ({len(html)} chars) — as of {data['asOf']}")
    print(f"Totals: {len(cases)} cases, "
          f"{sum(1 for c in cases if c['email'] or c['sms'])} outreach, "
          f"{sum(1 for c in cases if c['da'])} DA-assigned, "
          f"{sum(1 for c in cases if c['applicationDate'])} applications, "
          f"{eligible} Vishwas-eligible (7Q fully remitted, any category)")
    print(f"Application stages: {stages or 'none yet'}")
    print(f"Outreach Activity tab: {'found, ' + str(len(outreach)) + ' division rows' if outreach else 'not found yet'}")


if __name__ == "__main__":
    main()
