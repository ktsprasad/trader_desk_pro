"""Shared base for read-only REST broker adapters (Dhan, Upstox, INDstocks).

These connectors read holdings, positions, funds and the order book. They declare READ only: order
placement through them is not offered until it has been verified against a live account, so the desk
never routes a real order to them (live mode keeps them on the paper simulator).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..core.errors import BrokerError, ErrorKind
from ..core.models import Cap, Product
from ..core.ratelimit import Limiter
from ..core.secrets import secret_key

IST = timezone(timedelta(hours=5, minutes=30))

PRODUCT = {"CNC": Product.CNC, "D": Product.CNC, "DELIVERY": Product.CNC, "INTRADAY": Product.MIS, "I": Product.MIS,
           "MIS": Product.MIS, "MARGIN": Product.NRML, "NRML": Product.NRML, "MTF": Product.CNC, "CO": Product.MIS,
           "BO": Product.MIS}


def product(p: str | None) -> Product:
    return PRODUCT.get((p or "").upper(), Product.NRML)


class RestBroker:
    name = "rest"
    base_url = ""
    token_hours = 24
    capabilities = {Cap.READ}

    def __init__(self, account_id: str, settings: dict, secrets, manifest=None, http: Any = None):
        self.account_id = account_id
        self.settings = settings or {}
        self.secrets = secrets
        if manifest:
            self.capabilities = set(manifest.capabilities)
        self.limit = Limiter({"default": float(((manifest.limits if manifest else None) or {}).get("default_per_sec", 5))})
        self._http = http
        # (exchange, symbol) -> last price; set by the app to the delayed feed for brokers whose
        # holdings response carries no last price
        self.price_lookup: Callable[[str, str], float | None] | None = None

    # ------------------------------------------------------------ auth
    def token(self) -> str | None:
        return self.secrets.get(secret_key(self.account_id, "access_token")) if self.secrets else None

    def save_token(self, tok: str) -> None:
        self.secrets.set(secret_key(self.account_id, "access_token"), tok.strip())
        self.secrets.set(secret_key(self.account_id, "token_issued_at"), datetime.now(IST).isoformat())

    def session_status(self, now: datetime | None = None) -> dict:
        issued = self.secrets.get(secret_key(self.account_id, "token_issued_at")) if self.secrets else None
        tok = self.token()
        fresh = bool(tok)
        if tok and issued:
            fresh = (now or datetime.now(IST)) < datetime.fromisoformat(issued) + timedelta(hours=self.token_hours)
        return {"connected": fresh, "issued_at": issued, "needs_login": not fresh}

    def headers(self) -> dict:
        raise NotImplementedError

    # ------------------------------------------------------------ http
    def client(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(base_url=self.base_url, timeout=15.0)
        return self._http

    def get(self, path: str, params: dict | None = None) -> Any:
        if not self.token():
            raise BrokerError(ErrorKind.AUTH_EXPIRED, f"{self.name}: no access token; log in from Connections", self.name)
        self.limit("default")
        try:
            r = self.client().get(path, params=params, headers={"Accept": "application/json", **self.headers()})
        except Exception as e:
            raise BrokerError(ErrorKind.NETWORK, f"{self.name}: {e}", self.name)
        if r.status_code in (401, 403):
            raise BrokerError(ErrorKind.AUTH_EXPIRED, f"{self.name}: token rejected ({r.status_code}); log in again", self.name)
        if r.status_code == 429:
            raise BrokerError(ErrorKind.RATE_LIMITED, f"{self.name}: rate limited", self.name)
        if r.status_code >= 400:
            raise BrokerError(ErrorKind.REJECTED, f"{self.name}: HTTP {r.status_code} {r.text[:200]}", self.name)
        return r.json()

    def last(self, exchange: str, symbol: str, fallback: float) -> float:
        if self.price_lookup:
            try:
                v = self.price_lookup(exchange, symbol)
                if v:
                    return float(v)
            except Exception:
                pass
        return fallback

    # read-only: nothing to cancel or place, and the desk's sync has nothing to reconcile
    def orders(self, day=None) -> list:
        return []

    def margins(self, legs):
        raise BrokerError(ErrorKind.UNSUPPORTED, f"{self.name} is connected read-only", self.name)

    def place_order(self, o):
        raise BrokerError(ErrorKind.UNSUPPORTED, f"{self.name} is connected read-only; trade it in the broker's app", self.name)

    modify_order = cancel_order = place_gtt = place_order
