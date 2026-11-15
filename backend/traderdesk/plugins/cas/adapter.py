"""Mutual funds from a CAMS / KFintech Consolidated Account Statement (PDF), revalued with AMFI NAVs.

Parsing uses `casparser` (pip install casparser). The PDF password is used once to read the file and
is never stored. NAVs come from AMFI's public NAVAll.txt (8 semicolon-separated columns).
"""
from __future__ import annotations

from datetime import datetime

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Cap

AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"


def parse_amfi(text: str) -> dict[str, tuple[float, str]]:
    """ISIN -> (NAV, date). Skips headings, AMC lines and blanks; keys both ISIN columns."""
    out: dict[str, tuple[float, str]] = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) != 8 or not parts[0].strip().isdigit():
            continue
        try:
            nav = float(parts[6])
        except ValueError:
            continue
        if nav <= 0:
            continue
        for isin in (parts[1].strip(), parts[2].strip()):
            if isin and isin != "-":
                out[isin] = (nav, parts[7].strip())
    return out


def fetch_amfi(http=None) -> dict[str, tuple[float, str]]:
    import httpx
    r = (http or httpx).get(AMFI_URL, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return parse_amfi(r.text)


def _get(o, k, default=None):
    if isinstance(o, dict):
        return o.get(k, default)
    return getattr(o, k, default)


def holdings_from_cas(cas) -> list[dict]:
    if hasattr(cas, "model_dump"):
        cas = cas.model_dump()
    out = []
    for folio in _get(cas, "folios", []) or []:
        for s in _get(folio, "schemes", []) or []:
            units = float(_get(s, "close", 0) or 0)
            if units <= 0:
                continue
            val = _get(s, "valuation", {}) or {}
            nav = float(_get(val, "nav", 0) or 0)
            cost = _get(val, "cost")
            avg = float(cost) / units if cost else nav
            out.append({"exchange": "MF", "tradingsymbol": (_get(s, "isin") or _get(s, "amfi") or _get(s, "scheme", ""))[:64],
                        "isin": _get(s, "isin"), "name": (_get(s, "scheme") or "")[:255], "asset_class": "mutual_fund",
                        "quantity": units, "average_price": avg, "last_price": nav, "currency": "INR",
                        "folio": _get(folio, "folio"), "amc": _get(folio, "amc")})
    return out


class CasImport:
    name = "cas"
    capabilities = {Cap.READ, Cap.MF}
    upload_kind = "pdf"

    def __init__(self, account_id: str, settings: dict, secrets=None, manifest=None):
        self.account_id, self.settings = account_id, settings or {}

    def session_status(self, now=None) -> dict:
        return {"connected": True, "needs_login": False, "issued_at": None}

    def parse_upload(self, data: bytes, password: str | None = None) -> list[dict]:
        try:
            import casparser
        except ImportError:
            raise BrokerError(ErrorKind.UNSUPPORTED, "install casparser: pip install casparser", self.name)
        import io
        try:
            cas = casparser.read_cas_pdf(io.BytesIO(data), password or "")
        except Exception as e:
            raise BrokerError(ErrorKind.INVALID, f"could not read the CAS PDF (check the password): {type(e).__name__}", self.name)
        rows = holdings_from_cas(cas)
        if not rows:
            raise BrokerError(ErrorKind.INVALID, "no mutual fund holdings found in the CAS", self.name)
        return rows

    def holdings(self):
        raise BrokerError(ErrorKind.UNSUPPORTED, "from the last CAS upload", self.name)

    def positions(self):
        return []

    def orders(self, day=None):
        return []

    def place_order(self, o):
        raise BrokerError(ErrorKind.UNSUPPORTED, "import-only connection", self.name)

    modify_order = cancel_order = place_gtt = margins = place_order
