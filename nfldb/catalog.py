"""Every dataset the database pulls, and the table it lands in.

Almost everything comes from nflverse (https://github.com/nflverse), which
publishes free, regularly refreshed parquet files built from the NFL's own
game feeds, Pro Football Reference, Next Gen Stats, ESPN and OverTheCap.

A dataset is either one file, or one file per season (``per_season=True``,
with ``{season}`` in the URL). Several datasets can share a table: their
files are stacked together (for example regular season + playoff totals).
"""
from __future__ import annotations

from dataclasses import dataclass

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
NFLVERSE_RAW = "https://raw.githubusercontent.com/nflverse"


@dataclass(frozen=True)
class Dataset:
    name: str            # unique key, also the raw cache folder name
    table: str           # DuckDB table the files are loaded into
    url: str             # may contain {season}
    per_season: bool = False
    first_season: int | None = None
    final_season: int | None = None  # last season this file series exists for
    fmt: str = "parquet"  # parquet | csv
    optional: bool = False  # only loaded when asked for (big files)
    description: str = ""

    def urls(self, last_season: int) -> list[tuple[int | None, str]]:
        if not self.per_season:
            return [(None, self.url)]
        end = min(last_season, self.final_season or last_season)
        return [(s, self.url.format(season=s)) for s in range(self.first_season, end + 1)]


