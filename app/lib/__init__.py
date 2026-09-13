"""Shared helpers for the app screens.

db.py   talks to the DuckDB file
ui.py   the small amount of shared interface

The business logic is not here. It is in src/tracker, which the app, the scripts and the
tests all import, so all three run exactly the same code. Nothing is ever copied between
folders, so there is no second version to drift.

Importing this package puts the repo's src/ and conf/ folders on the import path. Every
screen imports `lib` before it imports `tracker` or `config`, so this is the one place
that has to know the folder layout.
"""

import os
import sys

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(_APP)

for _p in (os.path.join(PROJECT, "src"), os.path.join(PROJECT, "conf")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
