from datetime import datetime, timedelta

import pytest

from traderdesk.brokers.registry import PluginRegistry
from traderdesk.core.errors import BrokerError, ErrorKind
from traderdesk.core.models import Cap, OrderRequest, OrderStatus, OrderType, Product, Side
from traderdesk.core.ratelimit import TokenBucket
from traderdesk.core.secrets import MemoryStore
from traderdesk.plugins.kite.adapter import (IST, KiteBroker, kite_order_params, map_error, map_status,
                                             token_is_fresh)


class TokenException(Exception):
    pass


class FakeKite:
    def __init__(self):
        self.calls = []
        self.token = None

    def set_access_token(self, t):
        self.token = t

    def login_url(self):
        return "https://kite.zerodha.com/connect/login?api_key=x&v=3"

    def generate_session(self, rt, api_secret):
        assert api_secret == "sec"
        return {"access_token": "tok-" + rt, "user_id": "AB1234", "user_name": "Me"}

    def holdings(self):
        return [{"tradingsymbol": "NIFTYBEES", "exchange": "NSE", "quantity": 10, "t1_quantity": 2,
                 "average_price": 250.0, "last_price": 260.0, "isin": "INF204KB14I2", "close_price": 259.0}]

    def mf_holdings(self):
        return [{"tradingsymbol": "INF879O01027", "fund": "PPFAS", "quantity": 10, "average_price": 90,
                 "last_price": 94}]

    def place_order(self, **kw):
        self.calls.append(("place", kw))
        return "250101000001"

    def orders(self):
        return [{"order_id": "1", "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
                 "quantity": 1, "order_type": "LIMIT", "product": "CNC", "status": "TRIGGER PENDING",
                 "tag": "TDP-M-1"}]

    def profile(self):
        raise TokenException("Token is invalid or has expired.")


def kite(secrets=None):
    reg = PluginRegistry().discover()
    s = secrets or MemoryStore({"kite:api_secret": "sec"})
    return KiteBroker("kite", {"api_key": "x"}, s, reg.get("kite"), client=FakeKite()), s


def test_registry_finds_builtin_plugins_and_schema():
    reg = PluginRegistry().discover()
    assert {"kite", "paper"} <= set(reg.manifests)
    m = reg.get("kite")
    assert Cap.OCO in m.capabilities and m.requires_static_ip
    assert m.settings_schema["properties"]["api_secret"]["secret"] is True
    a = reg.create("paper", "p1", {"starting_cash": 1}, MemoryStore())
    assert a.cash == 1


def test_login_stores_token_in_vault_not_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    k, s = kite()
    who = k.complete_login("rt1")
    assert who["user_id"] == "AB1234"
    assert s.get("kite:access_token") == "tok-rt1"
    assert not list(tmp_path.iterdir())     # nothing written to disk
    assert k.session_status()["connected"]


def test_token_freshness_resets_at_six_ist():
    issued = datetime(2026, 11, 16, 8, 0, tzinfo=IST)
    assert token_is_fresh(issued, datetime(2026, 11, 16, 23, 0, tzinfo=IST))
    assert token_is_fresh(issued, datetime(2026, 11, 17, 5, 59, tzinfo=IST))
    assert not token_is_fresh(issued, datetime(2026, 11, 17, 6, 1, tzinfo=IST))
    assert not token_is_fresh(None)


def test_holdings_include_t1_and_mf():
    k, _ = kite()
    hs = k.holdings()
    assert hs[0].quantity == 12 and hs[0].asset_class == "etf"
    assert hs[1].asset_class == "mutual_fund" and hs[1].name == "PPFAS"


def test_order_params_and_tag_truncation():
    o = OrderRequest("NSE", "INFY", Side.BUY, 5, OrderType.SL, Product.MIS, price=1501, trigger_price=1500,
                     tag="TDP-abcdefgh-1234567890")
    p = kite_order_params(o)
    assert p["price"] == 1501 and p["trigger_price"] == 1500 and len(p["tag"]) == 20
    assert "price" not in kite_order_params(OrderRequest("NSE", "INFY", Side.BUY, 1, OrderType.MARKET))
    k, _ = kite()
    assert k.place_order(o).order_id == "250101000001"
    assert k.kite.calls[0][1]["variety"] == "regular"


def test_status_and_error_mapping():
    assert map_status("TRIGGER PENDING") == OrderStatus.TRIGGER_PENDING
    assert map_status("AMO REQ RECEIVED") == OrderStatus.PENDING
    k, _ = kite()
    assert k.orders()[0].status == OrderStatus.TRIGGER_PENDING
    assert map_error(TokenException("expired")).kind == ErrorKind.AUTH_EXPIRED
    assert map_error(TimeoutError("timed out")).kind == ErrorKind.NETWORK
    assert map_error(Exception("Insufficient funds. Required margin is 100")).kind == ErrorKind.INSUFFICIENT_MARGIN


def test_capability_gate():
    k, _ = kite()
    k.capabilities.discard(Cap.TRADE_FO)
    with pytest.raises(BrokerError) as e:
        k.place_order(OrderRequest("NFO", "NIFTY26NOVFUT", Side.BUY, 75, instrument_type="FUT", price=1))
    assert e.value.kind == ErrorKind.UNSUPPORTED


def test_token_bucket():
    t = {"now": 0.0}
    b = TokenBucket(2, clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s))
    assert b.try_acquire() and b.try_acquire() and not b.try_acquire()
    t["now"] += 0.5
    assert b.try_acquire()
    b.acquire()   # sleeps via the fake clock instead of blocking
    assert t["now"] >= 1.0
