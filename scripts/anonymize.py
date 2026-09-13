"""Replace real staff names with invented ones, everywhere they appear.

    python scripts/anonymize.py --check      # say what would change, change nothing
    python scripts/anonymize.py --apply      # do it

WHY THIS EXISTS

The project charter is explicit: the portfolio version carries synthetic or anonymized
data only, and no real ECCC staff names. That rule is easy to keep for the timeline export,
which simply never leaves the working folder. It is easy to miss everywhere else.

The names are not in the data. They are in the CONTRACT. conf/data_dictionary.xlsx lists
the real concierges, business analysts and product owners on sheet 09 as controlled values,
and quotes two of them in the notes. conf/name_lookup.csv records a real spelling
disagreement between two real sheets. tests/conftest.py builds its synthetic rows out of
real names, because they had to match the controlled values to be realistic.

So a repo with no real export in it can still publish sixteen people's names. This script
is what stops that, and it is the last thing to run before the first push.

WHAT IT CHANGES

  conf/data_dictionary.xlsx   sheet 09 allowed values, and any cell quoting a name
  conf/name_lookup.csv        the variant, the canonical spelling, and the note
  tests/conftest.py           the names in the synthetic fixtures
  tests/test_clean.py         the same names, asserted on
  src/tracker/clean.py        rule C07's docstring, which names both spellings

WHAT IT DOES NOT CHANGE

The structure. Same number of allowed values per list, same spelling-variant pair, same
tests. The cleaning rule that fixes a misspelled name still has a misspelled name to fix,
because the invented pair is built the same way the real one was.

WHERE THE MAP LIVES

conf/name_map.local.json, which is gitignored. Not in this file: see the note above the
loader. Copy conf/name_map.example.json to make your own.

AFTERWARDS

Regenerate the sample workbook and re-import, because the old one was built from the old
controlled values:

    python scripts/make_sample_data.py
    python scripts/create_tables.py --drop
    python scripts/import_timeline.py --approve --by demo
    python scripts/seed_demo.py
"""

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
for p in (os.path.join(PROJECT, "conf"),):
    if p not in sys.path:
        sys.path.insert(0, p)

import openpyxl  # noqa: E402

import config  # noqa: E402


# THE MAP LIVES OUTSIDE THE REPO, AND THAT IS THE WHOLE POINT.
#
# The first version of this script held the real names in a dictionary right here. So the
# one file whose job was to remove sixteen people's names from the repo was the file that
# published them. It passed its own check, because it only looked at the files it edits.
#
# The map is now read from conf/name_map.local.json, which .gitignore blocks. Commit
# conf/name_map.example.json instead: same shape, invented on both sides.
#
# Keys are real names, values are invented ones. Fixed rather than random, so running this
# twice is safe and two people running it get the same result.

MAP_PATH = os.path.join(PROJECT, "conf", "name_map.local.json")

if not os.path.exists(MAP_PATH):
    raise SystemExit(
        f"No name map at {MAP_PATH}.\n\n"
        "Copy conf/name_map.example.json to conf/name_map.local.json and fill in the real "
        "names as keys. That file is gitignored, so it never reaches GitHub.")

with open(MAP_PATH, encoding="utf-8") as fh:
    NAMES = json.load(fh)

# The name_lookup table is keyed on the lower case spelling, because matching a person's
# name has to survive someone typing it in a different case. So the lower case form of
# every name has to be swapped too, or the lookup keeps pointing at a real name while the
# value beside it points at an invented one, and cleaning rule C07 silently stops working.
# That is exactly what happened the first time this script was run.
NAMES.update({real.lower(): invented.lower()
              for real, invented in list(NAMES.items())
              if real.lower() != real})

# Longest first, so a full name is replaced before any shorter name inside it.
ORDER = sorted(NAMES, key=len, reverse=True)

TEXT_FILES = [
    os.path.join(PROJECT, "conf", "name_lookup.csv"),
    os.path.join(PROJECT, "tests", "conftest.py"),
    os.path.join(PROJECT, "tests", "test_clean.py"),
    # Cleaning rule C07's docstring names the two spellings it reconciles.
    os.path.join(PROJECT, "src", "tracker", "clean.py"),
]


def swap(text):
    """Replace every known name. Returns (new_text, how_many)."""
    if not isinstance(text, str):
        return text, 0
    n = 0
    for real in ORDER:
        if real in text:
            n += text.count(real)
            text = text.replace(real, NAMES[real])
    return text, n


def do_workbook(path, apply_changes):
    """Sheet 09 holds the allowed values. Names also appear inside notes cells, which is
    why every cell of every sheet is checked rather than one column of one sheet."""
    wb = openpyxl.load_workbook(path)
    total, touched = 0, []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        for row in ws.iter_rows():
            for cell in row:
                new, n = swap(cell.value)
                if n:
                    total += n
                    touched.append(f"{sheet}!{cell.coordinate}")
                    if apply_changes:
                        cell.value = new
    if apply_changes and total:
        shutil.copy(path, path + ".real.bak")
        wb.save(path)
    return total, touched


def do_text(path, apply_changes):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        old = fh.read()
    new, n = swap(old)
    if n and apply_changes:
        shutil.copy(path, path + ".real.bak")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
    return n


def remaining(path):
    """Anything that still looks like a Firstname Lastname and is not in the map."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    invented = set(NAMES.values())
    found = set(re.findall(r"\b[A-Z][a-z]{2,}(?:[- ][A-Z][a-z]+)+\b", text))
    return sorted(f for f in found if f not in invented and f in NAMES)


def main():
    ap = argparse.ArgumentParser(description="Swap real staff names for invented ones.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report only, change nothing")
    g.add_argument("--apply", action="store_true", help="rewrite the files")
    args = ap.parse_args()
    apply_changes = args.apply

    print(f"{len(NAMES)} names in the map.\n")

    total, touched = do_workbook(config.DICTIONARY, apply_changes)
    print(f"  {'rewrote' if apply_changes else 'would rewrite'} "
          f"{total:>3} cells in {os.path.basename(config.DICTIONARY)}")
    if touched and not apply_changes:
        print(f"        {', '.join(touched[:12])}"
              f"{' ...' if len(touched) > 12 else ''}")

    for path in TEXT_FILES:
        n = do_text(path, apply_changes)
        rel = os.path.relpath(path, PROJECT)
        print(f"  {'rewrote' if apply_changes else 'would rewrite'} {n:>3} mentions in {rel}")

    if apply_changes:
        print("\nOriginals kept beside each file as *.real.bak. Those are gitignored, and "
              "they are the only copy of the mapping in reverse, so do not commit them.")
        print("\nNow rebuild the demo data, which was generated from the old names:")
        print("  python scripts/make_sample_data.py")
        print("  python scripts/create_tables.py --drop")
        print("  python scripts/import_timeline.py --approve --by demo")
        print("  python scripts/seed_demo.py")
    else:
        print("\nNothing was changed. Run again with --apply.")


if __name__ == "__main__":
    main()
