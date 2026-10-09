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


def peq(x):
    """'p = 0.0123' or 'p < 0.0001'."""
    return "p < 0.0001" if x is not None and x < 1e-4 else f"p = {p(x)}"


def pct(x, d=2):
    return f"{x * 100:.{d}f}%"


def fig(name, alt):
    return (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="figures/{name}-dark.png">\n'
            f'  <img src="figures/{name}.png" alt="{alt}" width="100%">\n</picture>')


LADDER = [  # model, what it adds over the rung before
    ("Normal", "baseline: Normal fitted to the last 1000 days"),
    ("Student-t", "fat tails (shape)"),
    ("Hist. sim.", "no shape assumption (last 250 days)"),
    ("GARCH-N", "volatility clustering (timing)"),
    ("GARCH-t", "clustering + fat tails"),
    ("GJR-skew-t", "clustering + fat tails + asymmetry (bad news raises volatility more; heavier left tail)"),
]


def var_pass(m, model, alpha=0.01):
    r = next(x for x in m["var"] if x["model"] == model and x["alpha"] == alpha)
    return r["kupiec_p"] > 0.05 and r["christoffersen_p"] > 0.05


def calibrated(m, model):
    """Passes the 99% VaR coverage and independence tests and Berkowitz on the full PIT."""
    pit = next(x for x in m["pit"] if x["model"] == model)
    return var_pass(m, model) and pit["berkowitz_p"] > 0.05


