"""Real-time prices and open interest from a logged-in Kite Connect session.

When the day's Kite login is active, every price in the app (quotes, the last candle of daily charts, screeners,
futures and option OI) comes from Kite's quote API. Without it, the app falls back to Yahoo's delayed data and
NSE's end-of-day file and says so ("Delayed"). Kite allows about one quote call per second with up to 500
instruments, so quotes are batched and cached for a couple of seconds.

`adapter_fn()` returns a connected Kite adapter or None; tests pass one built on a fake client.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone

from ..core.calendar import IST
from .yahoo import ALIASES, Bar

# app symbol -> Kite instrument key, for indices; foreign / FX / commodity aliases have no Kite key
KITE_INDEX = {"NIFTY": "NSE:NIFTY 50", "NIFTY50": "NSE:NIFTY 50", "NIFTY 50": "NSE:NIFTY 50",
              "BANKNIFTY": "NSE:NIFTY BANK", "NIFTY BANK": "NSE:NIFTY BANK", "INDIAVIX": "NSE:INDIA VIX",
              "INDIA VIX": "NSE:INDIA VIX", "FINNIFTY": "NSE:NIFTY FIN SERVICE", "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
              "SENSEX": "BSE:SENSEX"}
TTL = 2.0           # a fresh batch is fetched at most this often
REUSE = 20.0        # single-symbol lookups reuse a batched quote this old (screens refresh every 20 s)
BACKOFF = 60.0      # after Kite refuses or fails, do not ask again for this long
BATCH = 500


def kite_key(symbol: str, exchange: str = "NSE") -> str | None:
    s = symbol.strip().upper()
    if s in KITE_INDEX:
        return KITE_INDEX[s]
    if s in ALIASES or exchange.upper() in ("US", "FX", "CRYPTO", "MF") or "=" in s or s.startswith("^"):
        return None
    return f"{exchange.upper()}:{s}"


def _ts(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=IST)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v)
            return d if d.tzinfo else d.replace(tzinfo=IST)
        except ValueError:
            return None
    return None


def normalise(q: dict) -> dict:
    ohlc = q.get("ohlc") or {}
    at = _ts(q.get("last_trade_time")) or _ts(q.get("timestamp"))
    prev = ohlc.get("close") or None
    last = q.get("last_price")
    return {"last": float(last) if last is not None else None, "prev_close": float(prev) if prev else None,
            "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
            "volume": q.get("volume") or q.get("volume_traded") or 0, "oi": q.get("oi"),
            "change_pct": round((float(last) - float(prev)) / float(prev) * 100, 2) if last is not None and prev else None,
            "at": at.isoformat() if at else None, "date": at.astimezone(IST).date().isoformat() if at else None}


class LiveQuotes:
    def __init__(self, adapter_fn):
        self.adapter_fn = adapter_fn
        self._cache: dict[str, tuple[float, dict]] = {}
        self._instruments: dict[str, tuple[date, list[dict]]] = {}
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self._failed_at = -1e9

    def adapter(self):
        try:
            return self.adapter_fn()
        except Exception:
            return None

    def status(self) -> dict:
        a = self.adapter()
        if a is None:
            return {"live": False, "provider": None,
                    "reason": "Delayed: log in to Kite (Connections) for real-time prices"}
        if self.last_error:          # cleared by the next successful call
            return {"live": False, "provider": "Kite", "reason": f"Delayed: Kite quotes failed ({self.last_error})"}
        return {"live": True, "provider": "Kite", "reason": "Real-time prices from Kite"}

    def backing_off(self) -> bool:
        return time.monotonic() - self._failed_at < BACKOFF

    # ------------------------------------------------------------ quotes
    def quotes(self, keys: list[str], max_age: float = TTL) -> dict[str, dict]:
        """Kite key -> normalised quote. Missing keys are simply absent (unknown instrument)."""
        if not keys or self.backing_off():
            return {}
        a = self.adapter()
        if a is None:
            return {}
        now = time.monotonic()
        out, need = {}, []
        with self._lock:
            for k in dict.fromkeys(keys):
                hit = self._cache.get(k)
                if hit and now - hit[0] < max_age:
                    out[k] = hit[1]
                else:
                    need.append(k)
        for i in range(0, len(need), BATCH):
            chunk = need[i:i + BATCH]
            try:
                raw = a.quote(chunk)
                self.last_error = None
            except Exception as e:
                # e.g. no market-data subscription or an expired session: fall back to delayed data at once,
                # and do not queue behind Kite's 1-call-per-second limit for every symbol
                self.last_error = getattr(e, "message", None) or type(e).__name__
                self._failed_at = time.monotonic()
                return out
            got = {k: normalise(v) for k, v in (raw or {}).items()}
            with self._lock:
                for k, v in got.items():
                    self._cache[k] = (time.monotonic(), v)
            out.update(got)
        return out

    def get(self, symbol: str, exchange: str = "NSE") -> dict | None:
        k = kite_key(symbol, exchange)
        return self.quotes([k], max_age=REUSE).get(k) if k else None

    def prefetch(self, symbols: list[str], exchange: str = "NSE") -> None:
        keys = [k for k in (kite_key(s, exchange) for s in symbols) if k]
        self.quotes(keys)

    # ------------------------------------------------------------ instruments (F&O symbols and expiries)
    def instruments(self, exchange: str = "NFO") -> list[dict]:
        a = None if self.backing_off() else self.adapter()
        if a is None:
            return []
        today = datetime.now(IST).date()
        with self._lock:
            hit = self._instruments.get(exchange)
            if hit and hit[0] == today:
                return hit[1]
        rows = a.instruments(exchange) or []
        keep = [{"tradingsymbol": r["tradingsymbol"], "name": r.get("name"), "expiry": str(r.get("expiry") or "")[:10],
                 "strike": float(r.get("strike") or 0), "type": r.get("instrument_type"), "lot": r.get("lot_size")}
                for r in rows if r.get("instrument_type") in ("FUT", "CE", "PE")]
        with self._lock:
            self._instruments[exchange] = (today, keep)
        return keep


def overlay_daily(bars: list[Bar], q: dict | None) -> list[Bar]:
    """Today's candle from the live quote: replaces Yahoo's (delayed) bar for the same day, or is appended."""
    if not q or q.get("last") is None or not q.get("date") or q.get("open") in (None, 0):
        return bars
    d = date.fromisoformat(q["date"])
    t = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
    today = Bar(t, float(q["open"]), float(max(q["high"] or 0, q["last"])), float(min(q["low"] or q["last"], q["last"])),
                float(q["last"]), float(q.get("volume") or 0))
    out = list(bars)
    if out and datetime.fromtimestamp(out[-1].t, tz=timezone.utc).astimezone(IST).date() == d:
        out[-1] = today
    elif not out or datetime.fromtimestamp(out[-1].t, tz=timezone.utc).astimezone(IST).date() < d:
        out.append(today)
    return out
