"""Market pulse: per-stock indicators and setups from daily bars (RSI, Money Flow Index, ATR, Bollinger
squeeze, untaken swing levels). Pure functions; the API feeds them Yahoo daily bars.

Every number here is computed from the bars passed in. Nothing is estimated or filled in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, pstdev

from ..market.yahoo import Bar
from .strategies import pullback_setup, sma


def rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder's RSI of the last close."""
    if len(closes) <= n:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = mean(gains[:n]), mean(losses[:n])
    for g, lo in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + lo) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return round(100 - 100 / (1 + ag / al), 1)


def mfi(bars: list[Bar], n: int = 14) -> float | None:
    """Money Flow Index: RSI of typical price weighted by volume."""
    if len(bars) <= n:
        return None
    tp = [(b.h + b.l + b.c) / 3 for b in bars]
    pos = neg = 0.0
    for i in range(len(bars) - n, len(bars)):
        flow = tp[i] * bars[i].v
        if tp[i] > tp[i - 1]:
            pos += flow
        elif tp[i] < tp[i - 1]:
            neg += flow
    if pos + neg == 0:
        return None
    if neg == 0:
        return 100.0
    return round(100 - 100 / (1 + pos / neg), 1)


def atr(bars: list[Bar], n: int = 14) -> float | None:
    if len(bars) <= n:
        return None
    trs = [max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)) for p, b in zip(bars, bars[1:])]
    return mean(trs[-n:])


def bb_width(closes: list[float], n: int = 20) -> float | None:
    if len(closes) < n:
        return None
    w = closes[-n:]
    m = mean(w)
    return 4 * pstdev(w) / m if m else None


def squeeze_rank(closes: list[float], n: int = 20, lookback: int = 120) -> float | None:
    """Where today's Bollinger width sits among the last `lookback` days: 0 = narrowest, 100 = widest."""
    if len(closes) < n + lookback // 2:
        return None
    widths = [bb_width(closes[: i + 1], n) for i in range(max(n - 1, len(closes) - lookback), len(closes))]
    widths = [w for w in widths if w is not None]
    cur = widths[-1]
    return round(sum(1 for w in widths if w < cur) / max(1, len(widths) - 1) * 100)


def weekly_closes(bars: list[Bar]) -> list[float]:
    out: dict[tuple[int, int], float] = {}
    for b in bars:
        y, w, _ = datetime.fromtimestamp(b.t, tz=timezone.utc).isocalendar()
        out[(y, w)] = b.c
    return [out[k] for k in sorted(out)]


def swing_levels(bars: list[Bar], k: int = 3, lookback: int = 120) -> list[dict]:
    """Fractal swing highs / lows of the last `lookback` days that price has not traded through since.
    Untaken highs are where buy stops rest; untaken lows are where sell stops rest."""
    b = bars[-lookback:]
    out = []
    for i in range(k, len(b) - k):
        win = b[i - k:i + k + 1]
        after = b[i + 1:]
        if b[i].h == max(x.h for x in win) and all(x.h < b[i].h for x in after):
            out.append({"price": round(b[i].h, 2), "kind": "swing high (untaken)", "side": "above",
                        "on": datetime.fromtimestamp(b[i].t, tz=timezone.utc).date().isoformat()})
        if b[i].l == min(x.l for x in win) and all(x.l > b[i].l for x in after):
            out.append({"price": round(b[i].l, 2), "kind": "swing low (untaken)", "side": "below",
                        "on": datetime.fromtimestamp(b[i].t, tz=timezone.utc).date().isoformat()})
    return out


def metrics(symbol: str, bars: list[Bar]) -> dict | None:
    """One screener row. None when there is too little history to say anything."""
    if len(bars) < 30:
        return None
    closes = [x.c for x in bars]
    last, prev = bars[-1], bars[-2]
    turn = [x.c * x.v for x in bars]
    avg20 = mean(turn[-21:-1]) if len(turn) > 21 else None
    tr_ratio = round(turn[-1] / avg20, 2) if avg20 else None
    chg = (last.c - prev.c) / prev.c * 100 if prev.c else None
    hi52 = max(x.h for x in bars[-252:])
    prior_hi = max(x.h for x in bars[-253:-1]) if len(bars) > 2 else hi52
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    a = atr(bars)
    r, rw, m = rsi(closes), rsi(weekly_closes(bars)), mfi(bars)
    sq = squeeze_rank(closes)
    gap = (last.o - prev.c) / prev.c * 100 if prev.c else 0.0
    if tr_ratio is not None and tr_ratio >= 1.5 and chg is not None and abs(chg) >= 0.5:
        flow = "Inflow" if chg > 0 else "Outflow"
    else:
        flow = "Normal"
    setups = []
    if last.c > prior_hi:
        setups.append("52-week high breakout")
    elif (hi52 - last.c) / hi52 <= 0.02:
        setups.append("Within 2% of 52-week high")
    pb = pullback_setup(bars)
    if pb:
        setups.append("Pullback to 20-DMA")
    if r is not None and r < 35 and s200 and last.c > s200:
        setups.append("RSI below 35 in an uptrend")
    if tr_ratio is not None and tr_ratio >= 2:
        setups.append(f"Turnover {tr_ratio}x the 20-day average")
    if sq is not None and sq <= 10:
        setups.append("Volatility squeeze (narrowest 10%)")
    if abs(gap) >= 2:
        setups.append(f"Gap {'up' if gap > 0 else 'down'} {abs(gap):.1f}%")
    return {"symbol": symbol, "last": round(last.c, 2), "change_pct": round(chg, 2) if chg is not None else None,
            "rsi": r, "rsi_weekly": rw, "mfi": m, "turnover_cr": round(turn[-1] / 1e7, 2),
            "turnover_ratio": tr_ratio, "flow": flow, "atr_pct": round(a / last.c * 100, 2) if a else None,
            "squeeze_rank": sq, "from_52w_high_pct": round((last.c - hi52) / hi52 * 100, 2),
            "above_20dma": s20 is not None and last.c > s20, "above_50dma": s50 is not None and last.c > s50,
            "above_200dma": s200 is not None and last.c > s200, "setups": setups, "plan": pb,
            "as_of": datetime.fromtimestamp(last.t, tz=timezone.utc).date().isoformat()}


