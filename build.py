"""
Rebuilds the Vishwas Performance Dashboard from the live Google Sheet.

Usage:  python build.py
Output: C:\\Users\\RPFC 1\\Documents\\Claude\\vishwas_dashboard\\output.html

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
    divisions = [(r[0].strip(), r[1].strip()) for r in roster_rows if len(r) >= 2 and r[0].strip()]

    cat1 = real_rows(fetch_csv(GIDS["cat1"])[2:], 4)
    cat2 = real_rows(fetch_csv(GIDS["cat2"])[2:], 4)
    cat3 = real_rows(fetch_csv(GIDS["cat3"])[2:], 4)
    cat4_raw = fetch_csv(GIDS["cat4"])[2:]
    cat4 = [r for r in cat4_raw if len(r) > 6 and r[6].strip()]

    by_div = {
        d: {
            "division": d, "name": n, "c1": 0, "c2": 0, "c3": 0, "c4": 0,
            "assessed": 0.0, "remit14b": 0.0,
            "email": 0, "sms": 0, "da": 0, "apps": 0,
        }
        for d, n in divisions
    }

    # Category I: DA=2 EO=3 Div=4 assessed=13 remit=14 email=20 sms=21 appdate=22 esttcode=5
    for r in cat1:
        d = by_div[r[4].strip()]
        d["c1"] += 1
        d["assessed"] += num(r[13])
        d["remit14b"] += num(r[14])
        if r[20].strip():
            d["email"] += 1
        if r[21].strip():
            d["sms"] += 1
        if r[2].strip():
            d["da"] += 1
        if r[22].strip() and r[5].strip() not in SAMPLE_ESTT_CODES:
            d["apps"] += 1

    # Category II: DA=2 EO=3 Div=4 esttcode=5 email=18 appdate=20
    for r in cat2:
        d = by_div[r[4].strip()]
        d["c2"] += 1
        if r[18].strip():
            d["email"] += 1
        if r[2].strip():
            d["da"] += 1
        if r[20].strip() and r[5].strip() not in SAMPLE_ESTT_CODES:
            d["apps"] += 1

    # Category III: DA=2 EO=3 Div=4 email=18 sms=19 appdate=20
    for r in cat3:
        d = by_div[r[4].strip()]
        d["c3"] += 1
        if r[18].strip():
            d["email"] += 1
        if r[19].strip():
            d["sms"] += 1
        if r[2].strip():
            d["da"] += 1
        if r[20].strip():
            d["apps"] += 1

    officers = []
    for d in by_div.values():
        officers.append({
            "division": d["division"], "name": d["name"],
            "c1": d["c1"], "c2": d["c2"], "c3": d["c3"],
            "assessed": d["assessed"], "remit14b": d["remit14b"],
            "email": d["email"], "sms": d["sms"], "da": d["da"], "apps": d["apps"],
        })
    officers.sort(key=lambda x: int(x["division"]))

    def cat_totals(rows, email_idx, sms_idx, appdate_idx, esttcode_idx, da_idx=2):
        count = len(rows)
        email = sum(1 for r in rows if r[email_idx].strip())
        sms = sum(1 for r in rows if sms_idx is not None and r[sms_idx].strip())
        da = sum(1 for r in rows if r[da_idx].strip())
        apps = sum(
            1 for r in rows
            if r[appdate_idx].strip() and r[esttcode_idx].strip() not in SAMPLE_ESTT_CODES
        )
        return count, email, sms, da, apps

    c1_count, c1_email, c1_sms, c1_da, c1_apps = cat_totals(cat1, 20, 21, 22, 5)
    c2_count, c2_email, c2_sms, c2_da, c2_apps = cat_totals(cat2, 18, None, 20, 5)
    c3_count, c3_email, c3_sms, c3_da, c3_apps = cat_totals(cat3, 18, 19, 20, 5)

    categories = [
        {"id": "cat1", "tag": "CATEGORY I", "title": "Ongoing litigation",
         "desc": "CGIT · High Court · Supreme Court", "color": "var(--cat1)",
         "count": c1_count, "email": c1_email, "sms": c1_sms, "da": c1_da, "apps": c1_apps},
        {"id": "cat2", "tag": "CATEGORY II", "title": "Finalised 14B / RRC",
         "desc": "Unpaid or partially paid orders", "color": "var(--cat2)",
         "count": c2_count, "email": c2_email, "sms": c2_sms, "da": c2_da, "apps": c2_apps},
        {"id": "cat3", "tag": "CATEGORY III", "title": "Pre-adjudication",
         "desc": "Notice issued, order awaited", "color": "var(--cat3)",
         "count": c3_count, "email": c3_email, "sms": c3_sms, "da": c3_da, "apps": c3_apps},
        {"id": "cat4", "tag": "CATEGORY IV", "title": "Pre-adjudication",
         "desc": "Notice not yet issued", "color": "var(--cat4)",
         "count": len(cat4), "email": 0, "sms": 0, "da": 0, "apps": 0, "notStarted": True},
    ]

    # DA rollup (DA name populated so far only in Category II)
    da_map = {}
    for r in cat2:
        da = r[2].strip()
        if da:
            entry = da_map.setdefault(da, {"cases": 0, "divisions": set()})
            entry["cases"] += 1
            entry["divisions"].add(r[4].strip())
    das = [
        {"name": name, "cases": v["cases"], "divisions": sorted(v["divisions"], key=int)}
        for name, v in sorted(da_map.items())
    ]
    da_unassigned_cat2 = sum(1 for r in cat2 if not r[2].strip())

    data = {
        "asOf": datetime.date.today().strftime("%d %b %Y"),
        "officers": officers,
        "categories": categories,
        "das": das,
        "daUnassignedCat2": da_unassigned_cat2,
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

    print(f"Built {out_path} ({len(html)} chars) — as of {data['asOf']}")
    print(f"Totals: {sum(c['count'] for c in categories)} cases, "
          f"{sum(c['email'] for c in categories)} outreach, "
          f"{sum(c['da'] for c in categories)} DA-assigned, "
          f"{sum(c['apps'] for c in categories)} applications")


if __name__ == "__main__":
    main()
