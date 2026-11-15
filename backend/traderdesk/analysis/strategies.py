"""Rule strategies on 5-minute NSE bars, their back-test, and the market regime score.

Everything here is plain arithmetic on bars, so the same code fires live signals, replays the
last 60 sessions for odds, and feeds the pre-market brief. Times are IST.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from statistics import mean

from ..market.yahoo import Bar, sessions

OR_END = time(9, 30)
LAST_ENTRY = time(14, 30)
RR = 2.0


@dataclass
class Setup:
    strategy: str
    side: str          # BUY | SELL
    at: str            # HH:MM IST of the trigger bar's close
    entry: float
    stop: float
    target: float
    reason: str
    bar_index: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("bar_index")
        return d


def _t(b: Bar) -> time:
    return b.ist().time()


def _close_time(b: Bar) -> str:
    m = b.ist().hour * 60 + b.ist().minute + 5
    return f"{m // 60:02d}:{m % 60:02d}"


def _plan(strategy, side, b: Bar, i: int, stop: float, reason: str) -> Setup | None:
    entry = b.c
    risk = entry - stop if side == "BUY" else stop - entry
    if risk <= 0 or risk / entry > 0.05:
        return None
    target = entry + RR * risk if side == "BUY" else entry - RR * risk
    return Setup(strategy, side, _close_time(b), round(entry, 2), round(stop, 2), round(target, 2), reason, i)


def orb(day: list[Bar]) -> Setup | None:
    """First 5-minute close outside the 09:15-09:30 range; stop at the range midpoint; 2R target."""
    rng = [b for b in day if _t(b) < OR_END]
    if len(rng) < 2:
        return None
    hi, lo = max(b.h for b in rng), min(b.l for b in rng)
    mid = (hi + lo) / 2
    for i, b in enumerate(day):
        if _t(b) < OR_END or _t(b) >= LAST_ENTRY:
            continue
        if b.c > hi:
            return _plan("ORB", "BUY", b, i, mid, f"first 5-min close above the opening range {lo:.2f}-{hi:.2f}")
        if b.c < lo:
            return _plan("ORB", "SELL", b, i, mid, f"first 5-min close below the opening range {lo:.2f}-{hi:.2f}")
    return None


def vwap_series(day: list[Bar]) -> list[float]:
    out, pv, vv = [], 0.0, 0.0
    for b in day:
        tp = (b.h + b.l + b.c) / 3
        w = b.v if b.v > 0 else 1.0     # indices carry no volume: equal weights
        pv += tp * w
        vv += w
        out.append(pv / vv)
    return out


def vwap_reclaim(day: list[Bar]) -> Setup | None:
    """After 09:45, a close back above VWAP after 3 closes below (long), or the mirror (short)."""
    vw = vwap_series(day)
    for i in range(3, len(day)):
        b = day[i]
        if _t(b) < time(9, 45) or _t(b) >= LAST_ENTRY:
            continue
        below = all(day[j].c < vw[j] for j in range(i - 3, i))
        above = all(day[j].c > vw[j] for j in range(i - 3, i))
        if below and b.c > vw[i]:
            return _plan("VWAP reclaim", "BUY", b, i, min(x.l for x in day[i - 3:i + 1]),
                         f"closed back above VWAP {vw[i]:.2f} after 3 bars below")
        if above and b.c < vw[i]:
            return _plan("VWAP reject", "SELL", b, i, max(x.h for x in day[i - 3:i + 1]),
                         f"closed back below VWAP {vw[i]:.2f} after 3 bars above")
    return None


STRATEGIES = {"orb": orb, "vwap": vwap_reclaim}
STRATEGY_INFO = {
    "orb": {"name": "Opening-range breakout", "origin": "Built-in · trader-entry", "market": "NSE cash"},
    "vwap": {"name": "VWAP reclaim / reject", "origin": "Built-in", "market": "NSE cash"},
}


def outcome(day: list[Bar], s: Setup) -> tuple[str, float]:
    """target | stop | open, and the R multiple. A bar touching both counts as the stop."""
    risk = abs(s.entry - s.stop)
    for b in day[s.bar_index + 1:]:
        if s.side == "BUY":
            if b.l <= s.stop:
                return "stop", -1.0
            if b.h >= s.target:
                return "target", RR
        else:
            if b.h >= s.stop:
                return "stop", -1.0
            if b.l <= s.target:
                return "target", RR
    last = day[-1].c
    r = (last - s.entry) / risk if s.side == "BUY" else (s.entry - last) / risk
    return "open", round(r, 2)


def backtest(bars: list[Bar], key: str, skip_day: str | None = None) -> dict:
    """Replay the rule on every past session in `bars` (Yahoo gives ~60 sessions of 5m bars)."""
    fn = STRATEGIES[key]
    res = []
    for d, day in sessions(bars).items():
        if d == skip_day:
            continue
        s = fn(day)
        if s:
            res.append(outcome(day, s))
    n = len(res)
    if not n:
        return {"sessions": len(sessions(bars)), "trades": 0, "target_pct": None, "stop_pct": None,
                "expectancy_r": None}
    return {"sessions": len(sessions(bars)), "trades": n,
            "target_pct": round(100 * sum(1 for o, _ in res if o == "target") / n, 1),
            "stop_pct": round(100 * sum(1 for o, _ in res if o == "stop") / n, 1),
            "expectancy_r": round(mean(r for _, r in res), 2)}


def today_setup(bars: list[Bar], key: str, day: str) -> Setup | None:
    d = sessions(bars).get(day)
    return STRATEGIES[key](d) if d else None


# ---------------------------------------------------------------- daily: levels, regime
def sma(vals: list[float], n: int) -> float | None:
    return mean(vals[-n:]) if len(vals) >= n else None


def pivots(prev: Bar) -> dict:
    p = (prev.h + prev.l + prev.c) / 3
    return {"previous_high": prev.h, "previous_low": prev.l, "previous_close": prev.c, "pivot": round(p, 2),
            "r1": round(2 * p - prev.l, 2), "s1": round(2 * p - prev.h, 2)}


def regime(daily: list[Bar], vix: float | None) -> dict:
    """0-100 from trend and volatility. Each point is listed so the score is explainable."""
    closes = [b.c for b in daily]
    if len(closes) < 60:
        return {"score": None, "label": "Not enough data", "parts": []}
    c, s20, s50 = closes[-1], sma(closes, 20), sma(closes, 50)
    s200 = sma(closes, 200)
    s20_prev = mean(closes[-25:-5])
    parts = [("close above 50-day average", 25, c > s50),
             ("close above 200-day average", 20, s200 is not None and c > s200),
             ("20-day average rising", 15, s20 > s20_prev),
             ("close above 20-day average", 15, c > s20),
             ("5-day change positive", 10, c > closes[-6]),
             ("India VIX below 15", 15, vix is not None and vix < 15)]
    score = sum(p for _, p, ok in parts if ok)
    label = "Constructive" if score >= 60 else "Neutral" if score >= 40 else "Defensive"
    return {"score": score, "label": label, "exposure_ceiling": max(20, min(100, round(score / 10) * 10)),
            "parts": [{"rule": r, "points": p, "ok": ok} for r, p, ok in parts]}


def pullback_setup(daily: list[Bar]) -> dict | None:
    """Swing long: uptrend (20 > 50 DMA) and price back within 1.5% of the 20 DMA.
    Entry above yesterday's high, stop under the 3-day low, 2R target."""
    closes = [b.c for b in daily]
    if len(closes) < 60:
        return None
    s20, s50 = sma(closes, 20), sma(closes, 50)
    last = daily[-1]
    if not (s20 > s50 and abs(last.l - s20) / s20 <= 0.015 and last.c >= s20 * 0.99):
        return None
    entry = round(last.h * 1.001, 2)
    stop = round(min(b.l for b in daily[-3:]) * 0.998, 2)
    if stop >= entry:
        return None
    return {"strategy": "Pullback to 20-DMA", "side": "BUY", "entry": entry, "stop": stop,
            "target": round(entry + RR * (entry - stop), 2)}


def pullback_history(daily: list[Bar], horizon: int = 10) -> dict:
    """Hit rate of the pullback rule over the daily history: target before stop within `horizon` days."""
    wins = losses = 0
    for i in range(60, len(daily) - 1):
        s = pullback_setup(daily[:i + 1])
        if not s:
            continue
        fut = daily[i + 1:i + 1 + horizon]
        if not fut or fut[0].h < s["entry"]:
            continue
        for b in fut:
            if b.l <= s["stop"]:
                losses += 1
                break
            if b.h >= s["target"]:
                wins += 1
                break
    n = wins + losses
    return {"trades": n, "target_pct": round(100 * wins / n, 1) if n else None}
