"""Download prices, run all five hypothesis tests, and write results.

    python scripts/run_all.py              # use cached prices in data/
    python scripts/run_all.py --refresh    # re-download from Yahoo Finance first

Outputs (all under results/ unless --out is given):
    summary.json            every headline number used in the README and the site
    var_backtest.csv        VaR violations and Kupiec / Christoffersen tests per model
    pit_tests.csv           PIT uniformity tests per model
    forecasts_<MKT>.csv.gz  daily out-of-sample PIT and VaR for every model
    gjr_skewt_params_<MKT>.csv GJR-skew-t parameters at each monthly refit
    data_quality.csv        every row the cleaner dropped or flagged
    run_log.csv             one line per stage: run_time, market, stage, status, count, error
and docs/data.json for the interactive page.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mdt import data as D  # noqa: E402
from mdt import forecast as F  # noqa: E402
from mdt import hypotheses as H  # noqa: E402


def _clean_json(o):
    if isinstance(o, dict):
        return {k: _clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean_json(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if not np.isfinite(f) else f
    if isinstance(o, np.integer):
        return int(o)
    return o


class RunLog:
    def __init__(self, path: Path):
        self.path = path
        self.t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.rows: list[dict] = []

    def add(self, market, stage, status, count=0, error=""):
        self.rows.append({"run_time": self.t, "market": market, "stage": stage,
                          "status": status, "count": count, "error": str(error)[:300]})
        print(f"[{market}] {stage}: {status} ({count}) {error}", flush=True)

    def write(self):
        df = pd.DataFrame(self.rows)
        if self.path.exists():
            df = pd.concat([pd.read_csv(self.path), df], ignore_index=True)
        df.to_csv(self.path, index=False)


def synthetic_prices(seed: int) -> pd.DataFrame:
    """Development-only stand-in for real prices (GARCH-t). Never written to results/."""
    from tests.helpers import simulate_garch_t

    r = simulate_garch_t(7000, seed=seed)
    close = 1000 * np.exp(r.cumsum())
    return pd.DataFrame({"close": close, "volume": 1.0}, index=r.index.rename("date"))


def site_payload(market, r, h1, h2, h3, h4, fc, var_df, pit_df) -> dict:
    mu, sd = h1["mean_daily"], h1["std_daily"]
    z = ((r - mu) / sd).to_numpy()
    edges = np.arange(-12, 12.0001, 0.25)
    counts, _ = np.histogram(np.clip(z, -11.99, 11.99), bins=edges)
    common = next(iter(fc.values())).index
    timeline = {"dates": [d.strftime("%Y-%m-%d") for d in common],
                "r": np.round(r.loc[common].to_numpy(), 5).tolist()}
    for name, f in fc.items():
        timeline[name] = np.round(f["q01"].to_numpy(), 5).tolist()
    return {
        "market": market,
        "name": D.MARKETS[market]["name"],
        "n": h1["n"], "start": h1["start"], "end": h1["end"],
        "z_hist": {"edges": edges.tolist(), "counts": counts.tolist()},
        "t_fit_z": {"nu": h2["nu"], "loc": (h2["loc"] - mu) / sd, "scale": h2["scale"] / sd},
        "h1": h1, "h2": h2, "h3": h3, "h4": h4,
        "var": var_df.to_dict(orient="records"),
        "pit": pit_df.to_dict(orient="records"),
        "timeline": timeline,
    }


def run_market(market, args, log, out_dir) -> dict | None:
    try:
        if args.synthetic:
            prices = synthetic_prices(seed=1 if market == "SPX" else 2)
        else:
            prices = D.load_or_fetch(market, ROOT / "data", refresh=args.refresh)
        prices, dq = D.clean(prices, market)
        r = D.log_returns(prices)
        log.add(market, "prices", "success", len(r))
    except Exception as e:
        log.add(market, "prices", "failed", 0, e)
        return None

    res = {"dq": dq}
    try:
        res["h1"] = H.h1_normal(r); res["h2"] = H.h2_student_t(r); res["h3"] = H.h3_bernoulli(r)
        log.add(market, "h1_h2_h3", "success", len(r))
    except Exception as e:
        log.add(market, "h1_h2_h3", "failed", 0, e); traceback.print_exc(); return None

    try:
        fc, params = F.all_forecasts(r)
        n_oos = len(next(iter(fc.values())))
        log.add(market, "forecasts", "success", n_oos)
    except Exception as e:
        log.add(market, "forecasts", "failed", 0, e); traceback.print_exc(); return None

    var_df = H.var_backtest(r, fc); var_df.insert(0, "market", market)
    pit_df = H.h5_uniform(fc); pit_df.insert(0, "market", market)
    res["h4"] = H.h4_poisson(r, fc["GARCH-t"])
    log.add(market, "h4_h5_var", "success", len(var_df))

    wide = pd.concat({k: v[["pit", "q01", "q05"]] for k, v in fc.items()}, axis=1)
    wide.columns = [f"{m}|{c}" for m, c in wide.columns]
    wide.insert(0, "r", r.loc[wide.index])
    wide.round(7).to_csv(out_dir / f"forecasts_{market}.csv.gz", compression="gzip")
    params.round(6).to_csv(out_dir / f"gjr_skewt_params_{market}.csv")
    res.update(var=var_df, pit=pit_df, params=params,
               site=site_payload(market, r, res["h1"], res["h2"], res["h3"], res["h4"],
                                 fc, var_df, pit_df))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download prices")
    ap.add_argument("--synthetic", action="store_true", help="dev mode: simulated prices")
    ap.add_argument("--out", default=None, help="output dir (default results/)")
    args = ap.parse_args()
    out_dir = Path(args.out) if args.out else ROOT / "results"
    if args.synthetic and not args.out:
        sys.exit("--synthetic requires --out so simulated numbers never land in results/")
    out_dir.mkdir(parents=True, exist_ok=True)
    site_path = (out_dir / "data.json") if args.synthetic else (ROOT / "docs" / "data.json")

    log = RunLog(out_dir / "run_log.csv")
    results = {m: run_market(m, args, log, out_dir) for m in D.MARKETS}
    ok = {m: v for m, v in results.items() if v is not None}

    if ok:
        pd.concat([v["var"] for v in ok.values()]).to_csv(out_dir / "var_backtest.csv", index=False)
        pit = pd.concat([v["pit"] for v in ok.values()])
        pit.drop(columns="hist").to_csv(out_dir / "pit_tests.csv", index=False)
        dq = [row for v in ok.values() for row in v["dq"]]
        pd.DataFrame(dq, columns=["market", "date", "action", "reason"]).to_csv(
            out_dir / "data_quality.csv", index=False)
        summary = {
            "generated_utc": log.t,
            "synthetic": bool(args.synthetic),
            "settings": {"window": F.WINDOW, "refit": F.REFIT, "hs_window": F.HS_WINDOW,
                         "tail_k": H.TAIL_K},
            "markets": {m: {"h1": v["h1"], "h2": v["h2"], "h3": v["h3"], "h4": v["h4"],
                            "data_quality_rows": len(v["dq"]),
                            "var": v["var"].to_dict(orient="records"),
                            "pit": v["pit"].drop(columns="hist").to_dict(orient="records")}
                        for m, v in ok.items()},
        }
        (out_dir / "summary.json").write_text(json.dumps(_clean_json(summary), indent=1))
        site = {"generated_utc": log.t, "synthetic": bool(args.synthetic),
                "settings": summary["settings"],
                "markets": {m: v["site"] for m, v in ok.items()}}
        site_path.parent.mkdir(parents=True, exist_ok=True)
        site_path.write_text(json.dumps(_clean_json(site), separators=(",", ":")))
        log.add("ALL", "write_outputs", "success", len(ok))
    log.write()
    if len(ok) < len(D.MARKETS):
        sys.exit(1)


if __name__ == "__main__":
    main()
