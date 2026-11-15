from datetime import datetime

from traderdesk.core.calendar import IST
from traderdesk.core.models import MarginQuote, OrderRequest, OrderType, Product, Side
from traderdesk.orders.risk import DayStats, RiskContext, check

from conftest import MARKET_HOURS

RULES = {"capital": 500000, "max_loss_per_trade": 2500, "max_daily_loss": 7500, "max_trades_per_day": 5,
         "max_orders_per_day": 20, "max_position_pct": 20, "min_reward_risk": 1.5, "require_stop": True,
         "fno_exposure_cap": 300000, "no_entries_before": "09:20", "no_entries_after": "15:10",
         "no_new_mis_after": "15:00", "expiry_day_no_new_fo_after": "13:30", "blocked": []}


def buy(**kw):
    d = dict(exchange="NSE", tradingsymbol="RELIANCE", side=Side.BUY, quantity=10, order_type=OrderType.LIMIT,
             product=Product.MIS, price=2950.0, stop=2900.0, target=3050.0)
    d.update(kw)
    return OrderRequest(**d)


def ctx(session, **kw):
    d = dict(ltp=2950.0, now=MARKET_HOURS, session=session)
    d.update(kw)
    return RiskContext(**d)


def test_clean_entry_passes(session):
    v = check(buy(), RULES, ctx(session))
    assert v.passed, v.reasons
    assert v.kind == "entry" and v.max_loss == 500 and v.reward_risk == 2.0


def test_missing_stop_blocks(session):
    v = check(buy(stop=None), RULES, ctx(session))
    assert not v.passed and any(r.startswith("stop:") for r in v.reasons)


def test_stop_on_wrong_side_blocks(session):
    v = check(buy(stop=3000.0), RULES, ctx(session))
    assert any("must be below" in r for r in v.reasons)


def test_max_loss_per_trade(session):
    v = check(buy(quantity=100), RULES, ctx(session))   # 100 x 50 = 5,000
    assert any(r.startswith("loss:") for r in v.reasons)


def test_reward_risk_minimum(session):
    v = check(buy(target=2960.0), RULES, ctx(session))
    assert any(r.startswith("reward:risk") for r in v.reasons)


def test_daily_loss_and_trade_count(session):
    v = check(buy(), RULES, ctx(session, today=DayStats(entries_placed=5, loss_at_risk=7200)))
    assert any(r.startswith("daily loss") for r in v.reasons)
    assert any(r.startswith("trades:") for r in v.reasons)


def test_position_size_cap(session):
    v = check(buy(quantity=40, product=Product.CNC, stop=2949.0, target=2952.0), {**RULES, "min_reward_risk": 1},
              ctx(session))   # 40 x 2950 = 1,18,000 = 23.6% of 5L
    assert any(r.startswith("position:") for r in v.reasons)


def test_window_before_open_and_after_cutoffs(session):
    early = datetime(2026, 11, 16, 9, 17, tzinfo=IST)
    assert any("before 09:20" in r for r in check(buy(), RULES, ctx(session, now=early)).reasons)
    late = datetime(2026, 11, 16, 15, 5, tzinfo=IST)
    assert any("no new MIS" in r for r in check(buy(), RULES, ctx(session, now=late)).reasons)
    closed = datetime(2026, 11, 15, 11, 0, tzinfo=IST)   # Sunday
    assert any("closed today" in r for r in check(buy(), RULES, ctx(session, now=closed)).reasons)


def test_holiday_and_uncovered_year_warning(session):
    session.holidays["2026-11-16"] = "Test holiday"
    v = check(buy(), RULES, ctx(session))
    assert any("holiday" in r for r in v.reasons)
    session.covered_years = set()
    v = check(buy(), RULES, ctx(session, now=datetime(2026, 11, 17, 10, 30, tzinfo=IST)))
    assert any("holiday list has no entries" in w for w in v.warnings)


def test_exit_is_never_blocked_by_entry_rules(session):
    sell = buy(side=Side.SELL, quantity=10, stop=None, target=None)
    v = check(sell, RULES, ctx(session, held_qty=10, now=datetime(2026, 11, 16, 15, 20, tzinfo=IST),
                               today=DayStats(entries_placed=99, orders_placed=99)))
    assert v.passed and v.kind == "exit"
    assert any(w.startswith("exit, not blocked") for w in v.warnings)


def test_delivery_short_sale_blocks(session):
    v = check(buy(side=Side.SELL, product=Product.CNC, stop=3000.0, target=2800.0), RULES, ctx(session, held_qty=0))
    assert any(r.startswith("short:") for r in v.reasons)


def test_intraday_short_allowed(session):
    v = check(buy(side=Side.SELL, stop=3000.0, target=2850.0), RULES, ctx(session, held_qty=0))
    assert v.passed, v.reasons


def test_fo_lot_size_cnc_and_option_market(session):
    o = OrderRequest("NFO", "NIFTY26NOV24000CE", Side.BUY, 70, OrderType.MARKET, Product.CNC,
                     instrument_type="CE", lot_size=75, stop=80, target=150)
    v = check(o, RULES, ctx(session, ltp=100))
    joined = " ".join(v.reasons)
    assert "lot size 75" in joined and "not CNC" in joined and "market orders on options" in joined


def test_expiry_day_cutoff(session):
    o = OrderRequest("NFO", "NIFTY26N1624000CE", Side.BUY, 75, OrderType.LIMIT, Product.NRML, price=100,
                     instrument_type="CE", lot_size=75, stop=80, target=150, expiry="2026-11-16")
    v = check(o, RULES, ctx(session, ltp=100, now=datetime(2026, 11, 16, 13, 45, tzinfo=IST)))
    assert any(r.startswith("expiry:") for r in v.reasons)


def test_margin_and_fo_exposure(session):
    o = OrderRequest("NFO", "NIFTY26NOVFUT", Side.BUY, 75, OrderType.LIMIT, Product.NRML, price=24000,
                     instrument_type="FUT", lot_size=75, stop=23990, target=24020)
    v = check(o, {**RULES, "max_loss_per_trade": 10000}, ctx(session, ltp=24000,
                                                              margin=MarginQuote(required=300000, available=100)))
    joined = " ".join(v.reasons)
    assert "F&O exposure" in joined and "margin:" in joined


def test_blocked_list_and_tick_size(session):
    v = check(buy(price=2950.03), {**RULES, "blocked": ["RELIANCE"]}, ctx(session))
    joined = " ".join(v.reasons)
    assert "blocked list" in joined and "tick size" in joined


def test_fingerprint_changes_with_order():
    assert buy().fingerprint() != buy(quantity=11).fingerprint()
    assert buy().fingerprint() == buy().fingerprint()
