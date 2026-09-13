"""Shared fixtures.

Two principles.

**No test touches the real export.** The synthetic rows below carry every kind of dirt the
real file was found to contain, deliberately, so a test failing here means a real import
would have failed too. Real ECCC data never becomes a test dependency.

**The dictionary is the real one.** It is the contract, so testing against a fake version
of it would prove nothing. If a rule changes in the dictionary and breaks a test, that is
the test doing its job.
"""

import os
import sys
import datetime

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

for p in (PROJECT, os.path.join(PROJECT, "conf"), os.path.join(PROJECT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _dictionary_path():
    try:
        import config
        if os.path.exists(config.DICTIONARY):
            return config.DICTIONARY
    except Exception:
        pass
    fallback = os.path.join(PROJECT, "conf", "data_dictionary.xlsx")
    if os.path.exists(fallback):
        return fallback
    pytest.skip("data dictionary not found, cannot run contract tests")


# ---------------------------------------------------------------- the contract


@pytest.fixture(scope="session")
def contract():
    from tracker.contract import load
    return load(_dictionary_path())


@pytest.fixture(scope="session")
def headers(contract):
    """The 24 exact source column names, in sheet order."""
    return contract.source_columns()


# ---------------------------------------------------------------- synthetic rows

# Every entry below exists because the real export contains something like it.
#   row 1  clean baseline
#   row 2  trailing space on status, lower case on hold, N/A priority
#   row 3  long form English date, TBD fiscal year, no separator in short name
#   row 4  leading newline in full name, sentinels in three date columns
#   row 5  a name spelled the way the Picklists sheet spells it
#   row 6  MGBH, whose French abbreviation the export and the SRS disagree about

def _row(**over):
    base = {
        "Instrument ID": "RSP-1",
        "Instrument Short Name": "CER - REP",
        "RDC Implementation Need": "Registration Submission",
        "Program Scope": "A scope sentence.",
        "Fiscal Year": "26/27",
        "Instrument Full Name": "Clean Electricity Regulations",
        "Complexity": "Low",
        "User Stories Deadline": datetime.datetime(2026, 5, 6),
        "Code Freeze": datetime.datetime(2026, 7, 15),
        "Client UAT Start (EcRc)": datetime.datetime(2026, 8, 24),
        "Client UAT End (EcRc)": datetime.datetime(2026, 9, 3),
        "External Testing": "N/A",
        "Target Blue Stamp Date (Non-IT)": "N/A",
        "Coming Into Force Regs (Non-IT)": datetime.datetime(2025, 1, 1),
        "Full Production Launch (External users in the system)": datetime.datetime(2026, 9, 3),
        "Release Number": "RSP-26.09-1",
        "Status": "In Progress",
        "Priority": 1,
        "Lead RSM Concierge": "Peter Quinlan",
        "Assigned Lead Business Analyst": "Nadia Sorensen",
        "IT Delivery Manager": "Victor Lindqvist",
        "IT Delivery TL": "Oscar Delgado",
        "Notes": "A note.",
        "Product Owner": "Owen Radcliffe",
    }
    base.update(over)
    return base


@pytest.fixture
def dirty_rows():
    """Six rows, each carrying dirt observed in the real export."""
    return [
        _row(),
        _row(**{
            "Instrument ID": "RSP-2",
            "Instrument Short Name": "VOC2 - COV2",
            "RDC Implementation Need": "Notification, Ownership Change",
            "Status": "Not Initiated ",          # trailing space, 11 real rows
            "Priority": "N/A",                   # 32 real rows
            "Complexity": "very high",           # case only
        }),
        _row(**{
            "Instrument ID": "RSP-17",
            "Instrument Short Name": "EPOD IDEA",       # no separator, 1 real row
            "RDC Implementation Need": "Report Submission",
            "Fiscal Year": "TBD",                        # key field placeholder, 1 real row
            "Client UAT Start (EcRc)": "Monday, April 5, 2027",   # 11 real rows
            "Client UAT End (EcRc)": "Friday, April 9, 2027",
            "Status": "On hold",                         # lower case, 1 real row
        }),
        _row(**{
            "Instrument ID": "RSP-26",
            "Instrument Short Name": "MSAPR - RMPA",
            "RDC Implementation Need": "Submissions, Initial Report",
            "Instrument Full Name": "\nMulti-Sector Air Pollutants Regulations (Part 1)",
            "Target Blue Stamp Date (Non-IT)": "TBD",    # third sentinel
            "Coming Into Force Regs (Non-IT)": "N/A",
            "Release Number": None,
            "Notes": "  padded note  ",
        }),
        _row(**{
            "Instrument ID": "RSP-11",
            "Instrument Short Name": "HQS - QSP",
            "RDC Implementation Need": "Annual voluntary submission",
            "Lead RSM Concierge": "Morgan Ashbey",        # the Picklists spelling
        }),
        _row(**{
            "Instrument ID": "RSP-7",
            "Instrument Short Name": "MGBH - COM",       # SRS says OMCG
            "RDC Implementation Need": "Annual update, permitting",
            "Fiscal Year": "27/28",
        }),
    ]


@pytest.fixture
def name_lookup():
    return {"morgan ashbey": "Morgan Ashby"}


@pytest.fixture
def srs():
    return {"CER": "REP", "VOC": "COV", "MGBH": "OMCG", "PCTSR": "RSSTI", "LFG": "SGE"}


@pytest.fixture
def known_ids():
    return {f"RSP-{i}" for i in range(1, 27)}


@pytest.fixture
def clean_rows(dirty_rows, contract, name_lookup, srs, known_ids):
    """Cleaned and keyed, which is what everything downstream consumes."""
    from tracker import clean as C
    from tracker import keys as K
    rows, log, divs, errs = C.clean_rows(
        dirty_rows, contract, name_lookup=name_lookup, srs=srs, known_ids=known_ids)
    K.add_keys(rows)
    return rows, log, divs, errs


@pytest.fixture
def now():
    return datetime.datetime(2026, 8, 27, 9, 0, 0)


# ---------------------------------------------------------------- workbooks


def write_workbook(path, headers_, rows_, sheet="RSP Dates", instruments=True):
    """Build a workbook on disk, so the gate can be tested without a real export."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(list(headers_))
    for r in rows_:
        ws.append([r.get(h) for h in headers_])
    if instruments:
        ws2 = wb.create_sheet("InstrumentID")
        ws2.append(["ID", "Instrument"])
        for i in range(1, 27):
            ws2.append([f"RSP-{i}", f"Instrument {i}"])
    wb.save(path)
    return path


@pytest.fixture
def good_workbook(tmp_path, headers, dirty_rows):
    return write_workbook(tmp_path / "good.xlsx", headers, dirty_rows)


@pytest.fixture
def workbook_missing_column(tmp_path, headers, dirty_rows):
    reduced = [h for h in headers if h != "Fiscal Year"]
    return write_workbook(tmp_path / "missing_col.xlsx", reduced, dirty_rows)


@pytest.fixture
def workbook_wrong_sheet(tmp_path, headers, dirty_rows):
    return write_workbook(tmp_path / "wrong_sheet.xlsx", headers, dirty_rows,
                          sheet="Something Else")
