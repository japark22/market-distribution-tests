"""Write README.md from results/summary.json so every number in it traces to a file.

    python scripts/build_readme.py [--summary results/summary.json] [--out README.md]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = "https://japark22.github.io/market-distribution-tests/"
REPO = "https://github.com/japark22/market-distribution-tests"


def p(x):
    if x is None:
        return "n/a"
    return "<0.0001" if x < 1e-4 else f"{x:.4f}"


def pct(x, d=2):
    return f"{x * 100:.{d}f}%"


def fig(name, alt):
    return (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="figures/{name}-dark.png">\n'
            f'  <img src="figures/{name}.png" alt="{alt}" width="100%">\n</picture>')


def verdicts(m):
    h1, h2, h3 = m["h1"], m["h2"], m["h3"]
    h4 = m["h4"]["raw"][str(m["h4"]["threshold_k"])]
    var = {r["model"]: r for r in m["var"] if r["alpha"] == 0.01}
    pit = {r["model"]: r for r in m["pit"]}
    out = {}
    out["H1"] = "rejected" if h1["jb_p"] < 0.05 else "not rejected"
    if h2["aic_t"] < h2["aic_normal"] and var["Student-t"]["christoffersen_p"] > 0.05:
        out["H2"] = "holds"
    elif h2["aic_t"] < h2["aic_normal"]:
        out["H2"] = "shape yes, timing no"
    else:
        out["H2"] = "rejected"
    if h3["markov_p"] > 0.05 and h3["runs_p"] > 0.05:
        out["H3"] = "holds"
    elif abs(h3["acf_sign"][0]) < 0.05:
        out["H3"] = "statistically rejected, economically tiny"
    else:
        out["H3"] = "rejected"
    out["H4"] = "rejected" if h4["dispersion_p"] < 0.05 else "not rejected"
    g = pit["GARCH-t"]
    if g["berkowitz_p"] > 0.05:
        out["H5"] = "GARCH-t passes"
    elif g["ks_stat"] < pit["Normal"]["ks_stat"] / 2:
        out["H5"] = "GARCH-t much closer, not perfect"
    else:
        out["H5"] = "rejected for all models"
    return out


def build(s: dict) -> str:
    mk = s["markets"]
    codes = list(mk)
    names = {"SPX": "S&P 500", "KOSPI": "KOSPI"}
    V = {c: verdicts(mk[c]) for c in codes}
    st = s["settings"]

    def row(label, test, f, h):
        cells = " | ".join(f"{f(mk[c])}<br>**{V[c][h]}**" for c in codes)
        return f"| {label} | {test} | {cells} |"

    def v99(m, model):
        return next(r for r in m["var"] if r["model"] == model and r["alpha"] == 0.01)

    def pitm(m, model):
        return next(r for r in m["pit"] if r["model"] == model)

    head = " | ".join(f"{names.get(c, c)} ({mk[c]['h1']['start'][:4]}–{mk[c]['h1']['end'][:4]}, "
                      f"{mk[c]['h1']['n']:,} days)" for c in codes)
    k = str(s["markets"][codes[0]]["h4"]["threshold_k"])
    glance = "\n".join([
        f"| Hypothesis | Test | {head} |",
        "|---|---|" + "---|" * len(codes),
        row("**H1 Normal**: daily returns are Normal", "Jarque-Bera, tail counts",
            lambda m: f"excess kurtosis {m['h1']['excess_kurtosis']:.1f}; "
                      f"{m['h1']['tails'][1]['observed']} days beyond 4σ vs "
                      f"{m['h1']['tails'][1]['expected_normal']:.1f} expected", "H1"),
        row("**H2 Student-t**: a fat-tailed t fits instead", "MLE ν, AIC, VaR clustering",
            lambda m: f"ν = {m['h2']['nu']:.1f}; ΔAIC = {m['h2']['aic_normal'] - m['h2']['aic_t']:,.0f} "
                      f"for t; static-t VaR breaches cluster (p = {p(v99(m, 'Student-t')['christoffersen_p'])})",
            "H2"),
        row("**H3 Bernoulli**: up/down is a memoryless coin", "runs test, Markov χ²",
            lambda m: f"P(up after up) {m['h3']['p_up_after_up']:.3f} vs after down "
                      f"{m['h3']['p_up_after_down']:.3f}; Markov p = {p(m['h3']['markov_p'])}", "H3"),
        row(f"**H4 Poisson**: >{k}σ days arrive independently", "dispersion test on monthly counts",
            lambda m: f"variance ÷ mean = {m['h4']['raw'][k]['dispersion']:.2f} (Poisson: 1); "
                      f"after GARCH-t filtering {m['h4']['garch_filtered']['dispersion']:.2f}", "H4"),
        row("**H5 Uniform**: forecast PITs are U(0,1)", "KS, Berkowitz",
            lambda m: f"KS distance: static Normal {pitm(m, 'Normal')['ks_stat']:.3f} → GARCH-t "
                      f"{pitm(m, 'GARCH-t')['ks_stat']:.3f}; Berkowitz p = "
                      f"{p(pitm(m, 'GARCH-t')['berkowitz_p'])}", "H5"),
    ])

    var_rows = []
    for c in codes:
        for r in mk[c]["var"]:
            if r["alpha"] != 0.01:
                continue
            var_rows.append(f"| {names.get(c, c)} | {r['model']} | {r['violations']} | {r['expected']:.0f} | "
                            f"{pct(r['rate'])} | {p(r['kupiec_p'])} | {p(r['christoffersen_p'])} |")
    pit_rows = []
    for c in codes:
        for r in mk[c]["pit"]:
            pit_rows.append(f"| {names.get(c, c)} | {r['model']} | {r['ks_stat']:.4f} | {p(r['berkowitz_p'])} | "
                            f"{pct(r['below_1pct'])} | {pct(r['above_99pct'])} |")

    def h1_line(c):
        h = mk[c]["h1"]
        yrs = h["worst_normal_wait_years"]
        wait = f"{yrs:.1e} years" if yrs and yrs > 1e4 else (f"{yrs:,.0f} years" if yrs else "never")
        return (f"- **{names.get(c, c)}**: worst day {h['worst_date']} at {h['worst_return'] * 100:.1f}% "
                f"(z = {h['worst_z']:.1f}). Under a Normal, a day that bad should come once every {wait}. "
                f"Excess kurtosis falls from {h['aggregation'][0]['excess_kurtosis']:.1f} (daily) to "
                f"{h['aggregation'][1]['excess_kurtosis']:.1f} (weekly) and "
                f"{h['aggregation'][2]['excess_kurtosis']:.1f} (monthly).")

    def h3_line(c):
        h = mk[c]["h3"]
        return (f"- **{names.get(c, c)}**: lag-1 autocorrelation of the sign is {h['acf_sign'][0]:+.3f}; "
                f"of the absolute return it is {h['acf_abs'][0]:+.3f}. Ljung-Box(10) p-value: "
                f"sign {p(h['ljung_box10_sign_p'])}, size {p(h['ljung_box10_abs_p'])}.")

    def h4_line(c):
        h = mk[c]["h4"]
        raw, oos, f = h["raw"][k], h["raw_oos_window"], h["garch_filtered"]
        return (f"- **{names.get(c, c)}**: {raw['events']} days beyond {k}σ across {raw['months']} months. "
                f"Months with none: {pct(raw['zero_months_observed'], 0)} observed vs "
                f"{pct(raw['zero_months_poisson'], 0)} under Poisson. Busiest month: {raw['max_month']} days "
                f"(Poisson probability of that many or more: {raw['p_max_or_more_poisson']:.1e}). "
                f"On the out-of-sample window the dispersion is {oos['dispersion']:.2f} for raw σ-events and "
                f"{f['dispersion']:.2f} for GARCH-t tail events (p = {p(f['dispersion_p'])}).")

    sens = []
    for c in codes:
        r = mk[c]["h4"]["raw"]
        sens.append(f"{names.get(c, c)}: " + ", ".join(f"{kk}σ → {v['dispersion']:.2f}" for kk, v in r.items()))

    story = ""
    return f"""# market-distribution-tests

