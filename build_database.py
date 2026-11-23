"""Build or refresh the NFL database.

    python build_database.py              # first run: download everything, build data/nfl.sqlite
    python build_database.py              # later runs: refresh only the current season
    python build_database.py --offline    # rebuild from files already downloaded
    python build_database.py --full-refresh
    python build_database.py --pbp        # also every play since 1999 (~350 MB more)
"""
from __future__ import annotations

import argparse
import logging

from nfldb import build, config, db


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last-season", type=int, default=None,
                    help=f"newest season to fetch (default: current, {config.current_season()})")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--offline", action="store_true", help="use only files already downloaded")
    g.add_argument("--full-refresh", action="store_true", help="re-download every file")
    ap.add_argument("--pbp", action="store_true", help="include play-by-play data (large)")
    ap.add_argument("--workers", type=int, default=8, help="parallel downloads")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    refresh = "none" if args.offline else "all" if args.full_refresh else "current"
    build.build(last_season=args.last_season, refresh=refresh, include_pbp=args.pbp, workers=args.workers)
    try:
        from analytics import pipeline
        if pipeline.retrain_if_trained():
            logging.info("Retrained the value and projection models on the new data")
    except ImportError as exc:  # scikit-learn not installed yet
        logging.info("Skipping models (%s). Install requirements and run train_models.py", exc)
    print()
    print(db.tables().to_string(index=False))


if __name__ == "__main__":
    main()
