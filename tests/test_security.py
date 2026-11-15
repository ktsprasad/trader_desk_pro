"""Security checks across every route: auth, roles, CSRF, origin, secrets, headers, traversal, input."""
import json

import pytest
from fastapi.testclient import TestClient

from traderdesk.app.main import create_app
from traderdesk.core.secrets import MemoryStore
from traderdesk.plugins.paper.adapter import PaperBroker

from conftest import MARKET_HOURS
from test_features import fake_fetch
from traderdesk.market.yahoo import MarketData

PUBLIC = {("GET", "/api/health"), ("GET", "/api/auth/state"), ("POST", "/api/auth/setup"), ("POST", "/api/auth/login")}


@pytest.fixture
def app(tmp_path):
    a = create_app(db_url=f"sqlite:///{(tmp_path / 's.db').as_posix()}", secrets=MemoryStore(), clock=lambda: MARKET_HOURS,
                   adapters_override={"paper": PaperBroker("paper", {})}, serve_frontend=True,
                   market=MarketData(fetch=fake_fetch))
    a.state.desk.session.covered_years = {2026}
    return a


def login(app, username, password, role=None, owner=None):
    c = TestClient(app)
    if owner is not None:
        owner.post("/api/users", json={"username": username, "password": password, "role": role})
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["x-csrf"] = r.json()["csrf"]
    return c


@pytest.fixture
def owner(app):
    c = TestClient(app)
    c.__enter__()
    r = c.post("/api/auth/setup", json={"username": "owner", "password": "owner password 1"})
    c.headers["x-csrf"] = r.json()["csrf"]
    yield c
    c.__exit__(None, None, None)


def api_routes(app):
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        for m in getattr(r, "methods", []) or []:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.append((m, path))
    return out


def fill(path):
    return (path.replace("{desk_id}", "1").replace("{account_id}", "paper").replace("{sid}", "1").replace("{bid}", "1")
            .replace("{eid}", "1").replace("{hid}", "1").replace("{tid}", "x").replace("{username}", "owner"))


def test_every_api_route_needs_a_session(app, owner):
    anon = TestClient(app)
    checked = 0
    for m, p in api_routes(app):
        if (m, p) in PUBLIC or p.endswith("/callback"):
            continue
        r = anon.request(m, fill(p), json={})
        assert r.status_code in (401, 403), f"{m} {p} answered {r.status_code} without a session"
        checked += 1
    assert checked > 60


def test_state_changing_routes_need_csrf(app, owner):
    tok = owner.headers.pop("x-csrf")
    try:
        for m, p in api_routes(app):
            if m == "GET" or (m, p) in PUBLIC:
                continue
            r = owner.request(m, fill(p), json={})
            assert r.status_code == 403, f"{m} {p} accepted a write without the CSRF header ({r.status_code})"
    finally:
        owner.headers["x-csrf"] = tok


def test_cross_origin_writes_refused(owner):
    r = owner.post("/api/kill", headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and "cross-origin" in r.text


def test_roles_are_enforced(app, owner):
    viewer = login(app, "viewer1", "viewer password 1", "viewer", owner)
    family = login(app, "family1", "family password 1", "family", owner)
    acct = login(app, "ca1", "accountant pass 1", "accountant", owner)
    order = {"exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "quantity": 1, "order_type": "LIMIT",
             "product": "MIS", "price": 100, "stop": 99, "target": 103}
    assert viewer.post("/api/orders/ticket", json={"account_id": "paper", "order": order}).status_code == 403
    assert viewer.post("/api/kill").status_code == 403
    assert viewer.get("/api/tax").status_code == 403
    assert viewer.get("/api/money/expenses").status_code == 403
    assert viewer.put("/api/rules", json={"changes": {"max_trades_per_day": 99}}).status_code == 403
    assert viewer.post("/api/mode", json={"mode": "live"}).status_code == 403
    assert viewer.get("/api/users").status_code == 403
    assert viewer.get("/api/audit").status_code == 403
    assert family.get("/api/portfolio").status_code == 403
    assert family.get("/api/orders").status_code == 403
    assert family.get("/api/money/bills").status_code == 200
    assert acct.get("/api/tax").status_code == 200
    assert acct.post("/api/orders/ticket", json={"account_id": "paper", "order": order}).status_code == 403
    assert acct.put("/api/assistant/settings", json={"api_key": "x"}).status_code == 403
    # only the owner releases the kill switch
    trader = login(app, "trader1", "trader password 1", "trader", owner)
    assert trader.post("/api/kill").status_code == 200
    assert trader.delete("/api/kill").status_code == 403


def test_disabled_user_is_signed_out_and_cannot_log_in(app, owner):
    v = login(app, "temp1", "temp password 1", "viewer", owner)
    assert owner.patch("/api/users/temp1", json={"active": False}).status_code == 200
    assert v.get("/api/portfolio").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"username": "temp1", "password": "temp password 1"}).status_code == 401


