"""Render the whole website to static files for GitHub Pages.

Every page of the live site (web/app.py) is rendered once and saved as
<out>/<path>/index.html. Pages that filter or search (player finder, value
portal, projections, "similar for less", search) are saved as shells plus JSON
data under <out>/data/, and web/static/static-pages.js runs the filters in the
browser. Run it through build_site.py.
"""
from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException
from starlette.requests import Request

from nfldb import config

from . import app as A
from . import queries as Q

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ URLs
_ATTR = re.compile(r'\b(href|src|action)="(/(?!/)[^"]*)"')


def map_url(url: str, base: str) -> str:
    """Live-site URL -> static-site URL. Mirrors window.siteHref in web/static/app.js.

    /players/ID              -> {base}players/ID/
    /teams/KC?season=2010    -> {base}teams/KC/2010/
    /value/alternatives/ID?season=2025 -> {base}value/alternatives/?id=ID&season=2025
    /players?season=2025     -> {base}players/?season=2025   (filtered in the browser)
    /static/style.css        -> {base}static/style.css
    """
    path, _, query = url.partition("?")
    q = dict(parse_qsl(query, keep_blank_values=True))
    parts = [p for p in path.split("/") if p]
    if parts[:1] == ["static"]:
        out = "/".join(parts)
    elif parts[:1] == ["teams"] and len(parts) == 2 and "season" in q:
        out = f"teams/{parts[1].upper()}/{q.pop('season')}/"
    elif parts[:2] == ["value", "alternatives"] and len(parts) == 3:
        season = q.pop("season", None)
        q["id"] = parts[2]
        if season:
            q["season"] = season
        out = "value/alternatives/"
    else:
        out = "/".join(parts) + "/" if parts else ""
    return base + out + ("?" + urlencode(q) if q else "")


def rewrite_links(page: str, base: str) -> str:
    def fix(m: re.Match) -> str:
        return f'{m.group(1)}="{html.escape(map_url(html.unescape(m.group(2)), base))}"'
    return _ATTR.sub(fix, page)


# ------------------------------------------------------------------ writing
class Writer:
    def __init__(self, out: Path, base: str):
        self.out, self.base = out, base
        self.files = 0
        self.bytes = 0

    def page(self, path: str, body: str | bytes) -> None:
        """path '' = home page, 'players/X' -> players/X/index.html, '404.html' kept as is."""
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        target = self.out / path if path.endswith(".html") else self.out / path / "index.html"
        self._write(target, rewrite_links(body, self.base).encode("utf-8"))

    def json(self, path: str, obj) -> None:
        self._write(self.out / path, json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8"))

    def _write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.files += 1
        self.bytes += len(data)


def _clean(v):
    """Make a value JSON friendly and short (floats rounded, NaN -> null)."""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 5) if abs(v) < 10 else round(v, 2)
    return v


def table_json(rows: list[dict], cols: list[str]) -> dict:
    """Columnar JSON: {"cols": [...], "rows": [[...], ...]}, read by static-pages.js."""
    return {"cols": cols, "rows": [[_clean(r.get(c)) for c in cols] for r in rows]}


# ------------------------------------------------------------------ rendering helpers
def _request(path: str = "/") -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
                    "query_string": b"", "headers": [], "app": A.app})


def _body(response) -> str:
    return response.body.decode("utf-8")


def _shell(name: str, **ctx) -> str:
    """Render a template directly (no request), for pages the browser fills in."""
    return A.templates.get_template(name).render(**ctx)


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)


def set_static_mode(base: str) -> None:
    A.templates.env.globals.update(static_site=True, site_base=base)


# ------------------------------------------------------------------ player pages (parallel)
_worker_con = None


def _init_worker(base: str) -> None:
    global _worker_con
    set_static_mode(base)
    _worker_con = _connect()


def _player_pages(args) -> tuple[int, int]:
    """Render a batch of player pages in a worker process."""
    ids, out, base = args
    w = Writer(Path(out), base)
    req = _request()
    for gsis_id in ids:
        try:
            w.page(f"players/{gsis_id}", _body(A.player_page(req, gsis_id, con=_worker_con)))
        except HTTPException:
            pass
    return w.files, w.bytes


