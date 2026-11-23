"""Train the true-value and next-season models and write their scores into the database.

    python train_models.py

Run it after building the database. Later runs of build_database.py retrain
automatically once this has been run once.
"""
import json
import logging

from analytics import pipeline

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    metrics = pipeline.train()
    tv, pr = metrics["true_value"], metrics["projection"]
    print()
    print("True value model (stats -> market price as % of cap)")
    print(f"  tested on {tv['test_contracts']} contracts signed {tv['test_years']}")
    print(f"  R2 {tv['r2']}   (baseline: position median price {tv['baseline_r2_position_median']})")
    print(f"  typical error {tv['mae_pct_of_cap']}% of the cap")
    print("Next-season model")
    print(f"  walk-forward backtest {pr['backtest_seasons']}, {pr['player_seasons']} player-seasons")
    print(f"  R2 {pr['r2']}   (baseline: same as this season {pr['naive_r2_same_as_this_season']})")
    print(f"  'Strong' calls right {pr['strong_hit_rate']:.0%} of the time (base rate {pr['base_rate_strong']:.0%})")
    print(f"  'Weak' calls right {pr['weak_hit_rate']:.0%} of the time (base rate {pr['base_rate_weak']:.0%})")
