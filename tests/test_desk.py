from datetime import timedelta

import pytest
from sqlalchemy import select

from traderdesk.core import audit
from traderdesk.core.errors import DeskError
from traderdesk.core.models import OrderChange, OrderRequest, OrderType, Product, Side
from traderdesk.db.models import DeskOrder

GOOD = dict(exchange="NSE", tradingsymbol="RELIANCE", side=Side.BUY, quantity=10, order_type=OrderType.LIMIT,
            product=Product.MIS, price=2950.0, stop=2900.0, target=3050.0)


def order(**kw):
    return OrderRequest(**{**GOOD, **kw})


def send(desk, o=None, user="me"):
    t = desk.ticket("paper", o or order(), user)
    assert t.stage == "checked", t.verdict["reasons"]
    return desk.confirm_and_send(t.id, t.fingerprint, f"{t.request['side']} {t.request['quantity']} "
                                 f"{t.request['tradingsymbol']}", user)


def test_ticket_has_tag_charges_margin_and_plain_words(desk):
    t = desk.ticket("paper", order(plan_id="orb1"), "me")
    assert t.client_tag == f"TDP-orb1-{t.id}"
    assert len(t.client_tag) <= 20
    assert t.charges["total"] > 0 and t.charges["breakeven"] > 2950
    assert t.margin["required"] == pytest.approx(2950 * 10 * 0.2)
    assert "BUY 10 RELIANCE" in t.confirm_text and "[PAPER]" in t.confirm_text


def test_block_cannot_be_sent(desk):
    t = desk.ticket("paper", order(stop=None), "me")
    assert t.stage == "blocked"
    with pytest.raises(DeskError) as e:
        desk.confirm_and_send(t.id, t.fingerprint, "BUY 10 RELIANCE")
    assert e.value.gate == "confirm"


def test_confirm_needs_exact_phrase_and_fingerprint(desk):
    t = desk.ticket("paper", order(), "me")
    with pytest.raises(DeskError, match="type exactly"):
        desk.confirm_and_send(t.id, t.fingerprint, "yes")
    with pytest.raises(DeskError, match="changed"):
        desk.confirm_and_send(t.id, "0" * 16, "BUY 10 RELIANCE")


def test_ticket_expires(desk, Db):
    t = desk.ticket("paper", order(), "me")
    with Db() as db, db.begin():
        db.get(DeskOrder, t.id).created_at -= timedelta(minutes=6)
    with pytest.raises(DeskError, match="older than 5 minutes"):
        desk.confirm_and_send(t.id, t.fingerprint, "BUY 10 RELIANCE")


def test_send_fill_arms_oco_and_stop_fires(desk, paper):
    r = send(desk, order(price=2960.0))   # limit above market fills at once in the simulator
    assert r.stage == "sent" and r.status == "COMPLETE"
    assert r.oco_gtt_id and r.oco_gtt_id in paper.gtts()
    paper.set_price("NSE:RELIANCE", 2899.0)   # stop hit
    assert not paper.gtts()
    pos = {p.tradingsymbol: p.quantity for p in paper.positions()}
    assert pos.get("RELIANCE", 0) == 0


def test_timeout_after_send_is_recovered_without_a_duplicate(desk, paper):
    paper.fail_next_after_send = True
    r = send(desk)
    assert r.stage == "sent"
    assert len([o for o in paper.orders() if o.tag == r.client_tag]) == 1


def test_timeout_before_send_retries_once(desk, paper):
    paper.fail_next_before_send = True
    r = send(desk)
    assert r.stage == "sent" and len(paper.orders()) == 1


def test_daily_trade_limit_counts_sent_orders(desk):
    with desk.db_factory() as db, db.begin():
        desk.update_rules(db, {"max_trades_per_day": 1}, "me")
    send(desk, order(tradingsymbol="RELIANCE", price=2940.0))
    t = desk.ticket("paper", order(tradingsymbol="INFY", price=1490.0, stop=1480.0, target=1520.0), "me")
    assert t.stage == "blocked" and any(r.startswith("trades:") for r in t.verdict["reasons"])


