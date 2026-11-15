"""INDstocks (INDmoney) Flash API, read-only. Fields follow api-docs.indstocks.com."""
from __future__ import annotations

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Holding, Position
from .._rest import RestBroker, product

BASE = "https://api.indstocks.com"


class IndstocksBroker(RestBroker):
    name = "indstocks"
    base_url = BASE
    token_hours = 24

    def headers(self) -> dict:
        return {"Authorization": self.token() or ""}   # raw token, no "Bearer" prefix (per the docs)

    def login_with_totp(self, pin: str, totp: str) -> dict:
        """POST /generate/token with x-api-key, mpin and totp. One token per 60 s; the MPIN is never stored."""
        import httpx
        cid = self.settings.get("client_id")
        if not cid:
            raise BrokerError(ErrorKind.INVALID, "indstocks: set the API client ID first", self.name)
        r = (self._http or httpx).post(f"{BASE}/generate/token", headers={"x-api-key": cid},
                                       json={"mpin": pin, "totp": totp}, timeout=15)
        if r.status_code >= 400:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, f"indstocks: token request failed ({r.status_code})", self.name)
        body = r.json() or {}
        tok = body.get("token") or (body.get("data") or {}).get("token")
        if not tok:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, "indstocks: no token in the response", self.name)
        self.save_token(tok)
        return {"user_id": cid}

    def holdings(self) -> list[Holding]:
        out = []
        for h in (self.get("/portfolio/holdings") or {}).get("data") or []:
            qty = float(h.get("total_qty") or 0)
            if not qty:
                continue
            avg = float(h.get("avg_price") or 0)
            sym = h.get("symbol", "")
            out.append(Holding(self.account_id, "NSE", sym, qty, avg, self.last("NSE", sym, avg), isin=h.get("isin")))
        return out

    def positions(self) -> list[Position]:
        out = []
        for p in (self.get("/portfolio/positions") or {}).get("data") or []:
            q = float(p.get("net_qty") or 0)
            if not q:
                continue
            avg = float(p.get("avg_price") or 0)
            ex = (p.get("exchange") or "NSE").upper()
            if p.get("segment") == "derivative":
                ex = "NFO" if ex == "NSE" else "BFO"
            sym = p.get("symbol", "")
            ltp = self.last(ex, sym, avg) if ex in ("NSE", "BSE") else avg
            out.append(Position(self.account_id, ex, sym, product(p.get("product")), q, avg, ltp,
                                (ltp - avg) * q + float(p.get("realized_profit") or 0)))
        return out

    def funds(self) -> dict:
        d = (self.get("/funds") or {}).get("data") or {}
        avl = d.get("detailed_avl_balance") or {}
        cash = avl.get("eq_cnc", d.get("withdrawal_balance", 0))
        return {"cash": float(cash or 0), "used": None}