# ------------------------------------------------------------------ the build
def build(out: Path, base: str = "/", workers: int | None = None, limit_players: int | None = None) -> dict:
    """Render the site into `out` (emptied first). `base` is the URL prefix it will be served from."""
    if not base.endswith("/"):
        base += "/"
    if not base.startswith("/"):
        base = "/" + base
    if not config.DB_PATH.exists():
        raise SystemExit(f"Database not found at {config.DB_PATH}. Run: python build_database.py")
    t0 = time.time()
    set_static_mode(base)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    w = Writer(out, base)
    con = _connect()
    req = _request()

    # static assets
    shutil.copytree(A.WEB_DIR / "static", out / "static")
    (out / ".nojekyll").write_text("")

    # simple pages
    w.page("", _body(A.home(req, con=con)))
    w.page("teams", _body(A.teams(req, con=con)))
    w.page("seasons", _body(A.seasons(req, con=con)))
    w.page("404.html", _shell("404.html"))

    seasons = Q.seasons_with_stats(con)
    for s in seasons:
        w.page(f"seasons/{s}", _body(A.season_page(req, s, con=con)))
    log.info("Seasons: %d pages", len(seasons))

    n = 0
    for t in Q.franchises(con):
        abbr = t["team_abbr"]
        w.page(f"teams/{abbr}", _body(A.team_page(req, abbr, season=None, con=con)))
        for h in Q.team_history(con, abbr):
            w.page(f"teams/{abbr}/{h['season']}", _body(A.team_page(req, abbr, season=h["season"], con=con)))
            n += 1
    log.info("Teams: %d team-season pages", n)

    # player finder: one JSON file per season with every column it can show or sort by
    latest = Q.latest_season(con)
    finder_cols = list(dict.fromkeys(
        ["player_id", "player_display_name", "position", "position_group", "recent_team", "age", "games",
         "cap_hit_musd"] + list(Q.SORTABLE)))
    for s in seasons:
        rows = Q.rows(con, "SELECT * FROM v_player_season WHERE season = ? AND season_type = 'REG'", (s,))
        w.json(f"data/players/{s}.json", table_json(rows, finder_cols))
    w.page("players", _shell(
        "players.html", static_page="players", results=[], cols=[], seasons=seasons, season=latest,
        position="", team="", sort="fantasy_points_ppr", positions=Q.POSITION_FILTERS,
        teams=Q.franchises(con), sortable=Q.SORTABLE,
        static_config={"season": latest, "sortable": Q.SORTABLE,
                       "columns": {p: A.finder_columns(p) for p in [""] + Q.POSITION_FILTERS}}))

    # search index
    players = Q.rows(con, """SELECT gsis_id, display_name, first_name || ' ' || last_name AS full_name, position,
                                    latest_team, rookie_season, last_season, status
                             FROM players WHERE display_name IS NOT NULL""")
    for p in players:
        if p["full_name"] == p["display_name"]:
            p["full_name"] = None
    w.json("data/search.json", {
        **table_json(players, ["gsis_id", "display_name", "full_name", "position", "latest_team",
                               "rookie_season", "last_season", "status"]),
        "teams": Q.rows(con, "SELECT team_abbr, team_name, team_nick, team_logo_espn FROM franchises"),
        "seasons": seasons})
    w.page("search", _shell("search_static.html", static_page="search"))

    # models: value portal, alternatives, projections
    if Q.models_ready(con):
        latest_cap = Q.latest_cap(con)
        value_seasons = Q.value_seasons(con)
        default_season = Q.default_value_season(con)
        value_cols = ["gsis_id", "display_name", "pos_group", "team", "age", "games", "true_value_pct", "cap_pct",
                      "true_value_today_musd", "cap_hit_today_musd", "surplus_today_musd", "value_pctile"]
        for vs in value_seasons:
            rows = Q.rows(con, """SELECT v.*, p.display_name FROM player_value v
                                  JOIN players p ON p.gsis_id = v.gsis_id WHERE v.season = ?""", (vs["season"],))
            w.json(f"data/value/{vs['season']}.json", table_json(rows, value_cols))
        w.page("value", _shell(
            "value.html", static_page="value", results=[], season=default_season, seasons=value_seasons,
            pos="", max_cost="", sort="surplus", positions=Q.VALUE_POSITIONS, pos_names=Q.POS_NAMES,
            latest_cap=latest_cap, static_config={"season": default_season}))
        w.page("value/alternatives", _shell(
            "alternatives_static.html", static_page="alternatives",
            static_config={"season": default_season, "seasons": [v["season"] for v in value_seasons],
                           "pos_names": Q.POS_NAMES}))

        bases = Q.projection_bases(con)
        proj_cols = ["gsis_id", "display_name", "pos_group", "team", "age", "base_value_pct",
                     "projected_value_pct", "outlook", "actual_value_pct"]
        for b in bases:
            rows = Q.projections(con, b, None, None, "projected", limit=100000)
            w.json(f"data/projections/{b}.json", table_json(rows, proj_cols))
        w.page("projections", _shell(
            "projections.html", static_page="projections", rows=[], base=bases[0], bases=bases, pos="",
            outlook="", sort="projected", positions=Q.VALUE_POSITIONS, pos_names=Q.POS_NAMES,
            latest_cap=latest_cap, static_config={"bases": bases, "latest_cap": latest_cap}))
        w.page("models", _body(A.models_page(req, con=con)))
        log.info("Models: value portal (%d seasons), projections (%d)", len(value_seasons), len(bases))
    else:
        log.warning("Models not trained, so the value, projection and model pages are skipped. "
                    "Run: python train_models.py")

    # player pages, in parallel
    ids = [r["gsis_id"] for r in Q.rows(con, "SELECT gsis_id FROM players ORDER BY gsis_id")]
    con.close()
    if limit_players:
        ids = ids[:limit_players]
    workers = workers or max(1, min(8, os.cpu_count() or 1))
    batches = [(ids[i:i + 500], str(out), base) for i in range(0, len(ids), 500)]
    t1 = time.time()
    with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(base,)) as pool:
        for i, (files, size) in enumerate(pool.map(_player_pages, batches), 1):
            w.files += files
            w.bytes += size
            if i % 10 == 0 or i == len(batches):
                log.info("Players: %d of %d pages", min(i * 500, len(ids)), len(ids))
    log.info("Player pages took %.0fs", time.time() - t1)

    static_bytes = sum(f.stat().st_size for f in (out / "static").rglob("*") if f.is_file())
    stats = {"files": w.files, "mb": round((w.bytes + static_bytes) / 1e6, 1), "seconds": round(time.time() - t0)}
    log.info("Site written to %s: %d files, %.1f MB, %ds", out, stats["files"], stats["mb"], stats["seconds"])
    return stats