def liquidity_levels(bars: list[Bar]) -> dict:
    """Levels where orders tend to cluster, nearest first above and below the last price."""
    if len(bars) < 10:
        return {"last": None, "above": [], "below": []}
    last = bars[-1].c
    prev = bars[-2]
    week = bars[-6:-1]
    lv = [{"price": round(prev.h, 2), "kind": "previous day high"}, {"price": round(prev.l, 2), "kind": "previous day low"},
          {"price": round(max(x.h for x in week), 2), "kind": "previous 5-day high"},
          {"price": round(min(x.l for x in week), 2), "kind": "previous 5-day low"}]
    hi52 = max(x.h for x in bars[-252:])
    lo52 = min(x.l for x in bars[-252:])
    lv += [{"price": round(hi52, 2), "kind": "52-week high"}, {"price": round(lo52, 2), "kind": "52-week low"}]
    lv += [{"price": x["price"], "kind": f"{x['kind']} from {x['on']}"} for x in swing_levels(bars)]
    above = sorted([x for x in lv if x["price"] > last], key=lambda x: x["price"])[:8]
    below = sorted([x for x in lv if x["price"] < last], key=lambda x: -x["price"])[:8]
    for x in above + below:
        x["distance_pct"] = round((x["price"] - last) / last * 100, 2)
    return {"last": round(last, 2), "above": above, "below": below}


# ---------------------------------------------------------------- sector money flow
def money_flow(bar: Bar) -> float:
    """Chaikin money flow of one day in rupees: traded value x close-location value (-1 at the low, +1 at the
    high). A stock that closes at its high on big value is strong inflow; at its low, strong outflow."""
    rng = bar.h - bar.l
    clv = ((bar.c - bar.l) - (bar.h - bar.c)) / rng if rng > 0 else 0.0
    return bar.c * bar.v * clv


def sector_flows(stocks: dict[str, tuple[str, list[Bar]]], top: int = 10) -> dict:
    """stocks: symbol -> (sector, daily bars). Uses each stock's latest day only."""
    rows = []
    for sym, (sector, bars) in stocks.items():
        if len(bars) < 2:
            continue
        b, p = bars[-1], bars[-2]
        rows.append({"symbol": sym, "sector": sector, "flow_cr": round(money_flow(b) / 1e7, 2),
                     "turnover_cr": round(b.c * b.v / 1e7, 2), "change_pct": round((b.c - p.c) / p.c * 100, 2) if p.c else None,
                     "last": round(b.c, 2), "as_of": datetime.fromtimestamp(b.t, tz=timezone.utc).date().isoformat()})
    sectors: dict[str, dict] = {}
    for r in rows:
        s = sectors.setdefault(r["sector"], {"sector": r["sector"], "flow_cr": 0.0, "turnover_cr": 0.0, "up": 0, "down": 0, "stocks": 0})
        s["flow_cr"] += r["flow_cr"]
        s["turnover_cr"] += r["turnover_cr"]
        s["stocks"] += 1
        if (r["change_pct"] or 0) > 0:
            s["up"] += 1
        elif (r["change_pct"] or 0) < 0:
            s["down"] += 1
    sec = sorted(({**s, "flow_cr": round(s["flow_cr"], 2), "turnover_cr": round(s["turnover_cr"], 2)} for s in sectors.values()),
                 key=lambda s: -s["flow_cr"])
    inflow = sorted([r for r in rows if r["flow_cr"] > 0], key=lambda r: -r["flow_cr"])
    outflow = sorted([r for r in rows if r["flow_cr"] < 0], key=lambda r: r["flow_cr"])
    up = sum(1 for r in rows if (r["change_pct"] or 0) > 0)
    down = sum(1 for r in rows if (r["change_pct"] or 0) < 0)
    ins = [s["sector"] for s in sec if s["flow_cr"] > 0][:2]
    outs = [s["sector"] for s in reversed(sec) if s["flow_cr"] < 0][:2]
    parts = []
    if ins:
        parts.append(f"Money moving into {' & '.join(ins)}")
    if outs:
        parts.append(f"{' & '.join(outs)} seeing outflows")
    parts.append(f"breadth {up} up / {down} down")
    return {"sectors": sec, "inflow": inflow[:top], "outflow": outflow[:top],
            "inflow_total_cr": round(sum(r["flow_cr"] for r in inflow), 2),
            "outflow_total_cr": round(sum(r["flow_cr"] for r in outflow), 2),
            "breadth": {"up": up, "down": down, "flat": len(rows) - up - down}, "headline": "; ".join(parts),
            "stocks": rows, "as_of": max((r["as_of"] for r in rows), default=None)}
