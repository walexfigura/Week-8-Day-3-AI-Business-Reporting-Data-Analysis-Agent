"""Matplotlib charts drawn ONLY from values computed by the analysis layer.

Each function receives already-computed data (the same dictionaries used for
the report tables), draws one chart, saves a PNG and returns the exact values
it plotted. The report stores those values in summary.json, and a test checks
that they equal the table values — so charts and narrative cannot disagree.

Design choices: one y-axis per chart (never dual axes), a fixed categorical
color order, thin marks, a light grid, direct labels only where they help.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: no display needed
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

BLUE = "#2a78d6"      # categorical slot 1: current period / increases
ORANGE = "#eb6834"    # categorical slot 2: second series
RED = "#e34948"       # decreases in the bridge
GRAY = "#9a9893"      # previous period / totals (de-emphasized)
INK = "#0b0b0b"
INK_2 = "#52514e"
SURFACE = "#fcfcfb"
GRID = "#e4e3df"


def _money_axis(ax: Any) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"${v:,.0f}"))


def _style(ax: Any, title: str, subtitle: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=13, color=INK, pad=22, fontweight="bold")
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=INK_2)


def _save(fig: Any, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=144, facecolor=SURFACE)
    plt.close(fig)
    return path.name


def sales_vs_ad_spend(daily: list[dict[str, Any]], path: Path, split_day: str, subtitle: str) -> dict[str, Any]:
    days = [d["day"] for d in daily]
    sales = [d["net_sales"] for d in daily]
    spend = [d["ad_spend"] for d in daily]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    x = range(len(days))
    ax.plot(x, sales, color=BLUE, linewidth=2, marker="o", markersize=4, label="Net product sales")
    ax.plot(x, spend, color=ORANGE, linewidth=2, marker="o", markersize=4, label="Meta ad spend")
    if split_day in days:
        ax.axvline(days.index(split_day) - 0.5, color=INK_2, linewidth=1, linestyle=(0, (3, 3)))
    ax.set_xticks(list(x), [d[5:] for d in days], rotation=0)
    _money_axis(ax)
    ax.set_ylim(bottom=0)
    _style(ax, "Daily net sales vs. Meta ad spend", subtitle)
    ax.legend(frameon=False, fontsize=9, loc="upper left", ncol=2)
    return {"file": _save(fig, path), "days": days, "net_sales": sales, "ad_spend": spend}


def contribution_comparison(previous: dict[str, Any], current: dict[str, Any], path: Path, labels: tuple[str, str],
                            subtitle: str) -> dict[str, Any]:
    keys = [("net", "Net sales"), ("cogs", "COGS"), ("fees", "Fulfillment\n+ payment"), ("ads", "Meta spend"),
            ("profit", "Contribution\nprofit")]
    prev_vals = [float(previous[k]) for k, _ in keys]
    cur_vals = [float(current[k]) for k, _ in keys]
    fig, ax = plt.subplots(figsize=(9, 4.4))
    width = 0.38
    xs = range(len(keys))
    ax.bar([x - width / 2 - 0.01 for x in xs], prev_vals, width, color=GRAY, label=labels[0])
    ax.bar([x + width / 2 + 0.01 for x in xs], cur_vals, width, color=BLUE, label=labels[1])
    last = len(keys) - 1
    for x, v in ((last - width / 2, prev_vals[-1]), (last + width / 2, cur_vals[-1])):
        ax.text(x, v, f"${v:,.0f}", ha="center", va="bottom", fontsize=9, color=INK)
    ax.set_xticks(list(xs), [label for _, label in keys])
    _money_axis(ax)
    _style(ax, "Contribution profit and its components", subtitle)
    ax.legend(frameon=False, fontsize=9)
    return {"file": _save(fig, path), "components": [k for k, _ in keys], "previous": prev_vals, "current": cur_vals}


def profit_bridge(bridge: dict[str, Any], path: Path, subtitle: str) -> dict[str, Any]:
    labels = ["Previous\nprofit"] + [s["label"].replace(" ", "\n", 1) for s in bridge["steps"]] + ["Current\nprofit"]
    impacts = [s["profit_impact"] for s in bridge["steps"]]
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    running = bridge["previous_profit"]
    ax.bar(0, running, color=GRAY, width=0.6)
    ax.text(0, running, f"${running:,.0f}", ha="center", va="bottom", fontsize=9, color=INK)
    for i, impact in enumerate(impacts, start=1):
        bottom = running if impact >= 0 else running + impact
        ax.bar(i, abs(impact), bottom=bottom, color=BLUE if impact >= 0 else RED, width=0.6)
        ax.text(i, bottom + abs(impact), f"{'+' if impact >= 0 else '−'}${abs(impact):,.0f}", ha="center",
                va="bottom", fontsize=9, color=INK)
        running += impact
    end = len(impacts) + 1
    ax.bar(end, bridge["current_profit"], color=GRAY, width=0.6)
    ax.text(end, bridge["current_profit"], f"${bridge['current_profit']:,.0f}", ha="center", va="bottom", fontsize=9,
            color=INK)
    ax.set_xticks(range(len(labels)), labels, fontsize=8.5)
    _money_axis(ax)
    _style(ax, "What moved contribution profit", subtitle)
    return {"file": _save(fig, path), "previous_profit": bridge["previous_profit"], "impacts": impacts,
            "current_profit": bridge["current_profit"], "ends_at": round(running, 2)}


def campaign_spend(campaigns: list[dict[str, Any]], path: Path, labels: tuple[str, str], subtitle: str) -> dict[str, Any]:
    rows = sorted(campaigns, key=lambda c: c["current_spend"])
    names = [f"{(c['campaign_name_untrusted_text'] or c['campaign_id'])[:38]}" for c in rows]
    prev = [c["previous_spend"] for c in rows]
    cur = [c["current_spend"] for c in rows]
    fig, ax = plt.subplots(figsize=(9, 0.6 * len(rows) + 1.8))
    ys = range(len(rows))
    h = 0.36
    ax.barh([y - h / 2 - 0.01 for y in ys], prev, h, color=GRAY, label=labels[0])
    ax.barh([y + h / 2 + 0.01 for y in ys], cur, h, color=BLUE, label=labels[1])
    for y, v in zip(ys, cur, strict=True):
        ax.text(v, y + h / 2, f" ${v:,.0f}", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(list(ys), names, fontsize=8.5)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"${v:,.0f}"))
    _style(ax, "Meta spend by campaign", subtitle)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    return {"file": _save(fig, path), "campaign_ids": [c["campaign_id"] for c in rows], "previous": prev, "current": cur}


def product_margins(products: list[dict[str, Any]], path: Path, labels: tuple[str, str], subtitle: str) -> dict[str, Any]:
    rows = [p for p in products if p["current_margin_pct"] is not None]
    rows.sort(key=lambda p: p["current_margin_pct"])
    missing = [p["sku"] for p in products if p["current_margin_pct"] is None and p["current_units"]]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(rows) + 1.8))
    ys = range(len(rows))
    cur = [p["current_margin_pct"] for p in rows]
    prev = [p["previous_margin_pct"] for p in rows]
    ax.barh(list(ys), cur, 0.55, color=BLUE, label=f"{labels[1]} margin %")
    ax.scatter([v if v is not None else float("nan") for v in prev], list(ys), color=INK, s=28, zorder=3,
               label=f"{labels[0]} margin %")
    for y, v in zip(ys, cur, strict=True):  # label inside the bar so it never collides with the marker
        ax.text(1.5, y, f"{v:.1f}%", va="center", fontsize=8.5, color="white", fontweight="bold")
    ax.set_yticks(list(ys), [p["sku"] for p in rows], fontsize=8.5)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:.0f}%"))
    note = f"  ·  No cost data (excluded): {', '.join(missing)}" if missing else ""
    _style(ax, "Product margin by SKU (before ads and fulfillment)", subtitle + note)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    return {"file": _save(fig, path), "skus": [p["sku"] for p in rows], "current_margin_pct": cur,
            "previous_margin_pct": prev, "excluded_missing_cost": missing}
