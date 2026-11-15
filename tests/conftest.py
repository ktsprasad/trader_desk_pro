from datetime import datetime

import pytest

from traderdesk.core.calendar import IST, Session, nse_session
from traderdesk.db.models import Account, make_session_factory
from traderdesk.orders.desk import OrderDesk
from traderdesk.plugins.paper.adapter import PaperBroker

# A Monday in a normal session, 10:30 IST
MARKET_HOURS = datetime(2026, 11, 16, 10, 30, tzinfo=IST)


@pytest.fixture
def session() -> Session:
    s = nse_session()
    s.covered_years = {2026}
    return s


@pytest.fixture
def Db(tmp_path):
    return make_session_factory(f"sqlite:///{(tmp_path / 't.db').as_posix()}")


@pytest.fixture
def paper():
    p = PaperBroker("paper", {"starting_cash": 1_000_000})
    p.set_price("NSE:RELIANCE", 2950.0)
    p.set_price("NSE:INFY", 1500.0)
    return p


@pytest.fixture
def clock():
    box = {"now": MARKET_HOURS}
    f = lambda: box["now"]  # noqa: E731
    f.box = box
    return f


@pytest.fixture
def desk(Db, paper, session, clock):
    with Db() as db, db.begin():
        db.add(Account(id="paper", plugin="paper", name="Paper"))
    return OrderDesk(Db, {"paper": paper}, session=session, clock=clock)
