"""Build an invented workbook shaped exactly like the official RSM timeline export.

    python scripts/make_sample_data.py [--rows 82] [--seed 7] [--out sample_data/...]

WHY THIS EXISTS

The project charter rules that the portfolio version of this tracker runs on synthetic or
anonymized data only, and that the real export never leaves the working folder. So the
repo needs a workbook that is the same shape as the real one and shares none of its
content. This script writes it.

WHAT "SAME SHAPE" MEANS

  - the sheet is called "RSP Dates", because the gate checks for it by name
  - the 24 column headers are read out of sheet 12 of the data dictionary, exactly, so
    the headers cannot drift away from the contract
  - there is an "InstrumentID" sheet, because the import reads the known ids from it
  - every controlled value comes from sheet 09 of the dictionary, so the cleaning rules
    recognise it

THE MESS IS DELIBERATE

Clean input would hide the thing this project is actually about. The real export was
profiled in full and the dirt below is the dirt it was found to contain:

  trailing space on a status         11 real rows
  lower case "on hold"                1 real row
  "N/A" in the Priority column       32 real rows
  a full English date as text        26 real values, e.g. "Monday, April 5, 2027"
  "TBD" in a key field                1 real row, which is why provisional keys exist
  a leading newline in a name         seen in the full name column
  a person's name spelled two ways    the Picklists sheet and RSP Dates disagree

A sample file with none of that would make the import look trivial and would leave the
cleaning log empty on the demo, which is the opposite of the point.
"""

import argparse
import csv
import datetime
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
for p in (os.path.join(PROJECT, "src"), os.path.join(PROJECT, "conf")):
    if p not in sys.path:
        sys.path.insert(0, p)

import openpyxl  # noqa: E402

import config  # noqa: E402


# ---------------------------------------------------------------- invented instruments
#
# Plausible-sounding but fictional. No ECCC instrument, real or planned, is named here.

INSTRUMENTS = [
    ("SWR",  "Solvent Waste Recovery Regulations"),
    ("ABQ",  "Ambient Air Quality Reporting Standard"),
    ("MTD",  "Marine Terminal Discharge Regulations"),
    ("CFR",  "Coastal Fisheries Registry"),
    ("GHR",  "Greenhouse Reporting Requirements"),
    ("PLN",  "Pipeline Leak Notification Regulations"),
    ("BRX",  "Biosolids Reuse Exemption Order"),
    ("QTL",  "Quarry Tailings Licence"),
    ("NFP",  "Non-Ferrous Processing Regulations"),
    ("WSD",  "Wastewater Systems Declaration"),
    ("HAL",  "Halocarbon Inventory Return"),
    ("TSR",  "Transport of Specified Residues Regulations"),
    ("ELR",  "Electronics Lifecycle Registry"),
    ("AGP",  "Agricultural Runoff Permit"),
    ("FPS",  "Fuel Purity Standard"),
    ("MCR",  "Mine Closure Reporting Regulations"),
    ("SNG",  "Synthetic Gas Blending Notice"),
    ("WLH",  "Wildlife Habitat Offset Permit"),
    ("CTX",  "Chemical Toxicity Exemption Order"),
    ("RBR",  "Rail Ballast Runoff Regulations"),
    ("OSW",  "Offshore Structures Waste Return"),
    ("PKG",  "Packaging Recovery Regulations"),
    ("SLD",  "Sludge Land Application Licence"),
    ("VNT",  "Venting and Flaring Declaration"),
]

NEEDS = [
    "Registration Submission",
    "Report Submission",
    "Notification, Ownership Change",
    "Annual voluntary submission",
    "Permit Application",
    "Annual update, permitting",
    "Submissions, Initial Report",
    "Renewal Submission",
]

SCOPES = [
    "Regulatees submit an annual return through the platform.",
    "Initial registration plus an annual confirmation.",
    "One-time notification with supporting documents.",
    "Quarterly reporting with an attached data file.",
    "Permit application, review, and issuance.",
]

