"""NSE F&O end-of-day bhavcopy (UDiFF format, free from nsearchives.nseindia.com).

One CSV per trading day with every futures and options contract: close, previous close, underlying price,
open interest and its change, volume and lot size. It is published in the evening after the close, so
"today" means the last trading day whose file is out. From it:

- OI flow: futures price change x OI change -> long buildup, short buildup, short covering, long unwinding;
- option pulse: put/call OI ratio, highest call / put OI strikes, max pain, biggest OI additions.
"""
from __future__ import annotations

import csv
import io
import threading
import time
import zipfile
from datetime import date, timedelta

URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36", "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
PUBLISHED_AFTER_IST = 19          # hour; before this, today's file is usually not out yet


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        tp = (r.get("FinInstrmTp") or "").strip()
        if tp not in ("STF", "IDF", "STO", "IDO"):
            continue
        out.append({"type": tp, "symbol": (r.get("TckrSymb") or "").strip(), "expiry": (r.get("XpryDt") or "").strip(),
                    "strike": _f(r.get("StrkPric")), "opt": (r.get("OptnTp") or "").strip(),
                    "close": _f(r.get("ClsPric")), "prev_close": _f(r.get("PrvsClsgPric")),
                    "underlying": _f(r.get("UndrlygPric")), "oi": _f(r.get("OpnIntrst")),
                    "chg_oi": _f(r.get("ChngInOpnIntrst")), "volume": _f(r.get("TtlTradgVol")),
                    "value": _f(r.get("TtlTrfVal")), "lot": _f(r.get("NewBrdLotQty")),
                    "date": (r.get("TradDt") or "").strip()})
    return out


def unzip(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    return z.read(z.namelist()[0]).decode("utf-8", errors="replace")


class FoBhavcopy:
    """Finds and caches the latest published file. `fetch(url) -> bytes | None` is injectable for tests."""

    def __init__(self, session, clock, fetch=None):
        self.session, self.clock = session, clock
        self.fetch = fetch or self._http
        self._cache: dict[str, list[dict]] = {}
        self._missing: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _http(url: str) -> bytes | None:
        import httpx
        r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def candidates(self, n: int = 7) -> list[date]:
        now = self.clock()
        from ..core.calendar import IST
        local = now.astimezone(IST)
        d = local.date() if local.hour >= PUBLISHED_AFTER_IST else local.date() - timedelta(days=1)
        out = []
        while len(out) < n:
            if self.session.is_trading_day(d)[0]:
                out.append(d)
            d -= timedelta(days=1)
        return out

    def load(self, d: date) -> list[dict] | None:
        key = d.strftime("%Y%m%d")
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if time.monotonic() - self._missing.get(key, -1e9) < 1800:
                return None
        data = self.fetch(URL.format(d=key))
        if not data:
            with self._lock:
                self._missing[key] = time.monotonic()
            return None
        rows = parse(unzip(data))
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if k >= key}   # keep the newest few only
            self._cache[key] = rows
        return rows

    def latest(self) -> tuple[date, list[dict]]:
        errors = []
        for d in self.candidates():
            try:
                rows = self.load(d)
            except Exception as e:      # network / NSE refusal: try an older day, then report
                errors.append(f"{d}: {type(e).__name__}")
                continue
            if rows:
                return d, rows
        raise LookupError("NSE F&O bhavcopy not available" + (f" ({'; '.join(errors[:3])})" if errors else ""))


# ---------------------------------------------------------------- analytics
FLOW = {(True, True): "Long buildup", (False, True): "Short buildup",
        (True, False): "Short covering", (False, False): "Long unwinding"}


