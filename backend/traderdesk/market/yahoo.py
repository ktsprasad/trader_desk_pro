"""Free delayed market data from Yahoo Finance's chart endpoint (unofficial, ~1-15 min delay).

Used for charts, briefs and signals when no paid broker feed is connected (Kite's personal plan
has no market-data API). Only the symbol goes over the wire. Results are cached briefly.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..core.calendar import IST

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TraderDeskPro/0.1"

# NSE names people type -> Yahoo symbols
ALIASES = {
    "NIFTY": "^NSEI", "NIFTY50": "^NSEI", "NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTY BANK": "^NSEBANK",
    "INDIAVIX": "^INDIAVIX", "INDIA VIX": "^INDIAVIX", "SENSEX": "^BSESN", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "SPX": "^GSPC", "S&P500": "^GSPC", "NDX": "^NDX", "NIKKEI": "^N225", "HANGSENG": "^HSI",
    "BRENT": "BZ=F", "USDINR": "INR=X", "GOLD": "GC=F", "SILVER": "SI=F", "DOW": "^DJI",
}
INTERVALS = {"1m": ("1m", "5d"), "5m": ("5m", "60d"), "15m": ("15m", "60d"), "1h": ("60m", "730d"),
             "1H": ("60m", "730d"), "1d": ("1d", "2y"), "1D": ("1d", "2y"), "1w": ("1wk", "10y"),
             "1W": ("1wk", "10y")}


def yahoo_symbol(symbol: str, exchange: str = "NSE") -> str:
    s = symbol.strip().upper()
    if s in ALIASES:
        return ALIASES[s]
    if s.startswith("^") or "=" in s or "." in s:
        return s
    if exchange == "US":
        return s
    return f"{s}.BO" if exchange == "BSE" else f"{s}.NS"


@dataclass
class Bar:
    t: int          # unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float

    def ist(self) -> datetime:
        return datetime.fromtimestamp(self.t, IST)

    def to_dict(self) -> dict:
        return {"time": self.t, "open": self.o, "high": self.h, "low": self.l, "close": self.c, "volume": self.v}


class MarketData:
    def __init__(self, fetch: Callable[[str, dict], dict] | None = None, ttl: float = 45.0):
        self._fetch = fetch or self._http
        self.ttl = ttl
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _http(sym: str, params: dict) -> dict:
        import httpx
        r = httpx.get(URL.format(sym=sym), params=params, headers={"User-Agent": UA}, timeout=10.0)
        r.raise_for_status()
        return r.json()

    def _cached(self, key, fn):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (now, val)
        return val

    def chart(self, symbol: str, interval: str = "5m", range_: str | None = None, exchange: str = "NSE") -> dict:
        iv, default_range = INTERVALS.get(interval, (interval, "5d"))
        ysym = yahoo_symbol(symbol, exchange)
        params = {"interval": iv, "range": range_ or default_range, "includePrePost": "false"}

        def load():
            js = self._fetch(ysym, params)
            res = ((js or {}).get("chart") or {}).get("result") or []
            if not res:
                err = ((js or {}).get("chart") or {}).get("error") or {}
                raise LookupError(err.get("description") or f"no data for {ysym}")
            r = res[0]
            q = (r.get("indicators") or {}).get("quote", [{}])[0]
            bars = []
            for i, t in enumerate(r.get("timestamp") or []):
                o, h, l, c = (q.get(k, [None])[i] for k in ("open", "high", "low", "close"))
                if None in (o, h, l, c):
                    continue
                bars.append(Bar(int(t), float(o), float(h), float(l), float(c), float((q.get("volume") or [0])[i] or 0)))
            meta = r.get("meta") or {}
            return {"symbol": symbol.upper(), "yahoo": ysym, "currency": meta.get("currency"),
                    "last": meta.get("regularMarketPrice") or (bars[-1].c if bars else None),
                    "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
                    "bars": bars}

        return self._cached((ysym, params["interval"], params["range"]), load)

    def quote(self, symbol: str, exchange: str = "NSE") -> dict:
        """Last price and change vs the previous daily close."""
        d = self.chart(symbol, "1d", "10d", exchange)
        bars = d["bars"]
        last = d["last"] if d["last"] is not None else (bars[-1].c if bars else None)
        prev = bars[-2].c if len(bars) >= 2 else d["prev_close"]
        chg = ((last - prev) / prev * 100) if last is not None and prev else None
        return {"symbol": d["symbol"], "last": last, "prev_close": prev, "change_pct": chg,
                "currency": d["currency"]}

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        out = {}
        for s in symbols:
            try:
                out[s] = self.quote(s)
            except Exception as e:   # one bad symbol never breaks a panel
                out[s] = {"symbol": s, "error": str(e)}
        return out


def sessions(bars: list[Bar]) -> dict[str, list[Bar]]:
    """Group intraday bars by IST trading date."""
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.ist().date().isoformat(), []).append(b)
    return out
