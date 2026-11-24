"""NFL Pro Spectre website.

Run it with:  python run_web.py   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nfldb import config

from . import queries as Q

WEB_DIR = Path(__file__).resolve().parent

app = FastAPI(title="NFL Pro Spectre")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


# ------------------------------------------------------------------ helpers
def get_db():
    if not config.DB_PATH.exists():
        raise HTTPException(503, f"Database not found at {config.DB_PATH}. Run: python build_database.py")
    con = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    try:
        yield con
    finally:
        con.close()


def fmt_num(v, decimals: int = 0):
    if v is None or v == "":
        return "–"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if decimals == 3 and abs(f) <= 1:
        return f"{f * 100:.1f}%"
    return f"{f:,.{decimals}f}"


def fmt_money(v):
    """Millions of dollars -> '$12.5M'."""
    if v is None:
        return "–"
    if v == 0:
        return "$0"
    return f"${v:,.1f}M" if abs(v) >= 1 else f"${v * 1000:,.0f}K"


def fmt_pct(v):
    return "–" if v is None else f"{v * 100:.1f}%"


templates.env.filters.update(num=fmt_num, money=fmt_money, pct=fmt_pct)
# build_site.py flips these to render the static GitHub Pages copy of the site.
templates.env.globals.update(static_site=False, site_base="/", static_page=None)


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


# ------------------------------------------------------------------ pages
@app.get("/", response_class=HTMLResponse)
def home(request: Request, con=Depends(get_db)):
    season = Q.latest_season(con)
    boards = [(col, label, Q.leaders(con, season, col, 5)) for col, label in Q.LEADER_BOARDS[:6]]
    return render(request, "index.html", season=season, boards=boards, teams=Q.franchises(con),
                  info=Q.build_info(con))


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", con=Depends(get_db)):
    q = q.strip()
    if not q:
        return RedirectResponse("/players")
    if q.isdigit() and len(q) == 4 and int(q) in Q.seasons_with_stats(con):
        return RedirectResponse(f"/seasons/{q}")
    players = Q.search_players(con, q, 50) if len(q) >= 2 else []
    teams = Q.search_teams(con, q)
    if len(teams) == 1 and not players:
        return RedirectResponse(f"/teams/{teams[0]['team_abbr']}")
    return render(request, "search.html", q=q, players=players, teams=teams)


@app.get("/api/search")
def api_search(q: str = "", con=Depends(get_db)):
    q = q.strip()
    if len(q) < 2:
        return JSONResponse([])
    out = [{"label": t["team_name"], "sub": "Team", "url": f"/teams/{t['team_abbr']}"}
           for t in Q.search_teams(con, q)]
    out += [{"label": p["display_name"],
             "sub": " · ".join(str(x) for x in (p["position"], p["latest_team"],
                                                 f"{p['rookie_season'] or '?'}–{p['last_season'] or '?'}") if x),
             "url": f"/players/{p['gsis_id']}"} for p in Q.search_players(con, q, 10)]
    return JSONResponse(out[:12])


@app.get("/players", response_class=HTMLResponse)
def players(request: Request, season: int | None = None, position: str = "", team: str = "",
            sort: str = "fantasy_points_ppr", con=Depends(get_db)):
    seasons = [s for s in Q.seasons_with_stats(con)]
    season = season or Q.latest_season(con)
    results = Q.player_finder(con, season, position or None, team or None, sort)
    cols = finder_columns(position)
    return render(request, "players.html", results=results, cols=cols, seasons=seasons, season=season,
                  position=position, team=team, sort=sort, positions=Q.POSITION_FILTERS,
                  teams=Q.franchises(con), sortable=Q.SORTABLE)


def finder_columns(position: str) -> list[tuple[str, str, int]]:
    """Stat columns the player finder shows for a position filter ("" = all positions)."""
    if not position:
        return [("passing_yards", "Pass Yds", 0), ("passing_tds", "Pass TD", 0), ("rushing_yards", "Rush Yds", 0),
                ("rushing_tds", "Rush TD", 0), ("receiving_yards", "Rec Yds", 0), ("receiving_tds", "Rec TD", 0),
                ("def_sacks", "Sacks", 1), ("def_interceptions", "Int", 0), ("fantasy_points_ppr", "PPR", 1)]
    return Q.STAT_SETS[Q.stat_set_for(position, position)] or [("games", "Games", 0)]


@app.get("/players/{gsis_id}", response_class=HTMLResponse)
def player_page(request: Request, gsis_id: str, con=Depends(get_db)):
    p = Q.player(con, gsis_id)
    if not p:
        raise HTTPException(404, "Player not found")
    reg = Q.player_career(con, gsis_id, "REG")
    post = Q.player_career(con, gsis_id, "POST")
    last = reg[-1] if reg else {}
    key = Q.stat_set_for(p.get("position") or last.get("position"), p.get("position_group") or last.get("position_group"))
    cols = Q.STAT_SETS[key]
    chart_cols = [c for c in cols if c[2] in (0, 1)]
    chart = {
        "seasons": [r["season"] for r in reg],
        "series": {label: [r.get(col) for r in reg] for col, label, _ in chart_cols},
        "cap": [r.get("cap_hit_musd") for r in reg],
    }
    totals = {col: sum((r.get(col) or 0) for r in reg) for col, _, d in cols if d in (0, 1)}
    totals["games"] = sum((r.get("games") or 0) for r in reg)
    values, projs, latest_cap = {}, [], None
    if Q.models_ready(con):
        values = Q.player_values(con, gsis_id)
        projs = Q.player_projections(con, gsis_id)
        latest_cap = Q.latest_cap(con)
        chart["value"] = [round(values[r["season"]]["true_value_today_musd"], 2) if r["season"] in values else None
                          for r in reg]
        chart["cap"] = [round(r["cap_hit_musd"] * r["salary_cap_musd_adj"], 2)
                        if r.get("cap_hit_musd") is not None and r.get("salary_cap_musd_adj") else None
                        for r in Q.rows(con, """SELECT s.season, c.cap_hit_musd,
                                                       sc.cap_factor_to_latest AS salary_cap_musd_adj
                                                FROM v_player_season s
                                                LEFT JOIN v_player_cap_by_season c ON c.gsis_id = s.player_id AND c.season = s.season
                                                LEFT JOIN salary_cap sc ON sc.season = s.season
                                                WHERE s.player_id = ? AND s.season_type = 'REG' ORDER BY s.season""",
                                        (gsis_id,))]
    forecast = [x for x in projs if x["kind"] == "forecast"]
    return render(request, "player.html", p=p, reg=reg, post=post, cols=cols, totals=totals,
                  values=values, forecast=forecast, backtest=[x for x in projs if x["kind"] == "backtest"],
                  latest_cap=latest_cap, pteam=Q.team(con, p["latest_team"]) if p.get("latest_team") else None,
                  roster=Q.player_roster_history(con, p), contracts=Q.player_contracts(con, gsis_id),
                  cap=Q.player_cap(con, gsis_id), chart=chart)


@app.get("/teams", response_class=HTMLResponse)
def teams(request: Request, con=Depends(get_db)):
    return render(request, "teams.html", teams=Q.franchises(con))


@app.get("/teams/{abbr}", response_class=HTMLResponse)
def team_page(request: Request, abbr: str, season: int | None = None, con=Depends(get_db)):
    abbr = abbr.upper()
    t = Q.team(con, abbr)
    if not t:
        raise HTTPException(404, "Team not found")
    history = Q.team_history(con, abbr)
    seasons = [h["season"] for h in history][::-1]
    season = season or (seasons[0] if seasons else Q.latest_season(con))
    return render(request, "team.html", t=t, season=season, seasons=seasons, history=history,
                  ts=Q.team_season(con, abbr, season), roster=Q.team_roster(con, abbr, season),
                  games=Q.team_games(con, abbr, season), top_cap=Q.team_top_cap(con, abbr, season),
                  payroll=Q.team_payroll(con, abbr, season))


@app.get("/seasons", response_class=HTMLResponse)
def seasons(request: Request, con=Depends(get_db)):
    champs = Q.rows(con, """SELECT r.season, r.team, f.team_name, f.team_logo_espn FROM v_team_record r
                            LEFT JOIN franchises f ON f.team_abbr = r.team WHERE won_super_bowl = 1""")
    champ_by = {c["season"]: c for c in champs}
    return render(request, "seasons.html", seasons=Q.seasons_with_stats(con), champs=champ_by)


@app.get("/seasons/{season}", response_class=HTMLResponse)
def season_page(request: Request, season: int, con=Depends(get_db)):
    if season not in Q.seasons_with_stats(con):
        raise HTTPException(404, "No data for that season")
    table = Q.standings(con, season)
    divisions: dict[str, list[dict]] = {}
    for row in table:
        divisions.setdefault(row["team_division"] or "Other", []).append(row)
    boards = [(col, label, Q.leaders(con, season, col, 10)) for col, label in Q.LEADER_BOARDS]
    return render(request, "season.html", season=season, divisions=divisions, boards=boards,
                  playoffs=Q.postseason(con, season), all_seasons=Q.seasons_with_stats(con))


# ------------------------------------------------------------------ models
def _need_models(con):
    if not Q.models_ready(con):
        raise HTTPException(503, "The models haven't been trained yet. Run: python train_models.py")


def _money_or_none(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@app.get("/value", response_class=HTMLResponse)
def value_portal(request: Request, season: int | None = None, pos: str = "", max_cost: str = "",
                 sort: str = "surplus", con=Depends(get_db)):
    _need_models(con)
    season = season or Q.default_value_season(con)
    cap = _money_or_none(max_cost)
    results = Q.value_board(con, season, pos or None, cap, sort, min_value_pct=0.005)
    return render(request, "value.html", results=results, season=season, seasons=Q.value_seasons(con),
                  pos=pos, max_cost=max_cost, sort=sort, positions=Q.VALUE_POSITIONS, pos_names=Q.POS_NAMES,
                  latest_cap=Q.latest_cap(con))


@app.get("/value/alternatives/{gsis_id}", response_class=HTMLResponse)
def value_alternatives(request: Request, gsis_id: str, season: int | None = None, con=Depends(get_db)):
    _need_models(con)
    season = season or Q.default_value_season(con)
    target = Q.player_value_row(con, gsis_id, season)
    if not target:
        raise HTTPException(404, "No value score for that player in that season")
    return render(request, "alternatives.html", t=target, alts=Q.alternatives(con, target),
                  pos_names=Q.POS_NAMES, seasons=Q.value_seasons(con))


@app.get("/projections", response_class=HTMLResponse)
def projections_page(request: Request, base: int | None = None, pos: str = "", outlook: str = "",
                     sort: str = "projected", con=Depends(get_db)):
    _need_models(con)
    bases = Q.projection_bases(con)
    base = base if base in bases else bases[0]
    return render(request, "projections.html", rows=Q.projections(con, base, pos or None, outlook or None, sort),
                  base=base, bases=bases, pos=pos, outlook=outlook, sort=sort,
                  positions=Q.VALUE_POSITIONS, pos_names=Q.POS_NAMES, latest_cap=Q.latest_cap(con))


@app.get("/models", response_class=HTMLResponse)
def models_page(request: Request, con=Depends(get_db)):
    _need_models(con)
    return render(request, "models.html", m=Q.model_metrics(con), pos_names=Q.POS_NAMES)
