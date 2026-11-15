"""Email source (IMAP, any provider): read-only access, alert parsing, review-then-import, attachments, no doubles."""
import imaplib
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from traderdesk.app.main import create_app
from traderdesk.brokers.registry import PluginRegistry
from traderdesk.core.errors import BrokerError
from traderdesk.core.models import Cap
from traderdesk.core.secrets import MemoryStore
from traderdesk.market.yahoo import MarketData
from traderdesk.plugins.email_imap.adapter import EmailInbox, classify_attachment, parse_alert
from traderdesk.plugins.paper.adapter import PaperBroker

from conftest import MARKET_HOURS
from test_features import fake_fetch


def mail(subject, body, sender="HDFC Bank <alerts@hdfcbank.net>", date="Mon, 16 Nov 2026 09:12:00 +0530",
         attach=None, msg_id=None):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = sender, subject, date
    m["Message-ID"] = msg_id or f"<{abs(hash(subject + body))}@test>"
    m.set_content(body)
    if attach:
        name, data, mime = attach
        main, sub = mime.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes()


class FakeImap:
    """Answers the few IMAP commands the adapter uses and fails the test on anything that writes."""
    WRITES = {"STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "DELETE"}

    def __init__(self, messages, password="right"):
        self.messages, self.password, self.calls, self.readonly = messages, password, [], None

    def __call__(self, host, port):
        self.host = host
        return self

    def login(self, user, pw):
        if pw != self.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"ok"]

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, cmd, *args):
        self.calls.append((cmd, args))
        assert cmd.upper() not in self.WRITES
        if cmd == "SEARCH":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]
        u, spec = args
        raw = self.messages[int(u) - 1]
        assert "PEEK" in spec, "bodies must be fetched with BODY.PEEK so nothing is marked read"
        if "HEADER.FIELDS" in spec:
            head = raw.split(b"\n\n", 1)[0]
            frm = [ln for ln in head.split(b"\n") if ln.lower().startswith(b"from:")][0] + b"\r\n"
            return "OK", [(f"{u} (UID {u} RFC822.SIZE {len(raw)} BODY[HEADER.FIELDS (FROM)] {{{len(frm)}}}".encode(), frm), b")"]
        return "OK", [(f"{u} (UID {u} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        return "BYE", [b""]


DEBIT = mail("Alert : Update on your HDFC Bank Credit Card",
             "Dear Customer, Rs.1,249.00 has been debited from your HDFC Bank Credit Card ending 4321 towards "
             "SWIGGY on 16-11-2026. Available limit Rs.98,000.")
UPI = mail("You have done a UPI txn", "Rs 450.00 debited from A/c XX7788 on 15-11-26 to VPA zepto.payu@hdfcbank "
           "(UPI Ref No 431234). Avl Bal Rs 12,000.50", sender="alerts@axisbank.com")
CREDIT = mail("Credit alert", "Your A/c XX7788 is credited with INR 85,000.00 on 01-11-2026 by NEFT from ACME PVT LTD.",
              sender="alerts@icicibank.com")
OTP = mail("OTP for transaction", "Your OTP for the card transaction of Rs 2,000 at AMAZON is 123456.")
PROMO = mail("Festive offer", "Get cashback upto Rs 500 when you pay with your card. Offer valid till Sunday.")
CAS = mail("Consolidated Account Statement - Oct 2026", "Please find attached.", sender="donotreply@camsonline.com",
           attach=("CAS_Oct2026.pdf", b"%PDF-1.4 fake", "application/pdf"))
CSV = mail("Your holdings export", "Attached.", sender="reports@indmoney.com",
           attach=("holdings.csv", b"Stock Name,Shares,Avg Buy Price,Current Price\nMSFT,2,400,431.05\n", "text/csv"))


def test_alert_parser_reads_common_bank_formats():
    a = parse_alert("Card alert", "Rs.1,249.00 has been debited from your HDFC Bank Credit Card ending 4321 towards SWIGGY on 16-11-2026.")
    assert a["direction"] == "debit" and a["amount"] == 1249.0 and a["merchant"] == "SWIGGY"
    assert a["category"] == "Food delivery" and a["account_hint"] == "••4321"
    u = parse_alert("UPI", "Rs 450.00 debited from A/c XX7788 on 15-11-26 to VPA zepto.payu@hdfcbank (UPI Ref No 431234). Avl Bal Rs 12,000.50")
    assert u["amount"] == 450.0 and "zepto" in u["merchant"] and u["category"] == "Food and groceries"
    c = parse_alert("Credit", "Your A/c XX7788 is credited with INR 85,000.00 on 01-11-2026 by NEFT from ACME PVT LTD.")
    assert c["direction"] == "credit" and c["amount"] == 85000.0 and c["category"] == "Income"
    assert c["merchant"] == "ACME PVT LTD"
    assert parse_alert("Spent", "You spent ₹ 299 at NETFLIX.COM using your card ending 1111.")["category"] == "Subscriptions"


def test_alert_parser_skips_otp_promos_and_non_transactions():
    assert parse_alert("OTP", "Your OTP for the card transaction of Rs 2,000 at AMAZON is 123456.") is None
    assert parse_alert("Offer", "Get cashback upto Rs 500 when you pay with your card.") is None
    assert parse_alert("Hello", "Lunch on Friday? It cost Rs 300 last time.") is None


def test_attachment_kinds():
    assert classify_attachment("CAS_Oct2026.pdf", "Consolidated Account Statement", "x@camsonline.com") == "cas"
    assert classify_attachment("holdings.csv", "", "") == "csv"
    assert classify_attachment("CN_123.pdf", "Contract Note for 16-11-2026", "noreply@zerodha.net") == "contract_note"
    assert classify_attachment("invoice.pdf", "Your invoice", "shop@example.com") is None


def test_inbox_is_opened_read_only_and_any_provider_works():
    assert PluginRegistry().discover().get("email").capabilities == {Cap.READ}
    fake = FakeImap([DEBIT, OTP, CAS])
    for prov, host in [("Gmail", "imap.gmail.com"), ("Outlook / Hotmail / Microsoft 365", "outlook.office365.com"),
                       ("Yahoo", "imap.mail.yahoo.com"), ("Zoho (India)", "imap.zoho.in")]:
        box = EmailInbox("m", {"provider": prov, "username": "me@example.com"}, MemoryStore({"m:password": "right"}))
        box.imap_factory = fake
        r = box.scan(days=30)
        assert fake.host == host and fake.readonly is True
        assert len(r["alerts"]) == 1 and r["attachments"][0]["kind"] == "cas"
    other = EmailInbox("m", {"provider": "Other (enter IMAP host)", "host": "mail.example.in", "username": "u"},
                       MemoryStore({"m:password": "right"}))
    other.imap_factory = fake
    assert other.test()["messages"] == 3 and fake.host == "mail.example.in"


def test_gmail_app_password_spaces_and_bad_password():
    fake = FakeImap([], password="abcdefghijklmnop")
    box = EmailInbox("m", {"provider": "Gmail", "username": "me@gmail.com"}, MemoryStore({"m:password": "abcd efgh ijkl mnop"}))
    box.imap_factory = fake
    assert box.test()["ok"]
    bad = EmailInbox("m", {"provider": "Gmail", "username": "me@gmail.com"}, MemoryStore({"m:password": "wrong"}))
    bad.imap_factory = fake
    with pytest.raises(BrokerError) as e:
        bad.test()
    assert e.value.kind.value == "auth_expired" and "App passwords" in e.value.message
    assert not EmailInbox("m", {"username": "x"}, MemoryStore()).session_status()["connected"]


def test_sender_filter():
    box = EmailInbox("m", {"username": "u", "senders": "axisbank.com"}, MemoryStore({"m:password": "right"}))
    box.imap_factory = FakeImap([DEBIT, UPI, CREDIT])
    r = box.scan()
    assert [a["amount"] for a in r["alerts"]] == [450.0]


@pytest.fixture
def client(tmp_path):
    app = create_app(db_url=f"sqlite:///{(tmp_path / 'e.db').as_posix()}", secrets=MemoryStore(), clock=lambda: MARKET_HOURS,
                     adapters_override={"paper": PaperBroker("paper", {})}, serve_frontend=False, market=MarketData(fetch=fake_fetch))
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"username": "owner", "password": "owner password 1"})
        c.headers["x-csrf"] = r.json()["csrf"]
        yield c


