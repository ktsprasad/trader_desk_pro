"""Net worth across accounts in INR."""
from __future__ import annotations

from collections import defaultdict

from ..core.models import Holding


def summarise(holdings: list[Holding], fx: dict[str, float] | None = None) -> dict:
    fx = {"INR": 1.0, **(fx or {})}
    by_class: dict[str, dict[str, float]] = defaultdict(lambda: {"invested": 0.0, "value": 0.0})
    by_account: dict[str, dict[str, float]] = defaultdict(lambda: {"invested": 0.0, "value": 0.0})
    day = 0.0
    rows = []
    missing_fx = set()
    for h in holdings:
        rate = fx.get(h.currency)
        if rate is None:
            missing_fx.add(h.currency)
            continue
        inv, val = h.invested * rate, h.value * rate
        for bucket in (by_class[h.asset_class], by_account[h.account]):
            bucket["invested"] += inv
            bucket["value"] += val
        if h.day_change is not None:
            day += h.day_change * rate
        rows.append({"account": h.account, "exchange": h.exchange, "symbol": h.tradingsymbol, "name": h.name,
                     "isin": h.isin, "asset_class": h.asset_class, "quantity": h.quantity,
                     "average_price": round(h.average_price, 4), "last_price": h.last_price,
                     "invested_inr": round(inv, 2), "value_inr": round(val, 2), "pnl_inr": round(val - inv, 2),
                     "pnl_pct": round(100 * (val - inv) / inv, 2) if inv else None,
                     "day_change_inr": round(h.day_change * rate, 2) if h.day_change is not None else None})
    invested = sum(b["invested"] for b in by_class.values())
    value = sum(b["value"] for b in by_class.values())
    rows.sort(key=lambda r: -r["value_inr"])

    def fmt(d):
        return {k: {"invested": round(v["invested"], 2), "value": round(v["value"], 2),
                    "pnl": round(v["value"] - v["invested"], 2),
                    "weight_pct": round(100 * v["value"] / value, 2) if value else 0} for k, v in d.items()}

    return {"invested": round(invested, 2), "value": round(value, 2), "pnl": round(value - invested, 2),
            "pnl_pct": round(100 * (value - invested) / invested, 2) if invested else None,
            "day_change": round(day, 2), "by_asset_class": fmt(by_class), "by_account": fmt(by_account),
            "holdings": rows, "missing_fx": sorted(missing_fx)}
