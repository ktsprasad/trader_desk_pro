"""Pre-market brief and post-market review from the rules engine.

The brief only states numbers it fetched; anything without a source is reported as unavailable
rather than guessed. An LLM step (Analysis providers) may add prose later, but prices come from here.
"""
from __future__ import annotations

from datetime import date
from statistics import mean

from ..market.yahoo import MarketData, sessions
from . import strategies as st

GLOBAL = [("S&P 500 (close)", "^GSPC"), ("Nasdaq 100", "^NDX"), ("Dow Jones", "^DJI"), ("Nikkei 225", "^N225"),
          ("Hang Seng", "^HSI"), ("Brent crude", "BZ=F"), ("USD/INR", "INR=X")]
INDEX = [("NIFTY", "NIFTY"), ("BANKNIFTY", "BANKNIFTY"), ("INDIA VIX", "INDIAVIX")]


def _q(md: MarketData, sym: str) -> dict:
    try:
        return md.quote(sym)
    except Exception as e:
        return {"symbol": sym, "error": str(e)}


def premarket(md: MarketData, day: date, watchlist: list[str], rules: dict) -> dict:
    cues = [{"name": n, **_q(md, s)} for n, s in GLOBAL]
    idx = {n: _q(md, s) for n, s in INDEX}
    vix = idx["INDIA VIX"].get("last")

    levels, reg, nifty_daily = {}, {"score": None, "label": "Not enough data", "parts": []}, []
    try:
        nifty_daily = md.chart("NIFTY", "1d", "2y")["bars"]
        done = [b for b in nifty_daily if b.ist().date() < day]
        if done:
            levels = st.pivots(done[-1])
            reg = st.regime(done, vix)
    except Exception as e:
        levels = {"error": str(e)}

    moves = [c["change_pct"] for c in cues[:5] if c.get("change_pct") is not None]
    g = mean(moves) if moves else 0.0
    score = reg.get("score")
    if g > 0.3 and (score or 50) >= 50:
        bias = "Mildly bullish"
    elif g < -0.3 and (score or 50) <= 60:
        bias = "Mildly bearish"
    elif g > 0.8:
        bias = "Bullish"
    elif g < -0.8:
        bias = "Bearish"
    else:
        bias = "Neutral"
    day_type = ("Range day likely" if vix is not None and vix < 13 else
                "Volatile day likely" if vix is not None and vix > 18 else "Normal range")
    why = [f"Global cues average {g:+.2f}% (S&P, Nasdaq, Dow, Nikkei, Hang Seng)."]
    if vix is not None:
        why.append(f"India VIX at {vix:.1f}.")
    if score is not None:
        why.append(f"NIFTY regime score {score} ({reg['label']}).")
    why.append("Wait for the 15-minute range before acting on gaps.")
    change = []
    if levels.get("previous_low"):
        change.append(f"NIFTY losing {levels['previous_low']:,.0f} (yesterday's low) in the first hour")
    if vix is not None:
        change.append(f"India VIX above {vix + 1.5:.1f}")

    setups = []
    for sym in watchlist[:15]:
        try:
            daily = md.chart(sym, "1d", "2y")["bars"]
            done = [b for b in daily if b.ist().date() < day]
            s = st.pullback_setup(done)
            if s:
                h = st.pullback_history(done)
                setups.append({"symbol": sym, **s, "hit_rate": h["target_pct"], "sample": h["trades"],
                               "source": "Rules"})
        except Exception:
            continue
        try:
            five = md.chart(sym, "5m")["bars"]
            bt = st.backtest(five, "orb", skip_day=day.isoformat())
            if bt["trades"] >= 10 and (bt["expectancy_r"] or 0) > 0:
                setups.append({"symbol": sym, "strategy": "Opening-range breakout", "side": "either",
                               "entry": None, "stop": None, "target": None, "hit_rate": bt["target_pct"],
                               "sample": bt["trades"], "expectancy_r": bt["expectancy_r"], "source": "Rules",
                               "note": "levels set by the 09:15-09:30 range"})
        except Exception:
            continue

    no_trade = [f"Daily loss limit ₹{rules.get('max_daily_loss', 0):,.0f} · max {rules.get('max_trades_per_day')} trades",
                f"No entries before {rules.get('no_entries_before')} or after {rules.get('no_entries_after')}",
                f"No new MIS after {rules.get('no_new_mis_after')}"]
    if rules.get("blocked"):
        no_trade.append("Blocked today: " + ", ".join(rules["blocked"]))

    return {"headline": f"{bias}, {day_type[0].lower() + day_type[1:]}", "bias": bias, "day_type": day_type,
            "summary": " ".join(why), "what_would_change": " or ".join(change) or None,
            "global_cues": cues, "indices": idx, "levels": levels, "regime": reg, "setups": setups,
            "no_trade": no_trade,
            "unavailable": ["GIFT Nifty", "FII/DII cash", "PCR / max pain / OI"],
            "sources": ["Yahoo Finance (delayed)", "Rules engine"]}


def review(md: MarketData, day: date, pre: dict | None) -> dict:
    """Grade the morning's bias against NIFTY's day, and score each setup."""
    try:
        bars = md.chart("NIFTY", "5m")["bars"]
        d = sessions(bars).get(day.isoformat())
    except Exception as e:
        return {"error": str(e)}
    if not d:
        return {"error": "no NIFTY bars for this session yet"}
    o, c = d[0].o, d[-1].c
    chg = (c - o) / o * 100
    hi, lo = max(b.h for b in d), min(b.l for b in d)
    rng = (hi - lo) / o * 100
    bias = (pre or {}).get("bias", "Neutral")
    up = "ullish" in bias
    down = "earish" in bias
    if (up and chg > 0.2) or (down and chg < -0.2) or (bias == "Neutral" and abs(chg) <= 0.3):
        grade = "A-" if abs(chg) < 0.6 else "A"
    elif (up and chg >= -0.2) or (down and chg <= 0.2):
        grade = "B"
    elif bias == "Neutral":
        grade = "C"
    else:
        grade = "D"
    return {"nifty_open": o, "nifty_close": c, "change_pct": round(chg, 2), "range_pct": round(rng, 2),
            "bias": bias, "grade": grade,
            "note": f"NIFTY moved {chg:+.2f}% open to close in a {rng:.2f}% range; the morning call was {bias.lower()}."}
