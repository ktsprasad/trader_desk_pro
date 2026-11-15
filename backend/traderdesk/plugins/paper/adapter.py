"""Paper simulator with the same interface as a live broker.

Market orders fill at the last price (plus optional slippage). Limit orders fill when the
price crosses them on `set_price`. SL / SL-M orders wait for the trigger. GTTs fire the
same way. State is in memory per process; the desk records every order in the database."""
from __future__ import annotations

import itertools
import threading
from datetime import date, datetime
from typing import AsyncIterator

from ...brokers.base import PluginManifest, require, trade_cap_for
from ...core.errors import BrokerError, ErrorKind
from ...core.models import (Cap, GttAck, GttRequest, Holding, MarginQuote, Order, OrderAck,
                            OrderChange, OrderRequest, OrderStatus, OrderType, Position, Product,
                            Side, Tick)


class PaperBroker:
    name = "paper"

    def __init__(self, account_id: str = "paper", settings: dict | None = None, secrets=None,
                 manifest: PluginManifest | None = None):
        settings = settings or {}
        self.account_id = account_id
        self.capabilities = set(manifest.capabilities) if manifest else {
            Cap.READ, Cap.TRADE_EQ, Cap.TRADE_FO, Cap.GTT, Cap.OCO}
        self.cash = float(settings.get("starting_cash", 500_000))
        self.slippage_ticks = int(settings.get("slippage_ticks", 0))
        self.prices: dict[str, float] = dict(settings.get("prices", {}))
        self._orders: dict[str, Order] = {}
        self._gtts: dict[str, GttRequest] = {}
        self._pos: dict[tuple[str, str, str], list[float]] = {}   # (exch, sym, product) -> [qty, avg]
        self._ids = itertools.count(1)
        self._amo: set[str] = set()
        self._lock = threading.RLock()
        # test hooks: make the next place_order raise a network error after (or before) it lands
        self.fail_next_after_send = False
        self.fail_next_before_send = False

    # ------------------------------------------------------------ market data
    def set_price(self, key: str, price: float) -> None:
        with self._lock:
            self.prices[key] = price
            self._match(key)

    def ltp(self, key: str) -> float:
        if key not in self.prices:
            raise BrokerError(ErrorKind.INVALID, f"no price for {key} in the paper simulator", self.name)
        return self.prices[key]

    # ------------------------------------------------------------ read
    def holdings(self) -> list[Holding]:
        out = []
        for (ex, sym, prod), (qty, avg) in self._pos.items():
            if prod == Product.CNC.value and qty > 0:
                out.append(Holding(self.account_id, ex, sym, qty, avg, self.prices.get(f"{ex}:{sym}", avg)))
        return out

    def positions(self) -> list[Position]:
        out = []
        for (ex, sym, prod), (qty, avg) in self._pos.items():
            if prod != Product.CNC.value and qty != 0:
                lp = self.prices.get(f"{ex}:{sym}", avg)
                out.append(Position(self.account_id, ex, sym, Product(prod), qty, avg, lp, (lp - avg) * qty))
        return out

    def orders(self, day: date | None = None) -> list[Order]:
        return list(self._orders.values())

    def funds(self) -> dict:
        used = sum(abs(q) * a * (0.2 if p == Product.MIS.value else 1.0)
                   for (_e, _s, p), (q, a) in self._pos.items() if q)
        return {"cash": round(self.cash, 2), "used": round(used, 2)}

    def margins(self, legs: list[OrderRequest]) -> MarginQuote:
        """Rough simulator margin: delivery buys need full value, MIS 20%, futures 15%,
        option buys the premium, option sells 10x the premium. Equity sells need none."""
        need = 0.0
        for o in legs:
            px = o.price or self.prices.get(f"{o.exchange}:{o.tradingsymbol}", 0)
            notional = px * o.quantity
            if o.instrument_type in ("CE", "PE"):
                need += notional if o.side == Side.BUY else notional * 10
            elif o.instrument_type == "FUT":
                need += notional * 0.15
            elif o.side == Side.BUY:
                need += notional * (0.2 if o.product == Product.MIS else 1.0)
        return MarginQuote(required=round(need, 2), available=round(self.cash, 2))

    # ------------------------------------------------------------ trade
    def place_order(self, o: OrderRequest) -> OrderAck:
        require(self, trade_cap_for(o))
        if self.fail_next_before_send:
            self.fail_next_before_send = False
            raise BrokerError(ErrorKind.NETWORK, "timeout (order never reached the simulator)", self.name)
        with self._lock:
            oid = f"P{next(self._ids):06d}"
            status = OrderStatus.TRIGGER_PENDING if o.order_type in (OrderType.SL, OrderType.SL_M) \
                else OrderStatus.OPEN
            order = Order(oid, self.account_id, o.exchange, o.tradingsymbol, o.side, o.quantity,
                          o.order_type, o.product, status, o.price, o.trigger_price, tag=o.tag,
                          placed_at=datetime.now())
            self._orders[oid] = order
            if o.amo:
                self._amo.add(oid)     # after-market orders wait for open_session()
            else:
                self._match(f"{o.exchange}:{o.tradingsymbol}")
        if self.fail_next_after_send:
            self.fail_next_after_send = False
            raise BrokerError(ErrorKind.NETWORK, "timeout after send", self.name)
        return OrderAck(oid, order.status)

    def modify_order(self, order_id: str, changes: OrderChange) -> OrderAck:
        with self._lock:
            o = self._get_open(order_id)
            if changes.quantity is not None:
                o.quantity = changes.quantity
            if changes.price is not None:
                o.price = changes.price
            if changes.trigger_price is not None:
                o.trigger_price = changes.trigger_price
            if changes.order_type is not None:
                o.order_type = changes.order_type
            self._match(f"{o.exchange}:{o.tradingsymbol}")
            return OrderAck(order_id, o.status)

    def cancel_order(self, order_id: str) -> OrderAck:
        with self._lock:
            o = self._get_open(order_id)
            o.status = OrderStatus.CANCELLED
            return OrderAck(order_id, o.status)

    def place_gtt(self, g: GttRequest) -> GttAck:
        require(self, Cap.GTT)
        if g.kind == "two-leg":
            require(self, Cap.OCO)
        gid = f"G{next(self._ids):06d}"
        self._gtts[gid] = g
        return GttAck(gid)

    def gtts(self) -> dict[str, GttRequest]:
        return dict(self._gtts)

    def cancel_gtt(self, gtt_id: str) -> None:
        self._gtts.pop(gtt_id, None)

    async def stream(self, instruments: list[str]) -> AsyncIterator[Tick]:  # pragma: no cover
        for k in instruments:
            if k in self.prices:
                yield Tick(k, self.prices[k], datetime.now())

    # ------------------------------------------------------------ simulator
    def _get_open(self, order_id: str) -> Order:
        o = self._orders.get(order_id)
        if not o:
            raise BrokerError(ErrorKind.NOT_FOUND, f"no order {order_id}", self.name)
        if not o.is_open:
            raise BrokerError(ErrorKind.REJECTED, f"order {order_id} is {o.status.value}", self.name)
        return o

    def open_session(self) -> None:
        """Release after-market orders to the book, as the exchange does at the open."""
        with self._lock:
            keys = {f"{self._orders[i].exchange}:{self._orders[i].tradingsymbol}" for i in self._amo}
            self._amo.clear()
            for k in keys:
                self._match(k)

    def _match(self, key: str) -> None:
        px = self.prices.get(key)
        if px is None:
            return
        for o in list(self._orders.values()):
            if not o.is_open or f"{o.exchange}:{o.tradingsymbol}" != key or o.order_id in self._amo:
                continue
            buy = o.side == Side.BUY
            if o.status == OrderStatus.TRIGGER_PENDING:
                trig = o.trigger_price or 0
                if (buy and px >= trig) or (not buy and px <= trig):
                    o.status = OrderStatus.OPEN
                    if o.order_type == OrderType.SL_M:
                        o.order_type = OrderType.MARKET
                else:
                    continue
            if o.order_type == OrderType.MARKET:
                self._fill(o, px + (0.05 * self.slippage_ticks * (1 if buy else -1)))
            elif o.price is not None and ((buy and px <= o.price) or (not buy and px >= o.price)):
                self._fill(o, o.price)
        for gid, g in list(self._gtts.items()):
            if f"{g.exchange}:{g.tradingsymbol}" != key:
                continue
            fired = None
            if g.kind == "single":
                t = g.trigger_values[0]
                if (g.last_price > t and px <= t) or (g.last_price < t and px >= t):
                    fired = g.orders[0]
            else:  # OCO: [stop, target]
                lo, hi = sorted(g.trigger_values)
                if px <= lo:
                    fired = g.orders[0]
                elif px >= hi:
                    fired = g.orders[1]
            if fired:
                del self._gtts[gid]
                self.place_order(fired)

    def _fill(self, o: Order, price: float) -> None:
        o.status = OrderStatus.COMPLETE
        o.filled_quantity = o.quantity
        o.average_price = round(price, 2)
        k = (o.exchange, o.tradingsymbol, o.product.value)
        qty, avg = self._pos.get(k, [0.0, 0.0])
        signed = o.quantity if o.side == Side.BUY else -o.quantity
        new = qty + signed
        if qty == 0 or (qty > 0) == (signed > 0):
            avg = (qty * avg + signed * price) / new if new else 0.0
        elif new != 0 and (new > 0) != (qty > 0):
            avg = price
        self._pos[k] = [new, avg]
        self.cash -= signed * price