NOTES = [
    "Scope confirmed with the program.",
    "Waiting on the final data dictionary.",
    "Dependent on the shared authentication component.",
    "Phase 1 only. Phase 2 is not scheduled.",
    "Bilingual forms still in translation.",
    None,
]


# ---------------------------------------------------------------- reading the contract


def controlled(wb, list_name):
    """The allowed values for one list, from sheet 09 of the dictionary.

    Read rather than typed, so if the dictionary is anonymized the sample data follows
    automatically and the two can never disagree.
    """
    ws = wb["09_Controlled_Values"]
    return [r[1] for r in ws.iter_rows(min_row=2, values_only=True)
            if r and r[0] == list_name and r[1]]


def source_headers(wb):
    ws = wb["12_Source_Profile"]
    return [r[1] for r in ws.iter_rows(min_row=2, values_only=True) if r and r[1]]


def name_variants():
    """variant -> canonical, from conf/name_lookup.csv.

    The sample deliberately writes a variant spelling so cleaning rule C07 has something
    to correct and the cleaning log has an entry a reader can go and look at.
    """
    if not os.path.exists(config.NAME_LOOKUP):
        return {}
    with open(config.NAME_LOOKUP, newline="", encoding="utf-8") as fh:
        return {r["variant"]: r["canonical"] for r in csv.DictReader(fh) if r.get("variant")}


# ---------------------------------------------------------------- building the rows


def long_date(d):
    """'Monday, April 5, 2027'. 26 values in the real export arrived like this."""
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def build_rows(wb, n_rows, rng):
    statuses = controlled(wb, "official_status")
    complexities = controlled(wb, "complexity")
    years = [y for y in controlled(wb, "fiscal_year") if y != "TBD"]
    concierges = controlled(wb, "rsm_concierge")
    analysts = controlled(wb, "business_analyst")
    owners = controlled(wb, "product_owner")
    variants = name_variants()

    rows = []
    # One instrument holds several rows, one per implementation need per fiscal year.
    # That is the whole reason the key is composite: 82 real rows were only 24 instruments.
    i = 0
    while len(rows) < n_rows:
        abbr, full = INSTRUMENTS[i % len(INSTRUMENTS)]
        rsp_id = f"RSP-{(i % len(INSTRUMENTS)) + 1}"
        for need in rng.sample(NEEDS, rng.choice([1, 2, 2, 3, 4])):
            if len(rows) >= n_rows:
                break
            year = rng.choice(years)
            base = datetime.date(2026 + (year == "27/28"), rng.randint(1, 11), rng.randint(1, 28))

            rows.append({
                "Instrument ID": rsp_id,
                "Instrument Short Name": f"{abbr} - {abbr[::-1]}",
                "RDC Implementation Need": need,
                "Program Scope": rng.choice(SCOPES),
                "Fiscal Year": year,
                "Instrument Full Name": full,
                "Complexity": rng.choice(complexities),
                "User Stories Deadline": datetime.datetime.combine(base, datetime.time()),
                "Code Freeze": datetime.datetime.combine(
                    base + datetime.timedelta(days=60), datetime.time()),
                "Client UAT Start (EcRc)": datetime.datetime.combine(
                    base + datetime.timedelta(days=90), datetime.time()),
                "Client UAT End (EcRc)": datetime.datetime.combine(
                    base + datetime.timedelta(days=104), datetime.time()),
                "External Testing": rng.choice(["N/A", "N/A", "Planned"]),
                "Target Blue Stamp Date (Non-IT)": "N/A",
                "Coming Into Force Regs (Non-IT)": datetime.datetime.combine(
                    base - datetime.timedelta(days=365), datetime.time()),
                "Full Production Launch (External users in the system)":
                    datetime.datetime.combine(
                        base + datetime.timedelta(days=rng.randint(110, 240)), datetime.time()),
                "Release Number": f"RSP-{base:%y.%m}-{rng.randint(1, 3)}",
                "Status": rng.choice(statuses),
                "Priority": len(rows) + 1,
                "Lead RSM Concierge": rng.choice(concierges),
                "Assigned Lead Business Analyst": rng.choice(analysts + [None]),
                "IT Delivery Manager": rng.choice(analysts),
                "IT Delivery TL": rng.choice(analysts + [None, None]),
                "Notes": rng.choice(NOTES),
                "Product Owner": rng.choice(owners + [None, None]),
            })
        i += 1

    # ---------------------------------------------------------- now make it dirty

    def some(k):
        return rng.sample(range(len(rows)), min(k, len(rows)))

    for j in some(11):                                    # 11 real rows
        rows[j]["Status"] = str(rows[j]["Status"]) + " "
    for j in some(1):                                     # 1 real row
        rows[j]["Status"] = "On hold"
    for j in some(32):                                    # 32 real rows
        rows[j]["Priority"] = "N/A"
    for j in some(26):                                    # 26 real values
        for col in ("Client UAT Start (EcRc)", "Client UAT End (EcRc)"):
            value = rows[j][col]
            if isinstance(value, datetime.datetime):
                rows[j][col] = long_date(value.date())
    for j in some(4):
        rows[j]["Complexity"] = str(rows[j]["Complexity"]).lower()
    for j in some(3):
        rows[j]["Notes"] = f"  {rows[j]['Notes'] or 'padded note'}  "
    for j in some(2):
        rows[j]["Instrument Full Name"] = "\n" + str(rows[j]["Instrument Full Name"])
    for j in some(5):
        rows[j]["Target Blue Stamp Date (Non-IT)"] = "TBD"

    # One row with TBD in a key field, which produces a provisional key. Exactly one, as
    # in the real export, because that is what makes the provisional path testable.
    rows[some(1)[0]]["Fiscal Year"] = "TBD"

    # One name spelled the way the other sheet spells it, so rule C07 has work to do.
    if variants:
        variant, canonical = next(iter(variants.items()))
        for j in some(2):
            if rows[j]["Lead RSM Concierge"] == canonical:
                rows[j]["Lead RSM Concierge"] = variant
                break
        else:
            rows[some(1)[0]]["Lead RSM Concierge"] = variant

    return rows


