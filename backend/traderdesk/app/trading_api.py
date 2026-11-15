"""Trading screens: market data, desk overview, positions, alerts, options, charges, signals,
briefs, analysis providers and the scheduler that runs them."""
from __future__ import annotations

import logging
import math
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..analysis import brief as briefs
from ..analysis import strategies as st
from ..core import audit
from ..core.errors import BrokerError
from ..core.models import OrderRequest, OrderType, Product, Segment, Side
from ..db.models import Account, AppState, Brief, DeskOrder, Signal
from ..orders.charges import ChargesEngine
from ..orders.pnl import fills_from_rows, match
from ..plugins.paper.adapter import PaperBroker

log = logging.getLogger("traderdesk.trading")
OPEN = ("OPEN", "TRIGGER_PENDING", "PENDING")
DEFAULT_WATCHLIST = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "LT", "ITC", "AXISBANK",
                     "BHARTIARTL"]
SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"


def state_get(db, key: str, default):
    row = db.get(AppState, key)
    return row.value if row else default


def state_set(db, key: str, value) -> None:
    row = db.get(AppState, key)
    if row:
        row.value = value
    else:
        db.add(AppState(key=key, value=value))


class ExitBody(BaseModel):
    account_id: str
    exchange: str
    tradingsymbol: str
    product: str
    quantity: float


class Leg(BaseModel):
    side: str
    type: str            # CE | PE
    strike: float
    premium: float
    lots: int = 1


class PayoffBody(BaseModel):
    legs: list[Leg]
    lot_size: int = 75
    spot: float


class BasketBody(BaseModel):
    account_id: str
    underlying: str = "NIFTY"
    expiry: str            # YYYY-MM-DD
    lot_size: int = 75
    legs: list[Leg]
    exchange: str = "NFO"
    symbols: list[str] | None = None   # exact tradingsymbols, one per leg (from the chain)


class CalcBody(BaseModel):
    broker: str = "kite"
    segment: str = "eq_intraday"
    exchange: str = "NSE"
    quantity: float
    buy: float
    sell: float


class StrategiesBody(BaseModel):
    strategies: dict[str, dict]


class WatchBody(BaseModel):
    symbols: list[str]


class StatusBody(BaseModel):
    status: str


class RunBody(BaseModel):
    kind: str = "pre"


class PipelineBody(BaseModel):
    pipeline: dict


