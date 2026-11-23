"""Read helpers for the rest of the app (dashboard, value portal, model).

    from nfldb import db
    db.query("SELECT * FROM v_player_season WHERE season = 2024 AND position = 'QB'")
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from . import config


def connect(read_only: bool = True) -> sqlite3.Connection:
    if not config.DB_PATH.exists():
        raise FileNotFoundError(
            f"{config.DB_PATH} does not exist yet. Run: python build_database.py")
    if read_only:
        return sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(config.DB_PATH)


def query(sql: str, params: list | tuple | None = None) -> pd.DataFrame:
    con = connect()
    try:
        return pd.read_sql_query(sql, con, params=params or [])
    finally:
        con.close()


def tables() -> pd.DataFrame:
    """Every table with its row and column count."""
    con = connect()
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = []
        for n in names:
            count = con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]
            cols = len(con.execute(f'PRAGMA table_info("{n}")').fetchall())
            rows.append((n, count, cols))
        return pd.DataFrame(rows, columns=["name", "rows", "columns"])
    finally:
        con.close()


def player_seasons(name: str, season_type: str = "REG") -> pd.DataFrame:
    """Every season for players whose name contains `name` (case-insensitive)."""
    return query("""
        SELECT season, player_display_name, position, recent_team, games, age,
               passing_yards, passing_tds, rushing_yards, rushing_tds,
               receptions, receiving_yards, receiving_tds, def_sacks, def_interceptions,
               fantasy_points_ppr, cap_hit_musd, cap_pct
        FROM v_player_season
        WHERE player_display_name LIKE '%' || ? || '%' AND season_type = ?
        ORDER BY player_display_name, season
    """, [name, season_type])


def team_season(team: str, season: int) -> pd.DataFrame:
    return query("SELECT * FROM v_team_season WHERE team = ? AND season = ?", [team.upper(), season])
