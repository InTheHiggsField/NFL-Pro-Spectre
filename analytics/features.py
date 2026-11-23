"""Turn the database into one row per player per season, ready for modeling.

Each row has the season's box score, snap counts, age, experience, draft slot
and position group, plus the same numbers from the season before (``prev_*``).
Snap counts start in 2012, so modeling starts there too.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

FIRST_SEASON = 2012
FULL_SEASON_GAMES = 17

POS_GROUPS = ["QB", "RB", "WR", "TE", "T", "G", "C", "IDL", "ED", "LB", "CB", "S", "K", "P", "LS"]

# OverTheCap's contract position -> model position group.
_OTC_TO_GROUP = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
                 "LT": "T", "RT": "T", "LG": "G", "RG": "G", "C": "C",
                 "IDL": "IDL", "ED": "ED", "LB": "LB", "CB": "CB", "S": "S",
                 "K": "K", "P": "P", "LS": "LS"}
# Roster position (used when a player has no contract on file) -> group.
_ROSTER_TO_GROUP = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
                    "T": "T", "OT": "T", "G": "G", "OG": "G", "C": "C", "OL": "G",
                    "DT": "IDL", "NT": "IDL", "DL": "IDL", "DE": "ED", "OLB": "ED",
                    "LB": "LB", "ILB": "LB", "MLB": "LB",
                    "CB": "CB", "DB": "CB", "FS": "S", "SS": "S", "S": "S",
                    "K": "K", "P": "P", "LS": "LS"}

# Season stat columns used as features (all from player_stats_season).
STAT_COLS = [
    "games", "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "sacks_suffered", "passing_epa", "passing_first_downs", "carries", "rushing_yards", "rushing_tds",
    "rushing_epa", "rushing_first_downs", "receptions", "targets", "receiving_yards", "receiving_tds",
    "receiving_epa", "receiving_air_yards", "receiving_yards_after_catch", "receiving_first_downs",
    "def_tackles_solo", "def_tackle_assists", "def_tackles_for_loss", "def_sacks", "def_qb_hits",
    "def_interceptions", "def_pass_defended", "def_fumbles_forced", "def_tds",
    "fg_made", "fg_att", "fg_made_50_59", "pat_made", "pt_att", "pt_inside_20", "pt_net_yards",
    "punt_return_yards", "kickoff_return_yards", "special_teams_tds", "fantasy_points_ppr",
]
RATE_COLS = ["passing_cpoe", "target_share", "air_yards_share", "wopr", "fg_pct"]
SNAP_COLS = ["snap_games", "off_snaps", "def_snaps", "st_snaps", "off_pct", "def_pct", "st_pct"]
# Counting columns get scaled to a full season for a season still in progress.
COUNT_COLS = STAT_COLS + ["snap_games", "off_snaps", "def_snaps", "st_snaps"]

BIO_COLS = ["age", "years_exp", "draft_overall", "draft_round", "years_left", "career_progress"]
CURRENT_COLS = STAT_COLS + RATE_COLS + SNAP_COLS
LAG_COLS = ["prev_" + c for c in CURRENT_COLS]
FEATURES = CURRENT_COLS + LAG_COLS + BIO_COLS + ["pos_group"]


def _q(con: sqlite3.Connection, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def position_groups(con: sqlite3.Connection) -> pd.DataFrame:
    """One position group per player: OverTheCap's (latest contract), else roster position."""
    otc = _q(con, """
        SELECT gsis_id, position FROM contracts c
        WHERE gsis_id IS NOT NULL AND year_signed = (
            SELECT max(year_signed) FROM contracts c2 WHERE c2.gsis_id = c.gsis_id)
        GROUP BY gsis_id""")
    otc["pos_group"] = otc["position"].map(_OTC_TO_GROUP)
    ros = _q(con, """
        SELECT gsis_id, coalesce(depth_chart_position, position) AS position FROM rosters
        WHERE gsis_id IS NOT NULL AND season >= ? GROUP BY gsis_id HAVING season = max(season)""",
             (FIRST_SEASON - 1,))
    ros["pos_group"] = ros["position"].map(_ROSTER_TO_GROUP)
    out = pd.concat([otc[["gsis_id", "pos_group"]].dropna(), ros[["gsis_id", "pos_group"]].dropna()])
    return out.drop_duplicates("gsis_id", keep="first")