def oi_flow(rows: list[dict], top: int = 15) -> dict:
    """Futures, all expiries summed per symbol; price change from the nearest expiry."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r["type"] in ("STF", "IDF"):
            by.setdefault(r["symbol"], []).append(r)
    items = []
    for sym, rs in by.items():
        oi = sum(r["oi"] for r in rs)
        chg = sum(r["chg_oi"] for r in rs)
        near = min(rs, key=lambda r: r["expiry"])
        prev_oi = oi - chg
        if not near["prev_close"] or prev_oi <= 0 or chg == 0 or near["close"] == near["prev_close"]:
            continue
        pchg = (near["close"] - near["prev_close"]) / near["prev_close"] * 100
        items.append({"symbol": sym, "index": near["type"] == "IDF", "price": near["close"], "price_chg_pct": round(pchg, 2),
                      "oi": oi, "oi_chg": chg, "oi_chg_pct": round(chg / prev_oi * 100, 2),
                      "signal": FLOW[(pchg > 0, chg > 0)]})
    return group(items, top)


def flow_item(symbol: str, index: bool, price: float, prev_price: float, oi: float, prev_oi: float) -> dict | None:
    chg = oi - prev_oi
    if not prev_price or prev_oi <= 0 or chg == 0 or price == prev_price:
        return None
    pchg = (price - prev_price) / prev_price * 100
    return {"symbol": symbol, "index": index, "price": price, "price_chg_pct": round(pchg, 2), "oi": oi, "oi_chg": chg,
            "oi_chg_pct": round(chg / prev_oi * 100, 2), "signal": FLOW[(pchg > 0, chg > 0)]}


def group(items: list[dict], top: int = 15) -> dict:
    out ={k: sorted([x for x in items if x["signal"] == k], key=lambda x: -abs(x["oi_chg_pct"]))[:top] for k in FLOW.values()}
    counts = {k: sum(1 for x in items if x["signal"] == k) for k in FLOW.values()}
    return {"groups": out, "counts": counts, "symbols": len(items)}


def option_symbols(rows: list[dict]) -> list[str]:
    return sorted({r["symbol"] for r in rows if r["type"] in ("STO", "IDO")})


def option_pulse(rows: list[dict], symbol: str, expiry: str | None = None) -> dict | None:
    opts = [r for r in rows if r["symbol"] == symbol.upper() and r["type"] in ("STO", "IDO")]
    if not opts:
        return None
    expiries = sorted({r["expiry"] for r in opts})
    exp = expiry if expiry in expiries else expiries[0]
    ch = [r for r in opts if r["expiry"] == exp]
    strikes: dict[float, dict] = {}
    for r in ch:
        s = strikes.setdefault(r["strike"], {"strike": r["strike"], "call_oi": 0.0, "put_oi": 0.0, "call_chg": 0.0,
                                             "put_chg": 0.0, "call_close": None, "put_close": None})
        side = "call" if r["opt"] == "CE" else "put"
        s[f"{side}_oi"] += r["oi"]
        s[f"{side}_chg"] += r["chg_oi"]
        s[f"{side}_close"] = r["close"]
    table = sorted(strikes.values(), key=lambda s: s["strike"])
    call_oi = sum(s["call_oi"] for s in table)
    put_oi = sum(s["put_oi"] for s in table)
    und = next((r["underlying"] for r in ch if r["underlying"]), None)

    def pain(at: float) -> float:
        return sum(s["call_oi"] * max(0.0, at - s["strike"]) + s["put_oi"] * max(0.0, s["strike"] - at) for s in table)

    max_pain = min(table, key=lambda s: pain(s["strike"]))["strike"] if table else None
    top_call = max(table, key=lambda s: s["call_oi"]) if table else None
    top_put = max(table, key=lambda s: s["put_oi"]) if table else None
    near = sorted(table, key=lambda s: abs(s["strike"] - (und or 0)))[:15] if und else table[:15]
    return {"symbol": symbol.upper(), "expiry": exp, "expiries": expiries, "underlying": und,
            "pcr": round(put_oi / call_oi, 2) if call_oi else None, "call_oi": call_oi, "put_oi": put_oi,
            "max_pain": max_pain, "call_wall": top_call["strike"] if top_call and top_call["call_oi"] else None,
            "put_wall": top_put["strike"] if top_put and top_put["put_oi"] else None,
            "call_adds": sorted(table, key=lambda s: -s["call_chg"])[:5],
            "put_adds": sorted(table, key=lambda s: -s["put_chg"])[:5],
            "strikes": sorted(near, key=lambda s: s["strike"])}


def most_active(rows: list[dict], top: int = 15) -> list[dict]:
    opts = [r for r in rows if r["type"] in ("STO", "IDO") and r["volume"] > 0]
    opts.sort(key=lambda r: -r["volume"])
    # volume and OI are in units (shares / index units), not lots; NSE's traded value is notional
    return [{"symbol": r["symbol"], "expiry": r["expiry"], "strike": r["strike"], "opt": r["opt"], "close": r["close"],
             "volume": r["volume"], "lots": round(r["volume"] / r["lot"]) if r["lot"] else None,
             "notional_cr": round(r["value"] / 1e7, 2), "oi": r["oi"], "oi_chg": r["chg_oi"]}
            for r in opts[:top]]
