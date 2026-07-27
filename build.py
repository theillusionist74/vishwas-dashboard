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


def main():
    roster_rows = fetch_csv(GIDS["roster"])[1:]
    roster = [
        {"division": r[0].strip(), "name": r[1].strip()}
        for r in roster_rows if len(r) >= 2 and r[0].strip()
    ]

    cat1 = real_rows(fetch_csv(GIDS["cat1"])[2:], 4)
    cat2 = real_rows(fetch_csv(GIDS["cat2"])[2:], 4)
    cat3 = real_rows(fetch_csv(GIDS["cat3"])[2:], 4)
    cat4_raw = fetch_csv(GIDS["cat4"])[2:]
    cat4 = [r for r in cat4_raw if len(r) > 6 and r[6].strip()]

    cases = []

    # Category I columns: 2 DA, 3 EO, 4 Div, 5 Estt code, 6 Estt name, 9 Legal
    # Forum, 10 Case No, 13 14B assessed, 14 14B remitted, 18 7Q status,
    # 20 email sent, 21 sms sent, 22 application date.
    for r in cat1:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        cases.append({
            "category": "cat1", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": r[9].strip(), "caseNo": r[10].strip(),
            "da": r[2].strip(),
            "email": bool(r[20].strip()), "sms": bool(r[21].strip()),
            "applicationDate": "" if is_sample else r[22].strip(),
            "status7q": r[18].strip(),
            "assessed": num(r[13]), "remit14b": num(r[14]),
        })

    # Category II columns: 2 DA, 3 EO, 4 Div, 5 Estt code, 6 Estt name,
    # 18 email sent, 20 application date. No reliable 14B/7Q figures at
    # this stage (see build notes) - status7q carried through for display
    # only, never used for Vishwas-eligibility (Category I only, below).
    for r in cat2:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        cases.append({
            "category": "cat2", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": "", "caseNo": "",
            "da": r[2].strip(),
            "email": bool(r[18].strip()), "sms": False,
            "applicationDate": "" if is_sample else r[20].strip(),
            "status7q": r[17].strip(),
            "assessed": 0, "remit14b": 0,
        })

    # Category III columns: 2 DA, 3 EO, 4 Div, 5 Estt code, 6 Estt name,
    # 18 email sent, 19 sms sent, 20 application date.
    for r in cat3:
        estt = r[5].strip()
        is_sample = estt in SAMPLE_ESTT_CODES
        cases.append({
            "category": "cat3", "division": r[4].strip(), "eo": r[3].strip(),
            "estt": estt, "esttName": r[6].strip(),
            "legalForum": "", "caseNo": "",
            "da": r[2].strip(),
            "email": bool(r[18].strip()), "sms": bool(r[19].strip()),
            "applicationDate": "" if is_sample else r[20].strip(),
            "status7q": r[17].strip(),
            "assessed": 0, "remit14b": 0,
        })

    data = {
        "asOf": datetime.date.today().strftime("%d %b %Y"),
        "roster": roster,
        "cases": cases,
        "cat4Count": len(cat4),
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

    eligible = sum(1 for c in cases if c["category"] == "cat1" and c["status7q"] == "Fully Remitted")
    print(f"Built {out_path} ({len(html)} chars) — as of {data['asOf']}")
    print(f"Totals: {len(cases)} cases, "
          f"{sum(1 for c in cases if c['email'] or c['sms'])} outreach, "
          f"{sum(1 for c in cases if c['da'])} DA-assigned, "
          f"{sum(1 for c in cases if c['applicationDate'])} applications, "
          f"{eligible} Vishwas-eligible (Cat I, 7Q fully remitted)")


if __name__ == "__main__":
    main()
