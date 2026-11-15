import pytest
from fastapi.testclient import TestClient

from traderdesk.app.main import create_app
from traderdesk.core.secrets import MemoryStore
from traderdesk.plugins.paper.adapter import PaperBroker

from conftest import MARKET_HOURS


@pytest.fixture
def client(tmp_path):
    paper = PaperBroker("paper", {"starting_cash": 1_000_000})
    paper.set_price("NSE:RELIANCE", 2950.0)
    app = create_app(db_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}", secrets=MemoryStore(),
                     clock=lambda: MARKET_HOURS, adapters_override={"paper": paper}, serve_frontend=False)
    app.state.desk.session.covered_years = {2026}
    with TestClient(app) as c:
        c.paper = paper
        yield c


def owner(c):
    r = c.post("/api/auth/setup", json={"username": "owner", "password": "correct horse 1"})
    assert r.status_code == 200, r.text
    c.headers["x-csrf"] = r.json()["csrf"]
    return r.json()


def test_setup_login_and_permissions(client):
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/auth/state").json()["setup_needed"]
    owner(client)
    assert client.post("/api/auth/setup", json={"username": "x", "password": "0123456789"}).status_code == 409
    assert client.post("/api/users", json={"username": "v", "password": "viewer pass 1", "role": "viewer"}).status_code == 200
    v = TestClient(client.app)
    r = v.post("/api/auth/login", json={"username": "v", "password": "wrong password"})
    assert r.status_code == 401
    r = v.post("/api/auth/login", json={"username": "v", "password": "viewer pass 1"})
    v.headers["x-csrf"] = r.json()["csrf"]
    assert v.get("/api/portfolio").status_code == 200
    assert v.post("/api/orders/ticket", json={"account_id": "paper", "order": {}}).status_code == 403
    assert v.get("/api/audit").status_code == 403


def test_csrf_and_origin_checks(client):
    owner(client)
    tok = client.headers.pop("x-csrf")
    assert client.post("/api/orders/sync").status_code == 403
    client.headers["x-csrf"] = tok
    assert client.post("/api/orders/sync", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/orders/sync").status_code == 200


def test_full_paper_order_flow(client):
    owner(client)
    order = {"exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "quantity": 10, "order_type": "LIMIT",
             "product": "MIS", "price": 2960, "stop": 2900, "target": 3050}
    t = client.post("/api/orders/ticket", json={"account_id": "paper", "order": order}).json()
    assert t["stage"] == "checked" and t["confirm_phrase"] == "BUY 10 RELIANCE"
    bad = client.post(f"/api/orders/{t['id']}/confirm", json={"fingerprint": t["fingerprint"], "typed": "ok"})
    assert bad.status_code == 409 and bad.json()["gate"] == "confirm"
    r = client.post(f"/api/orders/{t['id']}/confirm", json={"fingerprint": t["fingerprint"], "typed": "buy 10 reliance"})
    assert r.status_code == 200 and r.json()["status"] == "COMPLETE" and r.json()["oco_gtt_id"]
    bar = client.get("/api/topbar").json()
    assert bar["mode"] == "paper" and bar["trades_today"] == 1 and bar["risk_budget_left"] == 7500 - 600
    assert client.post("/api/kill").json()["kill_switch"]
    assert client.get("/api/topbar").json()["kill_switch"]
    assert client.get("/api/audit/verify").json()["ok"]
    assert any(a["action"] == "kill_switch" for a in client.get("/api/audit").json())


def test_rules_are_versioned(client):
    owner(client)
    assert client.get("/api/rules").json()["version"] == 1
    assert client.put("/api/rules", json={"changes": {"max_trades_per_day": 2}, "note": "tighter"}).json()["version"] == 2
    assert client.get("/api/rules").json()["rules"]["max_trades_per_day"] == 2


def test_account_secrets_go_to_vault(client):
    owner(client)
    r = client.post("/api/accounts", json={"id": "kite", "plugin": "kite", "name": "Zerodha",
                                           "settings": {"api_key": "k", "api_secret": "leak"}})
    assert r.status_code == 400
    r = client.post("/api/accounts", json={"id": "kite", "plugin": "kite", "name": "Zerodha",
                                           "settings": {"api_key": "k"}, "secrets": {"api_secret": "s"}})
    assert r.status_code == 200
    assert client.app.state.secrets.get("kite:api_secret") == "s"
    accts = {a["id"]: a for a in client.get("/api/accounts").json()}
    assert accts["kite"]["needs_login"] and "OCO" in accts["kite"]["capabilities"]
    assert "kite.zerodha.com" in client.get("/api/accounts/kite/login-url").json()["url"]


def test_replace_secret_and_bad_checksum_redirects_to_ui(client):
    from traderdesk.core.errors import BrokerError, ErrorKind
    owner(client)
    client.post("/api/accounts", json={"id": "kite", "plugin": "kite", "name": "Zerodha",
                                       "settings": {"api_key": " k "}, "secrets": {"api_secret": " old \n"}})
    vault = client.app.state.secrets
    assert vault.get("kite:api_secret") == "old"
    vault.set("kite:access_token", "stale")
    assert client.post("/api/accounts/kite/secrets", json={"secrets": {"api_key": "x"}}).status_code == 400
    assert client.post("/api/accounts/kite/secrets", json={"secrets": {"api_secret": " new "}}).status_code == 200
    assert vault.get("kite:api_secret") == "new" and vault.get("kite:access_token") is None

    adapter = client.app.state.adapters["kite"]
    def bad(_tok):
        raise BrokerError(ErrorKind.AUTH_EXPIRED, "Invalid `checksum`.", "kite")
    adapter.complete_login = bad
    r = client.get("/api/brokers/kite/callback?request_token=t&status=success", follow_redirects=False)
    assert r.status_code == 307 and "kite_error=" in r.headers["location"] and "secret" in r.headers["location"]
    assert any(a["action"] == "kite_login_failed" for a in client.get("/api/audit").json())


def test_portfolio_falls_back_to_imported_snapshot(client, tmp_path):
    import json
    owner(client)
    (tmp_path / "snaps").mkdir()
    (tmp_path / "snaps" / "portfolio_20261113_002003.json").write_text(json.dumps({
        "timestamp": "2026-11-13T00:20:01", "holdings": [{"tradingsymbol": "BSE", "exchange": "NSE", "quantity": 5,
                                                          "average_price": 3000, "last_price": 3900}],
        "mutual_funds": []}), encoding="utf-8")
    r = client.post("/api/portfolio/import", json={"folder": str(tmp_path / "snaps")}).json()
    assert r["imported"] == 1
    p = client.get("/api/portfolio").json()
    assert p["value"] == 19500 and p["sources"][0]["source"] == "snapshot"
    assert len(client.get("/api/portfolio/history").json()) == 1