DATASETS: list[Dataset] = [
    # ---- Reference -------------------------------------------------------
    Dataset("teams", "teams", f"{NFLVERSE_RAW}/nflverse-pbp/master/teams_colors_logos.csv",
            fmt="csv", description="Team names, divisions, colors and logos"),
    Dataset("players", "players", f"{NFLVERSE}/players/players.parquet",
            description="Every player with all cross-site IDs, bio, draft info"),

    # ---- Rosters ---------------------------------------------------------
    Dataset("rosters", "rosters", f"{NFLVERSE}/rosters/roster_{{season}}.parquet",
            per_season=True, first_season=1920, description="Season rosters back to 1920"),
    Dataset("rosters_weekly", "rosters_weekly", f"{NFLVERSE}/weekly_rosters/roster_weekly_{{season}}.parquet",
            per_season=True, first_season=2002, description="Week-by-week rosters"),
    Dataset("depth_charts", "depth_charts", f"{NFLVERSE}/depth_charts/depth_charts_{{season}}.parquet",
            per_season=True, first_season=2001, final_season=2024,
            description="Weekly depth charts by season/week (NFL format, 2001-2024)"),
    # From 2025 nflverse switched to timestamped ESPN depth chart snapshots
    # (column dt instead of season/week), so they get their own table.
    Dataset("depth_charts_espn", "depth_charts_espn", f"{NFLVERSE}/depth_charts/depth_charts_{{season}}.parquet",
            per_season=True, first_season=2025,
            description="Depth chart snapshots by timestamp (ESPN format, 2025+)"),

    # ---- Games -----------------------------------------------------------
    Dataset("games", "games", f"{NFLVERSE}/schedules/games.parquet",
            description="Every game since 1999: scores, betting lines, weather, QBs, coaches"),

    # ---- Player stats ----------------------------------------------------
    Dataset("player_stats_week", "player_stats_week",
            f"{NFLVERSE}/stats_player/stats_player_week_{{season}}.parquet",
            per_season=True, first_season=1999, description="Per-game player box score + EPA"),
    Dataset("player_stats_reg", "player_stats_season",
            f"{NFLVERSE}/stats_player/stats_player_reg_{{season}}.parquet",
            per_season=True, first_season=1999, description="Regular season player totals"),
    Dataset("player_stats_post", "player_stats_season",
            f"{NFLVERSE}/stats_player/stats_player_post_{{season}}.parquet",
            per_season=True, first_season=1999, description="Playoff player totals"),
    Dataset("snap_counts", "snap_counts", f"{NFLVERSE}/snap_counts/snap_counts_{{season}}.parquet",
            per_season=True, first_season=2012, description="Offense/defense/ST snaps per game"),
    Dataset("injuries", "injuries", f"{NFLVERSE}/injuries/injuries_{{season}}.parquet",
            per_season=True, first_season=2009, description="Weekly injury reports"),

    # ---- Team stats ------------------------------------------------------
    Dataset("team_stats_week", "team_stats_week",
            f"{NFLVERSE}/stats_team/stats_team_week_{{season}}.parquet",
            per_season=True, first_season=1999, description="Per-game team totals"),
    Dataset("team_stats_reg", "team_stats_season",
            f"{NFLVERSE}/stats_team/stats_team_reg_{{season}}.parquet",
            per_season=True, first_season=1999, description="Regular season team totals"),
    Dataset("team_stats_post", "team_stats_season",
            f"{NFLVERSE}/stats_team/stats_team_post_{{season}}.parquet",
            per_season=True, first_season=1999, description="Playoff team totals"),

    # ---- Advanced stats --------------------------------------------------
    Dataset("pfr_adv_pass", "pfr_adv_pass", f"{NFLVERSE}/pfr_advstats/advstats_season_pass.parquet",
            description="PFR advanced passing (pressures, drops, bad throws), 2018+"),
    Dataset("pfr_adv_rush", "pfr_adv_rush", f"{NFLVERSE}/pfr_advstats/advstats_season_rush.parquet",
            description="PFR advanced rushing (yards before/after contact), 2018+"),
    Dataset("pfr_adv_rec", "pfr_adv_rec", f"{NFLVERSE}/pfr_advstats/advstats_season_rec.parquet",
            description="PFR advanced receiving (drops, ADOT, broken tackles), 2018+"),
    Dataset("pfr_adv_def", "pfr_adv_def", f"{NFLVERSE}/pfr_advstats/advstats_season_def.parquet",
            description="PFR advanced defense (targets allowed, pressures, missed tackles), 2018+"),
    Dataset("ngs_passing", "ngs_passing", f"{NFLVERSE}/nextgen_stats/ngs_passing.parquet",
            description="Next Gen Stats passing (time to throw, CPOE), 2016+"),
    Dataset("ngs_rushing", "ngs_rushing", f"{NFLVERSE}/nextgen_stats/ngs_rushing.parquet",
            description="Next Gen Stats rushing (RYOE, box counts), 2016+"),
    Dataset("ngs_receiving", "ngs_receiving", f"{NFLVERSE}/nextgen_stats/ngs_receiving.parquet",
            description="Next Gen Stats receiving (separation, YAC over expected), 2016+"),
    Dataset("qbr_season", "qbr_season", f"{NFLVERSE}/espn_data/qbr_season_level.parquet",
            description="ESPN QBR by season, 2006+"),
    Dataset("qbr_week", "qbr_week", f"{NFLVERSE}/espn_data/qbr_week_level.parquet",
            description="ESPN QBR by week"),

    # ---- Draft / combine -------------------------------------------------
    Dataset("draft_picks", "draft_picks", f"{NFLVERSE}/draft_picks/draft_picks.parquet",
            description="Every draft pick since 1980 with career value (AV)"),
    Dataset("combine", "combine", f"{NFLVERSE}/combine/combine.parquet",
            description="Combine measurements since 2000"),

    # ---- Money -----------------------------------------------------------
    Dataset("contracts", "contracts_raw", f"{NFLVERSE}/contracts/historical_contracts.parquet",
            description="OverTheCap contracts with year-by-year cap hits and cash"),

    # ---- Optional: play by play (~13 MB per season) ---------------------
    Dataset("pbp", "pbp", f"{NFLVERSE}/pbp/play_by_play_{{season}}.parquet",
            per_season=True, first_season=1999, optional=True,
            description="Every play since 1999 with EPA/WPA (large; use --pbp)"),
]

# Consumer Price Index (CPI-U, monthly) from the St. Louis Fed. No API key needed.
CPI_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"


def get(name: str) -> Dataset:
    for d in DATASETS:
        if d.name == name:
            return d
    raise KeyError(name)
