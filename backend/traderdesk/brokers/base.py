"""The one contract every broker plugin implements."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from ..core.errors import BrokerError, ErrorKind
from ..core.models import (Cap, GttAck, GttRequest, Holding, MarginQuote, Order, OrderAck,
                           OrderChange, OrderRequest, Position, Tick)


@runtime_checkable
class BrokerAdapter(Protocol):
    name: str                       # 'kite', 'paper', ...
    capabilities: set[Cap]

    def holdings(self) -> list[Holding]: ...
    def positions(self) -> list[Position]: ...
    def orders(self, day: date | None = None) -> list[Order]: ...
    def place_order(self, o: OrderRequest) -> OrderAck: ...      # TRADE only
    def modify_order(self, order_id: str, changes: OrderChange) -> OrderAck: ...
    def cancel_order(self, order_id: str) -> OrderAck: ...
    def place_gtt(self, g: GttRequest) -> GttAck: ...
    def margins(self, legs: list[OrderRequest]) -> MarginQuote: ...
    def stream(self, instruments: list[str]) -> AsyncIterator[Tick]: ...


@dataclass
class PluginManifest:
    id: str
    name: str
    markets: list[str]
    assets: list[str]
    capabilities: set[Cap]
    auth: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, float] = field(default_factory=dict)
    requires_static_ip: bool = False
    settings_schema: dict[str, Any] = field(default_factory=dict)
    entry: str | None = None        # "module:Class"
    kind: str = "native"            # native | aggregator | mcp | import
    path: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any], schema: dict | None = None, path: str | None = None):
        return cls(
            id=d["id"], name=d.get("name", d["id"]), markets=list(d.get("markets", [])),
            assets=list(d.get("assets", [])), capabilities={Cap(c) for c in d.get("capabilities", [])},
            auth=dict(d.get("auth") or {}), limits=dict(d.get("limits") or {}),
            requires_static_ip=bool(d.get("requires_static_ip", False)), settings_schema=schema or {},
            entry=d.get("entry"), kind=d.get("kind", "native"), path=path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "markets": self.markets, "assets": self.assets,
            "capabilities": sorted(c.value for c in self.capabilities), "auth": self.auth,
            "limits": self.limits, "requires_static_ip": self.requires_static_ip,
            "settings_schema": self.settings_schema, "kind": self.kind,
        }


def require(adapter: BrokerAdapter, cap: Cap) -> None:
    if cap not in adapter.capabilities:
        raise BrokerError(ErrorKind.UNSUPPORTED, f"{adapter.name} cannot do {cap.value}", adapter.name)


def trade_cap_for(o: OrderRequest) -> Cap:
    return Cap.TRADE_FO if o.instrument_type in ("FUT", "CE", "PE") or o.exchange in ("NFO", "BFO", "MCX", "CDS") \
        else Cap.TRADE_EQ
