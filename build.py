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
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = "1siQzaeOVOwwPWGJlZPTg850sluGhoNFHyY1efvdww_Q"
GIDS = {
    "cat1": "749666687",
    "cat2": "913415292",
    "cat3": "96316066",
    "cat4": "507110994",
    "roster": "1133137544",
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


def real_rows(rows, div_col):
    return [r for r in rows if len(r) > div_col and r[div_col].strip().isdigit()]


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
    track the "current" division while scanning down.

    Returns [] if the tab doesn't exist yet or doesn't look like this shape.
    """
    rows = fetch_csv_by_name("Outreach")
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
    value_start = (c_date + 1) if c_date is not None else 5  # day-columns start right after DATE

    LABEL_MAP = {
        "venue": "venue",
        "employers participated": "employersParticipated",
        "employees participated": "employeesParticipated",
        "establishments identified": "establishmentsIdentified",
        "emails sent": "emails",
        "sms sent": "sms",
        "physical seminar": "seminars",
        "online webinar": "webinars",
        "contacted personally": "employersContacted",
    }

    by_div = {}
    order = []
    cur_div, cur_name = None, None
    for r in rows[header_row_idx + 1:]:
        div_cell = r[c_div].strip() if c_div is not None and len(r) > c_div else ""
        name_cell = r[c_name].strip() if c_name is not None and len(r) > c_name else ""
        if div_cell.isdigit():
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

    cat1 = real_rows(fetch_csv(GIDS["cat1"])[2:], 4)
    cat2 = real_rows(fetch_csv(GIDS["cat2"])[2:], 4)
    cat3 = real_rows(fetch_csv(GIDS["cat3"])[2:], 4)
    cat4 = real_rows(fetch_csv(GIDS["cat4"])[2:], 4)

    cases = []

    # Category I columns: 2 DA, 3 EO, 4 Div, 5 Estt code, 6 Estt name, 9 Legal
    # Forum, 10 Case No, 13 14B assessed, 14 14B remitted, 18 7Q status,
    # 20 email sent, 21 sms sent, 22 Application Id, 23 application date,
    # 24 DA receipt date, 25 SS receipt date, 26 APFC receipt date,
    # 28 approval date, 36 date of withdrawal of petition.
    for r in cat1:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        app_date = "" if is_sample else r[23].strip()
        da_date, ss_date, apfc_date = r[24].strip(), r[25].strip(), r[26].strip()
        approval_date = r[28].strip() if len(r) > 28 else ""
        cases.append({
            "category": "cat1", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": r[9].strip(), "caseNo": r[10].strip(),
            "da": r[2].strip(),
            "email": bool(r[20].strip()), "sms": bool(r[21].strip()),
            "applicationDate": fmt_app_date(app_date),
            "applicationStage": application_stage(app_date, da_date, ss_date, apfc_date, approval_date),
            "withdrawalDate": r[36].strip() if len(r) > 36 else "",
            "status7q": r[18].strip(),
            "assessed": num(r[13]), "remit14b": num(r[14]),
        })

    # Category II columns: 2 DA, 3 EO, 4 Div, 5 Estt code, 6 Estt name,
    # 18 email sent, 20 Application Id, 21 application date, 22 DA receipt,
    # 23 SS receipt, 24 APFC receipt, 26 approval date. No reliable 14B/7Q
    # figures at this stage (see build notes) - status7q carried through
    # for display only, never used for Vishwas-eligibility (Category I only).
    for r in cat2:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        app_date = "" if is_sample else r[21].strip()
        da_date, ss_date, apfc_date = r[22].strip(), r[23].strip(), r[24].strip()
        approval_date = r[26].strip() if len(r) > 26 else ""
        cases.append({
            "category": "cat2", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": "", "caseNo": "",
            "da": r[2].strip(),
            "email": bool(r[18].strip()), "sms": False,
            "applicationDate": fmt_app_date(app_date),
            "applicationStage": application_stage(app_date, da_date, ss_date, apfc_date, approval_date),
            "withdrawalDate": "",
            "status7q": r[17].strip(),
            "assessed": 0, "remit14b": 0,
        })

    # Category III columns: 2 DA, 3 EO, 4 Div, 5 Estt code, 6 Estt name,
    # 18 email sent, 19 sms sent, 20 application date (no Application Id
    # column in this sheet), 21 DA receipt, 22 SS receipt, 23 APFC receipt,
    # 25 approval date.
    for r in cat3:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        app_date = "" if is_sample else r[20].strip()
        da_date, ss_date, apfc_date = r[21].strip(), r[22].strip(), r[23].strip()
        approval_date = r[25].strip() if len(r) > 25 else ""
        cases.append({
            "category": "cat3", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": "", "caseNo": "",
            "da": r[2].strip(),
            "email": bool(r[18].strip()), "sms": bool(r[19].strip()),
            "applicationDate": fmt_app_date(app_date),
            "applicationStage": application_stage(app_date, da_date, ss_date, apfc_date, approval_date),
            "withdrawalDate": "",
            "status7q": r[17].strip(),
            "assessed": 0, "remit14b": 0,
        })

    # Category IV columns: same layout as Category III - 2 DA, 3 EO, 4 Div,
    # 5 Estt code, 6 Estt name, 17 status7q, 18 email sent, 19 sms sent,
    # 20 application date, 21 DA receipt, 22 SS receipt, 23 APFC receipt,
    # 25 approval date.
    for r in cat4:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        app_date = "" if is_sample else r[20].strip()
        da_date, ss_date, apfc_date = r[21].strip(), r[22].strip(), r[23].strip()
        approval_date = r[25].strip() if len(r) > 25 else ""
        cases.append({
            "category": "cat4", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": "", "caseNo": "",
            "da": r[2].strip(),
            "email": bool(r[18].strip()), "sms": bool(r[19].strip()),
            "applicationDate": fmt_app_date(app_date),
            "applicationStage": application_stage(app_date, da_date, ss_date, apfc_date, approval_date),
            "withdrawalDate": "",
            "status7q": r[17].strip(),
            "assessed": 0, "remit14b": 0,
        })

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
