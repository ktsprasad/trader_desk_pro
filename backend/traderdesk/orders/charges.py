"""Brokerage and statutory charges, from the versioned table in config/charges.yaml."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import yaml

from ..core.models import OrderRequest, Segment, Side

DEFAULT_TABLE = Path(__file__).resolve().parent.parent / "config" / "charges.yaml"


@dataclass
class Charges:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    dp: float

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp + self.gst + self.dp, 2)

    def to_dict(self) -> dict:
        d = {k: round(v, 2) for k, v in asdict(self).items()}
        d["total"] = self.total
        return d


def _pick(entries: list[dict], on: date) -> dict:
    eligible = [e for e in entries if _d(e["effective_from"]) <= on]
    if not eligible:
        raise ValueError(f"no charges entry effective on {on}")
    return max(eligible, key=lambda e: _d(e["effective_from"]))


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


class ChargesEngine:
    def __init__(self, table_path: Path | str = DEFAULT_TABLE):
        self.table = yaml.safe_load(Path(table_path).read_text(encoding="utf-8"))

    def broker_entry(self, broker: str, on: date) -> dict:
        entries = self.table["brokers"].get(broker) or self.table["brokers"]["kite"]
        e = _pick(entries, on)
        if "same_as" in e:
            return self.broker_entry(e["same_as"], on)
        return e

    def compute(self, broker: str, side: Side, segment: Segment, exchange: str, price: float, qty: float,
                on: date | None = None, dp_applies: bool = True) -> Charges:
        """One leg. `price` is the premium for options. Turnover = price x qty."""
        on = on or date.today()
        st = _pick(self.table["statutory"], on)
        br = self.broker_entry(broker, on)
        seg = segment.value
        turnover = price * qty
        rule = br["brokerage"][seg]
        brokerage = float(rule.get("flat", 0))
        if "pct" in rule:
            brokerage = turnover * rule["pct"]
            if "cap" in rule:
                brokerage = min(brokerage, rule["cap"])
        stt_rule = st["stt"][seg]
        stt = turnover * stt_rule.get("buy" if side == Side.BUY else "sell", 0)
        # STT is rounded to the nearest rupee by the exchange on contract notes
        stt = float(round(stt))
        ex_key = "BSE" if exchange in ("BSE", "BFO") else "NSE"
        exch = turnover * st["exchange_txn"][ex_key][seg]
        sebi = turnover * st["sebi_per_crore"] / 1e7
        stamp = turnover * st["stamp_buy"][seg] if side == Side.BUY else 0.0
        stamp = float(round(stamp))
        gst = (brokerage + exch + sebi) * st["gst"]
        dp = float(br.get("dp_charge_per_scrip_sell", 0)) if (
            side == Side.SELL and segment == Segment.EQ_DELIVERY and dp_applies) else 0.0
        return Charges(brokerage, stt, exch, sebi, stamp, gst, dp)

    def for_order(self, broker: str, o: OrderRequest, price: float, on: date | None = None) -> Charges:
        return self.compute(broker, o.side, o.segment, o.exchange, price, o.quantity, on)

    def round_trip(self, broker: str, o: OrderRequest, entry: float, exit_: float, on: date | None = None) -> dict:
        """Charges for entry + the opposite exit, and the net breakeven price."""
        entry_c = self.for_order(broker, o, entry, on)
        exit_side = Side.SELL if o.side == Side.BUY else Side.BUY
        exit_c = self.compute(broker, exit_side, o.segment, o.exchange, exit_, o.quantity, on)
        total = entry_c.total + exit_c.total
        per_unit = total / o.quantity if o.quantity else 0.0
        breakeven = entry + per_unit if o.side == Side.BUY else entry - per_unit
        return {"entry": entry_c.to_dict(), "exit": exit_c.to_dict(), "total": round(total, 2),
                "breakeven": round(breakeven, 2)}


def round_to_tick(price: float, tick: float, side: Side | None = None) -> float:
    """Round to tick. Buys round down and sells round up, so rounding never worsens the price."""
    if not tick:
        return price
    n = price / tick
    if side == Side.BUY:
        n = math.floor(n + 1e-9)
    elif side == Side.SELL:
        n = math.ceil(n - 1e-9)
    else:
        n = round(n)
    return round(n * tick, 4)


def round_to_lot(qty: float, lot: int) -> int:
    lot = max(1, int(lot or 1))
    return int(qty // lot) * lot
