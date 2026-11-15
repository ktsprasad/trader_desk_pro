"""Broker-neutral domain model. Every plugin translates to and from these types,
so the UI, the risk rules and the charges engine never see a broker's own fields."""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


class Cap(str, enum.Enum):
    READ = "READ"
    TRADE_EQ = "TRADE_EQ"
    TRADE_FO = "TRADE_FO"
    GTT = "GTT"
    OCO = "OCO"
    MF = "MF"
    STREAM = "STREAM"
    HISTORY = "HISTORY"


class Side(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"        # stop-limit
    SL_M = "SL-M"    # stop-market


class Product(str, enum.Enum):
    CNC = "CNC"      # equity delivery
    MIS = "MIS"      # intraday
    NRML = "NRML"    # F&O carry-forward


class Validity(str, enum.Enum):
    DAY = "DAY"
    IOC = "IOC"


class Segment(str, enum.Enum):
    """Charges segment. Derived from exchange + product + instrument type."""
    EQ_DELIVERY = "eq_delivery"
    EQ_INTRADAY = "eq_intraday"
    FUTURES = "futures"
    OPTIONS = "options"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"        # sent, broker has not acknowledged a final state
    OPEN = "OPEN"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


OPEN_STATES = {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING}
FO_EXCHANGES = {"NFO", "BFO", "MCX", "CDS"}


@dataclass
class Instrument:
    exchange: str
    tradingsymbol: str
    isin: str | None = None
    token: int | None = None
    lot_size: int = 1
    tick_size: float = 0.05
    instrument_type: str = "EQ"      # EQ, FUT, CE, PE, MF, US
    expiry: date | None = None
    strike: float | None = None
    currency: str = "INR"

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"

    @property
    def is_option(self) -> bool:
        return self.instrument_type in ("CE", "PE")

    @property
    def is_derivative(self) -> bool:
        return self.exchange in FO_EXCHANGES or self.instrument_type in ("FUT", "CE", "PE")


@dataclass
class Holding:
    account: str
    exchange: str
    tradingsymbol: str
    quantity: float
    average_price: float
    last_price: float
    isin: str | None = None
    asset_class: str = "equity"   # equity, etf, reit, invit, mutual_fund, us_stock, gold
    currency: str = "INR"
    close_price: float | None = None
    name: str | None = None

    @property
    def invested(self) -> float:
        return self.quantity * self.average_price

    @property
    def value(self) -> float:
        return self.quantity * self.last_price

    @property
    def pnl(self) -> float:
        return self.value - self.invested

    @property
    def day_change(self) -> float | None:
        if self.close_price:
            return (self.last_price - self.close_price) * self.quantity
        return None


@dataclass
class Position:
    account: str
    exchange: str
    tradingsymbol: str
    product: Product
    quantity: float           # signed: +long, -short
    average_price: float
    last_price: float
    pnl: float = 0.0


@dataclass
class OrderRequest:
    exchange: str
    tradingsymbol: str
    side: Side
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    product: Product = Product.CNC
    price: float | None = None
    trigger_price: float | None = None
    validity: Validity = Validity.DAY
    tag: str | None = None          # TDP-<plan>-<n>, set by the desk
    amo: bool = False
    # plan context: used by the risk check, never sent to the broker
    stop: float | None = None
    target: float | None = None
    plan_id: str | None = None
    instrument_type: str = "EQ"
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: str | None = None       # YYYY-MM-DD for F&O

    def fingerprint(self) -> str:
        """Hash of the fields that reach the broker. A confirmation is bound to it, so
        changing any of them after the risk check invalidates the confirm."""
        core = {
            "exchange": self.exchange, "tradingsymbol": self.tradingsymbol, "side": self.side.value,
            "quantity": self.quantity, "order_type": self.order_type.value, "product": self.product.value,
            "price": self.price, "trigger_price": self.trigger_price, "validity": self.validity.value,
            "amo": self.amo, "stop": self.stop, "target": self.target,
        }
        blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def segment(self) -> Segment:
        if self.instrument_type in ("CE", "PE"):
            return Segment.OPTIONS
        if self.instrument_type == "FUT" or self.exchange in FO_EXCHANGES:
            return Segment.FUTURES
        return Segment.EQ_INTRADAY if self.product == Product.MIS else Segment.EQ_DELIVERY

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, enum.Enum):
                d[k] = v.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OrderRequest":
        d = dict(d)
        d["side"] = Side(str(d["side"]).upper())
        d["order_type"] = OrderType(str(d.get("order_type", "LIMIT")).upper())
        d["product"] = Product(str(d.get("product", "CNC")).upper())
        d["validity"] = Validity(str(d.get("validity", "DAY")).upper())
        d["quantity"] = int(d["quantity"])
        d["exchange"] = str(d["exchange"]).upper()
        d["tradingsymbol"] = str(d["tradingsymbol"]).upper()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class OrderChange:
    quantity: int | None = None
    price: float | None = None
    trigger_price: float | None = None
    order_type: OrderType | None = None


@dataclass
class Order:
    order_id: str
    account: str
    exchange: str
    tradingsymbol: str
    side: Side
    quantity: int
    order_type: OrderType
    product: Product
    status: OrderStatus
    price: float | None = None
    trigger_price: float | None = None
    filled_quantity: int = 0
    average_price: float | None = None
    tag: str | None = None
    placed_at: datetime | None = None
    status_message: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATES


@dataclass
class OrderAck:
    order_id: str
    status: OrderStatus = OrderStatus.PENDING
    message: str | None = None


@dataclass
class GttRequest:
    exchange: str
    tradingsymbol: str
    last_price: float
    trigger_values: list[float]
    orders: list[OrderRequest]
    kind: str = "single"   # single | two-leg (OCO)


@dataclass
class GttAck:
    gtt_id: str


@dataclass
class MarginQuote:
    required: float
    available: float | None = None
    final: float | None = None    # after basket hedge benefit

    @property
    def sufficient(self) -> bool | None:
        if self.available is None:
            return None
        return self.available >= self.required


@dataclass
class Tick:
    instrument: str
    last_price: float
    ts: datetime
    volume: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
