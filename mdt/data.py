"""Price download, cleaning and log returns.

Prices come from Yahoo Finance via yfinance (index close levels, no dividends).
Every cleaning decision is written to results/data_quality.csv so it can be audited.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

MARKETS = {
    "SPX": {"ticker": "^GSPC", "name": "S&P 500", "start": "1990-01-01"},
    "KOSPI": {"ticker": "^KS11", "name": "KOSPI", "start": "1997-01-01"},
}

# A one-day index move larger than this is treated as a data error unless it is
# on the allow-list below. Neither index has a genuine close-to-close move this large
# in the sample window.
MAX_ABS_LOG_RETURN = 0.15


def download(ticker: str, start: str, retries: int = 3) -> pd.DataFrame:
    """Download daily OHLCV with exponential backoff. Returns columns close, volume."""
    import yfinance as yf

    last_err = None
    for attempt in range(retries):
        try:
            df = yf.download(ticker, start=start, auto_adjust=False, progress=False,
                             actions=False, threads=False)
            if df is None or df.empty:
                raise RuntimeError(f"empty response for {ticker}")
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            out = pd.DataFrame({"close": df["Close"], "volume": df["Volume"]})
            out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
            out.index.name = "date"
            return out.dropna(subset=["close"])
        except Exception as err:  # network or parsing failure
            last_err = err
            time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"download failed for {ticker}: {last_err}")


def clean(prices: pd.DataFrame, market: str) -> tuple[pd.DataFrame, list[dict]]:
    """Remove stale prints and impossible moves. Returns cleaned prices and an audit log."""
    log: list[dict] = []
    df = prices.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    # Weekend rows occasionally appear in Yahoo index data.
    weekend = df.index.dayofweek >= 5
    for d in df.index[weekend]:
        log.append({"market": market, "date": d.date(), "action": "drop", "reason": "weekend row"})
    df = df[~weekend]

    # Stale prints: close identical to the previous close AND no volume reported.
    # These are holiday/carry-forward rows, not real zero-return trading days.
    same = df["close"].eq(df["close"].shift(1))
    novol = df["volume"].fillna(0).eq(0)
    stale = same & novol
    for d in df.index[stale]:
        log.append({"market": market, "date": d.date(), "action": "drop",
                    "reason": "unchanged close with zero volume (stale print)"})
    df = df[~stale]

    r = np.log(df["close"]).diff()
    bad = r.abs() > MAX_ABS_LOG_RETURN
    for d, v in r[bad].items():
        log.append({"market": market, "date": d.date(), "action": "flag",
                    "reason": f"abs log return {v:+.4f} exceeds {MAX_ABS_LOG_RETURN}"})
    return df, log


def log_returns(prices: pd.DataFrame) -> pd.Series:
    r = np.log(prices["close"]).diff().dropna()
    r = r[r.abs() <= MAX_ABS_LOG_RETURN]
    r.name = "r"
    return r


def load_or_fetch(market: str, data_dir: Path, refresh: bool) -> pd.DataFrame:
    """Use the cached CSV unless refresh is requested or no cache exists."""
    path = data_dir / f"{market}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, index_col="date", parse_dates=True)
    spec = MARKETS[market]
    df = download(spec["ticker"], spec["start"])
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df