def verdicts(m):
    h1, h2, h3 = m["h1"], m["h2"], m["h3"]
    h4 = m["h4"]["raw"][str(m["h4"]["threshold_k"])]
    out = {}
    out["H1"] = "rejected" if h1["jb_p"] < 0.05 else "not rejected"
    if h2["aic_t"] < h2["aic_normal"]:
        out["H2"] = "shape yes, timing no" if not var_pass(m, "Student-t") else "holds"
    else:
        out["H2"] = "rejected"
    if h3["markov_p"] > 0.05 and h3["runs_p"] > 0.05:
        out["H3"] = "memoryless (but biased toward up days)" if h3["binom_p_vs_half"] < 0.05 else "holds"
    elif abs(h3["acf_sign"][0]) < 0.05:
        out["H3"] = "rejected, but the memory is tiny"
    else:
        out["H3"] = "rejected"
    if h4["dispersion_p"] < 0.05:
        out["H4"] = ("rejected; clustering explains it" if m["h4"]["garch_filtered"]["dispersion"] < 1.5
                     else "rejected")
    else:
        out["H4"] = "not rejected"
    ok = [name for name, _ in LADDER if calibrated(m, name)]
    out["H5"] = ("calibrated: " + ", ".join(ok)) if ok else "no model calibrated"
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
            lambda m: f"ν = {m['h2']['nu']:.1f}; AIC favours t by {m['h2']['aic_normal'] - m['h2']['aic_t']:,.0f}; "
                      f"static-t 99% VaR breaches {pct(v99(m, 'Student-t')['rate'])} of days, clustered "
                      f"(Christoffersen {peq(v99(m, 'Student-t')['christoffersen_p'])})",
            "H2"),
        row("**H3 Bernoulli**: up/down is a memoryless coin", "runs test, Markov χ²",
            lambda m: f"P(up after up) {m['h3']['p_up_after_up']:.3f} vs after down "
                      f"{m['h3']['p_up_after_down']:.3f}; Markov {peq(m['h3']['markov_p'])}", "H3"),
        row(f"**H4 Poisson**: >{k}σ days arrive independently", "dispersion test on monthly counts",
            lambda m: f"variance ÷ mean = {m['h4']['raw'][k]['dispersion']:.2f} (Poisson: 1); "
                      f"after GARCH-t filtering {m['h4']['garch_filtered']['dispersion']:.2f}", "H4"),
        row("**H5 Uniform**: forecast PITs are U(0,1)", "Berkowitz + 99% VaR coverage and independence",
            lambda m: f"GJR-skew-t: {v99(m, 'GJR-skew-t')['violations']} breaches vs "
                      f"{v99(m, 'GJR-skew-t')['expected']:.0f} expected, Berkowitz "
                      f"{peq(pitm(m, 'GJR-skew-t')['berkowitz_p'])}", "H5"),
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
        return (f"- **{names.get(c, c)}**: worst day {h['worst_date']} at {h['worst_simple_return'] * 100:+.1f}% "
                f"(z = {h['worst_z']:.1f}); best day {h['best_date']} at {h['best_simple_return'] * 100:+.1f}%. "
                f"Under a Normal, a day as bad as the worst should come once every {wait}. "
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

    a, b = codes[0], codes[-1]
    A, B = mk[a], mk[b]
    t4 = A["h1"]["tails"][1]
    ok_all = [name for name, _ in LADDER if all(calibrated(mk[c], name) for c in codes)]
    va = {m_: v99(A, m_) for m_, _ in LADDER}
    gt = pitm(A, "GARCH-t")
    if ok_all:
        last = (f"The last rung, **GJR-GARCH with skewed-t shocks**, lets bad news raise volatility more than "
                f"good news and gives the left tail more weight. "
                + ("It is the only model" if ok_all == ["GJR-skew-t"]
                   else f"The models that pass ({', '.join(ok_all)}) are the ones")
                + " whose 99% VaR passes both the Kupiec and Christoffersen tests "
                f"and whose PITs pass Berkowitz in both markets: "
                + "; ".join(f"{names.get(c, c)}: {v99(mk[c], 'GJR-skew-t')['violations']} breaches vs "
                            f"{v99(mk[c], 'GJR-skew-t')['expected']:.0f} expected" for c in codes) + ".")
    else:
        last = "No model in the ladder passes all three calibration tests in both markets."
    story = (
        f"**The chain of results.** Returns are **not Normal**: {names.get(a, a)} daily excess kurtosis is "
        f"{A['h1']['excess_kurtosis']:.1f}, with {t4['observed']} days beyond 4σ where a Normal expects "
        f"{t4['expected_normal']:.1f}. A Student-t **fixes the shape** (KS distance "
        f"{A['h2']['ks_normal']:.3f} → {A['h2']['ks_t']:.3f}), but a static t still breaches its 99% VaR on "
        f"{pct(va['Student-t']['rate'])} of days, and in bunches. The cause is **clustering**: the direction "
        f"of a day is close to a coin flip (lag-1 sign autocorrelation {A['h3']['acf_sign'][0]:+.3f}), its "
        f"size is not ({A['h3']['acf_abs'][0]:+.3f}). Clustering also **breaks Poisson**: monthly counts of "
        f"large moves have variance {A['h4']['raw'][k]['dispersion']:.1f}× their mean, and "
        f"{A['h4']['garch_filtered']['dispersion']:.2f}× once a GARCH-t filter takes the clustering out. "
        f"Adding volatility dynamics reduces the bunching (most breaches in any 10 days: "
        f"{va['Student-t']['max_cluster']} for the static t, {va['GARCH-t']['max_cluster']} for GARCH-t) but not "
        f"the bias: GARCH-t still breaches on {pct(va['GARCH-t']['rate'])} of days, and its misses are "
        f"one-sided (PIT below 1%: "
        f"{pct(gt['below_1pct'])}, above 99%: {pct(gt['above_99pct'])}). {last}"
    )
    ladder_rows = []
    for name, adds in LADDER:
        cells = []
        for c in codes:
            r = v99(mk[c], name)
            cells.append(f"{pct(r['rate'])} {'✅' if calibrated(mk[c], name) else '❌'}")
        ladder_rows.append(f"| {name} | {adds} | " + " | ".join(cells) + " |")
    ladder = "\n".join([
        "| Model | What it adds | " + " | ".join(f"{names.get(c, c)}: 99% breach rate (target 1.00%)"
                                              for c in codes) + " |",
        "|---|---|" + "---:|" * len(codes),
        *ladder_rows,
    ])
    gjr = {}
    for c in codes:
        path = ROOT / "results" / f"gjr_skewt_params_{c}.csv"
        if path.exists():
            import csv
            rows = list(csv.DictReader(path.open()))
            med = lambda key: sorted(float(r_[key]) for r_ in rows)[len(rows) // 2]
            gjr[c] = {kk: med(kk) for kk in ("alpha", "gamma", "beta", "lambda")}
    gjr_line = ""
    if gjr:
        gjr_line = ("Median GJR-skew-t parameters across monthly refits ("
                    + "; ".join(f"{names.get(c, c)}: α = {v['alpha']:.3f}, γ = {v['gamma']:.3f}, "
                                f"β = {v['beta']:.3f}, skew λ = {v['lambda']:+.3f}" for c, v in gjr.items())
                    + "). α near zero with a large γ means volatility responds mainly to *down* days, and "
                    "λ < 0 is a heavier left tail: the two asymmetries the symmetric models were missing.")
    t5 = {c: mk[c]["h2"]["tails"][2] for c in codes}
    nu_note = ("But one ν cannot fit every regime: "
               + ", ".join(f"ν = {mk[c]['h2']['nu']:.1f} for {names.get(c, c)}" for c in codes)
               + (" is" if len(codes) == 1 else " are") + " below 3, where the t has no finite fourth moment, and the fitted t over-predicts the "
               "most extreme days (beyond 5σ: "
               + "; ".join(f"{names.get(c, c)} {t5[c]['observed']} observed vs {t5[c]['expected_t']:.0f} "
                           f"predicted" for c in codes)
               + "). A single static t is averaging calm and stressed periods, which is a sign that "
               "volatility moves over time."
               if all(mk[c]["h2"]["nu"] < 3 for c in codes) else "")
    dq_path = ROOT / "results" / "data_quality.csv"
    big, bad = 0, 0
    if dq_path.exists():
        import csv
        for row_ in csv.DictReader(dq_path.open()):
            if row_["action"] == "keep":
                big += 1
                bad += "possible bad print" in row_["reason"]
    ext = "; ".join(f"{names.get(c, c)} worst {mk[c]['h1']['worst_date']} ({mk[c]['h1']['worst_simple_return'] * 100:+.1f}%), "
                    f"best {mk[c]['h1']['best_date']} ({mk[c]['h1']['best_simple_return'] * 100:+.1f}%)" for c in codes)
    extremes_line = (f"- **Extreme days are kept, not trimmed.** {ext}. `results/data_quality.csv` lists all {big} "
                     f"moves above 10%; {bad} of them reverse the next day (the signature of a bad print). "
                     "Recent extremes carry a lot of weight in tail statistics, so results can move when the "
                     "weekly refresh adds a crisis.")
    def pass95(c):
        return [x["model"] for x in mk[c]["var"] if x["alpha"] == 0.05
                and x["kupiec_p"] > 0.05 and x["christoffersen_p"] > 0.05]

    def cov95(c):
        return [x["model"] for x in mk[c]["var"] if x["alpha"] == 0.05 and x["kupiec_p"] > 0.05]

    v95_line = ("At the 95% level the picture is different, because the fat tails matter less that close to "
                "the centre. Models with the right breach rate (Kupiec p > 0.05): "
                + "; ".join(f"{names.get(c, c)}: {', '.join(cov95(c)) or 'none'}" for c in codes)
                + ". Models that also pass Christoffersen: "
                + "; ".join(f"{names.get(c, c)}: {', '.join(pass95(c)) or 'none'}" for c in codes)
                + ". GJR-skew-t at 95%: ")
    v95 = "; ".join(f"{names.get(c, c)} {next(x for x in mk[c]['var'] if x['model'] == 'GJR-skew-t' and x['alpha'] == 0.05)['violations']} vs "
                    f"{next(x for x in mk[c]['var'] if x['model'] == 'GJR-skew-t' and x['alpha'] == 0.05)['expected']:.0f}, "
                    f"Kupiec p = {p(next(x for x in mk[c]['var'] if x['model'] == 'GJR-skew-t' and x['alpha'] == 0.05)['kupiec_p'])}"
                    for c in codes)
    v95_line += v95 + "."
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
better than the Normal. {nu_note} The real test is a forecast. Six models, each adding one ingredient, produce a
one-day 99% VaR every day, out-of-sample. ✅ means the model passes Kupiec, Christoffersen and Berkowitz
(all p > 0.05).

{ladder}

{gjr_line}

{fig("var_breaches", "99% VaR breaches divided by expected, per model")}

| Market | Model | Breaches | Expected | Rate | Kupiec p (right rate) | Christoffersen p (no clustering) |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(var_rows)}

{v95_line}

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
Historical simulation has the smallest KS distance but still fails in the tails, which is why the
calibration verdict also requires the 99% VaR tests. (Its PIT is a mid-rank among 250 past returns while
its VaR is an interpolated quantile, so its two 1% rates differ slightly.)

## Method

| | |
|---|---|
| Data | Yahoo Finance index closes (`^GSPC`, `^KS11`), daily log returns. Index levels, not total return. |
| Cleaning | Weekend rows and unchanged-close zero-volume rows (stale holiday prints) dropped. Moves above 10% are kept and listed in `results/data_quality.csv` for review. |
| Static models | Normal and Student-t (MLE) on the trailing {st['window']} days; historical simulation on the trailing {st['hs_window']} days. |
| GARCH | GARCH(1,1) with Normal or standardised-t shocks, and GJR-GARCH(1,1) with Hansen skewed-t shocks; constant mean (`arch`). Re-estimated every {st['refit']} days on the trailing {st['window']} days; variance filtered forward daily with fixed parameters. |
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
- **One passing model is not a proven model.** GJR-skew-t passing at 99% is one level, two markets,
  and a handful of tests; it was added after the symmetric models failed, so treat it as the next
  hypothesis, not a final answer. No realised-volatility inputs or regime switching are tried.
{extremes_line}

## Reproduce

```bash
pip install -r requirements.txt
python scripts/run_all.py --refresh   # download prices, run every test (about 3 minutes)
python scripts/make_figures.py
python scripts/build_readme.py
pytest -q                             # simulation-based tests, including the no-lookahead check
```

```
mdt/forecast.py      rolling out-of-sample predictive distributions (6 models)
mdt/hypotheses.py    H1 to H5 tests, Kupiec, Christoffersen, Berkowitz
mdt/data.py          download, cleaning audit, log returns
scripts/             run_all, make_figures, build_readme
docs/                GitHub Pages site (index.html + data.json)
results/             summary.json, CSV outputs, GJR-skew-t parameter paths, run_log.csv
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
