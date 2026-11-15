"""Buy / exit marks for the chart, by trading style, with an honest back-test of the same rules.

Each style is a fixed rule set. Entries and exits are taken only from bars already closed (fills on the
next bar's open for daily styles, on the trigger bar's close for intraday, as the live signals do), so the
history shown is what the rule would have done, not hindsight. Nothing here places an order.

- intraday: opening-range breakout or VWAP reclaim on 5-minute bars; exit at 2R target, stop, or 15:15.
- swing:    pullback to the 20-DMA in an uptrend (20 > 50 DMA); buy above the day's high; exit at 2R target,
            stop under the 3-day low, or after 10 trading days.
- longterm: in when the 50-DMA is above the 200-DMA and price closes back above the 200-DMA; out after two
            closes below the 200-DMA.
- options:  the intraday signal mapped to an at-the-money call (long) or put (short); exits follow the
            underlying. Option P/L also depends on premium, time decay and volatility, which bars cannot show.
"""
from __future__ import annotations

from datetime import time
from statistics import mean

from ..market.yahoo import Bar, sessions
from . import strategies as st

SQUARE_OFF = time(15, 15)
SWING_HOLD = 10
STYLES = {"intraday": "Intraday", "swing": "Swing", "longterm": "Long-term", "options": "Options"}

RULES = {
    "intraday": ["Entry: first 5-minute close outside the 09:15-09:30 range (ORB), or a VWAP reclaim / rejection.",
                 "Stop: range midpoint (ORB) or the trigger bar's extreme (VWAP). Target: 2 x risk.",
                 "Exit: target, stop, or square-off at 15:15, whichever comes first. One trade a day."],
    "swing": ["Entry: uptrend (20-DMA above 50-DMA) and price pulls back within 1.5% of the 20-DMA; "
              "buy next day only if price trades above the signal day's high.",
              "Stop: below the 3-day low. Target: 2 x risk. Time exit: 10 trading days.", "Long only (delivery)."],
    "longterm": ["Entry: 50-DMA above 200-DMA and the close back above the 200-DMA; buy at the next open.",
                 "Exit: two consecutive closes below the 200-DMA; sell at the next open.",
                 "Long only. Compared with simply holding over the same period."],
    "options": ["Signal: the intraday rule on the underlying. Long signal = buy the at-the-money call; "
                "short signal = buy the at-the-money put. Exit when the underlying exits.",
                "Strike step comes from NSE's F&O file. Results are in underlying R; real option P/L also "
                "depends on premium, time decay (theta) and volatility, so it differs."],
}


def _t(b: Bar):
    return b.ist().time()


def _trade(side, eb, ep, xb, xp, reason, extra=None):
    ret = None if xp is None else ((xp - ep) / ep * 100 if side == "BUY" else (ep - xp) / ep * 100)
    return {"side": side, "entry_time": eb.t, "entry": round(ep, 2), "exit_time": xb.t if xb else None,
            "exit": round(xp, 2) if xp is not None else None, "exit_reason": reason,
            "return_pct": round(ret, 2) if xp is not None else None, **(extra or {})}


# ---------------------------------------------------------------- intraday (and options)
def intraday_trades(bars: list[Bar], strategy: str = "orb") -> list[dict]:
    fn = st.STRATEGIES[strategy]
    out = []
    days = list(sessions(bars).values())
    for k, day in enumerate(days):
        s = fn(day)
        if not s:
            continue
        eb = day[s.bar_index]
        risk = abs(s.entry - s.stop)
        exit_b = exit_p = None
        reason = "open"
        for b in day[s.bar_index + 1:]:
            if _t(b) >= SQUARE_OFF:
                exit_b, exit_p, reason = b, b.o, "15:15 square-off"
                break
            hit_stop = b.l <= s.stop if s.side == "BUY" else b.h >= s.stop
            hit_tgt = b.h >= s.target if s.side == "BUY" else b.l <= s.target
            if hit_stop:                                   # a bar touching both counts as the stop
                exit_b, exit_p, reason = b, s.stop, "stop"
                break
            if hit_tgt:
                exit_b, exit_p, reason = b, s.target, "target"
                break
        if exit_b is None and k < len(days) - 1:          # a past session that ended early (half day, gaps)
            exit_b, exit_p, reason = day[-1], day[-1].c, "session end"
        t = _trade(s.side, eb, s.entry, exit_b, exit_p, reason,
                   {"stop": s.stop, "target": s.target, "strategy": s.strategy, "why": s.reason})
        if exit_p is not None:
            t["r"] = round(((exit_p - s.entry) if s.side == "BUY" else (s.entry - exit_p)) / risk, 2) if risk else None
        out.append(t)
    return out


# ---------------------------------------------------------------- swing
def swing_trades(daily: list[Bar]) -> list[dict]:
    out, i, n = [], 60, len(daily)
    while i < n - 1:
        s = st.pullback_setup(daily[:i + 1])
        if not s:
            i += 1
            continue
        fb = daily[i + 1]
        if fb.h < s["entry"]:                              # never triggered
            i += 1
            continue
        fill = max(fb.o, s["entry"])
        exit_b = exit_p = None
        reason = "open"
        for j in range(i + 1, min(n, i + 1 + SWING_HOLD)):
            b = daily[j]
            if b.l <= s["stop"]:
                exit_b, exit_p, reason = b, min(b.o, s["stop"]) if j > i + 1 else s["stop"], "stop"
                break
            if b.h >= s["target"]:
                exit_b, exit_p, reason = b, max(b.o, s["target"]) if j > i + 1 else s["target"], "target"
                break
        else:
            last = min(n, i + 1 + SWING_HOLD) - 1
            if last - i >= SWING_HOLD:
                exit_b, exit_p, reason = daily[last], daily[last].c, f"{SWING_HOLD}-day time exit"
        t = _trade("BUY", fb, fill, exit_b, exit_p, reason, {"stop": s["stop"], "target": s["target"], "strategy": s["strategy"]})
        risk = fill - s["stop"]
        if exit_p is not None and risk > 0:
            t["r"] = round((exit_p - fill) / risk, 2)
        out.append(t)
        if exit_b is None:
            break
        i = daily.index(exit_b) + 1
    return out


