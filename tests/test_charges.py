from datetime import date

import pytest

from traderdesk.core.models import OrderRequest, OrderType, Product, Segment, Side
from traderdesk.orders.charges import ChargesEngine, round_to_lot, round_to_tick

E = ChargesEngine()


def test_delivery_buy_has_no_brokerage_and_full_stt_and_stamp():
    c = E.compute("kite", Side.BUY, Segment.EQ_DELIVERY, "NSE", 1000, 100, date(2026, 11, 16))
    assert c.brokerage == 0
    assert c.stt == 100          # 0.1% of 1,00,000
    assert c.stamp == 15         # 0.015%
    assert c.dp == 0             # DP only on sells
    assert c.exchange_txn == pytest.approx(2.97)
    assert c.gst == pytest.approx((2.97 + 0.1) * 0.18, abs=0.01)


def test_delivery_sell_charges_dp_once():
    c = E.compute("kite", Side.SELL, Segment.EQ_DELIVERY, "NSE", 1000, 100, date(2026, 11, 16))
    assert c.dp == 15.34 and c.stamp == 0 and c.stt == 100


def test_intraday_brokerage_is_lower_of_pct_and_20():
    small = E.compute("kite", Side.BUY, Segment.EQ_INTRADAY, "NSE", 100, 100, date(2026, 11, 16))
    big = E.compute("kite", Side.BUY, Segment.EQ_INTRADAY, "NSE", 1000, 1000, date(2026, 11, 16))
    assert small.brokerage == pytest.approx(3.0)   # 0.03% of 10,000
    assert big.brokerage == 20


def test_stt_on_options_changes_on_1_april_2026():
    before = E.compute("kite", Side.SELL, Segment.OPTIONS, "NFO", 100, 1000, date(2026, 3, 31))
    after = E.compute("kite", Side.SELL, Segment.OPTIONS, "NFO", 100, 1000, date(2026, 4, 1))
    assert before.stt == 100   # 0.1% of premium turnover 1,00,000
    assert after.stt == 150    # 0.15%
    assert after.brokerage == 20


def test_round_trip_breakeven_is_above_entry_for_a_buy():
    o = OrderRequest("NSE", "INFY", Side.BUY, 10, OrderType.LIMIT, Product.MIS, price=1500)
    rt = E.round_trip("kite", o, 1500, 1530, date(2026, 11, 16))
    assert rt["breakeven"] > 1500
    assert rt["total"] == pytest.approx(rt["entry"]["total"] + rt["exit"]["total"], abs=0.02)


def test_indstocks_flat_fee_and_unknown_broker_falls_back():
    c = E.compute("indstocks", Side.BUY, Segment.EQ_DELIVERY, "NSE", 100, 10, date(2026, 11, 16))
    assert c.brokerage == 5
    assert E.compute("nope", Side.BUY, Segment.EQ_DELIVERY, "NSE", 100, 10, date(2026, 11, 16)).brokerage == 0


def test_tick_and_lot_rounding_never_worsens_price():
    assert round_to_tick(100.03, 0.05, Side.BUY) == 100.0
    assert round_to_tick(100.03, 0.05, Side.SELL) == 100.05
    assert round_to_tick(100.05, 0.05, Side.BUY) == 100.05
    assert round_to_lot(130, 75) == 75
    assert round_to_lot(10, 1) == 10