def register(app, c) -> None:
    Db, need, desk, adapters, md = c.Db, c.need, c.desk, c.adapters, c.md
    charges = ChargesEngine()

    def now():
        return desk.clock()

    def today() -> date:
        return now().date()

    # ------------------------------------------------------------ market data
    @app.get("/api/market/chart")
    def chart(symbol: str, interval: str = "5m", exchange: str = "NSE", info=Depends(need("portfolio:read"))):
        try:
            d = md.chart(symbol, interval, exchange=exchange)
        except Exception as e:
            raise HTTPException(502, f"market data unavailable for {symbol}: {e}")
        bars = d["bars"]
        out = {"symbol": d["symbol"], "currency": d["currency"], "last": d["last"], "prev_close": d["prev_close"],
               "bars": [b.to_dict() for b in bars], "source": "Yahoo Finance (delayed)"}
        if interval in ("1m", "5m", "15m") and bars:
            day = bars[-1].ist().date().isoformat()
            sess = st.sessions(bars).get(day, [])
            vw = st.vwap_series(sess)
            out["vwap"] = [{"time": b.t, "value": round(v, 2)} for b, v in zip(sess, vw)]
            rng = [b for b in sess if b.ist().time() < st.OR_END]
            if rng:
                out["opening_range"] = {"high": max(b.h for b in rng), "low": min(b.l for b in rng),
                                        "from": rng[0].t, "to": rng[-1].t}
        if len(bars) >= 2:
            if interval in ("1m", "5m", "15m", "1h", "1H"):
                days = list(st.sessions(bars).values())
                ref = days[-2][-1].c if len(days) >= 2 else None   # previous session's last close
            else:
                ref = bars[-2].c
            if ref:
                out["change_pct"] = round((d["last"] - ref) / ref * 100, 2)
        return out

    @app.get("/api/market/odds")
    def odds(symbol: str, strategy: str = "orb", info=Depends(need("portfolio:read"))):
        if strategy not in st.STRATEGIES:
            raise HTTPException(400, "unknown strategy")
        try:
            bars = md.chart(symbol, "5m")["bars"]
        except Exception as e:
            raise HTTPException(502, f"market data unavailable: {e}")
        return {"symbol": symbol.upper(), "strategy": strategy, **st.backtest(bars, strategy, skip_day=today().isoformat())}

    @app.get("/api/market/quotes")
    def quotes(symbols: str, info=Depends(need("portfolio:read"))):
        return md.quotes([s for s in symbols.split(",") if s.strip()][:30])

    # ------------------------------------------------------------ positions, funds, alerts
    def _routes(db) -> list[tuple[Account, Any, bool]]:
        out, seen = [], set()
        for acct in db.execute(select(Account)).scalars():
            if acct.id not in adapters:
                continue
            try:
                a, paper = desk.broker_for(db, acct.id)
            except Exception:
                continue
            if id(a) in seen:
                continue
            seen.add(id(a))
            out.append((acct, a, paper))
        return out

    def _plans(db) -> dict:
        """Latest sent entry per symbol: its stop and target."""
        rows = db.execute(select(DeskOrder).where(DeskOrder.stage == "sent", DeskOrder.kind == "entry")
                          .order_by(DeskOrder.id.desc()).limit(300)).scalars()
        plans = {}
        for r in rows:
            q = r.request or {}
            plans.setdefault(q.get("tradingsymbol"), {"stop": q.get("stop"), "target": q.get("target"),
                                                       "oco": r.oco_gtt_id})
        return plans

    def positions_all(db) -> list[dict]:
        plans = _plans(db)
        out = []
        for acct, a, paper in _routes(db):
            try:
                ps = list(a.positions())
                if isinstance(a, PaperBroker):
                    ps += [type("P", (), {"exchange": h.exchange, "tradingsymbol": h.tradingsymbol,
                                          "product": Product.CNC, "quantity": h.quantity,
                                          "average_price": h.average_price, "last_price": h.last_price,
                                          "pnl": (h.last_price - h.average_price) * h.quantity})()
                           for h in a.holdings()]
            except BrokerError as e:
                out.append({"account_id": acct.id, "broker": acct.name, "error": e.message})
                continue
            for p in ps:
                pl = plans.get(p.tradingsymbol, {})
                stop = pl.get("stop")
                risk = (p.average_price - stop) if stop and p.quantity > 0 else (stop - p.average_price) if stop else None
                r_mult = round((p.last_price - p.average_price) / risk * (1 if p.quantity > 0 else -1), 2) \
                    if risk and risk > 0 else None
                out.append({"account_id": acct.id, "broker": acct.name + (" (paper)" if paper and acct.plugin != "paper" else ""),
                            "paper": paper, "exchange": p.exchange, "tradingsymbol": p.tradingsymbol,
                            "product": getattr(p.product, "value", p.product), "quantity": p.quantity,
                            "average_price": round(p.average_price, 2), "last_price": round(p.last_price, 2),
                            "pnl": round(p.pnl, 2), "stop": stop, "target": pl.get("target"), "r": r_mult,
                            "oco": bool(pl.get("oco"))})
        return out

    def funds_all(db) -> list[dict]:
        out = []
        for acct in db.execute(select(Account)).scalars():
            a = adapters.get(acct.id)
            st_ = {"connected": True}
            if a is not None and hasattr(a, "session_status"):
                st_ = a.session_status()
            row = {"id": acct.id, "name": acct.name, "plugin": acct.plugin, "live": acct.live,
                   "connected": st_.get("connected", True), "needs_login": st_.get("needs_login", False)}
            if a is not None and hasattr(a, "funds") and row["connected"]:
                try:
                    row.update(a.funds())
                except BrokerError as e:
                    row["error"] = e.message
            out.append(row)
        return out

    def alerts_for(db) -> list[dict]:
        out = []
        _, rules = desk.rules(db)
        stats = desk._today(db)
        lim = rules.get("max_daily_loss") or 0
        if lim:
            used = stats.loss_at_risk / lim * 100
            if used >= 25:
                out.append({"kind": "risk", "level": "warn" if used < 100 else "bad",
                            "text": f"Daily loss limit {used:.0f}% used", "at": None})
        start = datetime.combine(today(), datetime.min.time()) - timedelta(hours=6)
        for r in db.execute(select(DeskOrder).where(DeskOrder.updated_at >= start)
                            .order_by(DeskOrder.updated_at.desc()).limit(50)).scalars():
            q = r.request or {}
            t = (r.updated_at + timedelta(hours=5, minutes=30)).strftime("%H:%M")
            if r.stage == "sent" and r.status == "COMPLETE":
                out.append({"kind": "fill", "level": "good", "at": t,
                            "text": f"{q.get('side')} {q.get('quantity')} {q.get('tradingsymbol')} filled"
                                    + (" · OCO armed" if r.oco_gtt_id else "")})
            elif r.stage == "blocked":
                out.append({"kind": "block", "level": "bad", "at": t,
                            "text": f"{q.get('tradingsymbol')} blocked: {((r.verdict or {}).get('reasons') or ['risk'])[0]}"})
            elif r.status == "REJECTED":
                out.append({"kind": "reject", "level": "bad", "at": t,
                            "text": f"{q.get('tradingsymbol')} rejected: {r.error or ''}"})
        mis_after = rules.get("no_new_mis_after")
        if mis_after:
            out.append({"kind": "schedule", "level": "info", "at": "scheduled",
                        "text": f"No new MIS after {mis_after}; square off intraday before 15:20"})
        for s in db.execute(select(Signal).where(Signal.day == today().isoformat(), Signal.status == "waiting")).scalars():
            out.append({"kind": "signal", "level": "info", "at": s.at,
                        "text": f"{s.side} signal {s.symbol} · {s.strategy} waiting for you"})
        return out[:30]

    @app.get("/api/positions")
    def positions(info=Depends(need("orders:read"))):
        with Db() as db:
            return positions_all(db)

    @app.get("/api/alerts")
    def alerts(info=Depends(need("orders:read"))):
        with Db() as db, db.begin():
            return alerts_for(db)

    @app.post("/api/positions/exit")
    def exit_position(body: ExitBody, info=Depends(need("orders:ticket"))):
        side = Side.SELL if body.quantity > 0 else Side.BUY
        fo = body.exchange in ("NFO", "BFO", "MCX")
        it = "CE" if body.tradingsymbol.endswith("CE") else "PE" if body.tradingsymbol.endswith("PE") else \
            "FUT" if body.tradingsymbol.endswith("FUT") else "EQ"
        o = OrderRequest(body.exchange, body.tradingsymbol, side, int(abs(body.quantity)), OrderType.MARKET,
                         Product(body.product), instrument_type=it if fo else "EQ")
        r = desk.ticket(body.account_id, o, info.username)
        return {"id": r.id}

    @app.get("/api/desk/overview")
    def overview(info=Depends(need("orders:read"))):
        with Db() as db, db.begin():
            pos = positions_all(db)
            open_rows = db.execute(select(DeskOrder).where(DeskOrder.stage == "sent")
                                   .order_by(DeskOrder.id.desc()).limit(100)).scalars()
            open_orders = []
            for r in open_rows:
                q = r.request or {}
                held = any(p.get("tradingsymbol") == q.get("tradingsymbol") and p.get("quantity") for p in pos)
                if r.status in OPEN or (r.oco_gtt_id and held):
                    open_orders.append({"id": r.id, "tag": r.client_tag, "time": (r.sent_at or r.created_at)
                                        .replace(tzinfo=None).isoformat() + "Z", "order": q, "status": r.status,
                                        "oco": r.oco_gtt_id if held else None, "paper": r.paper})
            pre = db.execute(select(Brief).where(Brief.kind == "pre").order_by(Brief.id.desc()).limit(1)).scalar_one_or_none()
            plans = [{"id": s.id, "symbol": s.symbol, "strategy": s.strategy, "side": s.side, "entry": s.entry,
                      "stop": s.stop, "target": s.target, "status": s.status, "at": s.at}
                     for s in db.execute(select(Signal).where(Signal.day == today().isoformat(),
                                                              Signal.status.in_(["waiting", "ticketed"]))
                                         .order_by(Signal.id.desc()).limit(6)).scalars()]
            out = {"positions": pos, "open_orders": open_orders, "accounts": funds_all(db), "alerts": alerts_for(db),
                   "plans": plans, "day_pnl": round(sum(p.get("pnl", 0) for p in pos if "pnl" in p), 2),
                   "brief": ({"id": pre.id, "day": pre.day, "headline": pre.body.get("headline"),
                              "summary": pre.body.get("summary"), "day_type": pre.body.get("day_type"),
                              "created_at": pre.created_at.isoformat() + "Z"} if pre else None)}
        out["indices"] = md.quotes(["NIFTY", "BANKNIFTY", "INDIAVIX"])
        return out

    # ------------------------------------------------------------ options
    def payoff(legs: list[Leg], lot: int, spot: float) -> dict:
        strikes = sorted({l.strike for l in legs}) or [spot]
        lo, hi = min(strikes + [spot]) * 0.96, max(strikes + [spot]) * 1.04
        n = 120
        xs = [lo + (hi - lo) * i / n for i in range(n + 1)] + strikes
        xs = sorted(set(round(x, 2) for x in xs))

        def pnl_at(x):
            tot = 0.0
            for l in legs:
                intrinsic = max(0.0, x - l.strike) if l.type == "CE" else max(0.0, l.strike - x)
                per = intrinsic - l.premium
                tot += (per if l.side == "BUY" else -per) * l.lots * lot
            return tot

        ys = [pnl_at(x) for x in xs]
        # slope beyond the plotted range decides whether profit or loss is unlimited
        far_up = pnl_at(hi * 10) - pnl_at(hi * 9)
        far_dn = pnl_at(lo * 0.1) - pnl_at(lo * 0.2)
        be = []
        for i in range(1, len(xs)):
            if (ys[i - 1] < 0) != (ys[i] < 0) and ys[i] != ys[i - 1]:
                be.append(round(xs[i - 1] + (xs[i] - xs[i - 1]) * (-ys[i - 1]) / (ys[i] - ys[i - 1]), 2))
        net = sum((l.premium if l.side == "SELL" else -l.premium) * l.lots * lot for l in legs)
        return {"points": [{"x": x, "y": round(y, 2)} for x, y in zip(xs, ys)],
                "max_profit": None if far_up > 1e-6 or far_dn > 1e-6 else round(max(ys), 2),
                "max_loss": None if far_up < -1e-6 or far_dn < -1e-6 else round(min(ys), 2),
                "breakevens": be, "net_premium": round(net, 2)}

    @app.post("/api/options/payoff")
    def options_payoff(body: PayoffBody, info=Depends(need("portfolio:read"))):
        return payoff(body.legs, body.lot_size, body.spot)

    @app.get("/api/options/chain")
    def options_chain(underlying: str = "NIFTY", expiry: str | None = None, info=Depends(need("portfolio:read"))):
        """Live chain needs a broker with quotes (Kite Connect plan). Without one, say so plainly."""
        with Db() as db:
            kite = [a for acct in db.execute(select(Account).where(Account.plugin == "kite")).scalars()
                    if (a := adapters.get(acct.id)) is not None and a.session_status().get("connected")]
        spot = None
        try:
            spot = md.quote(underlying)["last"]
        except Exception:
            pass
        if not kite:
            return {"underlying": underlying, "spot": spot, "rows": [], "available": False,
                    "reason": "Log in to Kite for the option chain."}
        k = kite[0]
        try:
            inst = [i for i in k.kite.instruments("NFO") if i.get("name") == underlying.upper()
                    and i.get("instrument_type") in ("CE", "PE")]
            expiries = sorted({str(i["expiry"]) for i in inst})
            exp = expiry or (expiries[0] if expiries else None)
            chosen = [i for i in inst if str(i["expiry"]) == exp]
            if spot:
                chosen = sorted(chosen, key=lambda i: abs(i["strike"] - spot))[:44]
            keys = [f"NFO:{i['tradingsymbol']}" for i in chosen]
            q = k.kite.quote(keys) if keys else {}
        except Exception as e:
            return {"underlying": underlying, "spot": spot, "rows": [], "available": False,
                    "reason": f"Kite returned: {e}. Quotes need the Kite Connect (paid) plan."}
        rows: dict[float, dict] = {}
        for i in chosen:
            d = q.get(f"NFO:{i['tradingsymbol']}") or {}
            r = rows.setdefault(i["strike"], {"strike": i["strike"]})
            r[i["instrument_type"]] = {"symbol": i["tradingsymbol"], "ltp": d.get("last_price"),
                                       "oi": d.get("oi"), "lot": i.get("lot_size")}
        lot = chosen[0]["lot_size"] if chosen else None
        return {"underlying": underlying, "spot": spot, "expiry": exp, "expiries": expiries, "lot_size": lot,
                "rows": sorted(rows.values(), key=lambda r: r["strike"]), "available": True}

    @app.post("/api/options/basket")
    def options_basket(body: BasketBody, info=Depends(need("orders:ticket"))):
        """Hedge-first: every BUY leg is ticketed before any SELL leg. Each still needs its own confirm."""
        exp = date.fromisoformat(body.expiry)
        order = sorted(range(len(body.legs)), key=lambda i: 0 if body.legs[i].side == "BUY" else 1)
        ids = []
        for i in order:
            l = body.legs[i]
            sym = (body.symbols or [None] * len(body.legs))[i] or \
                f"{body.underlying.upper()}{exp:%y}{exp.strftime('%b').upper()}{int(l.strike)}{l.type}"
            o = OrderRequest(body.exchange, sym, Side(l.side), l.lots * body.lot_size, OrderType.LIMIT, Product.NRML,
                             price=l.premium, instrument_type=l.type, lot_size=body.lot_size, tick_size=0.05,
                             expiry=body.expiry, plan_id="basket")
            r = desk.ticket(body.account_id, o, info.username)
            ids.append(r.id)
        return {"tickets": ids, "order": "hedge-first (buys before sells)"}

    # ------------------------------------------------------------ charges
    RULE_TEXT = {"brokerage": "per the broker's table", "stt": "securities transaction tax",
                 "exchange_txn": "exchange transaction charge", "sebi": "₹10 per crore", "stamp": "on buy",
                 "gst": "18% on brokerage + exchange + SEBI", "dp": "per scrip, on delivery sells"}

    @app.post("/api/charges/calc")
    def charges_calc(body: CalcBody, info=Depends(need("portfolio:read"))):
        seg = Segment(body.segment)
        on = today()
        b = charges.compute(body.broker, Side.BUY, seg, body.exchange, body.buy, body.quantity, on)
        s = charges.compute(body.broker, Side.SELL, seg, body.exchange, body.sell, body.quantity, on)
        rows = []
        for k in ("brokerage", "stt", "exchange_txn", "sebi", "stamp", "gst", "dp"):
            v = getattr(b, k) + getattr(s, k)
            if v or k in ("brokerage", "stt", "gst"):
                rows.append({"charge": k, "rule": RULE_TEXT[k], "amount": round(v, 2)})
        total = round(b.total + s.total, 2)
        gross = round((body.sell - body.buy) * body.quantity, 2)
        return {"rows": rows, "total": total, "gross": gross, "net": round(gross - total, 2),
                "charges_pct_of_gross": round(total / gross * 100, 1) if gross > 0 else None,
                "breakeven_move": round(total / body.quantity, 2) if body.quantity else None,
                "table_version": charges.table.get("version"), "brokers": list(charges.table["brokers"].keys())}

    @app.get("/api/charges/month")
    def charges_month(month: str | None = None, info=Depends(need("portfolio:read"))):
        month = month or today().strftime("%Y-%m")
        y, m = map(int, month.split("-"))
        start = datetime(y, m, 1)
        end = datetime(y + (m == 12), m % 12 + 1, 1)
        with Db() as db:
            rows = list(db.execute(select(DeskOrder).where(DeskOrder.stage == "sent", DeskOrder.status == "COMPLETE",
                                                           DeskOrder.sent_at >= start, DeskOrder.sent_at < end)).scalars())
            accts = {a.id: a.plugin for a in db.execute(select(Account)).scalars()}
        fills = fills_from_rows(rows)
        closed = match(fills)
        parts: dict[str, float] = {}
        for r in rows:
            for k, v in ((r.charges or {}).get("entry") or {}).items():
                if k != "total":
                    parts[k] = parts.get(k, 0) + v
        total = round(sum(parts.values()), 2)
        gross = round(sum(c.pnl for c in closed), 2)
        compare = []
        for broker in charges.table["brokers"]:
            if broker == "paper":
                continue
            t = 0.0
            for f in fills:
                seg = OrderRequest(f.exchange, f.symbol, Side(f.side), int(f.qty), product=Product(f.product),
                                   instrument_type=f.instrument_type).segment
                t += charges.compute(broker, Side(f.side), seg, f.exchange, f.price, f.qty, f.at.date()).total
            compare.append({"broker": broker, "charges": round(t, 2)})
        return {"month": month, "orders": len(rows), "paper_orders": sum(1 for r in rows if r.paper),
                "gross": gross, "charges": total, "net": round(gross - total, 2),
                "costs_pct": round(total / gross * 100, 1) if gross > 0 else None,
                "parts": {k: round(v, 2) for k, v in sorted(parts.items(), key=lambda kv: -kv[1])},
                "compare": compare, "current_broker": next((p for p in accts.values() if p != "paper"), "paper"),
                "contract_notes": {"available": False, "reason": "Upload contract notes to match (not built yet)."}}

    # ------------------------------------------------------------ signals
    def strategies_state(db) -> dict:
        s = state_get(db, "strategies", {})
        for k in st.STRATEGIES:
            s.setdefault(k, {"enabled": k == "orb", "mode": "one_click"})
        return s

    def watchlist(db) -> list[str]:
        return state_get(db, "watchlist", {"symbols": DEFAULT_WATCHLIST})["symbols"]

    def backtests(db, refresh: bool = False) -> dict:
        cache = state_get(db, "backtests", {})
        if cache.get("day") == today().isoformat() and not refresh:
            return cache
        res: dict[str, Any] = {"day": today().isoformat(), "by_symbol": {}, "by_strategy": {}}
        agg: dict[str, list] = {k: [] for k in st.STRATEGIES}
        for sym in watchlist(db):
            try:
                bars = md.chart(sym, "5m")["bars"]
            except Exception:
                continue
            for k in st.STRATEGIES:
                bt = st.backtest(bars, k, skip_day=today().isoformat())
                res["by_symbol"][f"{sym}:{k}"] = bt
                if bt["trades"]:
                    agg[k].append(bt)
        for k, lst in agg.items():
            n = sum(b["trades"] for b in lst)
            res["by_strategy"][k] = {"trades": n, "symbols": len(lst),
                                     "target_pct": round(sum(b["target_pct"] * b["trades"] for b in lst) / n, 1) if n else None,
                                     "expectancy_r": round(sum(b["expectancy_r"] * b["trades"] for b in lst) / n, 2) if n else None}
        state_set(db, "backtests", res)
        return res

    def regime_now() -> dict:
        try:
            daily = md.chart("NIFTY", "1d", "2y")["bars"]
            vix = md.quote("INDIAVIX").get("last")
            return st.regime(daily, vix)
        except Exception as e:
            return {"score": None, "label": "Unavailable", "parts": [], "error": str(e)}

    def scan(user: str = "system") -> int:
        added = 0
        with Db() as db, db.begin():
            strat = strategies_state(db)
            syms = watchlist(db)
            bts = backtests(db)
        for sym in syms:
            try:
                bars = md.chart(sym, "5m")["bars"]
            except Exception:
                continue
            for k, cfg in strat.items():
                if not cfg.get("enabled"):
                    continue
                s = st.today_setup(bars, k, today().isoformat())
                if not s:
                    continue
                bt = bts["by_symbol"].get(f"{sym}:{k}") or bts["by_strategy"].get(k) or {}
                try:
                    with Db() as db, db.begin():
                        db.add(Signal(day=today().isoformat(), at=s.at, symbol=sym, strategy=s.strategy, side=s.side,
                                      entry=s.entry, stop=s.stop, target=s.target,
                                      confidence=bt.get("target_pct") or 0, reason=s.reason,
                                      status="alerted" if cfg.get("mode") == "alert_only" else "waiting"))
                        audit.record(db, "signals", "signal", {"symbol": sym, "strategy": s.strategy, "side": s.side,
                                                               "entry": s.entry}, user)
                    added += 1
                except IntegrityError:
                    pass
        return added

    def gate(db, s: Signal, reg: dict) -> dict:
        _, rules = desk.rules(db)
        stats = desk._today(db)
        lim = rules.get("max_daily_loss") or 0
        used = stats.loss_at_risk / lim * 100 if lim else 0
        strat = strategies_state(db)
        key = "orb" if s.strategy == "ORB" else "vwap"
        risk = abs((s.entry or 0) - (s.stop or 0))
        ceiling = (reg.get("exposure_ceiling") or 100) / 100
        qty = int(math.floor((rules.get("max_loss_per_trade") or 0) * ceiling / risk)) if risk else 0
        t = now().time()
        window = rules.get("no_entries_before", "09:20") <= t.strftime("%H:%M") <= rules.get("no_entries_after", "15:10")
        checks = [
            {"ok": bool(strat.get(key, {}).get("enabled")), "text": "Setup is in your strategy library, not ad hoc"},
            {"ok": reg.get("score") is None or reg["score"] >= 40,
             "text": f"Regime allows new risk (score {reg.get('score')}, ceiling {reg.get('exposure_ceiling')}%)"},
            {"ok": used < 100 and not desk.kill_switch_on(db), "text": f"Circuit breaker open: day loss {used:.0f}% of limit"},
            {"ok": qty >= 1, "text": f"Size {qty} within ₹{rules.get('max_loss_per_trade')} max loss after regime cut"},
            {"ok": window, "text": f"Inside entry window {rules.get('no_entries_before')}-{rules.get('no_entries_after')}"},
        ]
        return {"checks": checks, "passed": sum(c["ok"] for c in checks), "size": qty,
                "unchecked": ["Results / RBI events (no event calendar connected)"]}

    @app.get("/api/signals")
    def signals(day: str | None = None, info=Depends(need("orders:read"))):
        day = day or today().isoformat()
        with Db() as db:
            rows = db.execute(select(Signal).where(Signal.day == day).order_by(Signal.id.desc())).scalars()
            return [{"id": s.id, "at": s.at, "symbol": s.symbol, "strategy": s.strategy, "side": s.side,
                     "entry": s.entry, "stop": s.stop, "target": s.target, "confidence": s.confidence,
                     "reason": s.reason, "status": s.status} for s in rows]

    @app.get("/api/signals/{sid}/gate")
    def signal_gate(sid: int, info=Depends(need("orders:read"))):
        reg = regime_now()
        with Db() as db, db.begin():
            s = db.get(Signal, sid)
            if not s:
                raise HTTPException(404, "no signal")
            return gate(db, s, reg)

    @app.post("/api/signals/{sid}/status")
    def signal_status(sid: int, body: StatusBody, info=Depends(need("orders:ticket"))):
        if body.status not in ("ignored", "ticketed", "alerted", "waiting"):
            raise HTTPException(400, "bad status")
        with Db() as db, db.begin():
            s = db.get(Signal, sid)
            if not s:
                raise HTTPException(404, "no signal")
            s.status = body.status
            audit.record(db, "signals", "signal_" + body.status, {"id": sid, "symbol": s.symbol}, info.username)
        return {"ok": True}

    @app.get("/api/signals/summary")
    def signals_summary(info=Depends(need("orders:read"))):
        reg = regime_now()
        with Db() as db, db.begin():
            strat = strategies_state(db)
            bts = state_get(db, "backtests", {})
            sigs = list(db.execute(select(Signal).where(Signal.day == today().isoformat())).scalars())
            _, rules = desk.rules(db)
            stats = desk._today(db)
            lim = rules.get("max_daily_loss") or 0
            return {"regime": reg, "watchlist": watchlist(db),
                    "strategies": [{"key": k, **st.STRATEGY_INFO[k], **cfg,
                                    "backtest": (bts.get("by_strategy") or {}).get(k)} for k, cfg in strat.items()],
                    "today": {"total": len(sigs), "buy": sum(s.side == "BUY" for s in sigs),
                              "sell": sum(s.side == "SELL" for s in sigs),
                              "waiting": sum(s.status == "waiting" for s in sigs)},
                    "breaker": {"open": not desk.kill_switch_on(db) and (not lim or stats.loss_at_risk < lim),
                                "used_pct": round(stats.loss_at_risk / lim * 100) if lim else 0},
                    "backtest_day": bts.get("day")}

    @app.put("/api/signals/strategies")
    def put_strategies(body: StrategiesBody, info=Depends(need("rules:edit"))):
        with Db() as db, db.begin():
            cur = strategies_state(db)
            for k, v in body.strategies.items():
                if k in cur:
                    if v.get("mode") == "paper_auto":
                        raise HTTPException(400, "auto-execution is not offered: every order needs your confirm")
                    cur[k].update({x: v[x] for x in ("enabled", "mode") if x in v})
            state_set(db, "strategies", cur)
            audit.record(db, "signals", "strategies", cur, info.username)
        return {"ok": True}

    @app.put("/api/watchlist")
    def put_watchlist(body: WatchBody, info=Depends(need("rules:edit"))):
        syms = [s.strip().upper() for s in body.symbols if re.fullmatch(r"[A-Za-z0-9&\-^.=]{1,24}", s.strip())][:40]
        with Db() as db, db.begin():
            state_set(db, "watchlist", {"symbols": syms})
        return {"symbols": syms}

    @app.post("/api/signals/scan")
    def scan_now(info=Depends(need("orders:read"))):
        with Db() as db, db.begin():
            backtests(db, refresh=False)
        return {"added": scan(info.username)}

    @app.post("/api/signals/backtest")
    def run_backtest(info=Depends(need("orders:read"))):
        with Db() as db, db.begin():
            return backtests(db, refresh=True)["by_strategy"]

    # ------------------------------------------------------------ briefs
    def make_brief(kind: str, user: str = "system") -> Brief:
        d = today()
        with Db() as db, db.begin():
            _, rules = desk.rules(db)
            wl = watchlist(db)
            pre = db.execute(select(Brief).where(Brief.day == d.isoformat(), Brief.kind == "pre")
                             .order_by(Brief.id.desc()).limit(1)).scalar_one_or_none()
            pre_body = pre.body if pre else None
        body = briefs.premarket(md, d, wl, rules) if kind == "pre" else briefs.review(md, d, pre_body)
        with Db() as db, db.begin():
            b = Brief(day=d.isoformat(), kind=kind, body=body, grade=body.get("grade") if kind == "post" else None)
            db.add(b)
            if kind == "post" and pre:
                db.get(Brief, pre.id).grade = body.get("grade")
            audit.record(db, "briefs", f"{kind}_brief", {"day": d.isoformat()}, user)
            db.flush()
            return b

    @app.get("/api/briefs")
    def list_briefs(info=Depends(need("portfolio:read"))):
        with Db() as db:
            return [{"id": b.id, "day": b.day, "kind": b.kind, "market": b.market, "grade": b.grade,
                     "headline": b.body.get("headline") or b.body.get("note"), "created_at": b.created_at.isoformat() + "Z"}
                    for b in db.execute(select(Brief).order_by(Brief.id.desc()).limit(60)).scalars()]

    @app.get("/api/briefs/{bid}")
    def get_brief(bid: int, info=Depends(need("portfolio:read"))):
        with Db() as db:
            b = db.get(Brief, bid)
            if not b:
                raise HTTPException(404, "no brief")
            return {"id": b.id, "day": b.day, "kind": b.kind, "grade": b.grade, "body": b.body,
                    "created_at": b.created_at.isoformat() + "Z"}

    @app.post("/api/briefs/run")
    def run_brief(body: RunBody, info=Depends(need("orders:read"))):
        if body.kind not in ("pre", "post"):
            raise HTTPException(400, "kind is pre or post")
        b = make_brief(body.kind, info.username)
        return {"id": b.id}

    # ------------------------------------------------------------ analysis providers
    DEFAULT_PIPELINE = {"name": "Pre-market brief · NSE", "schedule": "08:45", "post_schedule": "15:45",
                        "scan_every_min": 5, "combine": "rules_first", "on_fail": "rules_only", "enabled": True,
                        "steps": [{"id": "levels_and_gaps", "kind": "Rules", "desc": "PDH/PDL, pivots, global cues",
                                   "on": True},
                                  {"id": "regime", "kind": "Rules", "desc": "trend and volatility score", "on": True},
                                  {"id": "setup_library", "kind": "Rules",
                                   "desc": "your setups on the watchlist, with history odds", "on": True},
                                  {"id": "llm_summary", "kind": "Claude",
                                   "desc": "writes the bias in prose; prices checked against the rules", "on": False}]}

    def skills_installed() -> list[dict]:
        out = []
        for p in sorted(SKILLS_ROOT.glob("**/SKILL.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^name:\s*(.+)$", text, re.M)
            out.append({"name": (m.group(1).strip() if m else p.parent.name), "path": str(p.parent.relative_to(SKILLS_ROOT))})
        return out

    @app.get("/api/providers")
    def providers(info=Depends(need("portfolio:read"))):
        import httpx
        key_set = bool(c.secrets.get("assistant:anthropic_api_key"))
        try:
            ollama = httpx.get("http://127.0.0.1:11434/api/tags", timeout=0.6).json().get("models", [])
            ollama_ok = True
        except Exception:
            ollama, ollama_ok = [], False
        with Db() as db:
            pipe = state_get(db, "pipeline", DEFAULT_PIPELINE)
            graded = [b for b in db.execute(select(Brief).where(Brief.kind == "pre", Brief.grade.is_not(None))
                                            .order_by(Brief.id.desc()).limit(30)).scalars()]
            n_sig = len(list(db.execute(select(Signal.id)).scalars()))
            bts = state_get(db, "backtests", {})
        good = sum(1 for b in graded if b.grade and b.grade[0] in "AB")
        return {"providers": [
                    {"id": "rules", "kind": "Rules", "name": "Rule engine", "desc": "levels, regime, setups, back-tests",
                     "on": True, "status": "Always on"},
                    {"id": "claude", "kind": "Claude", "name": "Claude", "desc": "AI assistant and brief prose",
                     "on": key_set, "status": "key set" if key_set else "add an API key in AI assistant"},
                    {"id": "ollama", "kind": "Local", "name": "Ollama",
                     "desc": ", ".join(m.get("name", "") for m in ollama[:3]) or "offline fallback",
                     "on": ollama_ok, "status": "running · 127.0.0.1:11434" if ollama_ok else "not running"},
                    {"id": "yahoo", "kind": "Feed", "name": "Yahoo Finance", "desc": "delayed NSE + global data",
                     "on": True, "status": "free, delayed"}],
                "skills": skills_installed(), "pipeline": pipe,
                "scorecard": [{"provider": "Rules · setup_library", "calls": n_sig,
                               "hit": (bts.get("by_strategy") or {}).get("orb", {}).get("target_pct"),
                               "avg_r": (bts.get("by_strategy") or {}).get("orb", {}).get("expectancy_r"),
                               "bias_correct": None, "cost": 0},
                              {"provider": "Rules · pre-market bias", "calls": len(graded),
                               "bias_correct": round(good / len(graded) * 100) if graded else None,
                               "hit": None, "avg_r": None, "cost": 0}]}

    @app.put("/api/providers/pipeline")
    def put_pipeline(body: PipelineBody, info=Depends(need("rules:edit"))):
        p = body.pipeline
        for k in ("schedule", "post_schedule"):
            if k in p and not re.fullmatch(r"\d{2}:\d{2}", str(p[k])):
                raise HTTPException(400, f"{k} must be HH:MM")
        with Db() as db, db.begin():
            cur = {**state_get(db, "pipeline", DEFAULT_PIPELINE), **p}
            state_set(db, "pipeline", cur)
            audit.record(db, "settings", "pipeline", cur, info.username)
        return cur

    # ------------------------------------------------------------ scheduler
    def tick():
        n = now()
        hm = n.strftime("%H:%M")
        if not c.session.is_trading_day(n.date())[0]:
            return
        with Db() as db:
            pipe = state_get(db, "pipeline", DEFAULT_PIPELINE)
            have = {b.kind for b in db.execute(select(Brief).where(Brief.day == n.date().isoformat())).scalars()}
            last_scan = state_get(db, "last_scan", {}).get("at")
        if not pipe.get("enabled", True):
            return
        if "pre" not in have and pipe["schedule"] <= hm < "09:15":
            make_brief("pre")
        if "post" not in have and pipe["post_schedule"] <= hm < "18:00":
            make_brief("post")
        if "09:20" <= hm <= "14:35" and (not last_scan or
                                          (n - datetime.fromisoformat(last_scan)).total_seconds() >= pipe["scan_every_min"] * 60):
            scan()
            with Db() as db, db.begin():
                state_set(db, "last_scan", {"at": n.isoformat()})

    def loop():
        while True:
            try:
                tick()
            except Exception as e:  # never let the scheduler die
                log.warning("scheduler tick failed: %s", e)
            _time.sleep(60)

    def start_scheduler():
        threading.Thread(target=loop, name="tdp-scheduler", daemon=True).start()

    app.state.start_scheduler = start_scheduler
    app.state.signals_summary_fn = lambda info: signals_summary(info=info)
    app.state.charges_month_fn = lambda month, info: charges_month(month, info=info)
    app.state.positions_all = positions_all
    app.state.funds_all = funds_all
    app.state.make_brief = make_brief
    app.state.scan = scan
