"""The order desk. Every order (stock, option, future) goes through the same five gates:

  1. Ticket   normalise (tick / lot rounding, option market -> protected limit), charges, margin
  2. Risk     PASS or BLOCK with reasons; a BLOCK cannot be overridden from the ticket
  3. Confirm  the exact order, bound to its fingerprint, typed back by the user, within 5 minutes,
              with the risk check run again at that moment
  4. Send     with a client tag TDP-<plan>-<n>; on a timeout the order book is re-read before any
              retry, so the same order is never sent twice
  5. Track    order book sync; a filled entry with stop + target arms an OCO GTT at the broker

Nothing is sent without step 3. Paper mode routes every account to a simulator with the same API.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..brokers.base import BrokerAdapter
from ..core import audit
from ..core.calendar import Session as MarketSession, nse_session
from ..core.errors import BrokerError, DeskError, ErrorKind
from ..core.models import (Cap, GttRequest, OrderChange, OrderRequest, OrderStatus, OrderType, Product, Side)
from ..db.models import Account, AppState, DeskOrder, RiskRules
from ..plugins.paper.adapter import PaperBroker
from .charges import ChargesEngine, round_to_lot, round_to_tick
from .risk import DayStats, RiskContext, check, load_default_rules

log = logging.getLogger(__name__)
CONFIRM_WINDOW = timedelta(minutes=5)
OPTION_PROTECTION_BAND = 0.05     # option market orders become limits 5% through the last price
STOP_LEG_BAND = 0.01              # OCO stop leg: limit 1% beyond the trigger


def confirm_phrase(o: OrderRequest) -> str:
    return f"{o.side.value} {o.quantity} {o.tradingsymbol}"


def describe(o: OrderRequest, broker_name: str, paper: bool, verdict: dict, charges: dict | None) -> str:
    px = {OrderType.MARKET: "at MARKET", OrderType.LIMIT: f"LIMIT {o.price or 0:,.2f}",
          OrderType.SL: f"SL trigger {o.trigger_price or 0:,.2f} limit {o.price or 0:,.2f}",
          OrderType.SL_M: f"SL-M trigger {o.trigger_price or 0:,.2f}"}[o.order_type]
    parts = [f"{o.side.value} {o.quantity} {o.tradingsymbol} on {o.exchange}, {px}, {o.product.value}",
             f"via {broker_name}{' [PAPER]' if paper else ' [LIVE - real money]'}"]
    if o.stop:
        parts.append(f"stop {o.stop:,.2f}")
    if o.target:
        parts.append(f"target {o.target:,.2f}")
    if verdict.get("max_loss") is not None:
        parts.append(f"max loss ₹{verdict['max_loss']:,.2f}")
    if charges:
        parts.append(f"charges ≈ ₹{charges['total']:,.2f}")
    return "; ".join(parts) + "."


class OrderDesk:
    def __init__(self, db_factory: Callable[[], Session], adapters: dict[str, BrokerAdapter],
                 charges: ChargesEngine | None = None, session: MarketSession | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.db_factory = db_factory
        self.adapters = adapters
        self.paper: dict[str, PaperBroker] = {}
        self.charges = charges or ChargesEngine()
        self.session = session or nse_session()
        self.clock = clock or (lambda: datetime.now(self.session.tz))
        self.price_source: Callable[[str, str], float | None] | None = None   # (exchange, symbol) -> last

    def _utcnow(self) -> datetime:
        """The desk's clock as naive UTC (what the database stores)."""
        now = self.clock()
        return (now - now.utcoffset()).replace(tzinfo=None) if now.tzinfo else now

    # ------------------------------------------------------------ state
    def _state(self, db: Session, key: str, default):
        row = db.get(AppState, key)
        return row.value if row else default

    def _set_state(self, db: Session, key: str, value) -> None:
        row = db.get(AppState, key)
        if row:
            row.value = value
        else:
            db.add(AppState(key=key, value=value))

    def mode(self, db: Session) -> str:
        return self._state(db, "mode", {"mode": "paper"})["mode"]

    def set_mode(self, db: Session, mode: str, user: str | None) -> None:
        if mode not in ("paper", "live"):
            raise ValueError("mode is paper or live")
        self._set_state(db, "mode", {"mode": mode})
        audit.record(db, "orders", "mode_change", {"mode": mode}, user)

    def kill_switch_on(self, db: Session) -> bool:
        k = self._state(db, "kill_switch", {})
        return k.get("date") == self.clock().date().isoformat()

    def rules(self, db: Session) -> tuple[int, dict]:
        row = db.execute(select(RiskRules).order_by(RiskRules.version.desc()).limit(1)).scalar_one_or_none()
        if row is None:
            row = RiskRules(version=1, rules=load_default_rules(), created_by="system", note="defaults")
            db.add(row)
            db.flush()
        return row.version, row.rules

    def update_rules(self, db: Session, changes: dict, user: str | None, note: str | None = None) -> int:
        version, current = self.rules(db)
        new = dict(current)
        new.update(changes)
        db.add(RiskRules(version=version + 1, rules=new, created_by=user, note=note))
        audit.record(db, "risk", "rules_change", {"from_version": version, "to_version": version + 1,
                                                  "changes": changes, "note": note}, user)
        return version + 1

    # ------------------------------------------------------------ routing
    def broker_for(self, db: Session, account_id: str) -> tuple[BrokerAdapter, bool]:
        acct = db.get(Account, account_id)
        if acct is None or account_id not in self.adapters:
            raise DeskError("ticket", f"no connected account {account_id!r}")
        live_adapter = self.adapters[account_id]
        if isinstance(live_adapter, PaperBroker):
            return live_adapter, True
        if self.mode(db) == "live" and acct.live:
            return live_adapter, False
        if account_id not in self.paper:
            self.paper[account_id] = PaperBroker(f"{account_id}-paper")
        return self.paper[account_id], True

    def _ltp(self, live: BrokerAdapter, route: BrokerAdapter, o: OrderRequest) -> float | None:
        """Last price: the live broker's quote when it has one (it also seeds the paper twin),
        else the simulator's own price."""
        key = f"{o.exchange}:{o.tradingsymbol}"
        if not isinstance(live, PaperBroker) and hasattr(live, "ltp"):
            try:
                v = live.ltp([key]).get(key)
                if v:
                    if isinstance(route, PaperBroker):
                        route.set_price(key, float(v))
                    return float(v)
            except Exception as e:
                log.info("no live quote for %s: %s", key, e)
        if isinstance(route, PaperBroker):
            if key not in route.prices and self.price_source is not None:
                try:   # free delayed feed seeds the simulator when no broker quote is available
                    v = self.price_source(o.exchange, o.tradingsymbol)
                    if v:
                        route.set_price(key, float(v))
                except Exception as e:
                    log.info("no fallback quote for %s: %s", key, e)
            return route.prices.get(key)
        return None

    def _held(self, adapter: BrokerAdapter, o: OrderRequest) -> tuple[float, float]:
        qty = val = 0.0
        try:
            for h in adapter.holdings():
                if h.tradingsymbol == o.tradingsymbol and h.exchange == o.exchange:
                    qty += h.quantity
                    val += h.value
            for p in adapter.positions():
                if p.tradingsymbol == o.tradingsymbol and p.exchange == o.exchange:
                    qty += p.quantity
                    val += p.quantity * p.last_price
        except BrokerError as e:
            log.warning("could not read holdings for the risk check: %s", e)
        return qty, val

    def _today(self, db: Session) -> DayStats:
        start = self.clock().replace(hour=0, minute=0, second=0, microsecond=0)   # midnight IST
        start_utc = (start - start.utcoffset()).replace(tzinfo=None) if start.tzinfo else start
        rows = db.execute(select(DeskOrder).where(DeskOrder.stage == "sent", DeskOrder.sent_at >= start_utc)).scalars()
        s = DayStats()
        for r in rows:
            s.orders_placed += 1
            if r.kind == "entry":
                s.entries_placed += 1
                s.loss_at_risk += r.max_loss or 0.0
                s.fo_notional += r.fo_notional or 0.0
        return s

    # ------------------------------------------------------------ gates 1 + 2
    def normalise(self, o: OrderRequest, ltp: float | None) -> tuple[OrderRequest, list[str]]:
        notes = []
        q = round_to_lot(o.quantity, o.lot_size)
        if q != o.quantity:
            notes.append(f"quantity rounded down to the lot size {o.lot_size}: {o.quantity} -> {q}")
        o = replace(o, quantity=q)
        if o.order_type == OrderType.MARKET and o.instrument_type in ("CE", "PE") and ltp:
            band = ltp * (1 + OPTION_PROTECTION_BAND) if o.side == Side.BUY else ltp * (1 - OPTION_PROTECTION_BAND)
            o = replace(o, order_type=OrderType.LIMIT, price=round_to_tick(band, o.tick_size, o.side))
            notes.append(f"option market order converted to a limit at {o.price} "
                         f"({int(OPTION_PROTECTION_BAND * 100)}% protection band)")
        for f in ("price", "trigger_price", "stop", "target"):
            v = getattr(o, f)
            if v is not None:
                side = o.side if f == "price" else None
                r = round_to_tick(v, o.tick_size, side)
                if abs(r - v) > 1e-9:
                    notes.append(f"{f} rounded to tick {o.tick_size}: {v} -> {r}")
                    o = replace(o, **{f: r})
        return o, notes

    def _assess(self, db: Session, account_id: str, o: OrderRequest):
        adapter, paper = self.broker_for(db, account_id)
        live_adapter = self.adapters[account_id]
        ltp = self._ltp(live_adapter, adapter, o)
        o, notes = self.normalise(o, ltp)
        held_qty, held_val = self._held(adapter, o)
        try:
            margin = adapter.margins([o])
        except BrokerError as e:
            margin = None
            notes.append(f"margin check unavailable: {e.message}")
        version, rules = self.rules(db)
        v = check(o, rules, RiskContext(ltp=ltp, held_qty=held_qty, held_value=held_val, today=self._today(db),
                                        margin=margin, now=self.clock(), session=self.session))
        verdict = v.to_dict()
        verdict["rules_version"] = version
        verdict["notes"] = notes
        if self.kill_switch_on(db) and v.kind == "entry":
            verdict["verdict"] = "BLOCK"
            verdict["reasons"] = verdict["reasons"] + ["kill switch: on for today; only exits are allowed"]
        broker_key = getattr(live_adapter, "name", "kite")
        px = v.entry_ref or o.price or ltp
        charges = None
        if px:
            exit_px = o.target or px
            charges = self.charges.round_trip(broker_key, o, px, exit_px, self.clock().date())
            charges["entry_only"] = charges["entry"]["total"]
            if v.max_loss and o.target and v.entry_ref:
                gross = abs(o.target - v.entry_ref) * o.quantity
                charges["pct_of_expected_profit"] = round(100 * charges["total"] / gross, 1) if gross else None
                if gross and charges["total"] > 0.25 * gross:
                    verdict["warnings"].append(
                        f"charges ₹{charges['total']:,.2f} are {charges['pct_of_expected_profit']}% of the "
                        "expected profit: the target barely clears its costs")
        return o, adapter, paper, verdict, charges, margin

    def ticket(self, account_id: str, o: OrderRequest, user: str | None = None) -> DeskOrder:
        with self.db_factory() as db, db.begin():
            o, adapter, paper, verdict, charges, margin = self._assess(db, account_id, o)
            row = DeskOrder(client_tag="pending", account_id=account_id, request={}, fingerprint=o.fingerprint(),
                            created_by=user, paper=paper, created_at=self._utcnow())
            db.add(row)
            db.flush()
            plan = (o.plan_id or "M")[:8]
            o = replace(o, tag=f"TDP-{plan}-{row.id}")
            row.client_tag = o.tag
            row.request = o.to_dict()
            row.kind = verdict["kind"]
            row.verdict = verdict
            row.charges = charges
            row.margin = {"required": margin.required, "available": margin.available} if margin else None
            row.max_loss = verdict.get("max_loss")
            row.fo_notional = verdict.get("value") if o.instrument_type in ("FUT", "CE", "PE") else None
            row.stage = "checked" if verdict["verdict"] == "PASS" else "blocked"
            row.confirm_text = describe(o, getattr(self.adapters[account_id], "name", account_id), paper,
                                        verdict, charges)
            audit.record(db, "orders", "ticket", {"tag": row.client_tag, "order": row.request,
                                                  "verdict": verdict["verdict"], "reasons": verdict["reasons"],
                                                  "paper": paper}, user)
            return row

    # ------------------------------------------------------------ gates 3 + 4
    def confirm_and_send(self, desk_id: int, fingerprint: str, typed: str, user: str | None = None) -> DeskOrder:
        with self.db_factory() as db, db.begin():
            row = db.get(DeskOrder, desk_id)
            if row is None:
                raise DeskError("confirm", f"no ticket {desk_id}")
            if row.stage != "checked":
                raise DeskError("confirm", f"ticket {row.client_tag} is {row.stage}; only a passed ticket can be sent")
            if fingerprint != row.fingerprint:
                raise DeskError("confirm", "the order changed after the risk check; build a new ticket")
            if self._utcnow() - row.created_at > CONFIRM_WINDOW:
                row.stage = "expired"
                raise DeskError("confirm", "the ticket is older than 5 minutes; build a new one")
            o = OrderRequest.from_dict(row.request)
            if typed.strip().upper() != confirm_phrase(o).upper():
                raise DeskError("confirm", f"type exactly: {confirm_phrase(o)}")
            # re-run the risk check now: prices, positions or today's count may have moved
            o2, adapter, paper, verdict, _, _ = self._assess(db, row.account_id, replace(o, tag=None))
            if paper != row.paper:
                raise DeskError("confirm", "paper/live mode changed since the ticket; build a new one")
            if verdict["verdict"] != "PASS":
                row.stage = "blocked"
                row.verdict = verdict
                audit.record(db, "orders", "blocked_at_confirm", {"tag": row.client_tag,
                                                                  "reasons": verdict["reasons"]}, user)
                return row
            audit.record(db, "orders", "confirm", {"tag": row.client_tag, "confirm_text": row.confirm_text,
                                                   "paper": paper}, user)
            self._send(db, row, adapter, o, user)
            return row

    def _send(self, db: Session, row: DeskOrder, adapter: BrokerAdapter, o: OrderRequest, user: str | None):
        attempts = 0
        while True:
            attempts += 1
            try:
                ack = adapter.place_order(o)
                row.broker_order_id = ack.order_id
                row.status = ack.status.value
                row.stage = "sent"
                row.sent_at = self._utcnow()
                audit.record(db, "orders", "sent", {"tag": row.client_tag, "broker_order_id": ack.order_id,
                                                    "attempts": attempts}, user)
                break
            except BrokerError as e:
                if e.kind == ErrorKind.NETWORK:
                    found = self._find_by_tag(adapter, o.tag)
                    if found:
                        row.broker_order_id = found.order_id
                        row.status = found.status.value
                        row.stage = "sent"
                        row.sent_at = self._utcnow()
                        audit.record(db, "orders", "sent", {"tag": row.client_tag, "broker_order_id": found.order_id,
                                                            "recovered_after_timeout": True}, user)
                        break
                    if attempts < 2:
                        continue
                row.stage = "failed"
                row.error = f"{e.kind.value}: {e.message}"
                audit.record(db, "orders", "send_failed", {"tag": row.client_tag, "error": row.error}, user)
                break
        if row.stage == "sent":
            self._sync_one(db, row, adapter)

    @staticmethod
    def _find_by_tag(adapter: BrokerAdapter, tag: str | None):
        if not tag:
            return None
        try:
            for x in adapter.orders():
                if x.tag == tag:
                    return x
        except BrokerError:
            return None
        return None

    # ------------------------------------------------------------ gate 5
    def sync(self, user: str | None = None) -> int:
        n = 0
        with self.db_factory() as db, db.begin():
            rows = db.execute(select(DeskOrder).where(DeskOrder.stage == "sent")).scalars().all()
            for row in rows:
                adapter, _ = self.broker_for(db, row.account_id)
                if self._sync_one(db, row, adapter):
                    n += 1
        return n

    def _sync_one(self, db: Session, row: DeskOrder, adapter: BrokerAdapter) -> bool:
        try:
            book = {x.order_id: x for x in adapter.orders()}
        except BrokerError:
            return False
        x = book.get(row.broker_order_id or "")
        if not x:
            return False
        changed = x.status.value != row.status
        row.status = x.status.value
        o = OrderRequest.from_dict(row.request)
        if (x.status == OrderStatus.COMPLETE and row.kind == "entry" and o.stop and o.target
                and not row.oco_gtt_id and Cap.OCO in adapter.capabilities):
            exit_side = Side.SELL if o.side == Side.BUY else Side.BUY
            fill = x.average_price or o.price or o.stop
            # the stop leg is a limit a band beyond the trigger, so a gap through the stop still fills
            band = o.stop * STOP_LEG_BAND
            stop_px = round_to_tick(o.stop - band if exit_side == Side.SELL else o.stop + band, o.tick_size,
                                    Side.BUY if exit_side == Side.SELL else Side.SELL)
            legs = [replace(o, side=exit_side, order_type=OrderType.LIMIT, price=stop_px, trigger_price=None,
                            tag=None, stop=None, target=None),
                    replace(o, side=exit_side, order_type=OrderType.LIMIT, price=o.target, trigger_price=None,
                            tag=None, stop=None, target=None)]
            try:
                ack = adapter.place_gtt(GttRequest(o.exchange, o.tradingsymbol, fill, [o.stop, o.target], legs,
                                                   kind="two-leg"))
                row.oco_gtt_id = ack.gtt_id
                audit.record(db, "orders", "oco_armed", {"tag": row.client_tag, "gtt_id": ack.gtt_id,
                                                         "stop": o.stop, "target": o.target})
            except BrokerError as e:
                audit.record(db, "orders", "oco_failed", {"tag": row.client_tag, "error": e.message})
        return changed

    # ------------------------------------------------------------ cancel / modify / flatten / kill
    def _row(self, db: Session, desk_id: int) -> DeskOrder:
        row = db.get(DeskOrder, desk_id)
        if row is None or not row.broker_order_id:
            raise DeskError("cancel", f"no sent order {desk_id}")
        return row

    def cancel(self, desk_id: int, user: str | None = None) -> DeskOrder:
        with self.db_factory() as db, db.begin():
            row = self._row(db, desk_id)
            adapter, _ = self.broker_for(db, row.account_id)
            adapter.cancel_order(row.broker_order_id)
            self._sync_one(db, row, adapter)
            audit.record(db, "orders", "cancel", {"tag": row.client_tag, "broker_order_id": row.broker_order_id}, user)
            return row

    def modify(self, desk_id: int, changes: OrderChange, user: str | None = None) -> DeskOrder:
        """Price / quantity / trigger changes. A modify that adds risk (bigger quantity) on an entry
        goes back through the risk check first."""
        with self.db_factory() as db, db.begin():
            row = self._row(db, desk_id)
            adapter, _ = self.broker_for(db, row.account_id)
            o = OrderRequest.from_dict(row.request)
            if changes.quantity and changes.quantity > o.quantity and row.kind == "entry":
                bigger = replace(o, quantity=changes.quantity, price=changes.price or o.price, tag=None)
                _, _, _, verdict, _, _ = self._assess(db, row.account_id, bigger)
                if verdict["verdict"] != "PASS":
                    raise DeskError("risk", "the larger order fails the risk check", {"reasons": verdict["reasons"]})
            adapter.modify_order(row.broker_order_id, changes)
            upd = {k: v for k, v in {"quantity": changes.quantity, "price": changes.price,
                                     "trigger_price": changes.trigger_price}.items() if v is not None}
            row.request = {**row.request, **upd}
            self._sync_one(db, row, adapter)
            audit.record(db, "orders", "modify", {"tag": row.client_tag, "changes": upd}, user)
            return row

    def cancel_all(self, account_id: str | None = None, symbol: str | None = None, user: str | None = None) -> int:
        n = 0
        with self.db_factory() as db, db.begin():
            q = select(DeskOrder).where(DeskOrder.stage == "sent")
            if account_id:
                q = q.where(DeskOrder.account_id == account_id)
            for row in db.execute(q).scalars().all():
                if symbol and row.request.get("tradingsymbol") != symbol.upper():
                    continue
                adapter, _ = self.broker_for(db, row.account_id)
                self._sync_one(db, row, adapter)
                if row.status in ("OPEN", "PENDING", "TRIGGER_PENDING"):
                    try:
                        adapter.cancel_order(row.broker_order_id)
                        self._sync_one(db, row, adapter)
                        n += 1
                    except BrokerError as e:
                        log.warning("cancel %s failed: %s", row.client_tag, e)
            audit.record(db, "orders", "cancel_all", {"account": account_id, "symbol": symbol, "cancelled": n}, user)
        return n

    def flatten(self, account_id: str, user: str | None = None) -> list[str]:
        """Square off every intraday / F&O position. Short legs first, long (hedge) legs last,
        so a spread is never left naked. These are exits, so the risk check never blocks them."""
        sent = []
        with self.db_factory() as db, db.begin():
            adapter, paper = self.broker_for(db, account_id)
            positions = [p for p in adapter.positions() if p.product in (Product.MIS, Product.NRML) and p.quantity]
            positions.sort(key=lambda p: p.quantity)   # negatives (shorts) first
            for p in positions:
                side = Side.BUY if p.quantity < 0 else Side.SELL
                opt = p.tradingsymbol.endswith(("CE", "PE"))
                o = OrderRequest(p.exchange, p.tradingsymbol, side, int(abs(p.quantity)), OrderType.MARKET, p.product,
                                 instrument_type="CE" if opt else ("FUT" if p.exchange in ("NFO", "BFO") else "EQ"))
                o, _ = self.normalise(o, p.last_price)
                row = DeskOrder(client_tag="pending", account_id=account_id, request={}, fingerprint=o.fingerprint(),
                                created_by=user, paper=paper, kind="exit", stage="checked",
                                confirm_text="flatten")
                db.add(row)
                db.flush()
                o = replace(o, tag=f"TDP-FLAT-{row.id}")
                row.client_tag = o.tag
                row.request = o.to_dict()
                self._send(db, row, adapter, o, user)
                sent.append(row.client_tag)
            audit.record(db, "orders", "flatten", {"account": account_id, "orders": sent}, user)
        return sent

    def kill(self, user: str | None = None) -> int:
        with self.db_factory() as db, db.begin():
            self._set_state(db, "kill_switch", {"date": self.clock().date().isoformat(), "by": user})
            audit.record(db, "orders", "kill_switch", {"date": self.clock().date().isoformat()}, user)
        return self.cancel_all(user=user)

    def release_kill(self, user: str | None = None) -> None:
        with self.db_factory() as db, db.begin():
            self._set_state(db, "kill_switch", {})
            audit.record(db, "orders", "kill_switch_released", {}, user)
