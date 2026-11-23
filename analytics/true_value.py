"""True value: what a season of production is worth on the open market.

How it is learned
-----------------
Every veteran contract on file (not a rookie draft deal) is a price the market
paid. We pair each contract with the player's season *before* signing (the
"platform" season) and the one before that, and learn

    platform-season stats, snaps, age, experience, position  ->  APY as % of the cap

with gradient-boosted trees. Expressing price as a share of the cap removes
cap growth (cap-based inflation), so a 2014 deal and a 2025 deal are comparable.

Age
---
Age is measured against how long careers at the position usually last
(``years_left``). The model may never value a player higher for having less
runway, and a market-learned runway factor discounts players past the usual
career end (see TrueValueModel).

Only the season before signing and the one before that are used: an MVP
year five seasons ago counts for nothing.

How it is used
--------------
For any player-season, the model answers "if this player hit free agency after
this season, what share of the cap would he get?". That is the season's true
value. Times today's cap it becomes today's dollars. Subtract what the player
actually cost that season and you get surplus value: the bargain score.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_absolute_error, r2_score

from . import features as F

TEST_FROM_YEAR = 2023  # contracts signed from this year on are held out for testing


def training_set(con, feats: pd.DataFrame) -> pd.DataFrame:
    contracts = pd.read_sql_query("""
        SELECT contract_id, gsis_id, player, position, team, year_signed, years, apy_musd, apy_cap_pct,
               draft_year
        FROM contracts
        WHERE gsis_id IS NOT NULL AND apy_cap_pct > 0 AND year_signed IS NOT NULL
          AND (draft_year IS NULL OR year_signed > draft_year)   -- drop rookie draft deals
    """, con)
    contracts["season"] = contracts["year_signed"] - 1  # platform season
    df = contracts.merge(feats, on=["gsis_id", "season"], how="inner")
    # A contract signed mid-season (an extension) still lines up with the prior season.
    # Keep only platform seasons that are complete.
    df = df[~df["partial_season"]]
    # If a player signed twice in a year (released and re-signed), keep the bigger deal.
    df = df.sort_values("apy_cap_pct").drop_duplicates(["gsis_id", "year_signed"], keep="last")
    return df.reset_index(drop=True)


# Raw age is replaced by age measured against the position's typical career
# (years_left, career_progress in features.py), so a 33-year-old kicker and a
# 33-year-old running back are not treated alike.
TV_FEATURES = [c for c in F.FEATURES if c != "age"]
# Holding production equal, more career runway can never lower value.
MONOTONIC = {"years_left": 1}


def make_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="squared_error", learning_rate=0.05, max_iter=600, max_leaf_nodes=31,
        min_samples_leaf=25, l2_regularization=1.0, categorical_features="from_dtype",
        monotonic_cst=MONOTONIC,
        early_stopping=True, validation_fraction=0.15, n_iter_no_change=40, random_state=7)


def _target(y_pct: pd.Series) -> np.ndarray:
    # Prices are skewed (many minimum deals, few huge ones); model sqrt of cap %.
    return np.sqrt(y_pct.clip(lower=0) * 100)


def _untarget(y: np.ndarray) -> np.ndarray:
    return np.clip(y, 0, None) ** 2 / 100


class TrueValueModel:
    """The stats model plus a career-runway adjustment learned from the market.

    Old players who still produce are rare, and the ones in the data (Brady, Brees)
    are exceptional, so trees alone undercharge for age. The runway curve fixes
    that from the market itself: for contracts the model did not train on, it
    measures how actual price compares with the stats-only price at each
    years_left, and fits a curve that can only rise with runway and never exceeds 1.
    """

    def __init__(self, model: HistGradientBoostingRegressor, runway: IsotonicRegression):
        self.model, self.runway = model, runway

    def stats_value(self, X: pd.DataFrame) -> np.ndarray:
        return _untarget(self.model.predict(X[TV_FEATURES]))

    def runway_factor(self, X: pd.DataFrame) -> np.ndarray:
        yl = X["years_left"].fillna(0).to_numpy()
        # Only players past the position's usual career end are discounted.
        return np.where(yl >= 0, 1.0, self.runway.predict(np.minimum(yl, 0)))

    def predict_pct(self, X: pd.DataFrame) -> np.ndarray:
        return self.stats_value(X) * self.runway_factor(X)


def _fit_runway(data: pd.DataFrame) -> IsotonicRegression:
    """Market price / stats price by years_left, from out-of-fold predictions."""
    oof = np.zeros(len(data))
    for tr, te in GroupKFold(5).split(data, groups=data["gsis_id"]):
        m = make_model().fit(data.iloc[tr][TV_FEATURES], _target(data.iloc[tr]["apy_cap_pct"]))
        oof[te] = _untarget(m.predict(data.iloc[te][TV_FEATURES]))
    use = oof > 0.01  # ratios on minimum-salary deals are noise
    ratio = (data["apy_cap_pct"].to_numpy()[use] / oof[use]).clip(0, 3)
    # Players with plenty of runway define "no adjustment": normalise to their level.
    yl = data["years_left"].fillna(0).to_numpy()[use]
    ratio = ratio / np.average(ratio[yl >= 0], weights=oof[use][yl >= 0])
    iso = IsotonicRegression(y_min=0.4, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(yl, ratio, sample_weight=oof[use])
    return iso


def fit(train: pd.DataFrame) -> TrueValueModel:
    model = make_model().fit(train[TV_FEATURES], _target(train["apy_cap_pct"]))
    return TrueValueModel(model, _fit_runway(train))


def predict_pct(model: TrueValueModel, X: pd.DataFrame) -> np.ndarray:
    return model.predict_pct(X)


def runway_table(model: TrueValueModel) -> list[dict]:
    return [{"years_left": y, "factor": round(float(model.runway.predict([y])[0]), 3)}
            for y in range(-12, 3)]


def evaluate(con, feats: pd.DataFrame) -> tuple[TrueValueModel, dict]:
    """Train on older contracts, test on the newest ones, then refit on everything."""
    data = training_set(con, feats)
    train = data[data["year_signed"] < TEST_FROM_YEAR]
    test = data[data["year_signed"] >= TEST_FROM_YEAR]
    model = fit(train)
    pred = predict_pct(model, test)
    stats_only = model.stats_value(test)
    old = test["years_left"] < -4
    # Baseline: every player gets his position's median price in the training data.
    med = train.groupby("pos_group", observed=True)["apy_cap_pct"].median()
    base = test["pos_group"].map(med).astype(float).fillna(train["apy_cap_pct"].median())
    by_pos = {}
    for pos, g in test.assign(pred=pred).groupby("pos_group", observed=True):
        if len(g) >= 20:
            by_pos[str(pos)] = {"n": int(len(g)), "r2": round(r2_score(g["apy_cap_pct"], g["pred"]), 3),
                                "mae_pct_of_cap": round(mean_absolute_error(g["apy_cap_pct"], g["pred"]) * 100, 2)}
    metrics = {
        "train_contracts": int(len(train)), "test_contracts": int(len(test)),
        "train_years": f"{int(train['year_signed'].min())}-{TEST_FROM_YEAR - 1}",
        "test_years": f"{TEST_FROM_YEAR}-{int(test['year_signed'].max())}",
        "r2": round(r2_score(test["apy_cap_pct"], pred), 3),
        "mae_pct_of_cap": round(mean_absolute_error(test["apy_cap_pct"], pred) * 100, 2),
        "spearman": round(pd.Series(pred).corr(test["apy_cap_pct"].reset_index(drop=True), method="spearman"), 3),
        "baseline_r2_position_median": round(r2_score(test["apy_cap_pct"], base), 3),
        "baseline_mae_pct_of_cap": round(mean_absolute_error(test["apy_cap_pct"], base) * 100, 2),
        "veterans_past_career_end": {
            "n": int(old.sum()),
            "overpriced_by_stats_only_pct": round(float(stats_only[old].sum() / test.loc[old, "apy_cap_pct"].sum() - 1) * 100, 1),
            "overpriced_with_runway_pct": round(float(pred[old].sum() / test.loc[old, "apy_cap_pct"].sum() - 1) * 100, 1),
        },
        "by_position": by_pos,
        "seasons_used": "the season before signing and the one before that; nothing older",
    }
    final = fit(data)
    metrics["runway_curve"] = runway_table(final)
    metrics["career_norms"] = F.career_norms(con).to_dict("records")
    return final, metrics


# Teams can't pay a player on his rookie deal market price, so the market data
# says "young = cheap". For true value we want the production's open-market
# worth, so every player is scored as if he had at least this much experience.
SCORING_MIN_EXPERIENCE = 4


def score(con, model, feats: pd.DataFrame) -> pd.DataFrame:
    """True value for every player-season, with actual cost and surplus."""
    out = feats[["gsis_id", "season", "team", "pos_group", "age", "games", "partial_season",
                 "off_pct", "def_pct"]].copy()
    out["pos_group"] = out["pos_group"].astype(str)
    as_veteran = feats.assign(years_exp=feats["years_exp"].clip(lower=SCORING_MIN_EXPERIENCE))
    out["stats_value_pct"] = model.stats_value(as_veteran)
    out["runway_factor"] = model.runway_factor(as_veteran)
    out["true_value_pct"] = out["stats_value_pct"] * out["runway_factor"]
    out["years_left"] = feats["years_left"].round(1)
    cap = pd.read_sql_query("SELECT season, cap_for_adjustment AS cap, cap_factor_to_latest FROM salary_cap", con)
    cost = pd.read_sql_query("SELECT gsis_id, season, cap_hit_musd, cap_pct FROM v_player_cap_by_season", con)
    out = out.merge(cap, on="season", how="left").merge(cost, on=["gsis_id", "season"], how="left")
    latest_cap = cap.sort_values("season")["cap"].iloc[-1]
    out["true_value_musd"] = out["true_value_pct"] * out["cap"]
    out["true_value_today_musd"] = out["true_value_pct"] * latest_cap
    out["cap_hit_today_musd"] = out["cap_pct"] * latest_cap
    out["surplus_pct"] = out["true_value_pct"] - out["cap_pct"]
    out["surplus_today_musd"] = out["surplus_pct"] * latest_cap
    # Percentile within position and season: 100 = most valuable at his position that year.
    out["value_pctile"] = (out.groupby(["season", "pos_group"])["true_value_pct"]
                           .rank(pct=True).mul(100).round(0))
    return out.drop(columns=["cap", "cap_factor_to_latest"])