def test_recheck_at_confirm_blocks_if_rules_tightened(desk):
    t = desk.ticket("paper", order(), "me")
    with desk.db_factory() as db, db.begin():
        desk.update_rules(db, {"max_loss_per_trade": 100}, "me")
    r = desk.confirm_and_send(t.id, t.fingerprint, "BUY 10 RELIANCE")
    assert r.stage == "blocked"


def test_cancel_modify_and_cancel_all(desk, paper):
    r = send(desk, order(price=2940.0))   # rests below market
    assert r.status == "OPEN"
    m = desk.modify(r.id, OrderChange(price=2945.0))
    assert m.request["price"] == 2945.0
    c = desk.cancel(r.id)
    assert c.status == "CANCELLED"
    send(desk, order(price=2930.0))
    send(desk, order(tradingsymbol="INFY", price=1490.0, stop=1480.0, target=1520.0))
    assert desk.cancel_all(symbol="INFY") == 1
    assert desk.cancel_all() == 1


def test_bigger_modify_goes_through_risk(desk):
    r = send(desk, order(price=2940.0))
    with pytest.raises(DeskError):
        desk.modify(r.id, OrderChange(quantity=500))


def test_kill_switch_cancels_and_blocks_entries_but_not_exits(desk, paper):
    send(desk, order(price=2940.0))
    assert desk.kill("me") == 1
    t = desk.ticket("paper", order(price=2940.0), "me")
    assert t.stage == "blocked" and any("kill switch" in r for r in t.verdict["reasons"])
    desk.release_kill("me")
    assert desk.ticket("paper", order(price=2940.0), "me").stage == "checked"


def test_flatten_closes_shorts_first(desk, paper):
    paper.set_price("NFO:NIFTY26NOV24000CE", 100.0)
    paper.set_price("NFO:NIFTY26NOV24200CE", 40.0)
    from traderdesk.core.models import OrderRequest as R
    paper.place_order(R("NFO", "NIFTY26NOV24000CE", Side.BUY, 75, OrderType.MARKET, Product.NRML, instrument_type="CE"))
    paper.place_order(R("NFO", "NIFTY26NOV24200CE", Side.SELL, 75, OrderType.MARKET, Product.NRML, instrument_type="CE"))
    tags = desk.flatten("paper", "me")
    assert len(tags) == 2
    with desk.db_factory() as db:
        rows = [db.execute(select(DeskOrder).where(DeskOrder.client_tag == t)).scalar_one() for t in tags]
    assert rows[0].request["tradingsymbol"] == "NIFTY26NOV24200CE" and rows[0].request["side"] == "BUY"
    assert all(r.request["order_type"] == "LIMIT" for r in rows)   # option market -> protected limit
    assert not [p for p in paper.positions() if p.quantity]


def test_option_market_ticket_becomes_protected_limit(desk, paper):
    paper.set_price("NFO:NIFTY26NOV24000CE", 100.0)
    o = OrderRequest("NFO", "NIFTY26NOV24000CE", Side.BUY, 80, OrderType.MARKET, Product.NRML,
                     instrument_type="CE", lot_size=75, stop=80.0, target=150.0)
    t = desk.ticket("paper", o, "me")
    assert t.request["quantity"] == 75 and t.request["order_type"] == "LIMIT" and t.request["price"] == 105.0
    assert t.stage == "checked", t.verdict["reasons"]


def test_every_step_is_in_the_audit_chain(desk):
    send(desk)
    with desk.db_factory() as db:
        assert audit.verify(db)["ok"]
        actions = [a.action for a in db.execute(select(audit.AuditLog)).scalars()]
    for a in ("ticket", "confirm", "sent", "oco_armed"):
        assert a in actions


def test_paper_amo_waits_for_the_open(desk, paper):
    r = send(desk, order(product=Product.CNC, price=2960.0, amo=True))
    assert r.stage == "sent" and r.status == "OPEN"
    paper.open_session()
    desk.sync()
    assert paper.orders()[0].status.value == "COMPLETE"


def test_live_mode_without_live_account_stays_paper(desk, Db):
    with Db() as db, db.begin():
        desk.set_mode(db, "live", "me")
    t = desk.ticket("paper", order(), "me")
    assert t.paper