# ---------------------------------------------------------------- writing the workbook


def write_workbook(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = config.SOURCE_SHEET
    ws.append(list(headers))
    for r in rows:
        ws.append([r.get(h) for h in headers])

    ws2 = wb.create_sheet(config.INSTRUMENT_SHEET)
    ws2.append(["ID", "Instrument"])
    for n, (abbr, full) in enumerate(INSTRUMENTS, start=1):
        ws2.append([f"RSP-{n}", full])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description="Build an invented timeline workbook.")
    ap.add_argument("--rows", type=int, default=82,
                    help="how many data rows. The real export had 82.")
    ap.add_argument("--seed", type=int, default=7,
                    help="same seed, same workbook. Change it to simulate a later week.")
    ap.add_argument("--out", help="where to write. Default is sample_data/<date>_sample.xlsx")
    args = ap.parse_args()

    if not os.path.exists(config.DICTIONARY):
        raise SystemExit(f"Data dictionary not found at {config.DICTIONARY}")

    rng = random.Random(args.seed)
    wb = openpyxl.load_workbook(config.DICTIONARY, data_only=True)

    headers = source_headers(wb)
    if len(headers) != 24:
        raise SystemExit(f"expected 24 source columns in sheet 12, found {len(headers)}")

    rows = build_rows(wb, args.rows, rng)

    out = args.out or os.path.join(
        config.SAMPLE_DATA, f"{datetime.date.today():%Y-%m-%d}_sample_timeline.xlsx")
    write_workbook(out, headers, rows)

    instruments = len({r["Instrument ID"] for r in rows})
    print(f"Wrote {out}")
    print(f"  {len(rows)} rows across {instruments} instruments, "
          f"{len(headers)} columns, sheet {config.SOURCE_SHEET!r}")
    print(f"  seed {args.seed}. Re-running with the same seed rebuilds the same file.")
    print("\nNext:  python scripts/import_timeline.py")


if __name__ == "__main__":
    main()
