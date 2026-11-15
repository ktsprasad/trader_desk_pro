"""The small set of errors every broker's own codes are mapped to."""
from __future__ import annotations

import enum


class ErrorKind(str, enum.Enum):
    AUTH_EXPIRED = "auth_expired"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    PRICE_BAND = "price_band"
    RATE_LIMITED = "rate_limited"
    REJECTED = "rejected_by_exchange"
    UNSUPPORTED = "unsupported"
    NETWORK = "network"          # timeout / connection: the order may or may not have reached the broker
    INVALID = "invalid"
    NOT_FOUND = "not_found"


class BrokerError(Exception):
    def __init__(self, kind: ErrorKind, message: str, broker: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.broker = broker

    def __repr__(self) -> str:
        return f"BrokerError({self.kind.value}, {self.message!r})"


class DeskError(Exception):
    """A gate refused the order. `gate` names which one."""

    def __init__(self, gate: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.gate = gate
        self.message = message
        self.details = details or {}
