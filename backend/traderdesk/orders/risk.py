"""Pre-trade risk check for Indian markets (INR, NSE session, lot sizes, F&O).

Extends the rules of trader-desk/day-trader-skill/trader-risk/check.py:

  * an ENTRY opens or adds to a position; an EXIT only reduces one. Getting out must always
    be possible, so for an exit every rule except format and short-selling becomes a warning;
  * every entry is checked for: blocked list, F&O allowed, stop on the right side, max loss
    per trade (INR at the stop), reward:risk, daily loss, trades and orders per day, position
    size % of capital, F&O notional cap, margin, entry window, no new MIS after a cut-off and
    expiry-day rules.

Pure function: everything it needs is passed in, so it is fully testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import yaml

from ..core.calendar import Session, nse_session
from ..core.models import MarginQuote, OrderRequest, OrderType, Product, Side

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "config" / "risk.yaml"


def load_default_rules() -> dict:
    return yaml.safe_load(DEFAULT_RULES.read_text(encoding="utf-8"))


@dataclass
class DayStats:
    orders_placed: int = 0
    entries_placed: int = 0
    loss_at_risk: float = 0.0      # sum of max losses of today's entries
    fo_notional: float = 0.0


@dataclass
class RiskContext:
    ltp: float | None               # latest price (premium for options)
    held_qty: float = 0.0           # signed: holdings + net positions for this instrument
    held_value: float = 0.0
    today: DayStats = field(default_factory=DayStats)
    margin: MarginQuote | None = None
    now: datetime | None = None     # aware datetime; default: now in the session's tz
    session: Session | None = None


@dataclass
class RiskVerdict:
    verdict: str                    # PASS | BLOCK
    kind: str                       # entry | exit
    reasons: list[str]
    warnings: list[str]
    info: list[str]
    entry_ref: float | None
    max_loss: float | None
    reward_risk: float | None
    value: float | None
    fingerprint: str

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("verdict", "kind", "reasons", "warnings", "info", "entry_ref",
                                              "max_loss", "reward_risk", "value", "fingerprint")}


def _t(v) -> time | None:
    if not v:
        return None
    h, m = str(v).split(":")
    return time(int(h), int(m))


def inr(x: float) -> str:
    return "₹{:,.0f}".format(x) if abs(x - round(x)) < 0.005 else "₹{:,.2f}".format(x)


def check(o: OrderRequest, rules: dict, ctx: RiskContext) -> RiskVerdict:
    r = dict(load_default_rules())
    r.update(rules or {})
    session = ctx.session or nse_session()
    now = (ctx.now or session.now()).astimezone(session.tz)
    reasons: list[str] = []
    warns: list[str] = []
    info: list[str] = []
    later: list[str] = []          # entry-only: become warnings for an exit
    long = o.side == Side.BUY
    is_fo = o.instrument_type in ("FUT", "CE", "PE")

    # ---- format
    if o.quantity <= 0:
        reasons.append("format: quantity must be positive")
    if o.lot_size > 1 and o.quantity % o.lot_size:
        reasons.append(f"format: quantity {o.quantity} is not a multiple of the lot size {o.lot_size}")
    if o.order_type in (OrderType.LIMIT, OrderType.SL) and not o.price:
        reasons.append(f"format: a {o.order_type.value} order needs a price")
    if o.order_type in (OrderType.SL, OrderType.SL_M) and not o.trigger_price:
        reasons.append(f"format: a {o.order_type.value} order needs a trigger price")
    for name, v in (("price", o.price), ("trigger price", o.trigger_price)):
        if v is not None and o.tick_size and abs(round(v / o.tick_size) * o.tick_size - v) > 1e-6:
            reasons.append(f"format: {name} {v} is not a multiple of the tick size {o.tick_size}")
    if is_fo and o.product == Product.CNC:
        reasons.append("format: F&O orders use NRML or MIS, not CNC")
    if o.order_type == OrderType.MARKET and o.instrument_type in ("CE", "PE"):
        reasons.append("format: market orders on options are not sent; use a limit with a protection band")

    # ---- reference price
    ref = None
    if o.order_type in (OrderType.LIMIT, OrderType.SL):
        ref = o.price
    elif o.order_type == OrderType.SL_M:
        ref = o.trigger_price
    if ref is None:
        ref = ctx.ltp
    elif ctx.ltp and o.order_type == OrderType.LIMIT and long and ctx.ltp < ref:
        info.append(f"limit {ref} is above the last price {ctx.ltp}: it will fill at market")
    value = ref * o.quantity if ref else None

    # ---- entry or exit
    held = ctx.held_qty
    exiting = (not long and held > 0 and o.quantity <= held + 1e-9) or (long and held < 0 and o.quantity <= -held + 1e-9)
    kind = "exit" if exiting else "entry"

    if not long and kind == "entry":
        if o.product == Product.CNC:
            reasons.append(f"short: selling {o.quantity} {o.tradingsymbol} but you hold {held:g}; "
                           "delivery short sales are not allowed")
        elif not is_fo and not r.get("allow_short_intraday", True):
            later.append("short: intraday short selling is off (allow_short_intraday: false)")

    if ref is None and kind == "entry":
        later.append(f"price: no price for {o.tradingsymbol} to size the order")

    # ---- entry rules
    if o.tradingsymbol in {str(b).upper() for b in r.get("blocked") or []}:
        later.append(f"blocked: {o.tradingsymbol} is on your blocked list")
    if is_fo and not r.get("allow_fo", True):
        later.append("asset: F&O is off (allow_fo: false)")

    max_loss = rr = None
    stop, target = o.stop, o.target
    if kind == "entry":
        if not stop:
            if r.get("require_stop", True):
                later.append("stop: no stop. Every entry needs one (require_stop: true)")
            else:
                warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
        elif ref:
            if (long and stop >= ref) or (not long and stop <= ref):
                later.append(f"stop: the stop {stop} must be {'below' if long else 'above'} the entry {ref}")
            else:
                risk_pu = abs(ref - stop)
                max_loss = round(risk_pu * o.quantity, 2)
                cap = r.get("max_loss_per_trade")
                if cap and max_loss > cap + 0.005:
                    later.append(f"loss: {o.quantity} x {risk_pu:.2f} = {inr(max_loss)} at the stop, over your "
                                 f"{inr(cap)} max loss per trade")
                if target:
                    if (long and target <= ref) or (not long and target >= ref):
                        later.append(f"target: the target {target} must be {'above' if long else 'below'} the entry {ref}")
                    else:
                        rr = round(abs(target - ref) / risk_pu, 2)
                        mn = r.get("min_reward_risk")
                        if mn and rr < mn - 1e-9:
                            later.append(f"reward:risk: {rr:.2f} is under your minimum {mn}")
                elif r.get("min_reward_risk"):
                    later.append("reward:risk: no target to measure it against")
                info.append(f"entry {ref}, stop {stop}, target {target or 'none'}: max loss {inr(max_loss)}"
                            + (f", reward:risk {rr:.2f}" if rr is not None else ""))
        daily = r.get("max_daily_loss")
        if daily and max_loss is not None and ctx.today.loss_at_risk + max_loss > daily + 0.005:
            later.append(f"daily loss: {inr(ctx.today.loss_at_risk)} already at risk today + {inr(max_loss)} "
                         f"= {inr(ctx.today.loss_at_risk + max_loss)}, over your {inr(daily)} daily limit")
        cap_t = r.get("max_trades_per_day")
        if cap_t and ctx.today.entries_placed >= cap_t:
            later.append(f"trades: {ctx.today.entries_placed} entries today, your limit is {cap_t}")
        cap_pct = r.get("max_position_pct")
        capital = r.get("capital")
        if cap_pct and capital and value is not None and not is_fo:
            after = ctx.held_value + value
            if after > capital * cap_pct / 100 + 0.005:
                later.append(f"position: {o.tradingsymbol} would be {inr(after)}, "
                             f"{100 * after / capital:.1f}% of {inr(capital)}, over your {cap_pct}% cap")
        if is_fo and value is not None:
            notional = value   # futures: contract value; options: premium paid or received
            fcap = r.get("fno_exposure_cap")
            if fcap and ctx.today.fo_notional + notional > fcap + 0.005:
                later.append(f"F&O exposure: {inr(ctx.today.fo_notional + notional)} would be over your "
                             f"{inr(fcap)} cap")
        if ctx.margin is not None and ctx.margin.sufficient is False:
            later.append(f"margin: needs {inr(ctx.margin.required)}, available {inr(ctx.margin.available or 0)}")

        # ---- time rules (IST)
        ok, why = session.is_trading_day(now.date())
        w = session.calendar_warning(now.date())
        if w:
            warns.append(w)
        t = now.time().replace(second=0, microsecond=0)
        if not o.amo:
            if not ok:
                later.append(f"window: the market is closed today ({why})")
            elif not (session.open <= now.time() < session.close):
                later.append(f"window: the market is closed ({now:%a %H:%M} IST; session "
                             f"{session.open:%H:%M}-{session.close:%H:%M})")
            else:
                b, a = _t(r.get("no_entries_before")), _t(r.get("no_entries_after"))
                if b and t < b:
                    later.append(f"window: no new entries before {b:%H:%M} IST (it is {t:%H:%M})")
                if a and t > a:
                    later.append(f"window: no new entries after {a:%H:%M} IST (it is {t:%H:%M})")
                mis = _t(r.get("no_new_mis_after"))
                if mis and o.product == Product.MIS and t >= mis:
                    later.append(f"window: no new MIS after {mis:%H:%M} IST")
                exp = _t(r.get("expiry_day_no_new_fo_after"))
                if exp and is_fo and o.expiry == now.date().isoformat() and t >= exp:
                    later.append(f"expiry: no new positions in contracts expiring today after {exp:%H:%M} IST")
        else:
            warns.append("AMO: placed after hours, goes to the exchange at the next open")

    cap_n = r.get("max_orders_per_day")
    if cap_n and ctx.today.orders_placed >= cap_n:
        later.append(f"daily limit: {ctx.today.orders_placed} orders today, your limit is {cap_n}")

    if kind == "entry":
        reasons.extend(later)
    else:
        warns.extend("exit, not blocked: " + x for x in later)

    return RiskVerdict("BLOCK" if reasons else "PASS", kind, reasons, warns, info, ref, max_loss, rr,
                       round(value, 2) if value is not None else None, o.fingerprint())
