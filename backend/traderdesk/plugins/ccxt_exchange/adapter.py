"""Crypto balances through CCXT (100+ exchanges), read-only. Use an API key with read permission only.

`pip install ccxt` (or `pip install -e "backend[crypto]"`) is needed; without it the connection says so.
Values are converted to INR with USDT/USD at the USD/INR rate from the market feed.
"""
from __future__ import annotations

from typing import Any

from ...core.errors import BrokerError, ErrorKind
from ...core.models import Cap, Holding
from ...core.secrets import secret_key

STABLE = {"USDT", "USDC", "FDUSD", "BUSD", "DAI", "USD"}


class CcxtExchange:
    name = "ccxt"
    capabilities = {Cap.READ}

    def __init__(self, account_id: str, settings: dict, secrets, manifest=None, client: Any = None):
        self.account_id, self.settings, self.secrets = account_id, settings or {}, secrets
        self._client = client
        self.price_lookup = None   # (exchange, symbol) -> price; the app sets it (used for USDINR)

    def client(self):
        if self._client is None:
            try:
                import ccxt  # noqa: PLC0415
            except ImportError:
                raise BrokerError(ErrorKind.UNSUPPORTED, "install ccxt: pip install ccxt", self.name)
            ex = getattr(ccxt, self.settings.get("exchange", "binance"))
            self._client = ex({"apiKey": self.settings.get("api_key", ""),
                               "secret": self.secrets.get(secret_key(self.account_id, "api_secret")) or "",
                               "enableRateLimit": True})
        return self._client

    def session_status(self, now=None) -> dict:
        ok = bool(self.settings.get("api_key")) and bool(self.secrets and self.secrets.get(secret_key(self.account_id, "api_secret")))
        return {"connected": ok, "needs_login": not ok, "issued_at": None}

    def _usdinr(self) -> float | None:
        if self.price_lookup:
            try:
                return self.price_lookup("FX", "USDINR")
            except Exception:
                return None
        return None

    def holdings(self) -> list[Holding]:
        c = self.client()
        try:
            bal = c.fetch_balance()
            totals = {k: float(v) for k, v in (bal.get("total") or {}).items() if v and float(v) > 0}
            tickers = c.fetch_tickers([f"{a}/USDT" for a in totals if a not in STABLE]) if any(a not in STABLE for a in totals) else {}
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(ErrorKind.NETWORK if "timeout" in str(e).lower() else ErrorKind.REJECTED, f"ccxt: {e}", self.name)
        out = []
        for asset, qty in totals.items():
            px = 1.0 if asset in STABLE else float((tickers.get(f"{asset}/USDT") or {}).get("last") or 0)
            if not px:
                continue
            out.append(Holding(self.account_id, self.settings.get("exchange", "crypto").upper(), asset, qty, px, px,
                               asset_class="crypto", currency="USD"))
        return out

    def positions(self):
        return []

    def orders(self, day=None):
        return []

    def funds(self) -> dict:
        return {"cash": None, "used": None}

    def place_order(self, o):
        raise BrokerError(ErrorKind.UNSUPPORTED, "crypto is connected read-only", self.name)

    modify_order = cancel_order = place_gtt = margins = place_order
