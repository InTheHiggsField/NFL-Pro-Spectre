"""Build (or refresh) the SQLite database from the downloaded files.

The files are parquet, so the build stages them in a scratch DuckDB file
(fast parquet reading, unnesting the contract data, the derived SQL), then
writes every table and view into data/nfl.sqlite as plain SQLite tables.
The scratch file is deleted at the end; the app only needs the SQLite file.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import duckdb

from . import catalog, config
from .download import FileResult, download_cpi, download_datasets

log = logging.getLogger(__name__)


def _sql_list(paths: list[Path]) -> str:
    return "[" + ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in paths) + "]"


def _load_table(con: duckdb.DuckDBPyConnection, table: str, paths: list[Path], fmt: str) -> int:
    if fmt == "csv":
        src = f"read_csv({_sql_list(paths)}, union_by_name = true, header = true)"
    else:
        src = f"read_parquet({_sql_list(paths)}, union_by_name = true)"
    try:
        con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM {src}')
    except duckdb.Error as exc:
        # Column types can drift between seasons; fall back to loading each
        # file and letting DuckDB widen the types as it stacks them.
        log.warning("  %s: combined load failed (%s); loading file by file", table, exc)
        con.execute(f'DROP TABLE IF EXISTS "{table}"')
        reader = "read_csv" if fmt == "csv" else "read_parquet"
        selects = " UNION ALL BY NAME ".join(
            f"SELECT * FROM {reader}('{p.as_posix()}')" for p in paths)
        con.execute(f'CREATE TABLE "{table}" AS {selects}')
    return con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def _load_cpi(con: duckdb.DuckDBPyConnection, path: Path | None) -> None:
    con.execute("CREATE OR REPLACE TABLE cpi_annual (year INTEGER, cpi_u DOUBLE, months INTEGER)")
    if path is None:
        return
    try:
        con.execute(f"""
            INSERT INTO cpi_annual
            SELECT year(obs_date::DATE) AS year, avg(cpi::DOUBLE), count(*)
            FROM read_csv('{path.as_posix()}', header = true, all_varchar = true,
                          names = ['obs_date', 'cpi'])
            WHERE cpi NOT IN ('.', '')
            GROUP BY 1 ORDER BY 1
        """)
    except duckdb.Error as exc:
        log.warning("Could not parse CPI file %s: %s", path, exc)


def _load_seeds(con: duckdb.DuckDBPyConnection) -> None:
    seed = (config.SEEDS_DIR / "salary_cap.csv").as_posix()
    con.execute(f"""
        CREATE OR REPLACE TABLE salary_cap_seed AS
        SELECT season::INTEGER AS season, TRY_CAST(salary_cap_musd AS DOUBLE) AS salary_cap_musd,
               NULLIF(note, '') AS note
        FROM read_csv('{seed}', header = true, all_varchar = true)
    """)


# Staging tables that are only inputs to the derived SQL.
_SKIP_EXPORT = {"contracts_raw", "salary_cap_seed"}

# Columns that get an index in SQLite when a table has them.
_INDEX_COLUMNS = [("player_id",), ("gsis_id",), ("otc_id",), ("game_id",), ("pfr_player_id",), ("pfr_id",),
                  ("full_name",), ("display_name",),
                  ("season", "week"), ("season", "team"), ("team", "season")]


def _sqlite_type(duck_type: str) -> str:
    t = duck_type.upper()
    if any(k in t for k in ("INT",)) and "[]" not in t and "STRUCT" not in t and "INTERVAL" not in t:
        return "INTEGER"
    if t == "BOOLEAN":
        return "INTEGER"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL"):
        return "REAL"
    return "TEXT"


def _export_expr(col: str, duck_type: str) -> str:
    """DuckDB expression that turns a column into something SQLite can store."""
    q = '"' + col.replace('"', '""') + '"'
    t = duck_type.upper()
    if t.endswith("]") or t.startswith(("STRUCT", "MAP", "UNION")):
        return f"to_json({q})::VARCHAR"          # lists/structs -> JSON text
    if t == "BOOLEAN":
        return f"{q}::INTEGER"
    if t.startswith("DECIMAL") or t == "HUGEINT":
        return f"{q}::DOUBLE"
    if t in ("DATE", "TIME") or t.startswith("TIMESTAMP") or t in ("UUID", "INTERVAL", "BLOB"):
        return f"{q}::VARCHAR"                    # ISO text
    return q


def _export_to_sqlite(con: duckdb.DuckDBPyConnection, sqlite_path: Path, batch: int = 50_000) -> None:
    objects = con.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' ORDER BY table_type, table_name
    """).fetchall()
    out = sqlite3.connect(sqlite_path)
    out.execute("PRAGMA journal_mode = OFF")
    out.execute("PRAGMA synchronous = OFF")
    for (name,) in objects:
        if name in _SKIP_EXPORT:
            continue
        cols = con.execute(f'DESCRIBE "{name}"').fetchall()  # (name, type, ...)
        col_names = [c[0] for c in cols]
        ddl = ", ".join('"' + c[0].replace('"', '""') + '" ' + _sqlite_type(c[1]) for c in cols)
        out.execute(f'CREATE TABLE "{name}" ({ddl})')
        select = ", ".join(_export_expr(c[0], c[1]) for c in cols)
        insert = f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})'
        cur = con.execute(f'SELECT {select} FROM "{name}"')
        n = 0
        while rows := cur.fetchmany(batch):
            out.executemany(insert, rows)
            n += len(rows)
        out.commit()
        for idx_cols in _INDEX_COLUMNS:
            if all(c in col_names for c in idx_cols):
                idx = f'ix_{name}_{"_".join(idx_cols)}'
                out.execute(f'CREATE INDEX "{idx}" ON "{name}" ({", ".join(idx_cols)})')
        log.info("  sqlite %-22s %10s rows", name, f"{n:,}")
    out.execute("ANALYZE")
    out.commit()
    out.close()


