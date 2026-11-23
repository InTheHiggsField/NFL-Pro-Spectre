"""Train both models, save them, and write their scores into data/nfl.sqlite.

Tables written:
    player_value       true value, cost and surplus for every player-season since 2012
    player_projection  next-season projections (walk-forward backtest + current forecasts)
    model_metrics      test results for both models, as JSON
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3

import joblib
import pandas as pd

from nfldb import config

from . import features as F
from . import projection as P
from . import true_value as TV

log = logging.getLogger(__name__)

MODEL_DIR = config.DATA_DIR / "models"
TV_PATH = MODEL_DIR / "true_value.joblib"
PROJ_PATH = MODEL_DIR / "projection.joblib"


def _write(con: sqlite3.Connection, name: str, df: pd.DataFrame, index_cols: list[str]) -> None:
    df.to_sql(name, con, if_exists="replace", index=False, chunksize=5000)
    for cols in index_cols:
        con.execute(f'CREATE INDEX IF NOT EXISTS "ix_{name}_{cols.replace(", ", "_")}" ON "{name}" ({cols})')


def train(con: sqlite3.Connection | None = None) -> dict:
    own = con is None
    con = con or sqlite3.connect(config.DB_PATH)
    log.info("Building features")
    feats = F.build(con)
    log.info("  %d player-seasons, %d features", len(feats), len(F.FEATURES))

    log.info("Training true-value model")
    tv_model, tv_metrics = TV.evaluate(con, feats)
    log.info("  test R2 %.3f (position-median baseline %.3f), MAE %.2f%% of cap",
             tv_metrics["r2"], tv_metrics["baseline_r2_position_median"], tv_metrics["mae_pct_of_cap"])
    values = TV.score(con, tv_model, feats)

    log.info("Training next-season model (walk-forward backtest)")
    proj_model, proj_metrics, proj = P.run(feats, values)
    log.info("  backtest R2 %.3f (same-as-last-season baseline %.3f)",
             proj_metrics["r2"], proj_metrics["naive_r2_same_as_this_season"])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(tv_model, TV_PATH)
    joblib.dump(proj_model, PROJ_PATH)

    metrics = {"trained_at": dt.datetime.now().isoformat(timespec="seconds"),
               "true_value": tv_metrics, "projection": proj_metrics}
    _write_tables(con, values, proj, metrics)
    if own:
        con.close()
    return metrics


def _write_tables(con, values: pd.DataFrame, proj: pd.DataFrame, metrics: dict) -> None:
    _write(con, "player_value", values, ["gsis_id, season", "season, pos_group"])
    _write(con, "player_projection", proj, ["gsis_id, base_season", "base_season, pos_group"])
    con.execute("DROP TABLE IF EXISTS model_metrics")
    con.execute("CREATE TABLE model_metrics (model TEXT, metrics TEXT, trained_at TEXT)")
    con.executemany("INSERT INTO model_metrics VALUES (?, ?, ?)",
                    [(k, json.dumps(v), metrics["trained_at"]) for k, v in metrics.items() if k != "trained_at"])
    con.commit()
    log.info("Wrote player_value (%d rows), player_projection (%d rows)", len(values), len(proj))


def retrain_if_trained(con: sqlite3.Connection | None = None) -> bool:
    """After a database rebuild, retrain on the new data so the model tables come back.

    Only runs if the models were trained before (about 20 seconds). Returns False otherwise.
    """
    if not (TV_PATH.exists() and PROJ_PATH.exists()):
        return False
    train(con)
    return True