**Five textbook distributions, each turned into a claim about markets and tested out-of-sample on
S&P 500 and KOSPI daily returns.**

Normal, Student-t, Bernoulli, Poisson and Uniform are usually taught as shapes to draw. Here each one
becomes a falsifiable hypothesis with a proper test, and the results are tied together by one
mechanism: volatility clustering. Forecasts for day *t* use only data up to *t*−1, and a unit test
enforces it.

**Interactive version: [{PAGES.replace('https://', '')}]({PAGES})**

{fig("hero", "Log-density of standardised daily returns with Normal and Student-t fits")}

## Research at a glance

{glance}

{story}

## H1 Normal and H2 Student-t: the tails

{h1_line(codes[0])}
{h1_line(codes[1]) if len(codes) > 1 else ''}

Fat tails shrink as returns are aggregated, but a Student-t with ν around
{', '.join(f"{mk[c]['h2']['nu']:.1f} ({names.get(c, c)})" for c in codes)} describes the daily shape far
better than the Normal. The real test is a forecast. Five models produce a one-day 99% VaR every day,
out-of-sample:

{fig("var_breaches", "99% VaR breaches divided by expected, per model")}

| Market | Model | Breaches | Expected | Rate | Kupiec p (right rate) | Christoffersen p (no clustering) |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(var_rows)}

Where the breaches land in time shows why the unconditional models fail: they arrive in bursts
during stress periods.

{fig("breach_timeline", "Timeline of VaR breaches per model")}

## H3 Bernoulli: is each day a coin flip?

