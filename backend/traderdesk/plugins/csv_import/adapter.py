"""Holdings from an uploaded CSV: INDmoney US / INDstocks / Kite Console exports or any broker.

The upload is parsed into a dated snapshot for this account; the portfolio reads the latest snapshot.
Column names are matched loosely so most broker exports work without mapping.
"""
from __future__ import annotations

import csv
import io
import re

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Cap

COLS = {
    "symbol": ["symbol", "tradingsymbol", "trading symbol", "instrument", "stock", "stock name", "ticker", "scrip", "name", "security"],
    "isin": ["isin"],
    "quantity": ["quantity", "qty", "quantity available", "units", "shares", "net qty", "total qty", "holding qty"],
    "average_price": ["average price", "avg price", "avg. cost", "avg cost", "average cost", "buy price", "avg buy price", "cost price"],
    "last_price": ["ltp", "last price", "current price", "cmp", "market price", "previous closing price", "close price", "price"],
    "exchange": ["exchange"],
    "invested": ["invested", "invested value", "buy value", "cost"],
    "value": ["current value", "market value", "value"],
}


def _num(v) -> float | None:
    if v is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def parse_holdings_csv(data: bytes, currency: str = "INR") -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    # find the header row: the first row naming a symbol and a quantity column
    hdr_i, mapping = None, {}
    for i, r in enumerate(rows[:30]):
        low = [c.strip().lower() for c in r]
        m = {}
        for key, names in COLS.items():
            for n in names:
                if n in low:
                    m[key] = low.index(n)
                    break
        if "symbol" in m and "quantity" in m:
            hdr_i, mapping = i, m
            break
    if hdr_i is None:
        raise BrokerError(ErrorKind.INVALID, "could not find symbol and quantity columns in the CSV", "csv")
    out = []
    for r in rows[hdr_i + 1:]:
        get = lambda k: r[mapping[k]] if k in mapping and mapping[k] < len(r) else None  # noqa: E731
        sym, qty = (get("symbol") or "").strip(), _num(get("quantity"))
        if not sym or not qty:
            continue
        avg = _num(get("average_price"))
        if avg is None and _num(get("invested")) is not None:
            avg = _num(get("invested")) / qty
        last = _num(get("last_price"))
        if last is None and _num(get("value")) is not None:
            last = _num(get("value")) / qty
        avg = avg or 0.0
        out.append({"exchange": (get("exchange") or ("US" if currency == "USD" else "NSE")).strip().upper() or "NSE",
                    "tradingsymbol": sym.upper()[:64], "isin": (get("isin") or "").strip() or None, "name": sym[:255],
                    "asset_class": "us_stock" if currency == "USD" else ("mutual_fund" if (get("isin") or "").startswith("INF") else "equity"),
                    "quantity": qty, "average_price": avg, "last_price": last if last is not None else avg, "currency": currency})
    if not out:
        raise BrokerError(ErrorKind.INVALID, "no holdings rows found in the CSV", "csv")
    return out


class CsvImport:
    name = "csv"
    capabilities = {Cap.READ}
    upload_kind = "csv"

    def __init__(self, account_id: str, settings: dict, secrets=None, manifest=None):
        self.account_id, self.settings = account_id, settings or {}

    def session_status(self, now=None) -> dict:
        return {"connected": True, "needs_login": False, "issued_at": None}

    def parse_upload(self, data: bytes, password: str | None = None) -> list[dict]:
        return parse_holdings_csv(data, self.settings.get("currency", "INR"))

    def holdings(self):
        # the portfolio falls back to this account's latest uploaded snapshot
        raise BrokerError(ErrorKind.UNSUPPORTED, "from the last upload", self.name)

    def positions(self):
        return []

    def orders(self, day=None):
        return []

    def place_order(self, o):
        raise BrokerError(ErrorKind.UNSUPPORTED, "import-only connection", self.name)

    modify_order = cancel_order = place_gtt = margins = place_order
