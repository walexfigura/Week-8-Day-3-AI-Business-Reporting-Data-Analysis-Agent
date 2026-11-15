"""Period comparison, variance bridge and anomaly tools."""

from __future__ import annotations

from typing import Any

from ..analysis.anomalies import detect_anomalies as run_rules
from ..analysis.variance_analysis import compare
from .marketing import TWO_PERIODS
from .registry import ToolContext, ToolSpec, two_periods


def compare_periods(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    previous, current = two_periods(args)
    return compare(ctx.require_con(), previous, current)


def detect_anomalies(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    previous, current = two_periods(args)
    return run_rules(ctx.require_con(), previous, current)


SPECS = [
    ToolSpec(
        name="compare_periods",
        description=("Compare two periods: headline KPIs with % changes, a contribution-profit bridge whose steps "
                     "sum exactly to the profit change, a cost-rate analysis (which costs grew faster than sales), "
                     "net-sales bridge (gross/discounts/refunds), fee changes, and comparability warnings "
                     "(unequal lengths, overlaps, missing data)."),
        properties=TWO_PERIODS,
        handler=compare_periods, category="comparisons",
    ),
    ToolSpec(
        name="detect_anomalies",
        description=("Run transparent threshold rules (not machine learning) comparing two periods: ad spend jumps, "
                     "margin drops, refund-rate rises, fee-per-order increases, missing product costs, campaign "
                     "spend spikes, product margin declines, attribution mismatches."),
        properties=TWO_PERIODS,
        handler=detect_anomalies, category="comparisons",
    ),
]
