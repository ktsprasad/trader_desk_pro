"""Dhan API v2, read-only. Field names follow dhanhq.co/docs/v2 (portfolio, funds, orders)."""
from __future__ import annotations

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Holding, Position
from .._rest import RestBroker, product

SEGMENT_EXCHANGE = {"NSE_EQ": "NSE", "BSE_EQ": "BSE", "NSE_FNO": "NFO", "BSE_FNO": "BFO", "MCX_COMM": "MCX",
                    "NSE_CURRENCY": "CDS", "BSE_CURRENCY": "BCD"}


class DhanBroker(RestBroker):
    name = "dhan"
    base_url = "https://api.dhan.co/v2"
    token_hours = 24

    def headers(self) -> dict:
        return {"access-token": self.token() or ""}

    def login_with_totp(self, pin: str, totp: str) -> dict:
        """Dhan's documented PIN + TOTP token call; nothing is stored except the returned token."""
        import httpx
        cid = self.settings.get("client_id", "")
        r = httpx.post("https://auth.dhan.co/app/generateAccessToken", params={"dhanClientId": cid, "pin": pin, "totp": totp},
                       timeout=15)
        if r.status_code >= 400:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, f"dhan: token request failed ({r.status_code})", self.name)
        tok = (r.json() or {}).get("accessToken") or (r.json() or {}).get("access_token")
        if not tok:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, "dhan: no token in the response", self.name)
        self.save_token(tok)
        return {"user_id": cid}

    def holdings(self) -> list[Holding]:
        out = []
        for h in self.get("/holdings") or []:
            qty = float(h.get("totalQty") or 0)
            if not qty:
                continue
            ex = h.get("exchange") or "NSE"
            ex = "BSE" if ex.upper().startswith("BSE") else "NSE"
            avg = float(h.get("avgCostPrice") or 0)
            out.append(Holding(self.account_id, ex, h.get("tradingSymbol", ""), qty, avg,
                               self.last(ex, h.get("tradingSymbol", ""), avg), isin=h.get("isin")))
        return out

    def positions(self) -> list[Position]:
        out = []
        for p in self.get("/positions") or []:
            q = float(p.get("netQty") or 0)
            if not q:
                continue
            ex = SEGMENT_EXCHANGE.get(p.get("exchangeSegment", ""), "NSE")
            avg = float(p.get("costPrice") or p.get("buyAvg") or 0)
            pnl = float(p.get("unrealizedProfit") or 0) + float(p.get("realizedProfit") or 0)
            ltp = avg + (float(p.get("unrealizedProfit") or 0) / q if q else 0)
            out.append(Position(self.account_id, ex, p.get("tradingSymbol", ""), product(p.get("productType")), q, avg, ltp, pnl))
        return out

    def funds(self) -> dict:
        f = self.get("/fundlimit") or {}
        cash = f.get("availabelBalance", f.get("availableBalance", 0))   # Dhan's documented spelling first
        return {"cash": float(cash or 0), "used": float(f.get("utilizedAmount") or 0)}