# ---------------------------------------------------------------- long term
def _sma_series(vals: list[float], k: int) -> list[float | None]:
    out, run = [], 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= k:
            run -= vals[i - k]
        out.append(run / k if i >= k - 1 else None)
    return out


def longterm_trades(daily: list[Bar]) -> list[dict]:
    closes = [b.c for b in daily]
    s50, s200 = _sma_series(closes, 50), _sma_series(closes, 200)
    out, pos = [], None
    for i in range(201, len(daily) - 1):
        if s200[i] is None or s50[i] is None:
            continue
        nxt = daily[i + 1]
        if pos is None:
            ok_now = s50[i] > s200[i] and closes[i] > s200[i]
            ok_before = s50[i - 1] is not None and s200[i - 1] is not None and s50[i - 1] > s200[i - 1] and closes[i - 1] > s200[i - 1]
            if ok_now and not ok_before:
                pos = (nxt, nxt.o)
        elif closes[i] < s200[i] and closes[i - 1] < s200[i - 1]:
            out.append(_trade("BUY", pos[0], pos[1], nxt, nxt.o, "2 closes below 200-DMA"))
            pos = None
    if pos:
        out.append(_trade("BUY", pos[0], pos[1], None, None, "open"))
    return out


# ---------------------------------------------------------------- packaging
def stats(trades: list[dict]) -> dict:
    done = [t for t in trades if t.get("return_pct") is not None and t["exit_reason"] != "open"]
    if not done:
        return {"trades": 0, "win_pct": None, "avg_return_pct": None, "total_return_pct": None, "avg_r": None}
    total = 1.0
    for t in done:
        total *= 1 + t["return_pct"] / 100
    rs = [t["r"] for t in done if t.get("r") is not None]
    return {"trades": len(done), "win_pct": round(100 * sum(1 for t in done if t["return_pct"] > 0) / len(done), 1),
            "avg_return_pct": round(mean(t["return_pct"] for t in done), 2), "total_return_pct": round((total - 1) * 100, 2),
            "avg_r": round(mean(rs), 2) if rs else None, "best_pct": max(t["return_pct"] for t in done),
            "worst_pct": min(t["return_pct"] for t in done)}


def strike_step(strikes: list[float]) -> float | None:
    s = sorted(set(strikes))
    diffs = [round(b - a, 2) for a, b in zip(s, s[1:]) if b > a]
    return max(set(diffs), key=diffs.count) if diffs else None


def option_leg(trade: dict, step: float | None) -> dict:
    kind = "CE" if trade["side"] == "BUY" else "PE"
    strike = round(round(trade["entry"] / step) * step, 2) if step else None
    return {**trade, "option": f"Buy {('%g' % strike) if strike else 'ATM'} {kind}", "option_type": kind, "strike": strike}


def markers(trades: list[dict], style: str) -> list[dict]:
    out = []
    for t in trades:
        label = t.get("option") or ("BUY" if t["side"] == "BUY" else "SELL")
        out.append({"time": t["entry_time"], "kind": "entry", "side": t["side"], "text": f"{label} {t['entry']:g}"})
        if t.get("exit_time"):
            win = (t.get("return_pct") or 0) > 0
            out.append({"time": t["exit_time"], "kind": "exit", "side": t["side"], "win": win,
                        "text": f"Exit {'+' if t['return_pct'] > 0 else ''}{t['return_pct']}%"})
    return out


def current(trades: list[dict], style: str, last_bar: Bar | None, closes: list[float]) -> dict:
    """What the rule says now."""
    if trades and trades[-1]["exit_reason"] == "open" and trades[-1].get("exit") is None:
        t = trades[-1]
        if style == "longterm":
            s200 = _sma_series(closes, 200)[-1]
            return {"state": "in", "text": f"Holding since the buy at {t['entry']:g}. Exit after two closes below the 200-DMA "
                                           f"(now {s200:,.2f}).", "trade": t}
        return {"state": "in", "text": f"{t.get('option') or t['side']} active from {t['entry']:g}: stop {t.get('stop')}, "
                                       f"target {t.get('target')}.", "trade": t}
    if style == "longterm" and len(closes) >= 200:
        s50, s200 = _sma_series(closes, 50)[-1], _sma_series(closes, 200)[-1]
        if s50 > s200 and closes[-1] > s200:
            return {"state": "watch", "text": "Trend is up (50-DMA above 200-DMA, price above 200-DMA) but the entry came "
                                              "earlier and was exited; the rule waits for a fresh cross back above the 200-DMA."}
        return {"state": "out", "text": f"No position: {'50-DMA below 200-DMA' if s50 <= s200 else 'price below the 200-DMA'} "
                                        f"(200-DMA {s200:,.2f})."}
    if style == "swing" and len(closes) >= 60:
        return {"state": "out", "text": "No open swing trade. A new one needs an uptrend and a pullback to the 20-DMA."}
    return {"state": "out", "text": "No open trade. Intraday signals fire between 09:20 and 14:30 IST."}
