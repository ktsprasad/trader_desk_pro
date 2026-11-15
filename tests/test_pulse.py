"""Market pulse: indicators, NSE F&O bhavcopy parsing, OI flow, option pulse, routes. No network."""
import io
import re
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from traderdesk.analysis import pulse as pl
from traderdesk.app.main import create_app
from traderdesk.core.calendar import IST, nse_session
from traderdesk.core.secrets import MemoryStore
from traderdesk.market import nse_fo as fo
from traderdesk.market.yahoo import Bar, MarketData
from traderdesk.plugins.paper.adapter import PaperBroker

from conftest import MARKET_HOURS
from test_features import fake_fetch

HDR = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,"
       "FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,"
       "TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def row(tp, sym, exp, strike, opt, close, prev, und, oi, chg, vol=100, lot=50):
    return (f"2026-11-13,2026-11-13,FO,NSE,{tp},1,,{sym},,{exp},{exp},{strike},{opt},{sym}X,0,0,0,{close},0,{prev},{und},0,"
            f"{oi},{chg},{vol},{vol * close * 10},1,F1,{lot},,,,,")


CSV = "\n".join([HDR,
    row("STF", "AAA", "2026-11-23", "", "", 110, 100, 110, 1200, 200),      # up, OI up -> long buildup
    row("STF", "AAA", "2026-12-29", "", "", 111, 101, 110, 100, 0),
    row("STF", "BBB", "2026-11-23", "", "", 90, 100, 90, 1100, 100),        # down, OI up -> short buildup
    row("STF", "CCC", "2026-11-23", "", "", 105, 100, 105, 900, -100),      # up, OI down -> short covering
    row("STF", "DDD", "2026-11-23", "", "", 95, 100, 95, 800, -200),        # down, OI down -> long unwinding
    row("IDF", "NIFTY", "2026-11-23", "", "", 25100, 25000, 25100, 5000, 500),
    row("IDO", "NIFTY", "2026-11-17", "24900.00", "PE", 20, 30, 25100, 3000, 1000),
    row("IDO", "NIFTY", "2026-11-17", "25000.00", "PE", 40, 50, 25100, 1000, 100),
    row("IDO", "NIFTY", "2026-11-17", "25000.00", "CE", 130, 100, 25100, 500, -50),
    row("IDO", "NIFTY", "2026-11-17", "25200.00", "CE", 30, 25, 25100, 4000, 2000, vol=9000),
    row("IDO", "NIFTY", "2026-11-24", "25200.00", "CE", 90, 80, 25100, 99999, 0),
    "2026-11-13,2026-11-13,CM,NSE,STK,1,,IGNORED,,,,,,X,0,0,0,1,0,1,1,0,0,0,0,0,0,F1,1,,,,,"])