def test_scan_review_import_and_no_doubles(client):
    r = client.post("/api/accounts", json={"id": "mail", "plugin": "email", "name": "My Gmail",
                                           "settings": {"provider": "Gmail", "username": "me@gmail.com"},
                                           "secrets": {"password": "right"}})
    assert r.status_code == 200
    assert client.post("/api/accounts", json={"id": "m2", "plugin": "email", "name": "x", "settings":
                                              {"username": "u", "password": "p"}}).status_code == 400   # secret as setting
    fake = FakeImap([DEBIT, UPI, CREDIT, OTP, PROMO, CAS, CSV])
    client.app.state.adapters["mail"].imap_factory = fake
    assert client.post("/api/accounts/mail/email/test").json()["messages"] == 7
    # importing before a scan does nothing
    assert client.post("/api/accounts/mail/email/import", json={"keys": ["x"]}).status_code == 400
    s = client.post("/api/accounts/mail/email/scan", json={"days": 60}).json()
    assert s["scanned"] == 7 and sorted(a["amount"] for a in s["alerts"]) == [450.0, 1249.0, 85000.0]
    assert {f["kind"] for f in s["attachments"]} == {"cas", "csv"}
    assert fake.readonly is True
    debit = [a for a in s["alerts"] if a["amount"] == 1249.0][0]
    credit = [a for a in s["alerts"] if a["amount"] == 85000.0][0]
    r = client.post("/api/accounts/mail/email/import", json={"keys": [debit["key"], credit["key"]],
                                                             "categories": {debit["key"]: "Shopping"}}).json()
    assert r == {"added": 2, "skipped": 0}
    rows = {e["amount"]: e for e in client.get("/api/money/expenses?month=2026-11").json()}
    assert rows[-1249.0]["category"] == "Shopping" and rows[-1249.0]["source"] == "email" and rows[-1249.0]["account"] == "••4321"
    assert rows[85000.0]["category"] == "Income"
    # a rescan marks them imported, and importing again adds nothing
    s2 = client.post("/api/accounts/mail/email/scan", json={}).json()
    assert {a["amount"]: a["imported"] for a in s2["alerts"]} == {1249.0: True, 450.0: False, 85000.0: True}
    assert client.post("/api/accounts/mail/email/import", json={"keys": [debit["key"]]}).json() == {"added": 0, "skipped": 1}
    src = client.get("/api/money/summary?month=2026-11").json()["sources"]
    assert any("My Gmail" in x["name"] and "2 from email" in x["status"] for x in src)
    log = client.get("/api/audit").json()
    acts = {x["action"] for x in (log["rows"] if isinstance(log, dict) else log)}
    assert {"email_scan", "email_import", "email_test"} <= acts


