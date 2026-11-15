"""Append-only, hash-chained audit log."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AuditLog, utcnow

GENESIS = "0" * 64


def _digest(prev: str, ts: str, username: str | None, module: str, action: str, detail: dict) -> str:
    blob = json.dumps([prev, ts, username, module, action, detail], sort_keys=True, default=str,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def record(db: Session, module: str, action: str, detail: dict | None = None, username: str | None = None) -> AuditLog:
    detail = json.loads(json.dumps(detail or {}, default=str))
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else GENESIS
    ts = utcnow()
    row = AuditLog(ts=ts, username=username, module=module, action=action, detail=detail, prev_hash=prev,
                   hash=_digest(prev, ts.isoformat(), username, module, action, detail))
    db.add(row)
    db.flush()
    return row


def verify(db: Session) -> dict:
    """Walk the chain. Returns {"ok": bool, "rows": n, "broken_at": id|None}."""
    prev = GENESIS
    n = 0
    for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        n += 1
        if row.prev_hash != prev or row.hash != _digest(prev, row.ts.isoformat(), row.username, row.module,
                                                        row.action, row.detail):
            return {"ok": False, "rows": n, "broken_at": row.id}
        prev = row.hash
    return {"ok": True, "rows": n, "broken_at": None}
