"""Read-only email source over IMAP: works with Gmail, Outlook, Yahoo, Zoho, iCloud, Rediffmail or any
IMAP server.

What it reads:
- bank, card and UPI alert emails -> expense / income candidates for Money (you review, then import);
- CAS PDFs from CAMS / KFintech and holdings CSVs -> imported through the CAS / CSV connections;
- contract-note PDFs are listed (parsing them is not built yet).

Read-only by construction: the folder is opened with EXAMINE (``select(readonly=True)``) and bodies are
fetched with ``BODY.PEEK[]``, so nothing is marked read, moved, flagged, deleted or sent. The app password
lives in the OS keychain under "<account>:password".
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import html
import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Cap
from ...core.secrets import secret_key

# provider -> (IMAP host, how to get an app password)
PROVIDERS: dict[str, tuple[str, str]] = {
    "Gmail": ("imap.gmail.com", "Google Account -> Security -> 2-Step Verification on -> App passwords."),
    "Outlook / Hotmail / Microsoft 365": ("outlook.office365.com", "account.microsoft.com -> Security -> Advanced security "
                                          "-> App passwords. Work accounts need IMAP enabled by the admin."),
    "Yahoo": ("imap.mail.yahoo.com", "Yahoo Account security -> Generate app password."),
    "Zoho (India)": ("imap.zoho.in", "Zoho Mail settings -> IMAP access on; accounts.zoho.in -> Security -> App passwords."),
    "Zoho": ("imap.zoho.com", "Zoho Mail settings -> IMAP access on; accounts.zoho.com -> Security -> App passwords."),
    "iCloud": ("imap.mail.me.com", "appleid.apple.com -> Sign-In and Security -> App-specific passwords."),
    "Rediffmail": ("imap.rediffmail.com", "Rediffmail settings -> enable POP/IMAP access; use your mail password."),
    "AOL": ("imap.aol.com", "AOL Account security -> Generate app password."),
}

MAX_MESSAGE = 15 * 1024 * 1024

AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
DEBIT = re.compile(r"\b(debited|spent|paid|withdrawn|purchase|charged|sent|deducted)\b", re.I)
CREDIT = re.compile(r"\b(credited|received|refund(?:ed)?|deposited)\b", re.I)
BANKISH = re.compile(r"\b(a/c|acct|account|card|upi|vpa|imps|neft|rtgs|wallet)\b", re.I)
NOT_TXN = re.compile(r"\b(otp|one[- ]time password|statement is ready|offer|cashback upto|eligible for|pre-approved)\b", re.I)
ACCT_HINT = re.compile(r"(?:card|a/c|acct|account)\s*(?:no\.?|number)?\s*(?:ending(?: with| in)?)?\s*[xX*]*\s*(\d{4})\b", re.I)
MERCHANT = [
    re.compile(r"\bat\s+([A-Za-z0-9][\w&.\-'* ]{1,40}?)(?=\s+on\b|\s+via\b|\s+ref|\s+using|\s+txn|[.,;]|$)", re.I),
    re.compile(r"\b(?:to|towards)\s+(?:vpa\s+)?([A-Za-z0-9][\w.\-@& ]{1,40}?)(?=\s+on\b|\s+ref|\s+upi|\s+via|[,;(]|\.\s|$)", re.I),
    re.compile(r"\binfo[:\s]+([\w.\-@*/ ]{3,40}?)(?=[.,;]|$)", re.I),
]
PAYER = re.compile(r"\b(?:by|from)\s+(?:(?:neft|imps|rtgs|upi|transfer)\s+)?(?:from\s+)?([A-Za-z0-9][\w.\-@& ]{1,40}?)"
                   r"(?=\s+on\b|\s+ref|\s+via|[,;(]|\.\s|\.$|$)", re.I)
CATEGORY_WORDS = [
    ("Food delivery", "swiggy zomato eatsure"),
    ("Food and groceries", "bigbasket blinkit zepto dmart grofers instamart jiomart grocery supermarket restaurant cafe"),
    ("Shopping", "amazon flipkart myntra ajio meesho nykaa tatacliq croma reliance"),
    ("Fuel and travel", "uber ola rapido irctc petrol fuel hpcl iocl bpcl indianoil shell makemytrip goibibo indigo airindia redbus fastag metro"),
    ("Subscriptions", "netflix spotify hotstar prime youtube apple.com google play icloud jiocinema sonyliv"),
    ("Utilities", "electricity bescom tneb msedcl airtel jio vodafone vi broadband water gas bharatgas indane recharge"),
    ("Investing", "zerodha groww kite upstox dhan coin mutual sip nps ppf indmoney"),
    ("Health", "pharmacy apollo medplus 1mg pharmeasy hospital clinic diagnostic"),
    ("Education", "school college university udemy coursera byju fees"),
    ("Rent and home", "rent nobroker society maintenance urbancompany"),
]
CAS_SENDERS = ("camsonline", "kfintech", "karvy", "cdslindia", "nsdl", "mfcentral")


def _text(msg) -> str:
    """Plain text of a message: text/plain parts, else HTML with tags stripped."""
    plain, htm = [], []
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            body = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if ct == "text/plain" else htm).append(body)
    if plain:
        s = "\n".join(plain)
    else:
        s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(htm), flags=re.S | re.I)
        s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()[:20000]


def _amount(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def guess_category(merchant: str, credit: bool) -> str:
    if credit:
        return "Income"
    m = merchant.lower()
    for cat, words in CATEGORY_WORDS:
        if any(w in m for w in words.split()):
            return cat
    return "Other"


def parse_alert(subject: str, body: str, sender: str = "") -> dict | None:
    """A bank / card / UPI transaction alert -> {direction, amount, merchant, account_hint, category}, else None."""
    text = f"{subject}. {body}"[:4000]
    if NOT_TXN.search(text) or not BANKISH.search(text):
        return None
    d, c = DEBIT.search(text), CREDIT.search(text)
    if not d and not c:
        return None
    kw = d if (d and (not c or d.start() <= c.start())) else c
    credit = kw is c
    best, dist = None, None
    for m in AMOUNT.finditer(text):
        before = text[max(0, m.start() - 30):m.start()].lower()
        if "bal" in before or "limit" in before or "avl" in before:
            continue
        v = _amount(m.group(1))
        if not v or v <= 0:
            continue
        gap = abs(m.start() - kw.start())
        if dist is None or gap < dist:
            best, dist = v, gap
    if best is None or dist > 200:
        return None
    merchant = None
    tail = text[kw.start():]
    for rx in ([PAYER] if credit else []) + MERCHANT:
        mm = rx.search(tail) or rx.search(text)
        if mm:
            cand = mm.group(1).strip(" .-*")
            if cand and not re.fullmatch(r"(your|the|a/c|account|card|you)\b.*", cand, re.I) and not cand.lower().startswith("xx"):
                merchant = cand
                break
    if not merchant:
        name, addr = parseaddr(sender)
        merchant = name or addr.split("@")[-1] or "Unknown"
    hint = ACCT_HINT.search(text)
    return {"direction": "credit" if credit else "debit", "amount": round(best, 2), "merchant": merchant[:128],
            "account_hint": f"••{hint.group(1)}" if hint else None, "category": guess_category(merchant, credit)}


def classify_attachment(filename: str, subject: str, sender: str) -> str | None:
    f, s, frm = filename.lower(), subject.lower(), sender.lower()
    if f.endswith(".csv"):
        return "csv"
    if f.endswith(".pdf"):
        if "contract note" in s or "contract_note" in f or "contractnote" in f or "contract note" in f:
            return "contract_note"
        if "consolidated account statement" in s or "cas" in re.split(r"[^a-z]+", f) or any(x in frm for x in CAS_SENDERS):
            return "cas"
    return None


def message_key(account_id: str, msg_id: str | None, uid: str) -> str:
    raw = f"{account_id}|{msg_id or 'uid:' + uid}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class EmailInbox:
    name = "email"
    capabilities = {Cap.READ}

    def __init__(self, account_id: str, settings: dict, secrets=None, manifest=None):
        self.account_id, self.settings, self.secrets = account_id, settings or {}, secrets
        prov = self.settings.get("provider") or "Gmail"
        self.provider = prov
        self.host = (self.settings.get("host") or "").strip() or PROVIDERS.get(prov, ("", ""))[0]
        self.port = int(self.settings.get("port") or 993)
        self.folder = self.settings.get("folder") or "INBOX"
        self.imap_factory = None    # tests inject a fake IMAP4_SSL

    # ------------------------------------------------------------ session
    def _password(self) -> str | None:
        pw = self.secrets.get(secret_key(self.account_id, "password")) if self.secrets else None
        if pw and self.provider == "Gmail":
            pw = pw.replace(" ", "")      # Google shows app passwords in groups of four
        return pw

    def session_status(self, now=None) -> dict:
        ok = bool(self._password())
        return {"connected": ok, "needs_login": not ok, "issued_at": None}

    def _open(self):
        if not self.host:
            raise BrokerError(ErrorKind.INVALID, "set the IMAP host (provider 'Other')", self.name)
        pw = self._password()
        if not pw:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, "no app password saved; use Replace password", self.name)
        try:
            if self.imap_factory:
                m = self.imap_factory(self.host, self.port)
            else:
                m = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ssl.create_default_context(), timeout=30)
        except (OSError, imaplib.IMAP4.error) as e:
            raise BrokerError(ErrorKind.NETWORK, f"cannot reach {self.host}:{self.port} ({type(e).__name__})", self.name)
        try:
            m.login(self.settings.get("username", ""), pw)
        except imaplib.IMAP4.error:
            help_ = PROVIDERS.get(self.provider, ("", "Check the IMAP settings with your provider."))[1]
            raise BrokerError(ErrorKind.AUTH_EXPIRED, f"{self.provider} refused the login. Use an app password. {help_}", self.name)
        typ, data = m.select(f'"{self.folder}"', readonly=True)      # EXAMINE: read-only
        if typ != "OK":
            m.logout()
            raise BrokerError(ErrorKind.INVALID, f"no folder {self.folder!r}", self.name)
        return m, int((data or [b"0"])[0] or 0)

    def test(self) -> dict:
        m, count = self._open()
        m.logout()
        return {"ok": True, "messages": count, "host": self.host}

    # ------------------------------------------------------------ reading
    def _senders(self) -> list[str]:
        return [s.strip().lower() for s in (self.settings.get("senders") or "").split(",") if s.strip()]

    def scan(self, days: int | None = None, limit: int = 300, now: datetime | None = None) -> dict:
        days = int(days or self.settings.get("lookback_days") or 30)
        since = ((now or datetime.now(timezone.utc)) - timedelta(days=max(1, min(days, 400)))).strftime("%d-%b-%Y")
        m, _ = self._open()
        try:
            typ, data = m.uid("SEARCH", None, "SINCE", since)
            uids = (data[0] or b"").split() if typ == "OK" and data else []
            uids = uids[-limit:]
            want = self._senders()
            alerts, files = [], []
            for uid in reversed(uids):           # newest first
                u = uid.decode()
                typ, d = m.uid("FETCH", u, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM)])")
                if typ != "OK" or not d or not isinstance(d[0], tuple):
                    continue
                size = re.search(rb"RFC822\.SIZE (\d+)", d[0][0])
                frm = d[0][1].decode("utf-8", errors="replace").lower()
                if want and not any(w in frm for w in want):
                    continue
                if size and int(size.group(1)) > MAX_MESSAGE:
                    continue
                typ, d = m.uid("FETCH", u, "(BODY.PEEK[])")
                if typ != "OK" or not d or not isinstance(d[0], tuple):
                    continue
                msg = email.message_from_bytes(d[0][1], policy=email.policy.default)
                subject, sender = str(msg.get("subject", "")), str(msg.get("from", ""))
                try:
                    on = parsedate_to_datetime(msg.get("date")).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    on = None
                key = message_key(self.account_id, msg.get("message-id"), u)
                base = {"uid": u, "key": key, "on": on.isoformat() if on else None, "subject": subject[:200],
                        "from": parseaddr(sender)[1] or sender[:120]}
                for i, part in enumerate(msg.walk()):
                    fn = part.get_filename()
                    if fn:
                        kind = classify_attachment(fn, subject, sender)
                        if kind:
                            files.append({**base, "key": f"{key}:{i}", "part": i, "filename": fn[:120], "kind": kind})
                a = parse_alert(subject, _text(msg), sender)
                if a:
                    alerts.append({**base, **a})
            return {"scanned": len(uids), "since": since, "alerts": alerts, "attachments": files}
        finally:
            try:
                m.logout()
            except Exception:
                pass

    def attachment(self, uid: str, part: int) -> tuple[str, bytes]:
        m, _ = self._open()
        try:
            typ, d = m.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                raise BrokerError(ErrorKind.INVALID, "message not found (it may have moved)", self.name)
            msg = email.message_from_bytes(d[0][1], policy=email.policy.default)
            for i, p in enumerate(msg.walk()):
                if i == part and p.get_filename():
                    return p.get_filename(), p.get_payload(decode=True) or b""
            raise BrokerError(ErrorKind.INVALID, "attachment not found", self.name)
        finally:
            try:
                m.logout()
            except Exception:
                pass

    # ------------------------------------------------------------ broker surface: none
    def holdings(self):
        raise BrokerError(ErrorKind.UNSUPPORTED, "an email source has no holdings of its own", self.name)

    def positions(self):
        return []

    def orders(self, day=None):
        return []

    def place_order(self, o):
        raise BrokerError(ErrorKind.UNSUPPORTED, "read-only email source", self.name)

    modify_order = cancel_order = place_gtt = margins = place_order