def test_attachment_goes_to_a_matching_connection(client):
    client.post("/api/accounts", json={"id": "mail", "plugin": "email", "name": "Mail",
                                       "settings": {"provider": "Yahoo", "username": "me@yahoo.com"}, "secrets": {"password": "right"}})
    client.post("/api/accounts", json={"id": "us", "plugin": "csv", "name": "INDmoney US", "settings": {"currency": "USD"}})
    client.app.state.adapters["mail"].imap_factory = FakeImap([CSV, CAS])
    s = client.post("/api/accounts/mail/email/scan", json={}).json()
    csv = [f for f in s["attachments"] if f["kind"] == "csv"][0]
    cas = [f for f in s["attachments"] if f["kind"] == "cas"][0]
    assert client.post("/api/accounts/mail/email/attachment", json={"key": cas["key"], "target": "us"}).status_code == 400
    r = client.post("/api/accounts/mail/email/attachment", json={"key": csv["key"], "target": "us"})
    assert r.status_code == 200 and r.json()["rows"] == 1
    assert client.post("/api/accounts/mail/email/attachment", json={"key": csv["key"], "target": "us"}).status_code == 409
    p = client.get("/api/portfolio").json()
    assert any(h["symbol"] == "MSFT" for h in p["holdings"])


def test_email_routes_need_the_owner(client):
    client.post("/api/accounts", json={"id": "mail", "plugin": "email", "name": "Mail",
                                       "settings": {"username": "u"}, "secrets": {"password": "right"}})
    client.post("/api/users", json={"username": "viewer1", "password": "viewer password 1", "role": "viewer"})
    v = TestClient(client.app)
    st = v.post("/api/auth/login", json={"username": "viewer1", "password": "viewer password 1"}).json()
    v.headers["x-csrf"] = st["csrf"]
    assert v.post("/api/accounts/mail/email/scan", json={}).status_code == 403
    assert v.post("/api/accounts/paper/email/scan", json={}).status_code == 403
    assert client.post("/api/accounts/paper/email/scan", json={}).status_code == 400
