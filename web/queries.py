"""SQL used by the website. Every function takes an open sqlite3 connection."""
from __future__ import annotations

import sqlite3

# Stat columns shown for each kind of player: (column, label, decimals)
STAT_SETS: dict[str, list[tuple[str, str, int]]] = {
    "QB": [("completions", "Cmp", 0), ("attempts", "Att", 0), ("passing_yards", "Pass Yds", 0),
           ("passing_tds", "Pass TD", 0), ("passing_interceptions", "Int", 0), ("sacks_suffered", "Sk", 0),
           ("passing_epa", "Pass EPA", 1), ("passing_cpoe", "CPOE", 1), ("carries", "Rush", 0),
           ("rushing_yards", "Rush Yds", 0), ("rushing_tds", "Rush TD", 0), ("fantasy_points_ppr", "PPR", 1)],
    "RB": [("carries", "Rush", 0), ("rushing_yards", "Rush Yds", 0), ("rushing_tds", "Rush TD", 0),
           ("rushing_epa", "Rush EPA", 1), ("targets", "Tgt", 0), ("receptions", "Rec", 0),
           ("receiving_yards", "Rec Yds", 0), ("receiving_tds", "Rec TD", 0),
           ("rushing_fumbles_lost", "Fum Lost", 0), ("fantasy_points_ppr", "PPR", 1)],
    "REC": [("targets", "Tgt", 0), ("receptions", "Rec", 0), ("receiving_yards", "Rec Yds", 0),
            ("receiving_tds", "Rec TD", 0), ("receiving_air_yards", "Air Yds", 0),
            ("receiving_yards_after_catch", "YAC", 0), ("target_share", "Tgt Share", 3),
            ("receiving_epa", "Rec EPA", 1), ("rushing_yards", "Rush Yds", 0), ("fantasy_points_ppr", "PPR", 1)],
    "DEF": [("def_tackles_solo", "Solo", 0), ("def_tackle_assists", "Ast", 0), ("def_tackles_for_loss", "TFL", 0),
            ("def_sacks", "Sacks", 1), ("def_qb_hits", "QB Hits", 0), ("def_interceptions", "Int", 0),
            ("def_pass_defended", "PD", 0), ("def_fumbles_forced", "FF", 0), ("def_tds", "TD", 0)],
    "K": [("fg_made", "FGM", 0), ("fg_att", "FGA", 0), ("fg_pct", "FG%", 3), ("fg_long", "Long", 0),
          ("fg_made_50_59", "50-59", 0), ("pat_made", "XPM", 0), ("pat_att", "XPA", 0)],
    "P": [("pt_att", "Punts", 0), ("pt_yards", "Yds", 0), ("pt_long", "Long", 0),
          ("pt_inside_20", "In 20", 0), ("pt_touchback", "TB", 0), ("pt_net_yards", "Net Yds", 0)],
    "OL": [],
}

# Columns the player finder may sort by (whitelist: these go into SQL).
SORTABLE = {c: label for cols in STAT_SETS.values() for c, label, _ in cols}
SORTABLE["games"] = "Games"

LEADER_BOARDS = [
    ("passing_yards", "Passing yards"), ("passing_tds", "Passing TDs"),
    ("rushing_yards", "Rushing yards"), ("rushing_tds", "Rushing TDs"),
    ("receiving_yards", "Receiving yards"), ("receptions", "Receptions"),
    ("def_sacks", "Sacks"), ("def_interceptions", "Interceptions"),
    ("fantasy_points_ppr", "Fantasy points (PPR)"),
]

