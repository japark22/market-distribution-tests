"""Render README figures (light and dark variants) from docs/data.json.

    python scripts/make_figures.py [--site docs/data.json] [--out figures]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781",
                  grid="#e1e0d9", axis="#c3c2b7", data="#2a78d6", normal="#eb6834",
                  t="#1baf7a", neutral="#c3c2b7"),
    "dark": dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", muted="#898781",
                 grid="#2c2c2a", axis="#383835", data="#3987e5", normal="#d95926",
                 t="#199e70", neutral="#5a5955"),
}
MODELS = ["Normal", "Student-t", "Hist. sim.", "GARCH-N", "GARCH-t", "GJR-skew-t"]
HIGHLIGHT = "GJR-skew-t"  # the last rung of the model ladder


def style(th):
    plt.rcParams.update({
        "figure.facecolor": th["surface"], "axes.facecolor": th["surface"],
        "savefig.facecolor": th["surface"], "axes.edgecolor": th["axis"],
        "axes.labelcolor": th["ink2"], "xtick.color": th["muted"], "ytick.color": th["muted"],
        "text.color": th["ink"], "axes.grid": True, "grid.color": th["grid"],
        "grid.linewidth": 0.8, "grid.linestyle": "-", "axes.spines.top": False,
        "axes.spines.right": False, "font.family": "DejaVu Sans", "font.size": 10,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.axisbelow": True, "legend.frameon": False,
    })


def tpdf_z(x, fit):
    return stats.t.pdf(x, fit["nu"], fit["loc"], fit["scale"])


def fig_hero(site, th, path):
    mk = site["markets"]
    fig, axes = plt.subplots(1, len(mk), figsize=(11, 4.4), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, (code, m) in zip(axes, mk.items()):
        e = np.array(m["z_hist"]["edges"]); c = np.array(m["z_hist"]["counts"])
        w = np.diff(e); mid = e[:-1] + w / 2
        dens = c / (c.sum() * w)
        keep = c > 0
        x = np.linspace(-12, 12, 800)
        ax.plot(x, stats.norm.pdf(x), color=th["normal"], lw=2, solid_capstyle="round")
        ax.plot(x, tpdf_z(x, m["t_fit_z"]), color=th["t"], lw=2, solid_capstyle="round")
        ax.scatter(mid[keep], dens[keep], s=22, color=th["data"], edgecolor=th["surface"],
                   linewidth=1.2, zorder=3)
        ax.set_yscale("log"); ax.set_ylim(1e-6, 1); ax.set_xlim(-12, 12)
        ax.set_xlabel("daily return, in standard deviations")
        h1 = m["h1"]
        ax.set_title(f"{m['name']}  ·  {h1['start'][:4]}–{h1['end'][:4]}  ·  {h1['n']:,} days")
        # direct labels
        ax.annotate("Normal", xy=(4.6, stats.norm.pdf(4.6)), xytext=(5.3, 3e-6),
                    color=th["ink2"], fontsize=9,
                    arrowprops=dict(arrowstyle="-", color=th["muted"], lw=0.8))
        xt = 6.0
        ax.annotate(f"Student-t (ν = {m['t_fit_z']['nu']:.1f})", xy=(xt, tpdf_z(xt, m["t_fit_z"])),
                    xytext=(5.0, 1e-2), color=th["ink2"], fontsize=9,
                    arrowprops=dict(arrowstyle="-", color=th["muted"], lw=0.8))
        wz = h1["worst_z"]
        one_day = 1.0 / (c.sum() * w[0])  # density of a single observation in one bin
        ax.annotate(f"worst day {h1['worst_date']}\n{h1['worst_simple_return']*100:.1f}% (z = {wz:.1f})",
                    xy=(max(wz, -11.9), one_day * 0.8), xytext=(-11.5, 2e-2), fontsize=9,
                    color=th["ink2"], arrowprops=dict(arrowstyle="-", color=th["muted"], lw=0.8))
    axes[0].set_ylabel("density (log scale)")
    axes[0].scatter([], [], s=22, color=th["data"], label="observed")
    axes[0].plot([], [], color=th["normal"], lw=2, label="Normal")
    axes[0].plot([], [], color=th["t"], lw=2, label="Student-t (MLE)")
    axes[0].legend(loc="upper left", fontsize=9, labelcolor=th["ink2"])
    fig.tight_layout()
    fig.savefig(path, dpi=160); plt.close(fig)


def fig_var(site, th, path):
    mk = site["markets"]
    fig, axes = plt.subplots(1, len(mk), figsize=(11, 3.8), sharey=True, sharex=True)
    top = max(r["violations"] / r["expected"] for m in mk.values() for r in m["var"] if r["alpha"] == 0.01)
    axes = np.atleast_1d(axes)
    for ax, (code, m) in zip(axes, mk.items()):
        rows = {r["model"]: r for r in m["var"] if r["alpha"] == 0.01}
        ratio = [rows[k]["violations"] / rows[k]["expected"] for k in MODELS]
        y = np.arange(len(MODELS))[::-1]
        ax.barh(y, ratio, height=0.5, color=[th["data"] if k == HIGHLIGHT else th["neutral"]
                                              for k in MODELS])
        ax.axvline(1.0, color=th["ink2"], lw=1)
        for yi, k, rt in zip(y, MODELS, ratio):
            r = rows[k]
            flag = []
            if r["kupiec_p"] < 0.05:
                flag.append("wrong rate")
            if r["christoffersen_p"] < 0.05:
                flag.append("clustered")
            note = ", ".join(flag) if flag else "passes both tests"
            ax.text(rt + 0.04, yi, f"{r['violations']} vs {r['expected']:.0f}  ·  {note}",
                    va="center", fontsize=8.5, color=th["ink2"])
        ax.set_yticks(y, MODELS); ax.set_xlim(0, max(3.2, top * 1.9))
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("99% VaR breaches ÷ expected  (1.0 = correct)")
        ax.set_title(f"{m['name']}  ·  out-of-sample {m['timeline']['dates'][0][:4]}–"
                     f"{m['timeline']['dates'][-1][:4]}")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def fig_rug(site, th, path):
    """When do 99% VaR breaches happen? One row of ticks per model."""
    mk = site["markets"]
    fig, axes = plt.subplots(len(mk), 1, figsize=(11, 1.5 + 1.25 * len(mk) * 1.6), sharex=False)
    axes = np.atleast_1d(axes)
    for ax, (code, m) in zip(axes, mk.items()):
        tl = m["timeline"]
        dates = np.array(tl["dates"], dtype="datetime64[D]")
        r = np.array(tl["r"])
        for i, k in enumerate(MODELS[::-1]):
            q = np.array(tl[k])
            hit = r < q
            col = th["data"] if k == HIGHLIGHT else th["ink2"]
            ax.vlines(dates[hit], i - 0.32, i + 0.32, color=col, lw=0.9)
        ax.set_yticks(range(len(MODELS)), MODELS[::-1]); ax.set_ylim(-0.6, len(MODELS) - 0.4)
        ax.set_xlim(dates[0] - np.timedelta64(60, "D"), dates[-1] + np.timedelta64(60, "D"))
        ax.grid(axis="y", visible=False)
        ax.set_title(f"{m['name']}: each tick is a day the loss exceeded that model's 99% VaR")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def fig_pit(site, th, path):
    mk = site["markets"]
    fig, axes = plt.subplots(len(mk), len(MODELS), figsize=(12.5, 2.3 * len(mk) + 0.4),
                             sharey=True)
    axes = np.atleast_2d(axes)
    for row, (code, m) in enumerate(mk.items()):
        pits = {p["model"]: p for p in m["pit"]}
        for col, k in enumerate(MODELS):
            ax = axes[row, col]
            h = np.array(pits[k]["hist"]); n = h.sum(); rel = h / (n / len(h))
            x = (np.arange(len(h)) + 0.5) / len(h)
            ax.bar(x, rel, width=1 / len(h) - 0.008,
                   color=th["data"] if k == HIGHLIGHT else th["neutral"])
            ax.axhline(1.0, color=th["ink2"], lw=1)
            ax.set_xlim(0, 1); ax.set_ylim(0, 2.2); ax.set_xticks([0, 0.5, 1])
            ax.grid(axis="x", visible=False)
            ax.set_title(k if row == 0 else "", fontsize=10)
            ax.text(0.5, 2.05, f"KS {pits[k]['ks_stat']:.3f}", ha="center", fontsize=8.5,
                    color=th["ink2"])
            if col == 0:
                ax.set_ylabel(f"{m['name']}\nfrequency ÷ uniform")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def fig_poisson(site, th, path):
    mk = site["markets"]
    fig, axes = plt.subplots(1, len(mk), figsize=(11, 3.6), sharey=False)
    axes = np.atleast_1d(axes)
    for ax, (code, m) in zip(axes, mk.items()):
        h4 = m["h4"]; raw = h4["raw"][str(h4["threshold_k"])]
        obs = np.array(raw["count_hist_observed"]); exp = np.array(raw["count_hist_poisson"])
        k = np.arange(len(obs))
        ax.bar(k[obs > 0], obs[obs > 0], width=0.55, color=th["data"], label="observed months")
        ax.plot(k, exp, color=th["neutral"], lw=2, marker="o", ms=5,
                markeredgecolor=th["surface"], label="Poisson, same mean")
        ax.set_yscale("log"); ax.set_ylim(1e-3, obs.max() * 3)
        ax.set_xticks(k[:: 2 if len(k) > 12 else 1]); ax.grid(axis="x", visible=False)
        ax.set_xlabel(f"days per month with |move| > {h4['threshold_k']}σ")
        ax.set_ylabel("months (log scale)")
        ax.set_title(f"{m['name']}  ·  variance ÷ mean = {raw['dispersion']:.1f} "
                     f"(Poisson: 1.0)")
        ax.legend(fontsize=9, labelcolor=th["ink2"])
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def fig_bernoulli(site, th, path):
    mk = site["markets"]
    fig, axes = plt.subplots(1, len(mk), figsize=(11, 3.4), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, (code, m) in zip(axes, mk.items()):
        h3 = m["h3"]; L = np.arange(1, len(h3["acf_sign"]) + 1)
        b = h3["acf_band"]
        ax.axhspan(-b, b, color=th["grid"], lw=0)
        ax.axhline(0, color=th["axis"], lw=1)
        ax.text(0.7, -b * 0.9, "95% band if there is no memory", fontsize=8, color=th["muted"],
                va="bottom")
        ax.plot(L, h3["acf_abs"], color=th["normal"], lw=2, marker="o", ms=4,
                markeredgecolor=th["surface"])
        ax.plot(L, h3["acf_sign"], color=th["data"], lw=2, marker="o", ms=4,
                markeredgecolor=th["surface"])
        ax.text(L[-1] + 0.3, h3["acf_abs"][-1], "size |r|", va="center", fontsize=9,
                color=th["ink2"])
        ax.text(L[-1] + 0.3, h3["acf_sign"][-1], "direction", va="center", fontsize=9,
                color=th["ink2"])
        ax.set_xlim(0.5, L[-1] + 3.5); ax.set_xlabel("lag (trading days)")
        ax.set_title(f"{m['name']}  ·  P(up) = {h3['p_up']:.3f}")
    axes[0].set_ylabel("autocorrelation")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


FIGS = {"hero": fig_hero, "var_breaches": fig_var, "breach_timeline": fig_rug,
        "pit": fig_pit, "poisson": fig_poisson, "bernoulli": fig_bernoulli}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=str(ROOT / "docs" / "data.json"))
    ap.add_argument("--out", default=str(ROOT / "figures"))
    a = ap.parse_args()
    site = json.loads(Path(a.site).read_text())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for theme, th in THEMES.items():
        style(th)
        for name, f in FIGS.items():
            suffix = "" if theme == "light" else "-dark"
            f(site, th, out / f"{name}{suffix}.png")
    print(f"wrote {len(FIGS) * len(THEMES)} figures to {out}")


if __name__ == "__main__":
    main()
