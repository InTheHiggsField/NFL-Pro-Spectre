# NFL Pro Spectre: data layer

One command downloads free public NFL data and builds a single SQLite database
file, `data/nfl.sqlite` (about 1.1 GB, plus about 110 MB of cached downloads).
Weekly rosters and depth charts are about half of that.
The dashboard, the value portal and the prediction model all read from it.

## Setup (PyCharm on Windows)

1. Open the `NFLProSpectre` folder in PyCharm.
2. Settings > Project > Python Interpreter > Add Interpreter > Virtualenv (Python 3.10 or newer).
3. In PyCharm's terminal:
   ```
   pip install -r requirements.txt
   python build_database.py
   ```
   The first build takes one to two minutes on a normal connection.
4. Run `python build_database.py` again any time (weekly during the season) to pull the
   newest games. It only re-downloads the current season and the files that change all year.

Other options: `--offline` (rebuild from cached files), `--full-refresh`, `--pbp`
(adds every play since 1999, several GB more in SQLite), `--last-season 2025`.

To browse the database: in PyCharm Professional, Database tool window > + > Data Source > SQLite,
file `data/nfl.sqlite`. [DB Browser for SQLite](https://sqlitebrowser.org/) also works. Close those
connections before rebuilding, because Windows locks the file.

How it's built: the source files are parquet, so the build stages them in a scratch DuckDB file
(`data/staging.duckdb`, deleted when the build finishes) to read them and run the derived SQL, then
writes everything into SQLite. Nothing else in the app needs DuckDB. Inside SQLite, list columns such as
`season_history` are stored as JSON text, true/false as 1/0, and dates as ISO text (`1995-09-17`).
The `v_*` names are regular tables in SQLite (precomputed at build time), so they're fast to query.

## Using it from Python

```python
from nfldb import db

db.player_seasons("Mahomes")                 # every season, with cap hit
db.team_season("KC", 2024)                   # record + team stat totals
db.query("""
    SELECT player_display_name, recent_team, passing_yards, passing_epa, cap_hit_musd
    FROM v_player_season
    WHERE season = 2025 AND season_type = 'REG' AND position = 'QB'
    ORDER BY passing_epa DESC
""")
```

## Website

```
python run_web.py
```
Then open http://127.0.0.1:8000. It needs `data/nfl.sqlite`, so build the database first.

- **Search** (top bar): players, teams or a year, with suggestions as you type.
- **Players** (`/players`): filter any season by position and team, sort by any stat; click column headers to re-sort.
- **Player pages**: bio, draft, a career chart (pick the stat) with cap hit overlaid, regular season and playoff
  tables, contracts with today's-cap equivalent, and cap hit by year. Players from before 1999 show their roster history.
- **Team pages**: pick a season to see record, team totals, cap spent, every game, the roster with stats and cap hits,
  the biggest cap hits, and a wins-by-season chart (gold = Super Bowl win; click a bar to jump to that season).
- **Seasons**: standings by division, playoff results and stat leaders for every year since 1999.

The code is in `web/`: `app.py` (routes), `queries.py` (all SQL), `templates/` (pages), `static/` (CSS, JS,
and a local copy of Chart.js so it works offline).

## Publishing to GitHub Pages (static copy)

GitHub Pages only serves files, so `build_site.py` renders every page of the website to `site/`
(about 26,000 pages, 200 MB, under a minute). Pages that filter or search (player finder, value portal,
"similar for less", projections, search and the suggestions box) ship as JSON in `site/data/` and filter in
the browser with `web/static/static-pages.js`, giving the same results as the live site.

```
python build_site.py                      # writes site/
python -m http.server 8001 -d site        # preview at http://localhost:8001
```

`.github/workflows/pages.yml` does the whole thing on GitHub on every push to `main`, every Tuesday
morning (to pick up the latest week) and on demand from the Actions tab: it builds the database from nflverse,
trains the models, runs `build_site.py --base /<repo>/` and publishes. Nothing generated is committed.
One-time setup: in the repo go to Settings > Pages and set Source to "GitHub Actions". On a free GitHub
plan the repo has to be public for Pages to work.

## Models: true value and next-season outlook

```
python train_models.py
```
Takes about 20 seconds and prints how well each model tests. After the first run, every
`python build_database.py` retrains them on the fresh data automatically.

- **True value** (`analytics/true_value.py`): learns what the open market pays for a season of production,
  from about 13,000 veteran contracts matched to the season before signing. Price is a share of the cap, so it
  is inflation-proof; times today's cap gives today's dollars. Tested on contracts signed 2023–2026 that it
  never saw: R² 0.74 (guessing each position's typical price scores -0.08), typical error 0.6% of the cap.
- **Next-season outlook** (`analytics/projection.py`): predicts next season's true value and calls it Strong
  (15%+ up), Steady, or Weak (15%+ down). Walk-forward backtest over 2016–2025: R² 0.76 vs 0.70 for
  "same as last season"; Strong calls came true 52% of the time and Weak calls 58%, against a 39% base rate.
- Results go into `player_value`, `player_projection` and `model_metrics` in the database. Trained models are
  saved in `data/models/`.
- Website pages: `/value` (bargain portal, with "similar for less" for any player), `/projections`, `/models`
  (how they work and how accurate they are). Player pages show true value, surplus and the outlook.

## What's in it

| Table / view | Rows | Seasons | What it is |
|---|---|---|---|
| `players` | 25k | all | Every player, all cross-site IDs (gsis, PFR, ESPN, OTC, PFF), bio, draft |
| `rosters` | 143k | 1920–now | Season rosters |
| `rosters_weekly` | 933k | 2002+ | Week-by-week rosters |
| `depth_charts` / `depth_charts_espn` | 869k / 1.4m | 2001–2024 / 2025+ | Depth charts (by week, then daily ESPN snapshots after nflverse changed format) |
| `games` | 7.5k | 1999–now | Scores, Vegas lines, weather, stadium, starting QBs, coaches |
| `player_stats_week` | 487k | 1999–now | Per-game box score + EPA for every player (offense, defense, kicking) |
| `player_stats_season` | 63k | 1999–now | Season totals, `season_type` = REG or POST |
| `team_stats_week`, `team_stats_season` | 15k / 1.2k | 1999–now | Team totals |
| `snap_counts` | 339k | 2012+ | Offense/defense/special teams snaps per game |
| `injuries` | 94k | 2009+ | Weekly injury reports |
| `pfr_adv_pass/rush/rec/def` | 0.8k–8k each | 2018+ | Pressures, drops, broken tackles, yards after contact, coverage |
| `ngs_passing/rushing/receiving` | 6k+ each | 2016+ | Next Gen Stats tracking metrics |
| `qbr_season`, `qbr_week` | 1.6k / 11k | 2006+ | ESPN QBR |
| `draft_picks`, `combine` | 13k / 9k | 1980+ / 2000+ | Draft results with career value, combine testing |
| `contracts` | 54k | – | Every OverTheCap contract: years, total, APY, guarantees, % of cap |
| `player_cap_seasons` | 43k | – | Per player per season: cap hit, cash paid, base salary, bonuses |
| `salary_cap` | 33 | 1994+ | League cap per year and a factor to today's cap |
| `cpi_annual` | ~80 | 1947+ | Consumer Price Index (from FRED), for plain inflation |
| `team_aliases`, `franchises`, `teams` | | | Maps OAK→LV, SD→LAC, STL→LA, nicknames→codes; colors and logos |

Ready-made `v_*` tables:

- `v_player_season`: season stats + age + draft info + that season's cap hit, also restated in today's cap dollars.
- `v_contracts`: contracts with APY/value adjusted to today two ways: by salary cap growth (`*_cap_adj_musd`, best for comparing NFL contracts) and by CPI (`*_cpi_adj_musd`).
- `v_team_record`, `v_team_season`: wins, losses, points, playoffs, Super Bowl, plus team stat totals.
- `v_team_payroll`: summed cap hits per team per season.

Money columns end in `_musd` and are in millions of US dollars.

## Sources and limits

- Stats, rosters, games, contracts: [nflverse](https://github.com/nflverse), which compiles the
  NFL's game feeds, Pro Football Reference, Next Gen Stats, ESPN and OverTheCap. It updates
  during the season, usually the day after games.
- Inflation: FRED CPI-U (`CPIAUCSL`). If FRED can't be reached the build still works and the CPI columns are empty.
- Detailed player stats start in 1999 (that's where the NFL's play-by-play feed starts). Rosters go back to 1920.
- Contract detail is thin before about 2011 (OverTheCap's coverage), so value analysis is strongest from 2011 on.
- The current season's cap is estimated from contracts until it's added to `nfldb/seeds/salary_cap.csv`.
- Playoff totals for the current season appear once the playoffs start.

## Code layout

```
build_database.py        command line entry point
run_web.py               starts the website
build_site.py            renders the static GitHub Pages copy of the website into site/
web/                     the website (FastAPI + Jinja templates; static_site.py = static build)
train_models.py          trains the models and writes their scores into the database
analytics/               features.py, true_value.py, projection.py, pipeline.py
nfldb/catalog.py         list of every dataset and where it comes from (add sources here)
nfldb/download.py        cached, parallel, retrying downloads into data/raw/
nfldb/build.py           stages files in DuckDB, writes data/nfl.sqlite (temp file, then swaps it in)
nfldb/sql/derived.sql    contracts flattening, salary cap, inflation, and all v_* views
nfldb/seeds/             hand-kept reference data (official salary caps)
nfldb/db.py              SQLite read helpers for the app (returns pandas DataFrames)
```
