"""Download dataset files into a local cache (data/raw/<dataset>/...)."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests

from . import config
from .catalog import CPI_URL, Dataset

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "nfl-pro-spectre-data/1.0 (python-requests)"}


@dataclass
class FileResult:
    dataset: str
    season: int | None
    path: Path
    status: str  # downloaded | cached | missing | failed


def local_path(ds: Dataset, season: int | None) -> Path:
    ext = "csv" if ds.fmt == "csv" else "parquet"
    name = f"{ds.name}_{season}.{ext}" if season is not None else f"{ds.name}.{ext}"
    return config.RAW_DIR / ds.name / name


def _fetch(url: str, dest: Path, retries: int = 4) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=_HEADERS, stream=True, timeout=(15, 120)) as r:
                if r.status_code == 404:
                    return "missing"
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            return "downloaded"
        except (requests.RequestException, OSError) as exc:
            if attempt == retries:
                log.warning("Failed %s: %s", url, exc)
                tmp.unlink(missing_ok=True)
                return "failed"
            time.sleep(2 ** attempt)
    return "failed"


def _needs_refresh(ds: Dataset, season: int | None, refresh: str, cur: int) -> bool:
    """refresh: 'none' (cache only), 'current' (this season + living files), 'all'."""
    if refresh == "all":
        return True
    if refresh == "current":
        # Single files (players, games, contracts...) are rewritten by nflverse
        # all season, so they count as current too.
        return season is None or season >= cur
    return False


def download_datasets(datasets: list[Dataset], last_season: int, refresh: str = "current",
                      workers: int = 8) -> list[FileResult]:
    cur = config.current_season()
    jobs: list[tuple[Dataset, int | None, str, Path]] = []
    results: list[FileResult] = []
    for ds in datasets:
        for season, url in ds.urls(last_season):
            dest = local_path(ds, season)
            if dest.exists() and not _needs_refresh(ds, season, refresh, cur):
                results.append(FileResult(ds.name, season, dest, "cached"))
            else:
                jobs.append((ds, season, url, dest))

    log.info("%d files cached, %d to download", len(results), len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch, url, dest): (ds, season, dest) for ds, season, url, dest in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            ds, season, dest = futures[fut]
            status = fut.result()
            if status in ("missing", "failed") and dest.exists():
                status = "cached"  # keep the older copy we already had
            results.append(FileResult(ds.name, season, dest, status))
            if i % 25 == 0 or i == len(jobs):
                log.info("  downloaded %d/%d", i, len(jobs))
    return results


def download_cpi(refresh: bool = True) -> Path | None:
    dest = config.RAW_DIR / "cpi" / "cpi_u_monthly.csv"
    if dest.exists() and not refresh:
        return dest
    status = _fetch(CPI_URL, dest, retries=2)
    if status != "downloaded" and not dest.exists():
        log.warning("Could not download CPI from FRED; CPI inflation columns will be empty. "
                    "Cap-based inflation still works.")
        return None
    return dest
