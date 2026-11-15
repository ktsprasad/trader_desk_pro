"""Market pulse page: screener (RSI, Money Flow, Stock Radar, Calm Zone), Liquidity Map, OI Flow and
Option Pulse. Read-only analysis: nothing here places or prepares an order by itself.

Prices are Yahoo delayed daily bars; OI and options come from NSE's end-of-day F&O bhavcopy. When a source
is unreachable the route says so (503) instead of showing made-up figures.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import Depends, HTTPException

from ..analysis import pulse as pl
from ..analysis import strategies as st
from ..core.calendar import IST
from ..market.nse_fo import FoBhavcopy, flow_item, group, most_active, oi_flow, option_pulse, option_symbols
from .trading_api import DEFAULT_WATCHLIST, state_get

UNIVERSE = Path(__file__).resolve().parents[1] / "config" / "universe.yaml"
SCREEN_TTL = 600
LIVE_SCREEN_TTL = 20      # with Kite live prices the screener refreshes every 20 s


def config() -> dict:
    try:
        return yaml.safe_load(UNIVERSE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def sector_map() -> dict[str, str]:
    """symbol -> sector, from config/universe.yaml."""
    out = {}
    for sector, syms in (config().get("sectors") or {}).items():
        for s in syms or []:
            out.setdefault(str(s).strip().upper(), str(sector))
    return out


def universe(db) -> tuple[list[str], list[str]]:
    base = list(sector_map()) + [str(s).strip().upper() for s in (config().get("pulse") or [])]
    watch = state_get(db, "watchlist", {"symbols": DEFAULT_WATCHLIST})["symbols"]
    out = list(dict.fromkeys(base + [s.upper() for s in watch]))
    return out, watch


def register(app, c) -> None:
    Db, need, md, desk = c.Db, c.need, c.md, c.desk
    cache: dict[str, tuple[float, dict]] = {}

    def fo() -> FoBhavcopy:
        if not hasattr(app.state, "fo_bhav"):
            app.state.fo_bhav = FoBhavcopy(desk.session, desk.clock, getattr(app.state, "fo_fetch", None))
        return app.state.fo_bhav

    def daily(sym: str):
        return md.chart(sym, "1d", "2y")["bars"]

    @app.get("/api/pulse/screener")
    def screener(refresh: bool = False, info=Depends(need("portfolio:read"))):
        with Db() as db:
            syms, watch = universe(db)
        key = ",".join(syms)
        hit = cache.get(key)
        ttl = LIVE_SCREEN_TTL if (md.live is not None and md.live.adapter() is not None) else SCREEN_TTL
        if hit and not refresh and time.monotonic() - hit[0] < ttl:
            return hit[1]

        if md.live is not None:
            try:
                md.live.prefetch(syms + ["NIFTY", "BANKNIFTY", "INDIAVIX"])   # one batched real-time call
            except Exception:
                pass

        def one(sym):
            try:
                return sym, pl.metrics(sym, daily(sym)), None
            except Exception as e:
                return sym, None, f"{type(e).__name__}: {str(e)[:80]}"

        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(one, syms))
        rows = [m for _, m, _ in res if m]
        failed = [{"symbol": s, "error": e or "not enough history"} for s, m, e in res if not m]
        market = {"quotes": md.quotes(["NIFTY", "BANKNIFTY", "INDIAVIX"])}
        try:
            market["regime"] = st.regime(daily("NIFTY"), market["quotes"]["INDIAVIX"].get("last"))
        except Exception as e:
            market["regime"] = {"score": None, "label": f"unavailable ({type(e).__name__})", "parts": []}
        live = md.live.status() if md.live else {"live": False, "reason": "Delayed"}
        out = {"rows": rows, "failed": failed, "universe": len(syms), "watchlist": watch, "market": market,
               "live": live["live"], "live_reason": live["reason"], "as_of_time": datetime.now(IST).strftime("%H:%M:%S"),
               "source": ("Kite real-time prices on Yahoo daily history" if live["live"] else "Yahoo Finance daily bars (delayed)"), "list_file": "backend/traderdesk/config/universe.yaml",
               "as_of": max((r["as_of"] for r in rows), default=None)}
        cache[key] = (time.monotonic(), out)
        return out

    @app.get("/api/pulse/liquidity")
    def liquidity(symbol: str, info=Depends(need("portfolio:read"))):
        sym = symbol.strip().upper()[:40]
        try:
            bars = daily(sym)
        except Exception as e:
            raise HTTPException(502, f"no daily bars for {sym}: {e}")
        out = pl.liquidity_levels(bars)
        out["symbol"] = sym
        out["turnover_cr"] = round(bars[-1].c * bars[-1].v / 1e7, 2) if bars else None
        try:
            day, rows = fo().latest()
            op = eod(day, "pulse", sym, None)
        except LookupError:
            day, op = None, None
        if op:
            last = out["last"] or 0
            for strike, kind in ((op["call_wall"], "highest call OI strike"), (op["put_wall"], "highest put OI strike"),
                                 (op["max_pain"], "max pain")):
                if strike is None:
                    continue
                lv = {"price": strike, "kind": f"{kind} ({op['expiry']})", "distance_pct": round((strike - last) / last * 100, 2) if last else None}
                (out["above"] if strike > last else out["below"]).append(lv)
            out["above"].sort(key=lambda x: x["price"])
            out["below"].sort(key=lambda x: -x["price"])
        out["options_day"] = day.isoformat() if day else None
        out["live"] = bool(md.live and md.live.status()["live"])
        return out

    # ------------------------------------------------------------ sector money-flow monitor
    flow_hist: dict[str, list[tuple[float, float]]] = {}      # symbol -> [(monotonic, flow_cr)] while live
    SURGE_WINDOW = 300.0

    def is_live() -> bool:
        return bool(md.live is not None and md.live.status()["live"])

    @app.get("/api/pulse/sectors")
    def sectors(info=Depends(need("portfolio:read"))):
        smap = sector_map()
        if not smap:
            raise HTTPException(404, "no sectors in config/universe.yaml")
        live = md.live is not None and md.live.adapter() is not None
        key = "sectors"
        hit = cache.get(key)
        if hit and time.monotonic() - hit[0] < (LIVE_SCREEN_TTL if live else SCREEN_TTL):
            return hit[1]
        if live:
            try:
                md.live.prefetch(list(smap))
            except Exception:
                pass

        def one(sym):
            try:
                return sym, daily(sym)
            except Exception:
                return sym, None

        with ThreadPoolExecutor(max_workers=8) as ex:
            got = dict(ex.map(one, list(smap)))
        stocks = {s: (smap[s], b) for s, b in got.items() if b}
        out = pl.sector_flows(stocks)
        out["failed"] = sorted(s for s, b in got.items() if not b)
        live_now = is_live()
        out["live"], out["as_of_time"] = live_now, datetime.now(IST).strftime("%H:%M:%S")
        out["source"] = ("Kite real-time quotes (today's value traded and range)" if live_now
                         else "Yahoo daily bars (delayed; last session if the market is closed)")
        out["live_reason"] = None if live_now else (md.live.status()["reason"] if md.live else "Delayed")
        # surge: change in a stock's money flow per minute, measured between live refreshes only
        surge = []
        now = time.monotonic()
        if live_now:
            for r in out["stocks"]:
                h = flow_hist.setdefault(r["symbol"], [])
                h.append((now, r["flow_cr"]))
                while h and now - h[0][0] > SURGE_WINDOW:
                    h.pop(0)
                if len(h) >= 2 and h[-1][0] - h[0][0] >= 30:
                    rate = (h[-1][1] - h[0][1]) / ((h[-1][0] - h[0][0]) / 60)
                    if rate:
                        surge.append({"symbol": r["symbol"], "sector": r["sector"], "cr_per_min": round(rate, 2)})
        else:
            flow_hist.clear()
        surge.sort(key=lambda x: -abs(x["cr_per_min"]))
        out["surge"] = surge[:8]
        del out["stocks"]
        cache[key] = (now, out)
        return out

    # ------------------------------------------------------------ chart buy / exit marks by style
    @app.get("/api/market/signals")
    def chart_signals(symbol: str, style: str = "intraday", strategy: str = "orb", info=Depends(need("portfolio:read"))):
        from ..analysis import chart_signals as cs
        if style not in cs.STYLES:
            raise HTTPException(422, f"style must be one of {sorted(cs.STYLES)}")
        if strategy not in st.STRATEGIES:
            raise HTTPException(422, f"strategy must be one of {sorted(st.STRATEGIES)}")
        sym = symbol.strip().upper()[:40]
        iv, rng = {"intraday": ("5m", None), "options": ("5m", None), "swing": ("1d", "2y"), "longterm": ("1d", "10y")}[style]
        try:
            d = md.chart(sym, iv, rng) if rng else md.chart(sym, iv)
        except Exception as e:
            raise HTTPException(502, f"market data unavailable for {sym}: {e}")
        bars = d["bars"]
        if style in ("intraday", "options"):
            trades = cs.intraday_trades(bars, strategy)
        elif style == "swing":
            trades = cs.swing_trades(bars)
        else:
            trades = cs.longterm_trades(bars)
        extra = {}
        if style == "options":
            step = None
            try:
                day, rows = fo().latest()
                opts = [r for r in rows if r["symbol"] == sym and r["type"] in ("STO", "IDO")]
                if opts:
                    near = min(r["expiry"] for r in opts)
                    step = cs.strike_step([r["strike"] for r in opts if r["expiry"] == near])
                    extra = {"expiry": near, "strike_step": step, "options_day": day.isoformat()}
                else:
                    extra = {"note": f"{sym} has no options on NSE; strikes are shown as ATM."}
            except LookupError:
                extra = {"note": "NSE F&O file unavailable; strikes are shown as ATM."}
            trades = [cs.option_leg(t, step) for t in trades]
        closes = [b.c for b in bars]
        hold = round((closes[-1] - closes[0]) / closes[0] * 100, 2) if len(closes) > 1 and closes[0] else None
        return {"symbol": sym, "style": style, "style_name": cs.STYLES[style], "interval": iv, "strategy": strategy,
                "bars": [b.to_dict() for b in bars], "last": d["last"], "live": bool(d.get("live")),
                "source": d.get("source", "Yahoo Finance (delayed)"), "markers": cs.markers(trades, style),
                "trades": list(reversed(trades[-30:])), "stats": cs.stats(trades), "buy_and_hold_pct": hold,
                "period": {"from": bars[0].t if bars else None, "to": bars[-1].t if bars else None},
                "now": cs.current(trades, style, bars[-1] if bars else None, closes), "rules": cs.RULES[style], **extra}

    @app.get("/api/pulse/assets")
    def assets(info=Depends(need("portfolio:read"))):
        items = config().get("assets") or []
        rows, failed = [], []
        fx = None
        for a in items:
            sym, ex = str(a["symbol"]).upper(), a.get("exchange", "NSE")
            try:
                d = md.chart(sym, "1d", "1y", exchange=ex)
                bars = d["bars"]
                closes = [b.c for b in bars]
                last = d["last"] if d["last"] is not None else (closes[-1] if closes else None)
                prev = closes[-2] if len(closes) >= 2 else None

                def ret(n):
                    return round((last - closes[-n - 1]) / closes[-n - 1] * 100, 2) if last and len(closes) > n and closes[-n - 1] else None

                row = {"symbol": sym, "name": a.get("name", sym), "last": last, "currency": d.get("currency"),
                       "change_pct": round((last - prev) / prev * 100, 2) if last and prev else None,
                       "ret_1m": ret(21), "ret_1y": ret(len(closes) - 1) if len(closes) > 30 else None,
                       "rsi": pl.rsi(closes), "high_52w": max(b.h for b in bars) if bars else None,
                       "low_52w": min(b.l for b in bars) if bars else None, "live": bool(d.get("live")),
                       "spark": [round(x, 4) for x in closes[-60:]]}
                rows.append(row)
                if sym == "USDINR":
                    fx = last
            except Exception as e:
                failed.append({"symbol": sym, "error": type(e).__name__})
        derived = []
        gold = next((r for r in rows if r["symbol"] == "GOLD"), None)
        silver = next((r for r in rows if r["symbol"] == "SILVER"), None)
        if fx and gold and gold["last"]:
            derived.append({"name": "Gold, international parity (₹ per 10 g)", "value": round(gold["last"] * fx / 31.1035 * 10, 0)})
        if fx and silver and silver["last"]:
            derived.append({"name": "Silver, international parity (₹ per kg)", "value": round(silver["last"] * fx / 31.1035 * 1000, 0)})
        return {"rows": rows, "failed": failed, "derived": derived, "live": is_live(),
                "as_of_time": datetime.now(IST).strftime("%H:%M:%S"),
                "source": "Yahoo Finance (delayed) for global prices; Kite real time for NSE ETFs when logged in"}

    eod_memo: dict[tuple, object] = {}

    def eod(day, name, *args):
        """End-of-day results never change for a given file: compute once per trading day."""
        key = (day.isoformat(), name, *args)
        if key not in eod_memo:
            if len(eod_memo) > 400:
                eod_memo.clear()
            fn = {"flow": oi_flow, "pulse": option_pulse, "active": most_active, "symbols": option_symbols}[name]
            eod_memo[key] = fn(bhav_rows(day), *args)
        return eod_memo[key]

    def bhav_rows(day):
        return fo().load(day) or []

    def bhav():
        try:
            return fo().latest()
        except LookupError as e:
            raise HTTPException(503, f"{e}. It is published by NSE each evening; check the internet connection.")

    # ------------------------------------------------------------ live OI (Kite) on top of the last EOD file
    live_cache: dict[tuple, tuple[float, object]] = {}

    def today_ist() -> str:
        return desk.clock().astimezone(IST).date().isoformat()

    def live_wanted(day) -> bool:
        """Live OI only when Kite is logged in and the newest EOD file is older than today
        (after the evening file is out, the day is final and the file is the better source)."""
        return md.live is not None and md.live.adapter() is not None and day.isoformat() < today_ist()

    def nfo() -> list[dict]:
        try:
            return md.live.instruments("NFO")
        except Exception:
            return []

    def cached(key, ttl, fn):
        hit = live_cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = fn()
        live_cache[key] = (time.monotonic(), val)
        return val

    def live_flow(rows):
        today = today_ist()
        futs: dict[str, dict] = {}
        for i in nfo():
            if i["type"] == "FUT" and i["expiry"] >= today and (i["name"] not in futs or i["expiry"] < futs[i["name"]]["expiry"]):
                futs[i["name"]] = i
        if not futs:
            return None
        eod = {(r["symbol"], r["expiry"]): r for r in rows if r["type"] in ("STF", "IDF")}
        qs = md.live.quotes(["NFO:" + f["tradingsymbol"] for f in futs.values()])
        items = []
        for name, f in futs.items():
            q, e = qs.get("NFO:" + f["tradingsymbol"]), eod.get((name, f["expiry"]))
            if not q or not e or q.get("oi") is None or not q.get("prev_close"):
                continue
            it = flow_item(name, e["type"] == "IDF", q["last"], q["prev_close"], float(q["oi"]), e["oi"])
            if it:
                items.append(it)
        return group(items) if items else None

    def live_chain(rows, symbol: str, expiry: str | None):
        today = today_ist()
        ins = [i for i in nfo() if i["name"] == symbol and i["type"] in ("CE", "PE") and i["expiry"] >= today]
        if not ins:
            return None
        exps = sorted({i["expiry"] for i in ins})
        exp = expiry if expiry in exps else exps[0]
        ch = [i for i in ins if i["expiry"] == exp]
        qs = md.live.quotes(["NFO:" + i["tradingsymbol"] for i in ch])
        und = (md.live.get(symbol) or {}).get("last")
        eod = {(r["strike"], r["opt"]): r for r in rows if r["symbol"] == symbol and r["expiry"] == exp}
        tp = "IDO" if symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY") else "STO"
        synth = []
        for i in ch:
            q = qs.get("NFO:" + i["tradingsymbol"])
            if not q or q.get("oi") is None:
                continue
            prev = eod.get((i["strike"], i["type"]))
            synth.append({"type": tp, "symbol": symbol, "expiry": exp, "strike": i["strike"], "opt": i["type"],
                          "close": q["last"], "prev_close": q["prev_close"], "underlying": und or 0, "oi": float(q["oi"]),
                          "chg_oi": float(q["oi"]) - (prev["oi"] if prev else 0.0), "volume": float(q.get("volume") or 0),
                          "value": 0.0, "lot": float(i.get("lot") or 0), "date": today})
        if not synth:
            return None
        p = option_pulse(synth, symbol, exp)
        p["expiries"] = exps
        return p

    def stamp():
        return datetime.now(IST).strftime("%H:%M:%S")

    @app.get("/api/pulse/oi-flow")
    def oiflow(info=Depends(need("portfolio:read"))):
        day, rows = bhav()
        if live_wanted(day):
            live = cached(("flow",), 15, lambda: live_flow(rows))
            if live:
                return {"date": today_ist(), **live, "live": True, "as_of": stamp(),
                        "source": f"Kite live futures OI vs NSE close of {day.isoformat()}"}
        return {"date": day.isoformat(), **eod(day, "flow"), "live": False, "source": "NSE F&O bhavcopy (end of day)",
                "live_reason": md.live.status()["reason"] if md.live else None}

    @app.get("/api/pulse/options")
    def options(symbol: str = "NIFTY", expiry: str | None = None, info=Depends(need("portfolio:read"))):
        day, rows = bhav()
        sym = symbol.strip().upper()
        live = live_wanted(day)

        def pick(s, e=None):
            if live:
                lp = cached(("chain", s, e), 15, lambda: live_chain(rows, s, e))
                if lp:
                    return lp, True
            return eod(day, "pulse", s, e), False

        p, is_live = pick(sym, expiry)
        if not p:
            raise HTTPException(404, f"{sym} has no options on NSE")
        idx = {s: pick(s)[0] for s in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")}
        return {"date": today_ist() if is_live else day.isoformat(), "pulse": p, "live": is_live,
                "as_of": stamp() if is_live else None,
                "indices": {k: {kk: v[kk] for kk in ("expiry", "underlying", "pcr", "max_pain", "call_wall", "put_wall")}
                            for k, v in idx.items() if v},
                "most_active": eod(day, "active"), "most_active_date": day.isoformat(), "symbols": eod(day, "symbols"),
                "source": (f"Kite live option OI (change vs NSE close of {day.isoformat()})" if is_live
                           else "NSE F&O bhavcopy (end of day)"),
                "live_reason": None if is_live else (md.live.status()["reason"] if md.live else None)}
