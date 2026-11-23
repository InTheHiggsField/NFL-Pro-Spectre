"""Next-season outlook: will a player have a strong or a weak season?

The model predicts a player's true value next season (see true_value.py) from
this season and last season: production, snaps, age, experience, draft slot,
position, and the true value of both seasons. Comparing the prediction with
the player's current level gives the outlook:

    Strong  projected at least 15% above this season's value
    Weak    projected at least 15% below
    Steady  in between
    Depth   worth under 1% of the cap either way (backups, special teamers)

It is checked with a walk-forward backtest: to predict season Y, the model is
trained only on seasons before Y, exactly like predicting a real future season.
Only players who played the next season are in the training data, so the
model predicts how well a player will play, not whether he will be on a roster.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

from . import features as F

OUTLOOK_BAND = 0.15
MIN_VALUE_FOR_OUTLOOK = 0.01  # 1% of the cap
BACKTEST_FROM = 2016
MIN_GAMES_FOR_TARGET = 4  # a next season with fewer games is mostly an injury story
FEATURES = F.FEATURES + ["tv", "prev_tv"]


def pairs(feats: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """Rows of (season t features, season t+1 true value)."""
    tv = values[["gsis_id", "season", "true_value_pct", "games", "partial_season"]]
    df = feats.merge(tv[["gsis_id", "season", "true_value_pct"]].rename(columns={"true_value_pct": "tv"}),
                     on=["gsis_id", "season"], how="left")
    prev = tv[["gsis_id", "season", "true_value_pct"]].rename(columns={"true_value_pct": "prev_tv"})
    df = df.merge(prev.assign(season=prev["season"] + 1), on=["gsis_id", "season"], how="left")
    nxt = tv.rename(columns={"true_value_pct": "next_tv", "games": "next_games", "partial_season": "next_partial"})
    df = df.merge(nxt.assign(season=nxt["season"] - 1), on=["gsis_id", "season"], how="left")
    return df


def make_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="squared_error", learning_rate=0.05, max_iter=500, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0, categorical_features="from_dtype",
        early_stopping=True, validation_fraction=0.15, n_iter_no_change=40, random_state=11)


def _trainable(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["next_tv"].notna() & (df["next_games"] >= MIN_GAMES_FOR_TARGET)
              & ~df["partial_season"] & (df["next_partial"] != True)]  # noqa: E712


def fit(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    m = make_model()
    m.fit(train[FEATURES], np.sqrt(train["next_tv"] * 100))
    return m


def predict(model, X: pd.DataFrame) -> np.ndarray:
    return np.clip(model.predict(X[FEATURES]), 0, None) ** 2 / 100


def outlook(projected: pd.Series, current: pd.Series) -> pd.Series:
    """Strong / Steady / Weak. Players worth under 1% of the cap either way are
    depth players, where a percentage swing means little: they get "Depth"."""
    ratio = projected / current.clip(lower=0.002)
    labels = np.select([np.maximum(projected, current) < MIN_VALUE_FOR_OUTLOOK,
                        ratio >= 1 + OUTLOOK_BAND, ratio <= 1 - OUTLOOK_BAND],
                       ["Depth", "Strong", "Weak"], "Steady")
    return pd.Series(labels, index=projected.index)


def backtest(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Walk-forward: for each target season, train on everything before it."""
    usable = _trainable(df)
    rows = []
    for target in range(BACKTEST_FROM, int(usable["season"].max()) + 2):
        base = target - 1
        train = usable[usable["season"] < base]
        test = usable[usable["season"] == base]
        if test.empty or len(train) < 2000:
            continue
        m = fit(train)
        rows.append(test.assign(projected_tv=predict(m, test)))
    bt = pd.concat(rows)
    y, p, naive = bt["next_tv"], bt["projected_tv"], bt["tv"]
    actual = outlook(y, naive)
    called = outlook(bt["projected_tv"], naive)
    strong_calls = called == "Strong"
    weak_calls = called == "Weak"
    by_season = {}
    for s, g in bt.groupby("season"):
        by_season[int(s) + 1] = {"n": int(len(g)), "r2": round(r2_score(g["next_tv"], g["projected_tv"]), 3),
                                 "naive_r2": round(r2_score(g["next_tv"], g["tv"]), 3)}
    metrics = {
        "backtest_seasons": f"{BACKTEST_FROM}-{int(bt['season'].max()) + 1}",
        "player_seasons": int(len(bt)),
        "r2": round(r2_score(y, p), 3),
        "mae_pct_of_cap": round(mean_absolute_error(y, p) * 100, 2),
        "naive_r2_same_as_this_season": round(r2_score(y, naive), 3),
        "naive_mae_pct_of_cap": round(mean_absolute_error(y, naive) * 100, 2),
        "strong_calls": int(strong_calls.sum()),
        "strong_hit_rate": round(float((actual[strong_calls] == "Strong").mean()), 3),
        "strong_wrong_way_rate": round(float((actual[strong_calls] == "Weak").mean()), 3),
        "weak_calls": int(weak_calls.sum()),
        "weak_hit_rate": round(float((actual[weak_calls] == "Weak").mean()), 3),
        "weak_wrong_way_rate": round(float((actual[weak_calls] == "Strong").mean()), 3),
        "base_rate_strong": round(float((actual[actual != "Depth"] == "Strong").mean()), 3),
        "base_rate_weak": round(float((actual[actual != "Depth"] == "Weak").mean()), 3),
        "by_target_season": by_season,
    }
    return bt, metrics


def run(feats: pd.DataFrame, values: pd.DataFrame):
    """Backtest, fit on all history, and project next season for everyone in the latest season."""
    df = pairs(feats, values)
    bt, metrics = backtest(df)
    model = fit(_trainable(df))
    # Forecast from the latest complete season (the current season, which can be
    # compared with how it is going) and, if a season is in progress, from it too.
    complete = df.loc[~df["partial_season"], "season"].max()
    bases = sorted({int(complete), int(df["season"].max())})
    now = df[df["season"].isin(bases)].copy()
    now["projected_tv"] = predict(model, now)
    proj = pd.concat([bt.assign(kind="backtest"), now.assign(kind="forecast")])
    out = proj[["gsis_id", "season", "pos_group", "age", "tv", "projected_tv", "next_tv", "kind"]].copy()
    out["pos_group"] = out["pos_group"].astype(str)
    out["projected_season"] = out["season"] + 1
    out["outlook"] = outlook(out["projected_tv"], out["tv"])
    out["actual_outlook"] = np.where(out["next_tv"].notna(), outlook(out["next_tv"], out["tv"]), None)
    return model, metrics, out.rename(columns={"season": "base_season", "tv": "base_value_pct",
                                               "projected_tv": "projected_value_pct",
                                               "next_tv": "actual_value_pct"})
