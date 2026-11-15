"""Upstox API v2, read-only, with the daily OAuth login. Fields follow upstox.com/developer/api-documentation."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Holding, Position
from ...core.secrets import secret_key
from .._rest import IST, RestBroker, product

BASE = "https://api.upstox.com/v2"


class UpstoxBroker(RestBroker):
    name = "upstox"
    base_url = BASE

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token() or ''}"}

    def redirect_uri(self) -> str:
        return self.settings.get("redirect_uri") or "http://127.0.0.1:8766/api/brokers/upstox/callback"

    def login_url(self) -> str:
        return f"{BASE}/login/authorization/dialog?" + urlencode({
            "client_id": self.settings.get("api_key", ""), "redirect_uri": self.redirect_uri(),
            "response_type": "code", "state": self.account_id})

    def complete_login(self, code: str) -> dict:
        import httpx
        secret = self.secrets.get(secret_key(self.account_id, "api_secret"))
        if not secret:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, "upstox: no API secret in the vault", self.name)
        r = (self._http or httpx).post(f"{BASE}/login/authorization/token", headers={"Accept": "application/json"},
                                       data={"code": code, "client_id": self.settings.get("api_key", ""),
                                             "client_secret": secret, "redirect_uri": self.redirect_uri(),
                                             "grant_type": "authorization_code"}, timeout=15)
        if r.status_code >= 400:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, f"upstox: login failed ({r.status_code}) {r.text[:200]}", self.name)
        d = r.json()
        self.save_token(d["access_token"])
        return {"user_id": d.get("user_id"), "user_name": d.get("user_name")}

    def session_status(self, now: datetime | None = None) -> dict:
        """Upstox tokens end at 03:30 IST the next day."""
        issued = self.secrets.get(secret_key(self.account_id, "token_issued_at")) if self.secrets else None
        now = now or datetime.now(IST)
        fresh = False
        if self.token() and issued:
            i = datetime.fromisoformat(issued)
            expiry = datetime.combine(i.date() + timedelta(days=1), time(3, 30), IST)
            fresh = now < expiry
        return {"connected": fresh, "issued_at": issued, "needs_login": not fresh}

    def holdings(self) -> list[Holding]:
        out = []
        for h in (self.get("/portfolio/long-term-holdings") or {}).get("data") or []:
            qty = float(h.get("quantity") or 0) + float(h.get("t1_quantity") or 0)
            if not qty:
                continue
            sym = h.get("tradingsymbol") or h.get("trading_symbol") or ""
            out.append(Holding(self.account_id, h.get("exchange") or "NSE", sym, qty, float(h.get("average_price") or 0),
                               float(h.get("last_price") or 0), isin=h.get("isin"), name=h.get("company_name"),
                               close_price=h.get("close_price")))
        return out

    def positions(self) -> list[Position]:
        out = []
        for p in (self.get("/portfolio/short-term-positions") or {}).get("data") or []:
            q = float(p.get("quantity") or 0)
            if not q:
                continue
            sym = p.get("tradingsymbol") or p.get("trading_symbol") or ""
            out.append(Position(self.account_id, p.get("exchange") or "NSE", sym, product(p.get("product")), q,
                                float(p.get("average_price") or 0), float(p.get("last_price") or 0), float(p.get("pnl") or 0)))
        return out

    def funds(self) -> dict:
        eq = ((self.get("/user/get-funds-and-margin") or {}).get("data") or {}).get("equity") or {}
        return {"cash": float(eq.get("available_margin") or 0), "used": float(eq.get("used_margin") or 0)}