def zipped(text: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("BhavCopy_NSE_FO_0_0_0_20261113_F_0000.csv", text)
    return b.getvalue()


def bars_from(closes, vols=None):
    t0 = int(datetime(2025, 1, 1, tzinfo=IST).timestamp())
    return [Bar(t0 + i * 86400, c, c * 1.01, c * 0.99, c, (vols or [1000] * len(closes))[i]) for i, c in enumerate(closes)]


def test_rsi_mfi_and_bollinger():
    assert pl.rsi([float(i) for i in range(1, 40)]) == 100.0
    assert pl.rsi([float(40 - i) for i in range(40)]) == 0.0
    zig = [100 + (1 if i % 2 else -1) for i in range(60)]
    assert 40 <= pl.rsi(zig) <= 60
    assert pl.rsi([1.0, 2.0]) is None
    up = bars_from([100 + i for i in range(30)])
    assert pl.mfi(up) == 100.0
    assert pl.bb_width([100.0] * 25) == 0.0
    flat_then_wild = [100.0 + (i % 7) * 3 for i in range(150)] + [100.0] * 20
    assert pl.squeeze_rank(flat_then_wild) == 0      # today's width is the narrowest of the window


def test_swing_levels_keep_only_untaken():
    closes = [100, 101, 102, 110, 102, 101, 100, 99, 98, 90, 98, 99, 100, 101, 102, 103, 104]
    lv = pl.swing_levels(bars_from(closes), k=2)
    kinds = {(round(x["price"]), x["side"]) for x in lv}
    assert (111, "above") in kinds and (89, "below") in kinds
    # once price trades above the swing high it is no longer listed
    taken = pl.swing_levels(bars_from(closes + [115, 116, 117]), k=2)
    assert not any(round(x["price"]) == 111 for x in taken)


def test_metrics_flags_breakout_and_inflow():
    closes = [100 + (i % 5) for i in range(260)] + [120]
    vols = [1000] * 260 + [5000]
    m = pl.metrics("TEST", bars_from(closes, vols))
    assert m["flow"] == "Inflow" and m["turnover_ratio"] >= 4
    assert "52-week high breakout" in m["setups"] and any(s.startswith("Turnover") for s in m["setups"])
    assert pl.metrics("SHORT", bars_from([100.0] * 10)) is None
    lq = pl.liquidity_levels(bars_from(closes, vols))
    assert all(x["price"] < lq["last"] for x in lq["below"]) and all(x["price"] > lq["last"] for x in lq["above"])


def test_bhavcopy_parse_oi_flow_and_option_pulse():
    rows = fo.parse(fo.unzip(zipped(CSV)))
    assert len(rows) == 11 and all(r["type"] in ("STF", "IDF", "STO", "IDO") for r in rows)
    f = fo.oi_flow(rows)
    first = {k: v[0]["symbol"] for k, v in f["groups"].items() if v}
    assert first["Long buildup"] in ("AAA", "NIFTY") and first["Short buildup"] == "BBB"
    assert first["Short covering"] == "CCC" and first["Long unwinding"] == "DDD"
    aaa = [x for x in f["groups"]["Long buildup"] if x["symbol"] == "AAA"][0]
    assert aaa["oi"] == 1300 and aaa["oi_chg"] == 200 and aaa["oi_chg_pct"] == pytest.approx(200 / 1100 * 100, abs=0.01)
    p = fo.option_pulse(rows, "nifty")
    assert p["expiry"] == "2026-11-17" and p["expiries"] == ["2026-11-17", "2026-11-24"]
    assert p["pcr"] == pytest.approx(4000 / 4500, abs=0.01)
    assert p["call_wall"] == 25200 and p["put_wall"] == 24900 and p["underlying"] == 25100
    assert p["max_pain"] == 25000          # payout to holders is lowest at 25000 for these strikes
    assert p["call_adds"][0]["strike"] == 25200
    assert fo.option_pulse(rows, "AAA") is None
    assert fo.most_active(rows, 1)[0]["strike"] == 25200 and fo.most_active(rows, 1)[0]["lots"] == 180


def test_latest_file_waits_for_publication_and_skips_holidays():
    seen = []

    def fetch(url):
        seen.append(re.search(r"_([0-9]{8})_F_", url).group(1))
        return zipped(CSV) if "20261113" in url else None

    s = nse_session()
    # Monday 16 Nov 10:30: today's file is not out, Saturday/Sunday are skipped -> Friday 13th
    b = fo.FoBhavcopy(s, lambda: MARKET_HOURS, fetch)
    d, rows = b.latest()
    assert d.isoformat() == "2026-11-13" and seen == ["20261113"] and rows
    # Monday 20:00: tries Monday first, falls back to Friday; the miss is remembered
    seen.clear()
    late = fo.FoBhavcopy(s, lambda: MARKET_HOURS.replace(hour=20), fetch)
    assert late.latest()[0].isoformat() == "2026-11-13" and seen == ["20261116", "20261113"]
    seen.clear()
    late.latest()
    assert seen == []                       # both cached: no second download
    with pytest.raises(LookupError):
        fo.FoBhavcopy(s, lambda: MARKET_HOURS, lambda u: None).latest()


@pytest.fixture
def client(tmp_path):
    app = create_app(db_url=f"sqlite:///{(tmp_path / 'p.db').as_posix()}", secrets=MemoryStore(), clock=lambda: MARKET_HOURS,
                     adapters_override={"paper": PaperBroker("paper", {})}, serve_frontend=False, market=MarketData(fetch=fake_fetch))
    app.state.fo_fetch = lambda url: zipped(CSV.replace("NIFTY", "RELIANCE").replace("25100", "125").replace("24900.00", "120.00")
                                            .replace("25000.00", "124.00").replace("25200.00", "130.00")
                                            .replace("25000", "120")) if "20261113" in url else None
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"username": "owner", "password": "owner password 1"})
        c.headers["x-csrf"] = r.json()["csrf"]
        yield c