def career_norms(con: sqlite3.Connection) -> pd.DataFrame:
    """Typical career for each position group, from players whose careers ended 2000-2023.

    Only careers of 4+ seasons count, so the norm describes established players rather
    than the many who wash out in camp: average age in the final season and average length.
    """
    p = _q(con, """SELECT gsis_id, birth_date, rookie_season, last_season FROM players
                   WHERE last_season BETWEEN 2000 AND 2023 AND rookie_season IS NOT NULL""")
    p = p.merge(position_groups(con), on="gsis_id")
    p["final_age"] = p["last_season"] - pd.to_datetime(p["birth_date"], errors="coerce").dt.year
    p["career_len"] = p["last_season"] - p["rookie_season"] + 1
    p = p[p["career_len"] >= 4]
    return (p.groupby("pos_group")
             .agg(career_end_age=("final_age", "mean"), career_length=("career_len", "mean"))
             .round(1).reset_index())


def team_games_played(con: sqlite3.Connection) -> pd.DataFrame:
    return _q(con, "SELECT season, team, games AS team_games FROM v_team_record")


def player_seasons(con: sqlite3.Connection, first_season: int = FIRST_SEASON - 1) -> pd.DataFrame:
    """Raw per-season rows (regular season), stats outer-joined with snap counts."""
    stats = _q(con, f"""
        SELECT player_id AS gsis_id, season, recent_team AS team,
               {', '.join(STAT_COLS + RATE_COLS)}
        FROM player_stats_season WHERE season_type = 'REG' AND season >= ?""", (first_season,))
    snaps = _q(con, """
        SELECT p.gsis_id, s.season, max(s.team) AS snap_team,
               count(*) AS snap_games, sum(s.offense_snaps) AS off_snaps, sum(s.defense_snaps) AS def_snaps,
               sum(s.st_snaps) AS st_snaps, avg(s.offense_pct) AS off_pct, avg(s.defense_pct) AS def_pct,
               avg(s.st_pct) AS st_pct
        FROM snap_counts s JOIN players p ON p.pfr_id = s.pfr_player_id
        WHERE s.game_type = 'REG' AND s.season >= ?
        GROUP BY p.gsis_id, s.season""", (first_season,))
    df = stats.merge(snaps, on=["gsis_id", "season"], how="outer")
    df["team"] = df["team"].fillna(df.pop("snap_team"))
    # A player with snaps but no stats row played but did nothing countable.
    df[STAT_COLS] = df[STAT_COLS].fillna(0)
    df["games"] = np.maximum(df["games"], df["snap_games"].fillna(0))

    bio = _q(con, "SELECT gsis_id, birth_date, rookie_season, draft_pick AS draft_overall, draft_round FROM players")
    df = df.merge(bio, on="gsis_id", how="left")
    birth = pd.to_datetime(df.pop("birth_date"), errors="coerce")
    sept1 = pd.to_datetime(df["season"].astype(int).astype(str) + "-09-01")
    df["age"] = ((sept1 - birth).dt.days / 365.25).round(1)
    df["years_exp"] = df["season"] - df.pop("rookie_season")
    df = df.merge(position_groups(con), on="gsis_id", how="left")
    # Age measured against how long careers at the position usually last:
    # years_left < 0 means the player is already past the typical career end.
    df = df.merge(career_norms(con), on="pos_group", how="left")
    df["years_left"] = df["career_end_age"] - df["age"]
    df["career_progress"] = (df["years_exp"] + 1) / df["career_length"]
    return df.drop(columns=["career_end_age", "career_length"])


def scale_partial_seasons(df: pd.DataFrame, con: sqlite3.Connection) -> pd.DataFrame:
    """Put counting stats of a season still in progress on a full-season pace."""
    tg = team_games_played(con)
    df = df.merge(tg, on=["season", "team"], how="left")
    full = df.groupby("season")["team_games"].transform("max")
    df["partial_season"] = full < FULL_SEASON_GAMES - 1  # 16-game seasons count as full
    factor = np.where(df["partial_season"] & (df["team_games"] > 0),
                      FULL_SEASON_GAMES / df["team_games"], 1.0)
    df[COUNT_COLS] = df[COUNT_COLS].mul(factor, axis=0)
    df["scale_factor"] = factor
    return df.drop(columns=["team_games"])


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    prev = df[["gsis_id", "season"] + CURRENT_COLS].copy()
    prev["season"] += 1
    prev.columns = ["gsis_id", "season"] + LAG_COLS
    return df.merge(prev, on=["gsis_id", "season"], how="left")


def build(con: sqlite3.Connection) -> pd.DataFrame:
    """Model-ready table: one row per player per season from 2012 on."""
    df = player_seasons(con)
    df = scale_partial_seasons(df, con)
    df = add_lags(df)
    df = df[(df["season"] >= FIRST_SEASON) & df["pos_group"].notna()].copy()
    df["pos_group"] = pd.Categorical(df["pos_group"], categories=POS_GROUPS)
    return df.reset_index(drop=True)
