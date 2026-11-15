"""Connectors: Dhan, Upstox, INDstocks (documented response shapes), CCXT, CSV and CAS uploads, AMFI NAVs."""
import httpx
import pytest
from fastapi.testclient import TestClient

from traderdesk.app.main import create_app
from traderdesk.brokers.registry import PluginRegistry
from traderdesk.core.errors import BrokerError
from traderdesk.core.models import Cap, OrderRequest, Side
from traderdesk.core.secrets import MemoryStore
from traderdesk.market.yahoo import MarketData
from traderdesk.plugins.cas.adapter import holdings_from_cas, parse_amfi
from traderdesk.plugins.ccxt_exchange.adapter import CcxtExchange
from traderdesk.plugins.csv_import.adapter import parse_holdings_csv
from traderdesk.plugins.dhan.adapter import DhanBroker
from traderdesk.plugins.indstocks.adapter import IndstocksBroker
from traderdesk.plugins.paper.adapter import PaperBroker
from traderdesk.plugins.upstox.adapter import UpstoxBroker

from conftest import MARKET_HOURS
from test_features import fake_fetch


def mock(routes: dict, seen: list | None = None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        body = routes.get(req.url.path)
        return httpx.Response(200 if body is not None else 404, json=body if body is not None else {"error": "nf"})
    return handler


def test_all_connectors_are_discovered():
    reg = PluginRegistry().discover()
    assert {"kite", "paper", "dhan", "upstox", "indstocks", "ccxt", "csv", "cas"} <= set(reg.manifests)
    for pid in ("dhan", "upstox", "indstocks", "ccxt", "csv"):
        assert reg.get(pid).capabilities == {Cap.READ}, pid    # read-only until verified live


def test_dhan_reads_holdings_positions_funds():
    sec = MemoryStore({"d:access_token": "JWT"})
    seen = []
    http = httpx.Client(base_url="https://api.dhan.co/v2", transport=httpx.MockTransport(mock({
        "/v2/holdings": [{"exchange": "ALL", "tradingSymbol": "HDFC", "securityId": "1330", "isin": "INE001A01036",
                          "totalQty": 10, "avgCostPrice": 2655.0}],
        "/v2/positions": [{"tradingSymbol": "TCS", "exchangeSegment": "NSE_EQ", "productType": "INTRADAY", "netQty": 5,
                           "costPrice": 3000.0, "buyAvg": 3000.0, "realizedProfit": 0, "unrealizedProfit": 50.0}],
        "/v2/fundlimit": {"availabelBalance": 98440.0, "utilizedAmount": 1560.0},
    }, seen)))
    d = DhanBroker("d", {"client_id": "100"}, sec, http=http)
    d.price_lookup = lambda ex, s: 2700.0
    h = d.holdings()
    assert h[0].tradingsymbol == "HDFC" and h[0].last_price == 2700.0 and h[0].average_price == 2655.0
    p = d.positions()
    assert p[0].product.value == "MIS" and p[0].last_price == pytest.approx(3010.0) and p[0].pnl == 50.0
    assert d.funds() == {"cash": 98440.0, "used": 1560.0}
    assert seen[0].headers["access-token"] == "JWT"
    with pytest.raises(BrokerError):
        d.place_order(OrderRequest("NSE", "TCS", Side.BUY, 1))


def test_dhan_without_token_asks_for_login():
    with pytest.raises(BrokerError) as e:
        DhanBroker("d", {"client_id": "1"}, MemoryStore()).holdings()
    assert e.value.kind.value == "auth_expired"


def test_upstox_login_url_holdings_and_expired_token():
    sec = MemoryStore({"u:access_token": "TOK", "u:token_issued_at": "2026-11-16T08:00:00+05:30"})
    seen = []
    http = httpx.Client(base_url="https://api.upstox.com/v2", transport=httpx.MockTransport(mock({
        "/v2/portfolio/long-term-holdings": {"status": "success", "data": [
            {"tradingsymbol": "INFY", "exchange": "NSE", "isin": "INE009A01021", "quantity": 4, "t1_quantity": 1,
             "average_price": 1400.0, "last_price": 1500.0, "close_price": 1490.0, "company_name": "Infosys"}]},
        "/v2/portfolio/short-term-positions": {"data": []},
        "/v2/user/get-funds-and-margin": {"data": {"equity": {"available_margin": 5000.0, "used_margin": 200.0}}},
    }, seen)))
    u = UpstoxBroker("u", {"api_key": "KEY"}, sec, http=http)
    assert "client_id=KEY" in u.login_url() and "response_type=code" in u.login_url()
    h = u.holdings()
    assert h[0].quantity == 5 and h[0].last_price == 1500.0
    assert u.funds() == {"cash": 5000.0, "used": 200.0}
    assert seen[0].headers["authorization"] == "Bearer TOK"
    from datetime import datetime
    from traderdesk.plugins._rest import IST
    assert u.session_status(datetime(2026, 11, 17, 3, 0, tzinfo=IST))["connected"]
    assert not u.session_status(datetime(2026, 11, 17, 4, 0, tzinfo=IST))["connected"]


def test_upstox_token_rejected_maps_to_auth_expired():
    http = httpx.Client(base_url="https://api.upstox.com/v2",
                        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"status": "error"})))
    u = UpstoxBroker("u", {"api_key": "K"}, MemoryStore({"u:access_token": "x"}), http=http)
    with pytest.raises(BrokerError) as e:
        u.holdings()
    assert e.value.kind.value == "auth_expired"


