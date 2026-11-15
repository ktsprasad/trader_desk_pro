"""Chart buy / exit marks by style: exits, no look-ahead, long-term trend rule, options mapping."""
import random
from datetime import date, datetime, timedelta

import pytest

from traderdesk.analysis import chart_signals as cs
from traderdesk.analysis import strategies as st
from traderdesk.core.calendar import IST
from traderdesk.market.yahoo import Bar

from test_features import day_bars, orb_long_day


def test_intraday_target_exit_and_markers():
    tr = cs.intraday_trades(orb_long_day(date(2026, 11, 12)) + orb_long_day(date(2026, 11, 13)))
    assert len(tr) == 2 and all(t["exit_reason"] == "target" for t in tr)
    t = tr[0]
    assert t["side"] == "BUY" and t["entry"] == 102 and t["exit"] == pytest.approx(105) and t["r"] == 2.0
    m = cs.markers(tr, "intraday")
    assert [x["kind"] for x in m] == ["entry", "exit", "entry", "exit"] and m[1]["win"] is True
    assert m[0]["time"] < m[1]["time"]


def test_intraday_square_off_at_1515_and_short_session_end():
    flat = [100, 101, 100.5, 102] + [102.2] * 75           # breakout, then nothing until the close
    tr = cs.intraday_trades(day_bars(date(2026, 11, 12), flat))
    assert tr[0]["exit_reason"] == "15:15 square-off"
    exit_t = datetime.fromtimestamp(tr[0]["exit_time"], IST)
    assert (exit_t.hour, exit_t.minute) == (15, 15)
    early = day_bars(date(2026, 11, 12), [100, 101, 100.5, 102, 102.2, 102.1])     # data stops at 09:40
    today = day_bars(date(2026, 11, 13), [100, 101, 100.5, 102, 102.3])
    tr = cs.intraday_trades(early + today)
    assert tr[0]["exit_reason"] == "session end" and tr[1]["exit_reason"] == "open"
    now = cs.current(tr, "intraday", None, [])
    assert now["state"] == "in" and "stop" in now["text"]


def walk(n, seed, drift=0.0008):
    rnd = random.Random(seed)
    px, out = 100.0, []
    t0 = int(datetime(2020, 1, 1, 9, 15, tzinfo=IST).timestamp())
    for i in range(n):
        o = px
        px = max(5.0, px * (1 + drift + rnd.gauss(0, 0.015)))
        h, lo = max(o, px) * (1 + abs(rnd.gauss(0, 0.004))), min(o, px) * (1 - abs(rnd.gauss(0, 0.004)))
        out.append(Bar(t0 + i * 86400, o, h, lo, px, 1e5))
    return out


def test_swing_entries_use_only_closed_bars():
    daily = walk(600, 7)
    tr = cs.swing_trades(daily)
    assert tr, "the random walk should give some pullbacks"
    idx = {b.t: i for i, b in enumerate(daily)}
    for t in tr:
        e = idx[t["entry_time"]]
        sig = st.pullback_setup(daily[:e])                 # the signal came from the day before the fill
        assert sig is not None and daily[e].h >= sig["entry"] and t["entry"] >= sig["entry"] - 1e-9
        if t["exit_time"]:
            assert 0 <= idx[t["exit_time"]] - e < cs.SWING_HOLD
    entries = [idx[t["entry_time"]] for t in tr]
    exits = [idx[t["exit_time"]] for t in tr if t["exit_time"]]
    assert all(a < b for a, b in zip(exits, entries[1:]))   # one position at a time


def test_longterm_trend_rule_enters_and_exits_on_next_open():
    t0 = int(datetime(2018, 1, 1, tzinfo=IST).timestamp())
    path = [100 - i * 0.1 for i in range(260)] + [74 + i * 0.4 for i in range(260)] + [178 - i * 0.6 for i in range(200)]
    bars = [Bar(t0 + i * 86400, c, c * 1.005, c * 0.995, c, 1e5) for i, c in enumerate(path)]
    tr = cs.longterm_trades(bars)
    assert len(tr) == 1 and tr[0]["exit_reason"] == "2 closes below 200-DMA"
    idx = {b.t: i for i, b in enumerate(bars)}
    e, x = idx[tr[0]["entry_time"]], idx[tr[0]["exit_time"]]
    closes = [b.c for b in bars]
    s50, s200 = cs._sma_series(closes, 50), cs._sma_series(closes, 200)
    assert s50[e - 1] > s200[e - 1] and closes[e - 1] > s200[e - 1] and tr[0]["entry"] == bars[e].o
    assert closes[x - 1] < s200[x - 1] and closes[x - 2] < s200[x - 2] and tr[0]["exit"] == bars[x].o
    assert tr[0]["return_pct"] > 0
    assert cs.current(tr, "longterm", bars[-1], closes)["state"] == "out"


def test_options_mapping_and_stats():
    assert cs.strike_step([24900, 24950, 25000, 25050, 25100, 25200]) == 50
    t = {"side": "BUY", "entry": 25209.0, "entry_time": 1, "exit_time": 2, "exit": 25300.0, "exit_reason": "target", "return_pct": 0.36}
    assert cs.option_leg(t, 50)["option"] == "Buy 25200 CE"
    assert cs.option_leg({**t, "side": "SELL"}, 100)["option"] == "Buy 25200 PE"
    assert cs.option_leg(t, None)["option"] == "Buy ATM CE"
    s = cs.stats([{**t, "return_pct": 10.0}, {**t, "return_pct": -5.0}, {**t, "exit_reason": "open", "exit": None, "return_pct": None}])
    assert s["trades"] == 2 and s["win_pct"] == 50.0 and s["total_return_pct"] == pytest.approx(4.5)
