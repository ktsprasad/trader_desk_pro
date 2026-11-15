"""Import the dated Kite snapshots from D:\\code\\kite\\portfolio_data\\portfolio_*.json as history.
Idempotent: a snapshot already imported (same file name + timestamp) is skipped."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Snapshot, SnapshotHolding
from ..plugins.kite.adapter import holding_from_kite, mf_from_kite

NAME = re.compile(r"portfolio_(\d{8})_(\d{6})\.json$")


def _taken_at(path: Path, doc: dict) -> datetime | None:
    ts = doc.get("timestamp")
    if ts:
        try:
            return datetime.fromisoformat(str(ts)).replace(tzinfo=None, microsecond=0)
        except ValueError:
            pass
    m = NAME.search(path.name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    return None


def import_snapshots(db: Session, folder: Path | str, account_id: str = "kite") -> dict:
    folder = Path(folder)
    done = skipped = failed = 0
    errors = []
    for p in sorted(folder.glob("portfolio_*.json")):
        if p.name == "portfolio_latest.json":
            continue   # a copy of the newest dated file
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            failed += 1
            errors.append(f"{p.name}: {e}")
            continue
        if not isinstance(doc, dict) or "holdings" not in doc:
            failed += 1
            errors.append(f"{p.name}: no holdings list")
            continue
        taken = _taken_at(p, doc)
        if taken is None:
            failed += 1
            errors.append(f"{p.name}: no timestamp")
            continue
        source = f"kite-snapshot:{p.name}"
        exists = db.execute(select(Snapshot).where(Snapshot.source == source, Snapshot.taken_at == taken)
                            ).scalar_one_or_none()
        if exists:
            skipped += 1
            continue
        holdings = [holding_from_kite(account_id, h) for h in doc.get("holdings") or []]
        holdings += [mf_from_kite(account_id, m) for m in doc.get("mutual_funds") or []]
        snap = Snapshot(taken_at=taken, source=source, account_id=account_id,
                        invested=round(sum(h.invested for h in holdings), 2),
                        value=round(sum(h.value for h in holdings), 2),
                        raw={"gtt_orders": doc.get("gtt_orders") or [], "mf_sips": doc.get("mf_sips") or []})
        db.add(snap)
        db.flush()
        for h in holdings:
            db.add(SnapshotHolding(snapshot_id=snap.id, exchange=h.exchange, tradingsymbol=h.tradingsymbol,
                                   isin=h.isin, name=h.name, asset_class=h.asset_class, quantity=h.quantity,
                                   average_price=h.average_price, last_price=h.last_price))
        done += 1
    return {"imported": done, "skipped": skipped, "failed": failed, "errors": errors}


def latest_snapshot(db: Session, account_id: str | None = None) -> Snapshot | None:
    q = select(Snapshot).order_by(Snapshot.taken_at.desc()).limit(1)
    if account_id:
        q = q.where(Snapshot.account_id == account_id)
    return db.execute(q).scalar_one_or_none()