def test_indstocks_raw_token_header_and_totp_login():
    seen = []
    routes = {"/portfolio/holdings": {"status": "success", "data": [{"symbol": "SBIN", "isin": "INE062A01020", "total_qty": 3, "avg_price": 800.0}]},
              "/funds": {"data": {"detailed_avl_balance": {"eq_cnc": 1234.5}}},
              "/generate/token": {"token": "NEWTOKEN"}}
    http = httpx.Client(base_url="https://api.indstocks.com", transport=httpx.MockTransport(mock(routes, seen)))
    sec = MemoryStore()
    b = IndstocksBroker("i", {"client_id": "CID"}, sec, http=http)
    b.login_with_totp("1234", "123456")
    assert sec.get("i:access_token") == "NEWTOKEN" and seen[0].headers["x-api-key"] == "CID"
    assert b.holdings()[0].tradingsymbol == "SBIN"
    assert seen[-1].headers["authorization"] == "NEWTOKEN"          # no Bearer prefix
    assert b.funds()["cash"] == 1234.5


class FakeCcxt:
    def fetch_balance(self):
        return {"total": {"BTC": 0.01, "USDT": 50.0, "DOGE": 0}}

    def fetch_tickers(self, syms):
        return {"BTC/USDT": {"last": 90000.0}}


def test_ccxt_balances_in_usd():
    c = CcxtExchange("x", {"exchange": "binance", "api_key": "k"}, MemoryStore({"x:api_secret": "s"}), client=FakeCcxt())
    h = {x.tradingsymbol: x for x in c.holdings()}
    assert h["BTC"].value == pytest.approx(900.0) and h["USDT"].value == 50.0 and "DOGE" not in h
    assert all(x.currency == "USD" and x.asset_class == "crypto" for x in h.values())


def test_csv_parser_handles_common_exports():
    kite = b"Symbol,ISIN,Quantity Available,Average Price,Previous Closing Price\nRELIANCE,INE002A01018,10,1300.5,1342.7\n"
    r = parse_holdings_csv(kite)
    assert r[0]["tradingsymbol"] == "RELIANCE" and r[0]["quantity"] == 10 and r[0]["last_price"] == 1342.7
    us = b"Report generated 15 Nov\n\nStock Name,Shares,Avg Buy Price,Current Value\nMSFT,0.42,\"$418.20\",\"$181.04\"\n"
    r = parse_holdings_csv(us, "USD")
    assert r[0]["asset_class"] == "us_stock" and r[0]["average_price"] == 418.2 and r[0]["last_price"] == pytest.approx(431.05, rel=1e-3)
    with pytest.raises(BrokerError):
        parse_holdings_csv(b"a,b\n1,2\n")


