"""Paths and season settings for the NFL database."""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Everything downloaded or built lives under data/ (git-ignored).
# Set NFLDB_DATA_DIR to put it somewhere else, e.g. a bigger drive.
DATA_DIR = Path(os.environ.get("NFLDB_DATA_DIR", PROJECT_ROOT / "data")).resolve()
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "nfl.sqlite"
# Scratch DuckDB file used while building (reads parquet, flattens contracts); deleted afterwards.
STAGING_PATH = DATA_DIR / "staging.duckdb"

PACKAGE_DIR = Path(__file__).resolve().parent
SQL_DIR = PACKAGE_DIR / "sql"
SEEDS_DIR = PACKAGE_DIR / "seeds"


def current_season(today: _dt.date | None = None) -> int:
    """The NFL season in progress (or most recently finished).

    A season is named for the year it kicks off in September, so January to
    August still belongs to the previous year's season.
    """
    today = today or _dt.date.today()
    return today.year if today.month >= 9 else today.year - 1