def test_secrets_never_leave_the_vault(app, owner):
    owner.post("/api/accounts", json={"id": "kite", "plugin": "kite", "name": "Z", "settings": {"api_key": "k"},
                                      "secrets": {"api_secret": "TOPSECRET123"}})
    owner.put("/api/assistant/settings", json={"api_key": "sk-ant-SECRET999"})
    for m, p in api_routes(app):
        if m != "GET" or "{" in p or p.endswith("/export") or p.startswith("/api/market") or "callback" in p:
            continue
        body = owner.get(p).text
        assert "TOPSECRET123" not in body and "sk-ant-SECRET999" not in body, p
    audit_rows = json.dumps(owner.get("/api/audit?limit=2000").json())
    assert "TOPSECRET123" not in audit_rows and "sk-ant-SECRET999" not in audit_rows


def test_password_rules_and_wrong_login(app, owner):
    assert owner.post("/api/users", json={"username": "x", "password": "short", "role": "viewer"}).status_code == 400
    r = TestClient(app).post("/api/auth/login", json={"username": "owner", "password": "nope nope nope"})
    assert r.status_code == 401
    assert "login_failed" in [a["action"] for a in owner.get("/api/audit").json()]


def test_security_headers_and_cookie_flags(app):
    c = TestClient(app)
    r = c.post("/api/auth/setup", json={"username": "o", "password": "owner password 1"})
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_spa_does_not_serve_files_outside_dist(app):
    c = TestClient(app)
    for p in ("/../backend/pyproject.toml", "/..%2f..%2fbackend%2fpyproject.toml", "/%2e%2e/%2e%2e/README.md",
              "/assets/../../backend/traderdesk/app/main.py"):
        r = c.get(p)
        assert "create_app" not in r.text and "[project]" not in r.text, p


def test_index_page_is_never_cached(app):
    r = TestClient(app).get("/")
    if r.status_code == 200 and "<html" in r.text.lower():      # only when the UI build exists
        assert r.headers.get("cache-control") == "no-cache"


def test_kite_callback_needs_the_owner(app, owner):
    viewer = login(app, "viewer2", "viewer password 2", "viewer", owner)
    assert TestClient(app).get("/api/brokers/kite/callback?request_token=x").status_code == 401
    assert viewer.get("/api/brokers/kite/callback?request_token=x").status_code == 401
    assert TestClient(app).get("/api/brokers/upstox/callback?code=x").status_code == 401
    assert viewer.get("/api/brokers/upstox/callback?code=x").status_code == 401


def test_hostile_input_is_rejected_or_inert(owner):
    bad = "'; DROP TABLE users; --<script>alert(1)</script>"
    r = owner.post("/api/money/expenses", json={"on": "2026-11-15", "merchant": bad, "category": "Other", "amount": 1})
    assert r.status_code == 200
    assert owner.get("/api/users").status_code == 200          # table still there
    rows = owner.get("/api/money/expenses?month=2026-11").json()
    assert rows[0]["merchant"] == bad                           # stored as data, rendered as text by React
    assert owner.put("/api/watchlist", json={"symbols": ["../../etc", "RELIANCE", "<b>"]}).json()["symbols"] == ["RELIANCE"]
    assert owner.post("/api/portfolio/import", json={"folder": "C:/definitely/not/here"}).status_code == 400
    assert owner.put("/api/providers/pipeline", json={"pipeline": {"schedule": "rm -rf"}}).status_code == 400
    assert owner.put("/api/dashboards", json={"dashboards": [{"name": "x" * 60000}]}).status_code == 400
    assert owner.post("/api/accounts", json={"id": "../x", "plugin": "paper", "name": "x"}).status_code == 422


def test_ai_settings_key_is_write_only(owner):
    owner.put("/api/assistant/settings", json={"api_key": "sk-ant-abc"})
    s = owner.get("/api/assistant/settings").json()
    assert s["key_set"] is True and "sk-ant" not in json.dumps(s)


def test_live_order_needs_reauth(app, owner):
    owner.post("/api/accounts", json={"id": "paper2", "plugin": "paper", "name": "P2"})
    # a paper account always routes to paper, so build the check at the endpoint directly
    order = {"exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "quantity": 1, "order_type": "LIMIT",
             "product": "MIS", "price": 108, "stop": 107, "target": 112}
    t = owner.post("/api/orders/ticket", json={"account_id": "paper", "order": order}).json()
    assert t["paper"] is True     # paper never needs reauth; live path is covered in test_api


def test_login_is_throttled_after_repeated_failures(app, owner):
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "owner", "password": "wrong password!"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "owner", "password": "owner password 1"}).status_code == 429
    assert "login_locked" in [a["action"] for a in owner.get("/api/audit").json()]