def test_pulse_routes(client):
    s = client.get("/api/pulse/screener").json()
    assert s["universe"] >= 40 and len(s["rows"]) >= 40 and s["market"]["regime"]["score"] is not None
    r0 = s["rows"][0]
    assert {"rsi", "rsi_weekly", "mfi", "flow", "squeeze_rank", "setups", "atr_pct"} <= set(r0)
    lq = client.get("/api/pulse/liquidity?symbol=RELIANCE").json()
    assert lq["options_day"] == "2026-11-13" and any("call OI" in x["kind"] for x in lq["above"] + lq["below"])
    of = client.get("/api/pulse/oi-flow").json()
    assert of["date"] == "2026-11-13" and of["counts"]["Short buildup"] == 1
    op = client.get("/api/pulse/options?symbol=RELIANCE").json()
    assert op["pulse"]["symbol"] == "RELIANCE" and op["most_active"] and "RELIANCE" in op["symbols"]
    assert client.get("/api/pulse/options?symbol=ZZZZ").status_code == 404


def test_pulse_says_when_nse_is_unreachable_and_needs_sign_in(client):
    client.app.state.fo_fetch = lambda url: None          # NSE has no file for any recent day
    r = client.get("/api/pulse/oi-flow")
    assert r.status_code == 503 and "bhavcopy" in r.json()["detail"]
    anon = TestClient(client.app)
    for p in ("/api/pulse/screener", "/api/pulse/oi-flow", "/api/pulse/options", "/api/pulse/liquidity?symbol=X"):
        assert anon.get(p).status_code == 401, p


def test_money_flow_and_sector_flows():
    b = Bar(0, 100, 110, 90, 110, 1e6)              # closed at the high: full inflow
    assert pl.money_flow(b) == pytest.approx(110 * 1e6)
    assert pl.money_flow(Bar(0, 100, 110, 90, 90, 1e6)) == pytest.approx(-90 * 1e6)
    assert pl.money_flow(Bar(0, 100, 100, 100, 100, 1e6)) == 0.0          # no range, no flow
    prev = Bar(-86400, 100, 101, 99, 100, 1)
    stocks = {"AAA": ("IT", [prev, Bar(0, 100, 110, 90, 110, 1e6)]), "BBB": ("IT", [prev, Bar(0, 100, 110, 90, 105, 1e6)]),
              "CCC": ("Metals", [prev, Bar(0, 100, 110, 90, 91, 2e6)])}
    f = pl.sector_flows(stocks)
    assert [s["sector"] for s in f["sectors"]] == ["IT", "Metals"]
    assert f["inflow"][0]["symbol"] == "AAA" and f["outflow"][0]["symbol"] == "CCC"
    assert f["inflow_total_cr"] == pytest.approx((110 * 1e6 + 105 * 1e6 * 0.5) / 1e7, abs=0.01)
    assert f["breadth"] == {"up": 2, "down": 1, "flat": 0}
    assert f["headline"].startswith("Money moving into IT") and "Metals seeing outflows" in f["headline"]


def test_sector_and_asset_routes(client):
    s = client.get("/api/pulse/sectors").json()
    assert len(s["sectors"]) >= 15 and s["live"] is False and s["surge"] == []
    assert s["headline"] and "breadth" in s["headline"]
    a = client.get("/api/pulse/assets").json()
    syms = {r["symbol"] for r in a["rows"]}
    assert {"GOLD", "USDINR", "BTC-USD"} <= syms and a["derived"] and not a["failed"]
    gold = [r for r in a["rows"] if r["symbol"] == "GOLD"][0]
    fx = [r for r in a["rows"] if r["symbol"] == "USDINR"][0]["last"]
    assert a["derived"][0]["value"] == pytest.approx(round(gold["last"] * fx / 31.1035 * 10), abs=1)


def test_chart_signal_route_all_styles(client):
    for style in ("intraday", "swing", "longterm", "options"):
        r = client.get(f"/api/market/signals?symbol=RELIANCE&style={style}")
        assert r.status_code == 200, style
        d = r.json()
        assert d["bars"] and d["rules"] and "state" in d["now"] and "trades" in d["stats"]
        times = {b["time"] for b in d["bars"]}
        assert all(m["time"] in times for m in d["markers"])          # every mark sits on a real bar
    o = client.get("/api/market/signals?symbol=RELIANCE&style=options").json()
    assert o["strike_step"] and all(("CE" in t["option"]) or ("PE" in t["option"]) for t in o["trades"])
    assert client.get("/api/market/signals?symbol=RELIANCE&style=crystal-ball").status_code == 422
    assert client.get("/api/market/signals?symbol=RELIANCE&style=intraday&strategy=magic").status_code == 422