{h3_line(codes[0])}
{h3_line(codes[1]) if len(codes) > 1 else ''}

{fig("bernoulli", "Autocorrelation of return sign vs absolute return")}

## H4 Poisson: do big moves arrive independently?

Threshold fixed in advance at {k}σ (full-sample σ). Counts are per calendar month.

{h4_line(codes[0])}
{h4_line(codes[1]) if len(codes) > 1 else ''}

Sensitivity of the dispersion ratio to the threshold: {'; '.join(sens)}.

{fig("poisson", "Monthly counts of large moves vs Poisson")}

## H5 Uniform: are the forecasts calibrated?

The probability integral transform F<sub>t|t−1</sub>(r<sub>t</sub>) of a correct forecast is
Uniform(0,1) and independent over time (Diebold, Gunther and Tay, 1998). The Berkowitz (2001)
test checks mean, variance and autocorrelation of Φ<sup>−1</sup>(PIT) jointly.

{fig("pit", "PIT histograms per model")}

| Market | Model | KS distance | Berkowitz p | PIT < 1% | PIT > 99% |
|---|---|---:|---:|---:|---:|
{chr(10).join(pit_rows)}

With several thousand out-of-sample days these tests have a lot of power, so even a good model can be
rejected for small deviations. Read the KS distance and the tail frequencies as effect sizes.

## Method

| | |
|---|---|
| Data | Yahoo Finance index closes (`^GSPC`, `^KS11`), daily log returns. Index levels, not total return. |
| Cleaning | Weekend rows and unchanged-close zero-volume rows (stale holiday prints) dropped; any move above 15% flagged and excluded. Every dropped row is listed in `results/data_quality.csv`. |
| Static models | Normal and Student-t (MLE) on the trailing {st['window']} days; historical simulation on the trailing {st['hs_window']} days. |
| GARCH | GARCH(1,1), constant mean, Normal or standardised-t shocks (`arch`). Re-estimated every {st['refit']} days on the trailing {st['window']} days; variance filtered forward daily with fixed parameters. |
| Point-in-time | Every forecast for day *t* uses returns dated *t*−1 or earlier. `tests/test_forecast.py` multiplies all returns after a cut-off by 5 and checks that earlier forecasts do not move. |
| VaR tests | Kupiec (1995) unconditional coverage, Christoffersen (1998) independence. |
| Refresh | GitHub Actions re-downloads prices, reruns the tests, and regenerates this README, the figures and the site every week. |

Last run: {s['generated_utc']}. All numbers above are read from [`results/summary.json`](results/summary.json).

## Limitations

- **Index levels without dividends.** This understates the mean by roughly the dividend yield, which
  barely matters for one-day tails but does shift the share of up days slightly.
- **Single-name price limits.** Korean stocks had a ±15% daily limit until June 2015 (±30% since),
  which can truncate KOSPI index tails in the earlier years.
- **Many tests, large samples.** Some p-values will be small by chance or for economically trivial
  deviations. The figures and effect sizes matter more than any single p-value.
- **Model set is deliberately simple.** No asymmetric GARCH, no realised-volatility inputs, no
  regime switching. The point is the chain of hypotheses, not the best VaR model.

## Reproduce

```bash
pip install -r requirements.txt
python scripts/run_all.py --refresh   # download prices, run every test (about 3 minutes)
python scripts/make_figures.py
python scripts/build_readme.py
pytest -q                             # simulation-based tests, including the no-lookahead check
```

```
mdt/forecast.py      rolling out-of-sample predictive distributions (5 models)
mdt/hypotheses.py    H1 to H5 tests, Kupiec, Christoffersen, Berkowitz
mdt/data.py          download, cleaning audit, log returns
scripts/             run_all, make_figures, build_readme
docs/                GitHub Pages site (index.html + data.json)
results/             summary.json, CSV outputs, run_log.csv
tests/               known-distribution simulations and the lookahead test
```

## References

- Berkowitz, J. (2001). Testing density forecasts, with applications to risk management. *JBES*.
- Christoffersen, P. (1998). Evaluating interval forecasts. *International Economic Review*.
- Cont, R. (2001). Empirical properties of asset returns: stylized facts and statistical issues. *Quantitative Finance*.
- Diebold, F., Gunther, T., Tay, A. (1998). Evaluating density forecasts. *International Economic Review*.
- Kupiec, P. (1995). Techniques for verifying the accuracy of risk measurement models. *Journal of Derivatives*.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=str(ROOT / "results" / "summary.json"))
    ap.add_argument("--out", default=str(ROOT / "README.md"))
    a = ap.parse_args()
    s = json.loads(Path(a.summary).read_text())
    if s.get("synthetic") and Path(a.out).resolve() == (ROOT / "README.md").resolve():
        raise SystemExit("refusing to write README.md from synthetic results")
    Path(a.out).write_text(build(s))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