def build(last_season: int | None = None, refresh: str = "current", include_pbp: bool = False,
          workers: int = 8) -> Path:
    """Download what is needed and rebuild data/nfl.sqlite.

    refresh: 'none' uses only cached files, 'current' re-downloads the current
    season and the files nflverse rewrites all year, 'all' re-downloads everything.
    """
    started = time.time()
    last_season = last_season or config.current_season()
    datasets = [d for d in catalog.DATASETS if include_pbp or not d.optional]

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %d datasets through season %d into %s", len(datasets), last_season, config.RAW_DIR)
    results = download_datasets(datasets, last_season, refresh=refresh, workers=workers)
    if refresh == "none":
        cached = config.RAW_DIR / "cpi" / "cpi_u_monthly.csv"
        cpi_path = cached if cached.exists() else None
    else:
        cpi_path = download_cpi()

    by_table: dict[str, list[FileResult]] = defaultdict(list)
    for r in results:
        if r.path.exists():
            by_table[catalog.get(r.dataset).table].append(r)
    problems = [r for r in results if r.status in ("missing", "failed") and not r.path.exists()]

    # Stage in a scratch DuckDB file, write a temporary SQLite file, then swap it
    # in at the end, so a failed build never leaves a half-written database.
    staging = config.STAGING_PATH
    for p in (staging, Path(str(staging) + ".wal")):
        p.unlink(missing_ok=True)
    con = duckdb.connect(str(staging))
    con.execute("SET preserve_insertion_order = false")

    counts: dict[str, int] = {}
    for table, files in sorted(by_table.items()):
        fmt = catalog.get(files[0].dataset).fmt
        paths = sorted({f.path for f in files})
        counts[table] = _load_table(con, table, paths, fmt)
        log.info("  %-22s %10s rows from %d file(s)", table, f"{counts[table]:,}", len(paths))

    _load_seeds(con)
    _load_cpi(con, cpi_path)
    con.execute((config.SQL_DIR / "derived.sql").read_text(encoding="utf-8"))
    for t in ("contracts", "player_cap_seasons", "salary_cap", "team_aliases", "franchises", "cpi_annual"):
        counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        log.info("  %-22s %10s rows (derived)", t, f"{counts[t]:,}")

    con.execute("CREATE OR REPLACE TABLE _build_info (key VARCHAR, value VARCHAR)")
    info = {
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "last_season": str(last_season),
        "datasets": ",".join(d.name for d in datasets),
        "missing_files": ";".join(f"{r.dataset}:{r.season}" for r in problems),
    }
    con.executemany("INSERT INTO _build_info VALUES (?, ?)", list(info.items()))

    log.info("Writing SQLite database")
    tmp_db = config.DB_PATH.with_suffix(".building.sqlite")
    tmp_db.unlink(missing_ok=True)
    _export_to_sqlite(con, tmp_db)
    con.close()
    for p in (staging, Path(str(staging) + ".wal")):
        p.unlink(missing_ok=True)

    try:
        os.replace(tmp_db, config.DB_PATH)
    except PermissionError as exc:
        raise SystemExit(
            f"Built {tmp_db} but could not replace {config.DB_PATH}: it is open somewhere "
            "(PyCharm database tool, DB Browser, a notebook, the dashboard). Close it and run again."
        ) from exc

    expected_missing = [r for r in problems if r.season is not None and r.season >= last_season]
    other_missing = [r for r in problems if r not in expected_missing]
    if expected_missing:
        log.info("Not published yet (normal mid-season): %s",
                 ", ".join(f"{r.dataset} {r.season}" for r in expected_missing))
    if other_missing:
        log.warning("Could not get: %s", ", ".join(f"{r.dataset} {r.season or ''}" for r in other_missing))
    log.info("Done in %.0fs -> %s", time.time() - started, config.DB_PATH)
    return config.DB_PATH
