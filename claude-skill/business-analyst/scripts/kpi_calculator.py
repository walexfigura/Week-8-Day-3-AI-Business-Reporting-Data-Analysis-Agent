#!/usr/bin/env python3
"""Deterministic KPI calculator for the Business Analyst Skill (standard library only).

Reads a DAILY summary CSV (one row per date) and compares two periods:

    python scripts/kpi_calculator.py assets/sample-data/daily-summary.csv \
        --previous 2026-10-26 2026-11-01 --current 2026-11-02 2026-11-08

Required columns (extra columns are ignored):
    date, orders, gross_sales, discounts, refunds, cogs,
    pick_pack, packaging, shipping_labels, payment_processing, ad_spend
Optional: net_sales (checked against gross - discounts - refunds), units

Rules (same as the companion repository):
* Missing days or blank values are MISSING, never zero. Any gap makes the
  affected totals and contribution profit unavailable.
* Contribution profit = net sales - COGS - fulfillment - payment fees - ad spend.
* The profit bridge must sum exactly to the profit change.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

REQUIRED = ["date", "orders", "gross_sales", "discounts", "refunds", "cogs", "pick_pack", "packaging",
            "shipping_labels", "payment_processing", "ad_spend"]
MONEY = [c for c in REQUIRED if c not in ("date", "orders")]


def parse_amount(raw: str) -> Decimal | None:
    raw = (raw or "").replace("$", "").replace(",", "").strip()
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise SystemExit(f"Not a number: {raw!r}") from None


def load(path: str) -> dict[date, dict[str, Decimal | None]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"Missing required column(s): {', '.join(missing)}")
        rows: dict[date, dict[str, Decimal | None]] = {}
        for r in reader:
            d = date.fromisoformat(r["date"][:10])
            if d in rows:
                raise SystemExit(f"Duplicate row for {d}: aggregate to one row per day first")
            rows[d] = {c: parse_amount(r[c]) for c in REQUIRED[1:]}
            if "net_sales" in r and r["net_sales"].strip():
                expected = rows[d]["gross_sales"] - rows[d]["discounts"] - rows[d]["refunds"]  # type: ignore[operator]
                if parse_amount(r["net_sales"]) != expected:
                    print(f"WARNING {d}: net_sales column {r['net_sales']} != gross - discounts - refunds ({expected})")
    return rows


def totals(rows, start: date, end: date) -> dict:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    missing_days = [d.isoformat() for d in days if d not in rows]
    out: dict = {"days": len(days), "missing_days": missing_days, "missing_values": []}
    for col in REQUIRED[1:]:
        values = [rows[d][col] for d in days if d in rows]
        if any(v is None for v in values) or missing_days:
            out[col] = None
            if any(v is None for v in values):
                out["missing_values"].append(col)
        else:
            out[col] = sum(values, Decimal(0))
    def sub(*cols):
        return None if any(out[c] is None for c in cols) else cols
    out["net_sales"] = (out["gross_sales"] - out["discounts"] - out["refunds"]) if sub("gross_sales", "discounts", "refunds") else None
    out["fulfillment"] = (out["pick_pack"] + out["packaging"] + out["shipping_labels"]) if sub("pick_pack", "packaging", "shipping_labels") else None
    out["payment"] = out["payment_processing"]
    parts = [out["net_sales"], out["cogs"], out["fulfillment"], out["payment"], out["ad_spend"]]
    out["profit"] = None if any(p is None for p in parts) else parts[0] - parts[1] - parts[2] - parts[3] - parts[4]
    return out


def fmt(v) -> str:
    if v is None:
        return "n/a"
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def pct(cur, prev) -> str:
    if cur is None or prev is None or prev == 0:
        return "n/a"
    return f"{(cur - prev) / abs(prev) * 100:+.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--previous", nargs=2, required=True, metavar=("START", "END"))
    ap.add_argument("--current", nargs=2, required=True, metavar=("START", "END"))
    a = ap.parse_args()
    rows = load(a.csv)
    p = totals(rows, date.fromisoformat(a.previous[0]), date.fromisoformat(a.previous[1]))
    c = totals(rows, date.fromisoformat(a.current[0]), date.fromisoformat(a.current[1]))
    print(f"Previous {a.previous[0]}..{a.previous[1]} ({p['days']} days) vs current {a.current[0]}..{a.current[1]} ({c['days']} days)")
    if p["days"] != c["days"]:
        print("WARNING: periods have different lengths; compare daily averages, not totals.")
    for label, t in (("previous", p), ("current", c)):
        if t["missing_days"]:
            print(f"WARNING: {label} period has no data for {', '.join(t['missing_days'])} (missing, not zero)")
        if t["missing_values"]:
            print(f"WARNING: {label} period has blank values in: {', '.join(t['missing_values'])} (missing, not zero)")
    print(f"\n{'Metric':<28}{'Previous':>14}{'Current':>14}{'Change':>14}{'% change':>10}")
    for key, label in [("gross_sales", "Gross product sales"), ("discounts", "Discounts"), ("refunds", "Refunds"),
                       ("net_sales", "Net product sales"), ("cogs", "COGS"), ("fulfillment", "Fulfillment costs"),
                       ("payment", "Payment fees"), ("ad_spend", "Meta ad spend"), ("profit", "Contribution profit")]:
        change = None if p[key] is None or c[key] is None else c[key] - p[key]
        print(f"{label:<28}{fmt(p[key]):>14}{fmt(c[key]):>14}{fmt(change):>14}{pct(c[key], p[key]):>10}")
    if p["profit"] is None or c["profit"] is None:
        print("\nContribution profit unavailable for at least one period: do not report a value or a bridge.")
        return 0
    print(f"{'Contribution margin':<28}{p['profit'] / p['net_sales'] * 100:>13.1f}%{c['profit'] / c['net_sales'] * 100:>13.1f}%")
    print("\nProfit bridge (impact on contribution profit)")
    impacts = [("Net product sales", c["net_sales"] - p["net_sales"]), ("COGS", -(c["cogs"] - p["cogs"])),
               ("Fulfillment costs", -(c["fulfillment"] - p["fulfillment"])), ("Payment fees", -(c["payment"] - p["payment"])),
               ("Meta ad spend", -(c["ad_spend"] - p["ad_spend"]))]
    for label, v in impacts:
        print(f"  {label:<26}{v:>+14,.2f}")
    total = sum((v for _, v in impacts), Decimal(0))
    change = c["profit"] - p["profit"]
    print(f"  {'= profit change':<26}{change:>+14,.2f}   reconciles: {total == change}")
    print("\nCost as % of net sales (previous -> current)")
    for key, label in [("cogs", "COGS"), ("fulfillment", "Fulfillment"), ("payment", "Payment fees"), ("ad_spend", "Meta ad spend")]:
        prev_rate, cur_rate = p[key] / p["net_sales"] * 100, c[key] / c["net_sales"] * 100
        above = c[key] - p[key] * (c["net_sales"] / p["net_sales"])
        print(f"  {label:<26}{prev_rate:>7.2f}% -> {cur_rate:>6.2f}%   cost above proportional: {above:>+12,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
