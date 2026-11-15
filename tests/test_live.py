"""Real-time data: with a logged-in Kite session every price, today's candle, futures OI and option OI come
from Kite quotes; without it the app falls back to delayed data and says so."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from traderdesk.app.main import create_app
from traderdesk.brokers.registry import PluginRegistry
from traderdesk.core.calendar import IST
from traderdesk.core.errors import BrokerError, ErrorKind
from traderdesk.core.secrets import MemoryStore
from traderdesk.market.live import LiveQuotes, kite_key, overlay_daily
from traderdesk.market.yahoo import MarketData
from traderdesk.plugins.kite.adapter import KiteBroker
from traderdesk.plugins.paper.adapter import PaperBroker

from conftest import MARKET_HOURS
from test_features import fake_fetch
from test_pulse import CSV, zipped

TODAY = datetime(2026, 11, 16, 10, 29, 58, tzinfo=IST)


def q(last, prev, oi=None, o=None, h=None, lo=None, vol=1_000_000):
    return {"last_price": last, "ohlc": {"open": o or prev, "high": h or max(last, prev), "low": lo or min(last, prev), "close": prev},
            "volume": vol, "oi": oi, "last_trade_time": TODAY}


class LiveKite:
    """Fake kiteconnect client: quote() and instruments() like the real API."""

    def __init__(self):
        self.calls, self.fail = [], None
        self.book = {"NSE:RELIANCE": q(1350.0, 1339.7, o=1340.0, h=1356.0, lo=1338.0), "NSE:NIFTY 50": q(25210.0, 25144.3),
                     "NSE:NIFTY BANK": q(61100.0, 61034.1), "NSE:INDIA VIX": q(15.1, 15.5),
                     "NFO:AAA26NOVFUT": q(115.0, 110.0, oi=1500), "NFO:BBB26NOVFUT": q(88.0, 90.0, oi=1000),
                     "NFO:NIFTY26N1724900PE": q(15.0, 20.0, oi=5000), "NFO:NIFTY26N1725000PE": q(30.0, 40.0, oi=1500),
                     "NFO:NIFTY26N1725000CE": q(200.0, 130.0, oi=400), "NFO:NIFTY26N1725200CE": q(60.0, 30.0, oi=9000)}

    def set_access_token(self, t):
        pass

    def quote(self, keys):
        self.calls.append(list(keys))
        if self.fail:
            raise self.fail
        return {k: self.book[k] for k in keys if k in self.book}

    def instruments(self, exchange):
        fut = lambda s: {"tradingsymbol": f"{s}26NOVFUT", "name": s, "expiry": date(2026, 11, 23), "strike": 0,  # noqa: E731
                         "instrument_type": "FUT", "lot_size": 50}
        opt = lambda k, t: {"tradingsymbol": f"NIFTY26N17{k}{t}", "name": "NIFTY", "expiry": date(2026, 11, 17),  # noqa: E731
                            "strike": float(k), "instrument_type": t, "lot_size": 65}
        return [fut("AAA"), fut("BBB"), {**fut("AAA"), "tradingsymbol": "AAA25OCTFUT", "expiry": date(2026, 10, 28)},
                opt(24900, "PE"), opt(25000, "PE"), opt(25000, "CE"), opt(25200, "CE")]


def kite_adapter(client, fresh=True):
    s = MemoryStore({"kite:api_secret": "sec", "kite:access_token": "tok"})
    if fresh:
        s.set("kite:token_issued_at", datetime.now(IST).isoformat())
    a = KiteBroker("kite", {"api_key": "x"}, s, PluginRegistry().discover().get("kite"), client=client)
    a.limit = lambda group: None          # no 1-per-second sleeps in tests
    return a


def test_kite_keys_and_daily_overlay():
    assert kite_key("NIFTY") == "NSE:NIFTY 50" and kite_key("INDIAVIX") == "NSE:INDIA VIX"
    assert kite_key("RELIANCE") == "NSE:RELIANCE" and kite_key("USDINR") is None and kite_key("AAPL", "US") is None
    md = MarketData(fetch=fake_fetch)
    bars = md.chart("RELIANCE", "1d")["bars"]
    live = {"last": 130.0, "open": 128.0, "high": 131.0, "low": 127.0, "volume": 5.0, "date": "2026-11-16"}
    out = overlay_daily(bars, live)
    assert out[-1].c == 130.0 and out[-1].h == 131.0 and len(out) in (len(bars), len(bars) + 1)
    assert overlay_daily(bars, None) is bars


def test_market_data_uses_kite_when_logged_in_and_falls_back():
    client = LiveKite()
    md = MarketData(fetch=fake_fetch)
    adapter = kite_adapter(client)
    md.live = LiveQuotes(lambda: adapter)
    r = md.quote("RELIANCE")
    assert r["live"] and r["last"] == 1350.0 and r["change_pct"] == pytest.approx((1350 - 1339.7) / 1339.7 * 100, abs=0.01)
    d = md.chart("RELIANCE", "1d")
    assert d["live"] and d["source"] == "Kite (real-time)" and d["bars"][-1].c == 1350.0 and d["bars"][-1].h == 1356.0
    assert "intraday candles Yahoo" in md.chart("RELIANCE", "5m")["source"]
    n = len(client.calls)
    md.quotes(["NIFTY", "BANKNIFTY", "INDIAVIX"])
    assert len(client.calls) == n + 1 and set(client.calls[-1]) == {"NSE:NIFTY 50", "NSE:NIFTY BANK", "NSE:INDIA VIX"}
    assert md.quote("USDINR")["live"] is False                 # not on Kite: stays on Yahoo
    # Kite refuses (no market-data subscription, expired token...): delayed, with the reason
    md2 = MarketData(fetch=fake_fetch)
    c2 = LiveKite()
    c2.fail = BrokerError(ErrorKind.AUTH_EXPIRED, "Insufficient permission for that call.", "kite")
    md2.live = LiveQuotes(lambda: kite_adapter(c2))
    assert md2.quote("RELIANCE")["live"] is False
    st = md2.live.status()
    assert st["live"] is False and "Insufficient permission" in st["reason"]
    # not logged in: no Kite call at all
    md3 = MarketData(fetch=fake_fetch)
    c3 = LiveKite()
    md3.live = LiveQuotes(lambda: None)
    assert md3.quote("RELIANCE")["live"] is False and c3.calls == [] and "log in to Kite" in md3.live.status()["reason"]


@pytest.fixture
def live_client(tmp_path):
    kc = LiveKite()
    app = create_app(db_url=f"sqlite:///{(tmp_path / 'l.db').as_posix()}", secrets=MemoryStore(), clock=lambda: MARKET_HOURS,
                     adapters_override={"paper": PaperBroker("paper", {}), "kite": kite_adapter(kc)},
                     serve_frontend=False, market=MarketData(fetch=fake_fetch))
    app.state.fo_fetch = lambda url: zipped(CSV) if "20261113" in url else None
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"username": "owner", "password": "owner password 1"})
        c.headers["x-csrf"] = r.json()["csrf"]
        c.post("/api/accounts", json={"id": "kite", "plugin": "kite", "name": "Zerodha", "settings": {"api_key": "x"}})
        c.app.state.adapters["kite"] = kite_adapter(kc)
        c.kc = kc
        yield c


def test_screens_report_live_data(live_client):
    c = live_client
    assert c.get("/api/topbar").json()["data"]["live"] is True
    ch = c.get("/api/market/chart?symbol=RELIANCE&interval=1d").json()
    assert ch["live"] and ch["last"] == 1350.0 and ch["source"] == "Kite (real-time)"
    s = c.get("/api/pulse/screener").json()
    assert s["live"] is True and s["market"]["quotes"]["NIFTY"]["last"] == 25210.0
    rel = [r for r in s["rows"] if r["symbol"] == "RELIANCE"][0]
    assert rel["last"] == 1350.0


def test_live_oi_flow_and_option_chain(live_client):
    c = live_client
    f = c.get("/api/pulse/oi-flow").json()
    assert f["live"] is True and f["date"] == "2026-11-16" and "Kite live" in f["source"]
    aaa = f["groups"]["Long buildup"][0]
    assert aaa["symbol"] == "AAA" and aaa["oi_chg"] == 300 and aaa["price_chg_pct"] == pytest.approx(4.55, abs=0.01)
    assert f["groups"]["Long unwinding"][0]["symbol"] == "BBB"       # 90 -> 88, OI 1100 -> 1000
    o = c.get("/api/pulse/options?symbol=NIFTY").json()
    p = o["pulse"]
    assert o["live"] is True and p["expiry"] == "2026-11-17" and p["underlying"] == 25210.0
    assert p["pcr"] == pytest.approx(6500 / 9400, abs=0.01) and p["call_wall"] == 25200 and p["put_wall"] == 24900
    strike = {s["strike"]: s for s in p["strikes"]}
    assert strike[25200]["call_chg"] == 9000 - 4000 and strike[24900]["put_chg"] == 5000 - 3000


def test_after_the_evening_file_the_day_is_final(live_client):
    c = live_client
    c.app.state.desk.clock = lambda: MARKET_HOURS.replace(day=13, hour=20)
    f = c.get("/api/pulse/oi-flow").json()
    assert f["live"] is False and f["date"] == "2026-11-13"


def test_expired_kite_login_means_delayed(live_client):
    c = live_client
    c.app.state.adapters["kite"] = kite_adapter(c.kc, fresh=False)
    t = c.get("/api/topbar").json()["data"]
    assert t["live"] is False and "log in to Kite" in t["reason"]
    assert c.get("/api/market/chart?symbol=RELIANCE&interval=1d").json()["live"] is False
    assert c.get("/api/pulse/oi-flow").json()["live"] is False


def test_surge_rate_needs_live_data(live_client, monkeypatch):
    import traderdesk.app.pulse_api as papi
    c = live_client
    t = [1000.0]
    monkeypatch.setattr(papi.time, "monotonic", lambda: t[0])
    import traderdesk.market.live as lv
    monkeypatch.setattr(lv.time, "monotonic", lambda: t[0])
    first = c.get("/api/pulse/sectors").json()
    assert first["live"] is True and first["surge"] == []              # one sample is not a rate
    rel = [r for r in first["inflow"] + first["outflow"] if r["symbol"] == "RELIANCE"][0]
    # one minute later RELIANCE has traded 600,000 more shares and sits at the day's high
    c.kc.book["NSE:RELIANCE"] = q(1356.0, 1339.7, o=1340.0, h=1356.0, lo=1338.0, vol=1_600_000)
    t[0] += 60
    second = c.get("/api/pulse/sectors").json()
    surge = {x["symbol"]: x["cr_per_min"] for x in second["surge"]}
    expected = (1356.0 * 1_600_000 * 1.0) / 1e7 - rel["flow_cr"]
    assert surge["RELIANCE"] == pytest.approx(expected, abs=0.05)


def test_kite_refusal_backs_off_instead_of_queueing_every_symbol():
    client = LiveKite()
    client.fail = BrokerError(ErrorKind.AUTH_EXPIRED, "Insufficient permission for that call.", "kite")
    md = MarketData(fetch=fake_fetch)
    md.live = LiveQuotes(lambda: kite_adapter(client))
    for s in ["RELIANCE", "TCS", "INFY", "SBIN", "ITC"]:
        assert md.chart(s, "1d")["live"] is False
    assert len(client.calls) == 1                      # one refused call, then delayed data with no more waits
    assert md.live.status()["live"] is False


def test_one_download_per_symbol_even_when_two_screens_ask_together():
    import threading
    import time as _t
    calls = []

    def slow_fetch(sym, params):
        calls.append(sym)
        _t.sleep(0.2)
        return fake_fetch(sym, params)

    md = MarketData(fetch=slow_fetch)
    ts = [threading.Thread(target=md.chart, args=("RELIANCE", "1d", "2y")) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls == ["RELIANCE.NS"]


def test_batched_quote_is_reused_by_single_lookups():
    client = LiveKite()
    md = MarketData(fetch=fake_fetch)
    md.live = LiveQuotes(lambda: kite_adapter(client))
    md.live.prefetch(["RELIANCE", "NIFTY", "BANKNIFTY"])
    n = len(client.calls)
    for s in ["RELIANCE", "NIFTY", "BANKNIFTY"]:
        md.chart(s, "1d")
    assert len(client.calls) == n                     # no extra Kite calls after the batch
