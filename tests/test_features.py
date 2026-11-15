"""Strategies, tax, payoff, P/L matching and the new screens' endpoints, offline."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from traderdesk.analysis import strategies as st
from traderdesk.app.main import create_app
from traderdesk.core.calendar import IST
from traderdesk.core.secrets import MemoryStore
from traderdesk.market.yahoo import Bar, MarketData
from traderdesk.orders.pnl import Fill, match
from traderdesk.plugins.paper.adapter import PaperBroker
from traderdesk.tax import engine as tax

from conftest import MARKET_HOURS


def day_bars(d: date, path: list[float], start=(9, 15)) -> list[Bar]:
    t0 = datetime(d.year, d.month, d.day, *start, tzinfo=IST)
    out = []
    for i, px in enumerate(path):
        t = t0 + timedelta(minutes=5 * i)
        prev = path[i - 1] if i else px
        out.append(Bar(int(t.timestamp()), prev, max(prev, px) + 0.5, min(prev, px) - 0.5, px, 1000))
    return out


def orb_long_day(d: date) -> list[Bar]:
    # range 99.5-101.5 for 09:15-09:30, then a breakout close at 102, then a run to the 2R target
    return day_bars(d, [100, 101, 100.5, 102, 103, 104, 105, 106, 107, 108])


def test_orb_fires_long_with_midpoint_stop_and_2r_target():
    s = st.orb(orb_long_day(date(2026, 11, 16)))
    assert s and s.side == "BUY" and s.entry == 102
    assert s.stop == pytest.approx((101.5 + 99.5) / 2) and s.target == pytest.approx(102 + 2 * (102 - s.stop))
    assert s.at == "09:35"


def test_backtest_counts_target_and_stop():
    bars = orb_long_day(date(2026, 11, 12)) + day_bars(date(2026, 11, 13), [100, 101, 100.5, 102, 99, 98, 97])
    bt = st.backtest(bars, "orb")
    assert bt["trades"] == 2 and bt["target_pct"] == 50.0 and bt["stop_pct"] == 50.0
    assert bt["expectancy_r"] == pytest.approx(0.5)


def test_regime_needs_history_and_scores_trend():
    rising = [Bar(i * 86400, 100 + i, 101 + i, 99 + i, 100 + i, 0) for i in range(220)]
    r = st.regime(rising, 12.0)
    assert r["score"] == 100 and r["label"] == "Constructive"
    assert st.regime(rising[:30], 12)["score"] is None


def test_pivots():
    p = st.pivots(Bar(0, 0, 110, 90, 100, 0))
    assert p["pivot"] == 100 and p["r1"] == 110 and p["s1"] == 90


def test_tax_rules_stcg_ltcg_exemption_cess_and_advance():
    r = tax.rules_for("2026-27")
    out = tax.compute([tax.Line("equity_stcg", 18400), tax.Line("equity_ltcg", 42600),
                       tax.Line("fo", 21300, 184200), tax.Line("vda", -2410)], r, 0.30, date(2026, 11, 15), paid=7560)
    rows = {x["kind"]: x for x in out["rows"]}
    assert rows["equity_stcg"]["tax"] == pytest.approx(3680)
    assert rows["equity_ltcg"]["tax"] == 0 and out["ltcg_left"] == 125000 - 42600
    assert rows["fo"]["tax"] == pytest.approx(6390)
    assert rows["vda"]["taxable"] == 0
    assert out["cess"] == pytest.approx((3680 + 6390) * 0.04)
    assert out["next_due"]["due"] == "2026-12-15"
    assert tax.fy_of(date(2027, 3, 31)) == "2026-27" and tax.fy_of(date(2027, 4, 1)) == "2027-28"


def test_short_term_loss_sets_off_long_term_gain():
    out = tax.compute([tax.Line("equity_stcg", -50000), tax.Line("equity_ltcg", 200000)],
                      tax.rules_for("2026-27"), 0.3, date(2026, 11, 15))
    rows = {x["kind"]: x for x in out["rows"]}
    assert rows["equity_ltcg"]["taxable"] == 200000 - 50000 - 125000


def test_fifo_matching_long_and_short():
    t = datetime(2026, 11, 16, 10)
    f = lambda side, q, px, m=0: Fill(t + timedelta(minutes=m), "a", "NSE", "X", "MIS", "EQ", side, q, px, True)  # noqa
    closed = match([f("BUY", 10, 100), f("BUY", 5, 110, 1), f("SELL", 12, 120, 2), f("SELL", 3, 90, 3)])
    assert [c.pnl for c in closed] == [200, 20, -60]
    assert closed[0].kind == "intraday"


# ---------------------------------------------------------------- API with a fake feed
def fake_fetch(sym, params):
    base = {"^NSEI": 26000, "^NSEBANK": 58000, "^INDIAVIX": 11.8}.get(sym, 100)
    if params["interval"] == "1d":
        n = 260
        start = datetime(2025, 11, 1, 9, 15, tzinfo=IST)
        ts = [int((start + timedelta(days=i)).timestamp()) for i in range(n)]
        closes = [base * (1 + i / 2000) for i in range(n)]
    else:
        ts, closes = [], []
        for d in (date(2026, 11, 13), date(2026, 11, 16)):
            for b in orb_long_day(d):
                ts.append(b.t)
                closes.append(b.c * base / 100)
    q = {"open": closes, "high": [x * 1.002 for x in closes], "low": [x * 0.998 for x in closes],
         "close": closes, "volume": [1000] * len(closes)}
    return {"chart": {"result": [{"meta": {"currency": "INR", "regularMarketPrice": closes[-1],
                                           "chartPreviousClose": closes[0]},
                                  "timestamp": ts, "indicators": {"quote": [q]}}]}}


@pytest.fixture
def client(tmp_path):
    paper = PaperBroker("paper", {"starting_cash": 1_000_000})
    app = create_app(db_url=f"sqlite:///{(tmp_path / 'f.db').as_posix()}", secrets=MemoryStore(),
                     clock=lambda: MARKET_HOURS, adapters_override={"paper": paper}, serve_frontend=False,
                     market=MarketData(fetch=fake_fetch))
    app.state.desk.session.covered_years = {2026}
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"username": "owner", "password": "correct horse 1"})
        c.headers["x-csrf"] = r.json()["csrf"]
        c.paper = paper
        yield c


def test_chart_has_vwap_and_opening_range(client):
    d = client.get("/api/market/chart?symbol=RELIANCE&interval=5m").json()
    assert d["bars"] and d["vwap"] and d["opening_range"]["high"] > d["opening_range"]["low"]


def test_paper_ticket_gets_a_price_from_the_feed(client):
    o = {"exchange": "NSE", "tradingsymbol": "INFY", "side": "BUY", "quantity": 5, "order_type": "MARKET",
         "product": "MIS", "stop": 100, "target": 115}
    t = client.post("/api/orders/ticket", json={"account_id": "paper", "order": o}).json()
    assert client.paper.prices["NSE:INFY"] == pytest.approx(100 * (1 + 259 / 2000))   # last daily close
    assert t["charges"]["total"] > 0


def test_signals_scan_gate_and_summary(client):
    assert client.post("/api/signals/scan").json()["added"] >= 1
    sigs = client.get("/api/signals").json()
    assert sigs[0]["strategy"] == "ORB" and sigs[0]["side"] == "BUY"
    g = client.get(f"/api/signals/{sigs[0]['id']}/gate").json()
    assert len(g["checks"]) == 5 and g["size"] >= 1
    s = client.get("/api/signals/summary").json()
    assert s["regime"]["score"] is not None and s["today"]["buy"] >= 1
    assert client.put("/api/signals/strategies", json={"strategies": {"orb": {"mode": "paper_auto"}}}).status_code == 400


def test_premarket_brief_and_review(client):
    pre = client.post("/api/briefs/run", json={"kind": "pre"}).json()
    b = client.get(f"/api/briefs/{pre['id']}").json()["body"]
    assert b["levels"]["pivot"] and b["regime"]["score"] is not None and "GIFT Nifty" in b["unavailable"]
    post = client.post("/api/briefs/run", json={"kind": "post"}).json()
    assert client.get(f"/api/briefs/{post['id']}").json()["grade"]


def test_payoff_iron_condor(client):
    legs = [{"side": "BUY", "type": "PE", "strike": 25900, "premium": 38.2},
            {"side": "SELL", "type": "PE", "strike": 26000, "premium": 61.4},
            {"side": "SELL", "type": "CE", "strike": 26400, "premium": 52.1},
            {"side": "BUY", "type": "CE", "strike": 26500, "premium": 31.7}]
    p = client.post("/api/options/payoff", json={"legs": legs, "lot_size": 75, "spot": 26184}).json()
    credit = (61.4 + 52.1 - 38.2 - 31.7) * 75
    assert p["max_profit"] == pytest.approx(credit) and p["max_loss"] == pytest.approx(credit - 100 * 75)
    assert len(p["breakevens"]) == 2


def test_charges_calculator(client):
    r = client.post("/api/charges/calc", json={"quantity": 23, "buy": 1013.5, "sell": 1029.5}).json()
    assert r["gross"] == pytest.approx(368) and 0 < r["total"] < r["gross"] and r["net"] < r["gross"]


def test_money_expenses_budgets_bills(client):
    client.post("/api/money/expenses", json={"on": "2026-11-15", "merchant": "Swiggy", "category": "Food delivery",
                                             "amount": 486})
    client.post("/api/money/expenses", json={"on": "2026-11-01", "merchant": "Salary", "category": "Income",
                                             "amount": 142000})
    client.put("/api/money/budgets", json={"budgets": {"Food delivery": 400}})
    s = client.get("/api/money/summary?month=2026-11").json()
    assert s["spent"] == 486 and s["income"] == 142000 and s["budgets"]["Food delivery"] == 400
    client.post("/api/money/bills", json={"name": "Airtel", "amount": 999, "due": "2026-11-18", "recurrence": "monthly"})
    b = client.get("/api/money/bills").json()
    assert b["next_10_days"]["count"] == 1
    client.post(f"/api/money/bills/{b['bills'][0]['id']}/paid")
    b = client.get("/api/money/bills").json()
    assert [x["status"] for x in b["bills"]] == ["paid", "due"] and b["bills"][1]["due"] == "2026-12-18"


def test_metals_tax_and_portfolio_overview(client):
    client.post("/api/metals", json={"metal": "gold", "app": "PhonePe", "vault": "SafeGold", "grams": 0.31,
                                     "cost": 3640, "bought_on": "2026-05-01"})
    client.put("/api/metals/rates", json={"rates": {"gold": {"sell": 12560, "buy": 13180}}})
    m = client.get("/api/metals").json()
    assert m["value"] == pytest.approx(0.31 * 12560)
    client.post("/api/tax/entries", json={"on": "2026-06-01", "kind": "equity_stcg", "amount": 10000})
    t = client.get("/api/tax").json()
    assert t["rows"][0]["tax"] == pytest.approx(2000) and t["fy"] == "2026-27"
    p = client.get("/api/portfolio/overview").json()
    assert p["metals"]["value"] and "allocation" in p


def test_users_sessions_and_dashboards(client):
    client.post("/api/users", json={"username": "ca", "password": "accountant 1", "role": "accountant"})
    assert client.patch("/api/users/ca", json={"role": "viewer"}).status_code == 200
    assert client.patch("/api/users/owner", json={"active": False}).status_code == 400
    assert any(s["current"] for s in client.get("/api/sessions").json())
    d = client.get("/api/dashboards").json()["dashboards"]
    d[0]["widgets"].pop()
    assert client.put("/api/dashboards", json={"dashboards": d}).status_code == 200
    assert len(client.get("/api/dashboards").json()["dashboards"][0]["widgets"]) == 6


def test_desk_overview_and_exit_ticket(client):
    o = {"exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "quantity": 10, "order_type": "LIMIT",
         "product": "MIS", "price": 120, "stop": 100, "target": 150}
    client.paper.set_price("NSE:RELIANCE", 110)
    t = client.post("/api/orders/ticket", json={"account_id": "paper", "order": o}).json()
    client.post(f"/api/orders/{t['id']}/confirm", json={"fingerprint": t["fingerprint"], "typed": "BUY 10 RELIANCE"})
    ov = client.get("/api/desk/overview").json()
    pos = [p for p in ov["positions"] if p["tradingsymbol"] == "RELIANCE"]
    assert pos and pos[0]["stop"] == 100 and pos[0]["quantity"] == 10
    assert ov["indices"]["NIFTY"]["last"]
    ex = client.post("/api/positions/exit", json={"account_id": "paper", "exchange": "NSE",
                                                  "tradingsymbol": "RELIANCE", "product": "MIS", "quantity": 10}).json()
    rows = {r["id"]: r for r in client.get("/api/orders").json()}
    assert rows[ex["id"]]["order"]["side"] == "SELL" and rows[ex["id"]]["kind"] == "exit"


def test_assistant_without_key_explains(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = client.get("/api/assistant/settings").json()
    assert s["key_set"] is False and s["model"] == "claude-sonnet-5-5"


def test_money_export_csv_for_a_month(client):
    client.post("/api/money/expenses", json={"on": "2026-11-10", "merchant": "Cafe", "category": "Food delivery", "amount": 250})
    r = client.get("/api/money/export?month=2026-11")
    assert r.status_code == 200 and "expenses-2026-11.csv" in r.headers["content-disposition"]
    assert "Cafe" in r.text and "-250" in r.text
    assert client.get("/api/money/export").status_code == 200
    assert client.get('/api/money/export?month=2026-11";x').status_code == 422     # no header injection
