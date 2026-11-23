"""Build the static copy of the website for GitHub Pages.

    python build_site.py                          # writes site/, for previewing at http://localhost:8001
    python -m http.server 8001 -d site            # preview it
    python build_site.py --base /NFL-Pro-Spectre/ # what the GitHub Actions workflow runs

The site is served from https://<user>.github.io/<repo>/, so links need the
repo name as a prefix (--base). The workflow in .github/workflows/pages.yml
fills it in automatically. The live site (python run_web.py) is unchanged.
"""
import argparse
import logging
from pathlib import Path

from web import static_site

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="output folder (emptied first), default site/")
    ap.add_argument("--base", default="/", help="URL path the site is served from, e.g. /NFL-Pro-Spectre/")
    ap.add_argument("--workers", type=int, default=None, help="processes for player pages (default: CPU count)")
    ap.add_argument("--limit-players", type=int, default=None, help="only render this many player pages (quick test)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    static_site.build(Path(args.out), base=args.base, workers=args.workers, limit_players=args.limit_players)
