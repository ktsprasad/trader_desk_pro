"""Realised P/L from the desk's filled orders, matched first-in-first-out per instrument."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Fill:
    at: datetime
    account: str
    exchange: str
    symbol: str
    product: str
    instrument_type: str
    side: str
    qty: float
    price: float
    paper: bool
    charges: float = 0.0


@dataclass
class Closed:
    symbol: str
    exchange: str
    product: str
    instrument_type: str
    qty: float
    entry: float
    exit: float
    opened: datetime
    closed: datetime
    pnl: float
    paper: bool

    @property
    def kind(self) -> str:
        if self.instrument_type in ("CE", "PE", "FUT") or self.exchange in ("NFO", "BFO", "MCX"):
            return "fo"
        if self.product == "MIS" or self.opened.date() == self.closed.date():
            return "intraday"
        return "equity_ltcg" if (self.closed - self.opened).days > 365 else "equity_stcg"


def fills_from_rows(rows) -> list[Fill]:
    out = []
    for r in rows:
        if r.stage != "sent" or r.status != "COMPLETE":
            continue
        q = r.request or {}
        px = q.get("price") or (r.verdict or {}).get("entry_ref")
        if not px:
            continue
        out.append(Fill(r.sent_at or r.created_at, r.account_id, q["exchange"], q["tradingsymbol"],
                        q.get("product", "CNC"), q.get("instrument_type", "EQ"), q["side"], float(q["quantity"]),
                        float(px), bool(r.paper), float(((r.charges or {}).get("entry") or {}).get("total", 0))))
    return sorted(out, key=lambda f: f.at)


def match(fills: list[Fill]) -> list[Closed]:
    open_: dict[tuple, deque] = defaultdict(deque)   # key -> deque[[signed qty, price, at]]
    closed: list[Closed] = []
    for f in fills:
        key = (f.account, f.exchange, f.symbol, f.product, f.paper)
        signed = f.qty if f.side == "BUY" else -f.qty
        book = open_[key]
        while signed and book and (book[0][0] > 0) != (signed > 0):
            lot = book[0]
            take = min(abs(lot[0]), abs(signed))
            long_lot = lot[0] > 0
            pnl = (f.price - lot[1]) * take if long_lot else (lot[1] - f.price) * take
            closed.append(Closed(f.symbol, f.exchange, f.product, f.instrument_type, take, lot[1], f.price,
                                 lot[2], f.at, round(pnl, 2), f.paper))
            lot[0] += take if lot[0] < 0 else -take
            signed += take if signed < 0 else -take
            if not lot[0]:
                book.popleft()
        if signed:
            book.append([signed, f.price, f.at])
    return closed