POSITION_FILTERS = ["QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "K", "P"]


def stat_set_for(position: str | None, position_group: str | None) -> str:
    pos, grp = (position or "").upper(), (position_group or "").upper()
    if pos == "QB":
        return "QB"
    if pos in ("RB", "FB", "HB"):
        return "RB"
    if pos in ("WR", "TE") or grp in ("WR", "TE"):
        return "REC"
    if pos == "K":
        return "K"
    if pos == "P":
        return "P"
    if grp in ("DL", "LB", "DB"):
        return "DEF"
    if grp == "OL":
        return "OL"
    return "REC"


def rows(con: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[dict]:
    cur = con.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def one(con: sqlite3.Connection, sql: str, params: tuple | list = ()) -> dict | None:
    r = rows(con, sql, params)
    return r[0] if r else None


# ---------------------------------------------------------------- general
def seasons_with_stats(con) -> list[int]:
    return [r["season"] for r in rows(con, "SELECT DISTINCT season FROM v_team_record ORDER BY season DESC")]


def latest_season(con) -> int:
    return one(con, "SELECT max(season) AS s FROM player_stats_season")["s"]


def franchises(con) -> list[dict]:
    return rows(con, """SELECT team_abbr, team_name, team_nick, team_conf, team_division, team_color,
                               team_logo_espn FROM franchises ORDER BY team_conf, team_division, team_name""")


def build_info(con) -> dict:
    return {r["key"]: r["value"] for r in rows(con, "SELECT key, value FROM _build_info")}


# ---------------------------------------------------------------- search
def search_players(con, q: str, limit: int = 25) -> list[dict]:
    like = f"%{q.strip()}%"
    return rows(con, """
        SELECT gsis_id, display_name, position, latest_team, rookie_season, last_season, headshot, status
        FROM players
        WHERE display_name LIKE ? OR (first_name || ' ' || last_name) LIKE ?
        ORDER BY (display_name LIKE ?) DESC, last_season DESC NULLS LAST, display_name
        LIMIT ?
    """, (like, like, f"{q.strip()}%", limit))


def search_teams(con, q: str) -> list[dict]:
    like = f"%{q.strip()}%"
    return rows(con, """SELECT team_abbr, team_name, team_logo_espn FROM franchises
                        WHERE team_name LIKE ? OR team_nick LIKE ? OR team_abbr = upper(?)""",
                (like, like, q.strip()))


# ---------------------------------------------------------------- players
def player(con, gsis_id: str) -> dict | None:
    return one(con, "SELECT * FROM players WHERE gsis_id = ?", (gsis_id,))


def player_career(con, gsis_id: str, season_type: str = "REG") -> list[dict]:
    return rows(con, """SELECT * FROM v_player_season WHERE player_id = ? AND season_type = ?
                        ORDER BY season""", (gsis_id, season_type))


def player_roster_history(con, p: dict) -> list[dict]:
    # Rosters before 1999 rarely carry IDs, so also match on PFR ID or name + birth date.
    return rows(con, """
        SELECT r.season, coalesce(a.franchise, r.team) AS team, r.team AS team_code, r.position,
               r.jersey_number, r.status
        FROM rosters r LEFT JOIN team_aliases a ON a.alias = r.team
        WHERE r.gsis_id = ? OR r.pfr_id = ? OR (r.full_name = ? AND r.birth_date = ?)
        GROUP BY r.season, r.team ORDER BY r.season""",
                (p["gsis_id"], p.get("pfr_id"), p.get("display_name"), p.get("birth_date")))


def player_contracts(con, gsis_id: str) -> list[dict]:
    return rows(con, """SELECT team, year_signed, years, value_musd, apy_musd, guaranteed_musd, apy_cap_pct,
                               apy_cap_adj_musd, is_active
                        FROM v_contracts WHERE gsis_id = ? ORDER BY year_signed""", (gsis_id,))


def player_cap(con, gsis_id: str) -> list[dict]:
    return rows(con, """SELECT season, team, cap_hit_musd, cap_pct, cash_paid_musd, base_salary_musd
                        FROM player_cap_seasons WHERE gsis_id = ? ORDER BY season""", (gsis_id,))


def player_finder(con, season: int, position: str | None, team: str | None, sort: str,
                  limit: int = 100) -> list[dict]:
    sort_col = sort if sort in SORTABLE else "fantasy_points_ppr"
    where, params = ["season = ?", "season_type = 'REG'"], [season]
    if position:
        if position in ("OL", "DL", "LB", "DB"):
            where.append("position_group = ?")
        else:
            where.append("position = ?")
        params.append(position)
    if team:
        where.append("recent_team = ?")
        params.append(team)
    params.append(limit)
    return rows(con, f"""
        SELECT * FROM v_player_season WHERE {' AND '.join(where)}
        ORDER BY "{sort_col}" DESC NULLS LAST LIMIT ?""", params)


# ---------------------------------------------------------------- teams
def team(con, abbr: str) -> dict | None:
    return one(con, "SELECT * FROM franchises WHERE team_abbr = ?", (abbr,))


def team_history(con, abbr: str) -> list[dict]:
    return rows(con, """SELECT season, wins, losses, ties, points_for, points_against, point_diff,
                               made_playoffs, playoff_wins, won_super_bowl, team_code_that_season
                        FROM v_team_record WHERE team = ? ORDER BY season""", (abbr,))


def team_season(con, abbr: str, season: int) -> dict | None:
    return one(con, "SELECT * FROM v_team_season WHERE team = ? AND season = ?", (abbr, season))


def team_roster(con, abbr: str, season: int) -> list[dict]:
    return rows(con, """
        SELECT r.gsis_id, r.full_name, r.position, r.jersey_number, r.status, r.years_exp,
               s.games, s.fantasy_points_ppr, c.cap_hit_musd
        FROM rosters r
        JOIN team_aliases a ON a.alias = r.team AND a.franchise = ?
        LEFT JOIN player_stats_season s ON s.player_id = r.gsis_id AND s.season = r.season AND s.season_type = 'REG'
        LEFT JOIN v_player_cap_by_season c ON c.gsis_id = r.gsis_id AND c.season = r.season
        WHERE r.season = ?
        ORDER BY CASE r.position WHEN 'QB' THEN 1 WHEN 'RB' THEN 2 WHEN 'WR' THEN 3 WHEN 'TE' THEN 4
                 WHEN 'OL' THEN 5 WHEN 'DL' THEN 6 WHEN 'LB' THEN 7 WHEN 'DB' THEN 8 ELSE 9 END,
                 s.games DESC NULLS LAST, r.full_name""", (abbr, season))


def team_games(con, abbr: str, season: int) -> list[dict]:
    return rows(con, """
        SELECT g.week, g.game_type, g.gameday, g.home_team, g.away_team, g.home_score, g.away_score,
               ah.franchise AS home_f, aa.franchise AS away_f
        FROM games g
        LEFT JOIN team_aliases ah ON ah.alias = g.home_team
        LEFT JOIN team_aliases aa ON aa.alias = g.away_team
        WHERE g.season = ? AND (ah.franchise = ? OR aa.franchise = ?)
        ORDER BY g.gameday""", (season, abbr, abbr))


def team_top_cap(con, abbr: str, season: int, limit: int = 10) -> list[dict]:
    return rows(con, """SELECT gsis_id, player, position, cap_hit_musd, cap_pct FROM player_cap_seasons
                        WHERE team = ? AND season = ? ORDER BY cap_hit_musd DESC LIMIT ?""",
                (abbr, season, limit))


def team_payroll(con, abbr: str, season: int) -> dict | None:
    return one(con, """SELECT p.cap_hit_musd, s.salary_cap_musd FROM v_team_payroll p
                       LEFT JOIN salary_cap s ON s.season = p.season
                       WHERE p.team = ? AND p.season = ?""", (abbr, season))


# ---------------------------------------------------------------- seasons
def standings(con, season: int) -> list[dict]:
    return rows(con, """SELECT * FROM v_team_season WHERE season = ?
                        ORDER BY team_conf, team_division, wins DESC, ties DESC, point_diff DESC""", (season,))


def leaders(con, season: int, column: str, limit: int = 10) -> list[dict]:
    assert column in SORTABLE, column
    return rows(con, f"""SELECT player_id, player_display_name, position, recent_team, "{column}" AS value
                         FROM player_stats_season WHERE season = ? AND season_type = 'REG'
                         AND "{column}" IS NOT NULL ORDER BY "{column}" DESC LIMIT ?""", (season, limit))


def postseason(con, season: int) -> list[dict]:
    return rows(con, """SELECT game_type, gameday, home_team, away_team, home_score, away_score
                        FROM games WHERE season = ? AND game_type <> 'REG' ORDER BY gameday""", (season,))


# ---------------------------------------------------------------- models
VALUE_POSITIONS = ["QB", "RB", "WR", "TE", "T", "G", "C", "IDL", "ED", "LB", "CB", "S", "K", "P", "LS"]
POS_NAMES = {"T": "Tackle", "G": "Guard", "C": "Center", "IDL": "Interior DL", "ED": "Edge",
             "LB": "Linebacker", "CB": "Cornerback", "S": "Safety", "QB": "Quarterback",
             "RB": "Running back", "WR": "Wide receiver", "TE": "Tight end", "K": "Kicker",
             "P": "Punter", "LS": "Long snapper"}


def models_ready(con) -> bool:
    return one(con, "SELECT count(*) AS n FROM sqlite_master WHERE name IN ('player_value', 'player_projection')")["n"] == 2


def model_metrics(con) -> dict:
    import json
    out = {r["model"]: json.loads(r["metrics"]) for r in rows(con, "SELECT model, metrics FROM model_metrics")}
    out["trained_at"] = (one(con, "SELECT max(trained_at) AS t FROM model_metrics") or {}).get("t")
    return out


def value_seasons(con) -> list[dict]:
    return rows(con, """SELECT season, max(partial_season) AS partial FROM player_value
                        GROUP BY season ORDER BY season DESC""")


def default_value_season(con) -> int:
    return one(con, "SELECT max(season) AS s FROM player_value WHERE partial_season = 0")["s"]


def value_board(con, season: int, pos: str | None, max_cost: float | None, sort: str,
                min_value_pct: float = 0.0, limit: int = 100) -> list[dict]:
    order = {"surplus": "v.surplus_today_musd DESC", "value": "v.true_value_pct DESC",
             "cheapest": "v.cap_pct ASC"}.get(sort, "v.surplus_today_musd DESC")
    where, params = ["v.season = ?", "v.cap_pct IS NOT NULL", "v.true_value_pct >= ?"], [season, min_value_pct]
    if pos:
        where.append("v.pos_group = ?")
        params.append(pos)
    if max_cost is not None:
        where.append("v.cap_hit_today_musd <= ?")
        params.append(max_cost)
    params.append(limit)
    return rows(con, f"""
        SELECT v.*, p.display_name, p.headshot
        FROM player_value v JOIN players p ON p.gsis_id = v.gsis_id
        WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?""", params)


def player_values(con, gsis_id: str) -> dict[int, dict]:
    return {r["season"]: r for r in rows(con, "SELECT * FROM player_value WHERE gsis_id = ?", (gsis_id,))}


def player_value_row(con, gsis_id: str, season: int) -> dict | None:
    return one(con, """SELECT v.*, p.display_name FROM player_value v JOIN players p ON p.gsis_id = v.gsis_id
                       WHERE v.gsis_id = ? AND v.season = ?""", (gsis_id, season))


def alternatives(con, target: dict, share: float = 0.8, limit: int = 25) -> list[dict]:
    """Same position, at least `share` of the target's true value, costing less."""
    return rows(con, """
        SELECT v.*, p.display_name, p.headshot
        FROM player_value v JOIN players p ON p.gsis_id = v.gsis_id
        WHERE v.season = ? AND v.pos_group = ? AND v.gsis_id <> ?
          AND v.true_value_pct >= ? AND v.cap_pct IS NOT NULL AND v.cap_pct < ?
        ORDER BY v.true_value_pct - v.cap_pct DESC LIMIT ?""",
                (target["season"], target["pos_group"], target["gsis_id"],
                 target["true_value_pct"] * share, target["cap_pct"] if target["cap_pct"] is not None else 1.0,
                 limit))


def projection_bases(con) -> list[int]:
    return [r["base_season"] for r in rows(con, """SELECT DISTINCT base_season FROM player_projection
                                                   WHERE kind = 'forecast' ORDER BY base_season DESC""")]


def projections(con, base: int, pos: str | None, outlook: str | None, sort: str, limit: int = 100) -> list[dict]:
    order = {"projected": "pr.projected_value_pct DESC",
             "rise": "(pr.projected_value_pct - pr.base_value_pct) DESC",
             "fall": "(pr.projected_value_pct - pr.base_value_pct) ASC"}.get(sort, "pr.projected_value_pct DESC")
    where, params = ["pr.kind = 'forecast'", "pr.base_season = ?", "pr.outlook <> 'Depth'"], [base]
    if pos:
        where.append("pr.pos_group = ?")
        params.append(pos)
    if outlook:
        where.append("pr.outlook = ?")
        params.append(outlook)
    params.append(limit)
    return rows(con, f"""
        SELECT pr.*, p.display_name, v.team, sc.cap_for_adjustment AS latest_cap,
               v.cap_hit_today_musd
        FROM player_projection pr
        JOIN players p ON p.gsis_id = pr.gsis_id
        LEFT JOIN player_value v ON v.gsis_id = pr.gsis_id AND v.season = pr.base_season
        LEFT JOIN salary_cap sc ON sc.season = (SELECT max(season) FROM salary_cap)
        WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?""", params)


def player_projections(con, gsis_id: str) -> list[dict]:
    return rows(con, """SELECT pr.*, sc.cap_for_adjustment AS latest_cap FROM player_projection pr
                        LEFT JOIN salary_cap sc ON sc.season = (SELECT max(season) FROM salary_cap)
                        WHERE gsis_id = ? ORDER BY base_season, kind""", (gsis_id,))


def latest_cap(con) -> float:
    return one(con, "SELECT cap_for_adjustment AS c FROM salary_cap ORDER BY season DESC LIMIT 1")["c"]
