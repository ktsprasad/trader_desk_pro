import json

from sqlalchemy import select, update

from traderdesk.core import audit
from traderdesk.db.models import AuditLog, Snapshot, SnapshotHolding
from traderdesk.portfolio.importer import import_snapshots
from traderdesk.portfolio.networth import summarise
from traderdesk.core.models import Holding


def test_hash_chain_detects_edits(Db):
    with Db() as db, db.begin():
        for i in range(3):
            audit.record(db, "orders", "x", {"i": i}, "me")
    with Db() as db:
        assert audit.verify(db) == {"ok": True, "rows": 3, "broken_at": None}
    with Db() as db, db.begin():
        db.execute(update(AuditLog).where(AuditLog.id == 2).values(detail={"i": 99}))
    with Db() as db:
        res = audit.verify(db)
    assert not res["ok"] and res["broken_at"] == 2


def _snap(path, ts, qty):
    path.write_text(json.dumps({
        "timestamp": ts,
        "holdings": [{"tradingsymbol": "BSE", "exchange": "NSE", "isin": "INE118H01025", "quantity": qty,
                      "t1_quantity": 0, "average_price": 3247.14, "last_price": 3909.1}],
        "positions": {"net": [], "day": []},
        "mutual_funds": [{"tradingsymbol": "INF879O01027", "fund": "PPFAS", "quantity": 10, "average_price": 90,
                          "last_price": 94}],
        "gtt_orders": [], "mf_sips": [],
    }), encoding="utf-8")


def test_import_is_idempotent_and_skips_latest(Db, tmp_path):
    _snap(tmp_path / "portfolio_20251212_121212.json", "2025-12-12T12:12:12", 5)
    _snap(tmp_path / "portfolio_20261113_002003.json", "2026-11-13T00:20:01.82", 6)
    _snap(tmp_path / "portfolio_latest.json", "2026-11-13T00:20:01.82", 6)
    (tmp_path / "portfolio_bad.json").write_text("{nope", encoding="utf-8")
    with Db() as db, db.begin():
        r = import_snapshots(db, tmp_path)
    assert r["imported"] == 2 and r["failed"] == 1
    with Db() as db, db.begin():
        assert import_snapshots(db, tmp_path)["skipped"] == 2
    with Db() as db:
        snaps = list(db.execute(select(Snapshot).order_by(Snapshot.taken_at)).scalars())
        assert len(snaps) == 2 and snaps[1].value == round(6 * 3909.1 + 940, 2)
        assert db.execute(select(SnapshotHolding)).scalars().all().__len__() == 4


def test_networth_by_class_and_fx():
    hs = [Holding("kite", "NSE", "A", 10, 100, 110, close_price=105),
          Holding("kite", "MF", "B", 1, 1000, 900, asset_class="mutual_fund"),
          Holding("indm", "NASDAQ", "AAPL", 1, 100, 200, asset_class="us_stock", currency="USD"),
          Holding("x", "X", "Y", 1, 1, 1, currency="EUR")]
    s = summarise(hs, {"USD": 85.0})
    assert s["invested"] == 1000 + 1000 + 8500
    assert s["value"] == 1100 + 900 + 17000
    assert s["by_asset_class"]["us_stock"]["value"] == 17000
    assert s["day_change"] == 50
    assert s["missing_fx"] == ["EUR"]
    assert s["holdings"][0]["symbol"] == "AAPL"
