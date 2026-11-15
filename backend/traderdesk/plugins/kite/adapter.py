"""Zerodha Kite Connect v3 adapter.

Ported from D:\\code\\kite (kite_auth.py, portfolio_fetcher.py, the place_*_gtts scripts).
Differences from the old scripts, on purpose:
  * the API secret and the daily access token live in the secrets vault, never in files;
  * SSL verification stays on;
  * every call goes through a per-endpoint token bucket;
  * Kite's exceptions are mapped to the core's small error set.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, AsyncIterator

from ...brokers.base import PluginManifest, require, trade_cap_for
from ...core.errors import BrokerError, ErrorKind
from ...core.models import (Cap, GttAck, GttRequest, Holding, MarginQuote, Order, OrderAck,
                            OrderChange, OrderRequest, OrderStatus, OrderType, Position, Product,
                            Side, Tick)
from ...core.ratelimit import Limiter
from ...core.secrets import secret_key

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_RESET = time(6, 0)   # Kite access tokens are invalidated around 06:00 IST each day

STATUS_MAP = {
    "OPEN": OrderStatus.OPEN, "COMPLETE": OrderStatus.COMPLETE, "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED, "TRIGGER PENDING": OrderStatus.TRIGGER_PENDING,
}


def map_status(s: str | None) -> OrderStatus:
    s = (s or "").upper()
    if s in STATUS_MAP:
        return STATUS_MAP[s]
    if "CANCEL" in s:
        return OrderStatus.CANCELLED if s.endswith("CANCELLED") else OrderStatus.PENDING
    return OrderStatus.PENDING   # AMO REQ RECEIVED, VALIDATION PENDING, OPEN PENDING, MODIFY PENDING...


def map_error(e: Exception) -> BrokerError:
    name = type(e).__name__
    msg = str(e)
    low = msg.lower()
    if name == "TokenException":
        kind = ErrorKind.AUTH_EXPIRED
    elif name == "NetworkException" or isinstance(e, (TimeoutError, ConnectionError)) or "timed out" in low:
        kind = ErrorKind.NETWORK
    elif "margin" in low or "insufficient" in low:
        kind = ErrorKind.INSUFFICIENT_MARGIN
    elif "circuit" in low or "price band" in low or "outside the" in low:
        kind = ErrorKind.PRICE_BAND
    elif "too many requests" in low or "rate" in low and "limit" in low:
        kind = ErrorKind.RATE_LIMITED
    elif name in ("InputException", "DataException"):
        kind = ErrorKind.INVALID
    else:
        kind = ErrorKind.REJECTED
    return BrokerError(kind, msg, "kite")


def token_is_fresh(issued_at: datetime | None, now: datetime | None = None) -> bool:
    """A token is good until the next 06:00 IST after it was issued."""
    if issued_at is None:
        return False
    now = (now or datetime.now(IST)).astimezone(IST)
    issued = issued_at.astimezone(IST)
    reset = datetime.combine(now.date(), TOKEN_RESET, IST)
    if now < reset:
        reset -= timedelta(days=1)
    return issued >= reset


class KiteBroker:
    name = "kite"

    def __init__(self, account_id: str, settings: dict, secrets, manifest: PluginManifest | None = None,
                 client: Any = None):
        self.account_id = account_id
        self.settings = settings
        self.secrets = secrets
        self.capabilities = set(manifest.capabilities) if manifest else {
            Cap.READ, Cap.TRADE_EQ, Cap.TRADE_FO, Cap.GTT, Cap.OCO, Cap.MF}
        limits = (manifest.limits if manifest else {}) or {}
        self.limit = Limiter({"orders": limits.get("orders_per_sec", 10), "quotes": limits.get("quotes_per_sec", 1),
                              "history": limits.get("history_per_sec", 3), "default": limits.get("default_per_sec", 10)})
        if client is None:
            from kiteconnect import KiteConnect
            client = KiteConnect(api_key=settings.get("api_key", ""))
        self.kite = client
        tok = self.secrets.get(secret_key(account_id, "access_token")) if secrets else None
        if tok:
            self.kite.set_access_token(tok)

    # ------------------------------------------------------------ auth (daily, one tap)
    def login_url(self) -> str:
        return self.kite.login_url()

    def complete_login(self, request_token: str) -> dict:
        secret = self.secrets.get(secret_key(self.account_id, "api_secret")) or self.settings.get("api_secret")
        if not secret:
            raise BrokerError(ErrorKind.AUTH_EXPIRED, "no Kite API secret in the vault", self.name)
        try:
            data = self.kite.generate_session(request_token, api_secret=secret)
        except Exception as e:
            raise map_error(e)
        self.kite.set_access_token(data["access_token"])
        self.secrets.set(secret_key(self.account_id, "access_token"), data["access_token"])
        self.secrets.set(secret_key(self.account_id, "token_issued_at"), datetime.now(IST).isoformat())
        return {"user_id": data.get("user_id"), "user_name": data.get("user_name")}

    def session_status(self, now: datetime | None = None) -> dict:
        issued = self.secrets.get(secret_key(self.account_id, "token_issued_at")) if self.secrets else None
        issued_dt = datetime.fromisoformat(issued) if issued else None
        fresh = token_is_fresh(issued_dt, now)
        return {"connected": fresh, "issued_at": issued, "needs_login": not fresh}

    def _call(self, group: str, fn, *a, **kw):
        self.limit(group)
        try:
            return fn(*a, **kw)
        except BrokerError:
            raise
        except Exception as e:
            raise map_error(e)

    # ------------------------------------------------------------ read
    def holdings(self) -> list[Holding]:
        out = [holding_from_kite(self.account_id, h) for h in self._call("default", self.kite.holdings)]
        if Cap.MF in self.capabilities:
            out += [mf_from_kite(self.account_id, m) for m in self._call("default", self.kite.mf_holdings)]
        return out

    def positions(self) -> list[Position]:
        net = (self._call("default", self.kite.positions) or {}).get("net", [])
        return [Position(self.account_id, p["exchange"], p["tradingsymbol"], Product(p.get("product", "MIS")),
                         p.get("quantity", 0), p.get("average_price", 0.0), p.get("last_price", 0.0),
                         p.get("pnl", 0.0)) for p in net if p.get("quantity")]

    def orders(self, day: date | None = None) -> list[Order]:
        return [order_from_kite(self.account_id, o) for o in self._call("default", self.kite.orders)]

    def gtts(self) -> list[dict]:
        return self._call("default", self.kite.get_gtts)

    def funds(self) -> dict:
        eq = ((self._call("default", self.kite.margins) or {}).get("equity") or {})
        return {"cash": float((eq.get("available") or {}).get("live_balance", eq.get("net", 0)) or 0),
                "used": float((eq.get("utilised") or {}).get("debits", 0) or 0)}

    def margins(self, legs: list[OrderRequest]) -> MarginQuote:
        params = [kite_margin_param(o) for o in legs]
        if len(params) > 1:
            res = self._call("default", self.kite.basket_order_margins, params, consider_positions=True)
            required = float(res.get("initial", {}).get("total", 0))
            final = float(res.get("final", {}).get("total", required))
        else:
            res = self._call("default", self.kite.order_margins, params)
            required = final = float(sum(r.get("total", 0) for r in res))
        m = self._call("default", self.kite.margins)
        seg = "commodity" if legs and legs[0].exchange == "MCX" else "equity"
        avail = float(((m or {}).get(seg) or {}).get("net", 0))
        return MarginQuote(required=required, available=avail, final=final)

    # ------------------------------------------------------------ trade
    def place_order(self, o: OrderRequest) -> OrderAck:
        require(self, trade_cap_for(o))
        oid = self._call("orders", self.kite.place_order, variety="amo" if o.amo else "regular",
                         **kite_order_params(o))
        return OrderAck(str(oid))

    def modify_order(self, order_id: str, changes: OrderChange) -> OrderAck:
        kw = {k: v for k, v in {"quantity": changes.quantity, "price": changes.price,
                                "trigger_price": changes.trigger_price,
                                "order_type": changes.order_type.value if changes.order_type else None}.items()
              if v is not None}
        self._call("orders", self.kite.modify_order, variety="regular", order_id=order_id, **kw)
        return OrderAck(order_id)

    def cancel_order(self, order_id: str) -> OrderAck:
        self._call("orders", self.kite.cancel_order, variety="regular", order_id=order_id)
        return OrderAck(order_id, OrderStatus.CANCELLED)

    def place_gtt(self, g: GttRequest) -> GttAck:
        require(self, Cap.GTT)
        if g.kind == "two-leg":
            require(self, Cap.OCO)
        orders = [{"transaction_type": x.side.value, "quantity": x.quantity, "price": x.price,
                   "order_type": x.order_type.value, "product": x.product.value} for x in g.orders]
        res = self._call("orders", self.kite.place_gtt, trigger_type=g.kind, tradingsymbol=g.tradingsymbol,
                         exchange=g.exchange, trigger_values=g.trigger_values, last_price=g.last_price,
                         orders=orders)
        return GttAck(str(res.get("trigger_id")))

    def quote(self, keys: list[str]) -> dict[str, dict]:
        """Full quote (last price, day OHLC with previous close, volume, OI) for up to 500 instruments."""
        return self._call("quotes", self.kite.quote, keys)

    def instruments(self, exchange: str = "NFO") -> list[dict]:
        return self._call("quotes", self.kite.instruments, exchange)

    def ltp(self, keys: list[str]) -> dict[str, float]:
        res = self._call("quotes", self.kite.ltp, keys)
        return {k: float(v["last_price"]) for k, v in res.items()}

    async def stream(self, instruments: list[str]) -> AsyncIterator[Tick]:  # pragma: no cover - needs Connect plan
        raise BrokerError(ErrorKind.UNSUPPORTED, "live ticks need the Kite Connect plan; not wired yet", self.name)
        yield  # noqa


# ---------------------------------------------------------------- translation (pure, tested)

def kite_order_params(o: OrderRequest) -> dict:
    p = {"exchange": o.exchange, "tradingsymbol": o.tradingsymbol, "transaction_type": o.side.value,
         "quantity": o.quantity, "product": o.product.value, "order_type": o.order_type.value,
         "validity": o.validity.value}
    if o.order_type in (OrderType.LIMIT, OrderType.SL):
        p["price"] = o.price
    if o.order_type in (OrderType.SL, OrderType.SL_M):
        p["trigger_price"] = o.trigger_price
    if o.tag:
        p["tag"] = o.tag[:20]   # Kite tags are max 20 characters
    return p


def kite_margin_param(o: OrderRequest) -> dict:
    return {"exchange": o.exchange, "tradingsymbol": o.tradingsymbol, "transaction_type": o.side.value,
            "variety": "regular", "product": o.product.value, "order_type": o.order_type.value,
            "quantity": o.quantity, "price": o.price or 0, "trigger_price": o.trigger_price or 0}


def holding_from_kite(account: str, h: dict) -> Holding:
    sym = h.get("tradingsymbol", "")
    qty = float(h.get("quantity", 0)) + float(h.get("t1_quantity", 0))
    cls = "equity"
    if sym.endswith(("BEES", "ETF", "IETF")) or "ETF" in sym:
        cls = "etf"
    elif sym.endswith(("-RR", "-IV")) or "INVIT" in sym or "REIT" in sym:
        cls = "reit_invit"
    return Holding(account, h.get("exchange", "NSE"), sym, qty, float(h.get("average_price", 0)),
                   float(h.get("last_price", 0)), isin=h.get("isin"), asset_class=cls,
                   close_price=h.get("close_price"))


def mf_from_kite(account: str, m: dict) -> Holding:
    return Holding(account, "MF", m.get("tradingsymbol", ""), float(m.get("quantity", 0)),
                   float(m.get("average_price", 0)), float(m.get("last_price", 0)),
                   isin=m.get("tradingsymbol"), asset_class="mutual_fund", name=m.get("fund"))


def order_from_kite(account: str, o: dict) -> Order:
    ts = o.get("order_timestamp")
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = None
    return Order(str(o["order_id"]), account, o.get("exchange", ""), o.get("tradingsymbol", ""),
                 Side(o.get("transaction_type", "BUY")), int(o.get("quantity", 0)),
                 OrderType(o.get("order_type", "LIMIT")), Product(o.get("product", "CNC")),
                 map_status(o.get("status")), o.get("price"), o.get("trigger_price"),
                 int(o.get("filled_quantity", 0)), o.get("average_price"), o.get("tag"), ts,
                 o.get("status_message"))