def test_amfi_parser_and_cas_mapping():
    text = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n\n"
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)\n\nAxis Mutual Fund\n\n"
            "135762;INF846K01WO1;-;Axis Children's Fund;Direct Plan;Growth Option;31.0814;13-Nov-2026\n"
            "1;INF0;INF1;Zero;;;0;13-Nov-2026\n")
    navs = parse_amfi(text)
    assert navs == {"INF846K01WO1": (31.0814, "13-Nov-2026")}
    cas = {"folios": [{"folio": "123/45", "amc": "PPFAS", "schemes": [
        {"scheme": "Parag Parikh Flexi Cap - Direct Growth", "isin": "INF879O01027", "close": 98.058,
         "valuation": {"nav": 95.9, "value": 9403.8, "cost": 9000.0}},
        {"scheme": "Old fund", "isin": "INF000", "close": 0, "valuation": {"nav": 10}}]}]}
    rows = holdings_from_cas(cas)
    assert len(rows) == 1 and rows[0]["asset_class"] == "mutual_fund" and rows[0]["average_price"] == pytest.approx(9000 / 98.058)


@pytest.fixture
def client(tmp_path):
    app = create_app(db_url=f"sqlite:///{(tmp_path / 'c.db').as_posix()}", secrets=MemoryStore(), clock=lambda: MARKET_HOURS,
                     adapters_override={"paper": PaperBroker("paper", {})}, serve_frontend=False, market=MarketData(fetch=fake_fetch))
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"username": "owner", "password": "owner password 1"})
        c.headers["x-csrf"] = r.json()["csrf"]
        yield c


def test_csv_upload_shows_in_portfolio_and_disconnect(client):
    assert client.post("/api/accounts", json={"id": "indus", "plugin": "csv", "name": "INDmoney US",
                                              "settings": {"source": "INDmoney US", "currency": "USD"}}).status_code == 200
    csv = b"Stock Name,Shares,Avg Buy Price,Current Price\nMSFT,0.42,418.20,431.05\n"
    r = client.post("/api/accounts/indus/upload", files={"file": ("us.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json()["rows"] == 1
    p = client.get("/api/portfolio").json()
    msft = [h for h in p["holdings"] if h["symbol"] == "MSFT"][0]
    usdinr = 100 * (1 + 259 / 2000)          # fake feed's last daily close
    assert msft["value_inr"] == pytest.approx(0.42 * 431.05 * usdinr, rel=1e-3)
    bad = client.post("/api/accounts/indus/upload", files={"file": ("x.csv", b"nothing,here\n", "text/csv")})
    assert bad.status_code == 400
    assert client.delete("/api/accounts/indus").status_code == 200
    assert client.delete("/api/accounts/paper").status_code == 400


def test_token_paste_keeps_the_token(client):
    client.post("/api/accounts", json={"id": "dh", "plugin": "dhan", "name": "Dhan", "settings": {"client_id": "100"}})
    assert client.get("/api/accounts").json()[1]["needs_login"]
    assert client.post("/api/accounts/dh/secrets", json={"secrets": {"access_token": "JWT"}}).status_code == 200
    acc = {a["id"]: a for a in client.get("/api/accounts").json()}
    assert acc["dh"]["connected"] and acc["dh"]["capabilities"] == ["READ"]
    assert client.post("/api/accounts/dh/totp-login", json={"pin": "12", "totp": "1"}).status_code == 422
    assert client.post("/api/accounts/paper/totp-login", json={"pin": "1234", "totp": "123456"}).status_code == 400


def test_read_only_account_cannot_take_orders(client):
    client.post("/api/accounts", json={"id": "dh", "plugin": "dhan", "name": "Dhan", "settings": {"client_id": "100"}})
    o = {"exchange": "NSE", "tradingsymbol": "TCS", "side": "BUY", "quantity": 1, "order_type": "LIMIT", "product": "MIS",
         "price": 108, "stop": 107, "target": 112}
    t = client.post("/api/orders/ticket", json={"account_id": "dh", "order": o})
    # in paper mode it routes to the simulator twin; it never reaches Dhan
    assert t.status_code == 200 and t.json()["paper"] is True
