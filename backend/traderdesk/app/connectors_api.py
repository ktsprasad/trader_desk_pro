"""Connector actions: OAuth callbacks other than Kite, TOTP token logins, file uploads (CSV, CAS),
AMFI NAV refresh, the read-only email source (scan, import alerts, import attachments), and
disconnecting an account."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core import audit
from ..core.auth import allowed
from ..core.errors import BrokerError
from ..core.secrets import secret_key
from ..db.models import Account, AppState, EmailSeen, Expense, Snapshot, SnapshotHolding

MAX_UPLOAD = 15 * 1024 * 1024
EMAIL_CATEGORIES = {"Rent and home", "Food and groceries", "Food delivery", "Shopping", "Utilities", "Fuel and travel",
                    "Subscriptions", "Health", "Education", "Investing", "Income", "Other"}


class EmailScan(BaseModel):
    days: int | None = Field(default=None, ge=1, le=400)


class EmailImport(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=500)
    categories: dict[str, str] = {}


class EmailAttachment(BaseModel):
    key: str = Field(max_length=80)
    target: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=64)


class TotpLogin(BaseModel):
    pin: str = Field(min_length=4, max_length=12)
    totp: str = Field(min_length=6, max_length=8)


def register(app, c) -> None:
    Db, need, adapters, secrets = c.Db, c.need, c.adapters, c.secrets

    def _acct(db, account_id):
        a = db.get(Account, account_id)
        if not a or account_id not in adapters:
            raise HTTPException(404, "no such connected account")
        return a

    def save_snapshot(acct, rows, filename: str, username: str, via: str | None = None) -> dict:
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        with Db() as db, db.begin():
            snap = Snapshot(taken_at=now, source=f"upload:{acct.id}:{now.isoformat()}", account_id=acct.id,
                            invested=sum(r["quantity"] * r["average_price"] for r in rows),
                            value=sum(r["quantity"] * r["last_price"] for r in rows),
                            raw={"file": filename[:120], "rows": len(rows), "plugin": acct.plugin, "via": via})
            db.add(snap)
            db.flush()
            for r in rows:
                db.add(SnapshotHolding(snapshot_id=snap.id, exchange=r["exchange"][:16], tradingsymbol=r["tradingsymbol"],
                                       isin=r.get("isin"), name=r.get("name"), asset_class=r["asset_class"],
                                       quantity=r["quantity"], average_price=r["average_price"], last_price=r["last_price"]))
            audit.record(db, "portfolio", "upload", {"account": acct.id, "rows": len(rows), "file": filename[:120],
                                                     "via": via}, username)
            return {"rows": len(rows), "invested": round(snap.invested, 2), "value": round(snap.value, 2), "snapshot": snap.id}

    @app.get("/api/brokers/upstox/callback")
    def upstox_callback(code: str, request: Request, state: str | None = None):
        info = c.sessions.get(request.cookies.get("tdp_session"))
        if info is None or not allowed(info.role, "brokers:manage"):
            raise HTTPException(401, "sign in as the owner, then log in to Upstox again")
        with Db() as db:
            ids = [a.id for a in db.execute(select(Account).where(Account.plugin == "upstox")).scalars()]
        acct = state if state in ids else (ids[0] if ids else None)
        if not acct or acct not in adapters:
            raise HTTPException(404, "no Upstox account")
        try:
            who = adapters[acct].complete_login(code)
        except BrokerError as e:
            with Db() as db, db.begin():
                audit.record(db, "brokers", "upstox_login_failed", {"account": acct, "error": e.message[:200]}, info.username)
            return RedirectResponse(f"/?kite_error={quote('Upstox: ' + e.message)}")
        with Db() as db, db.begin():
            audit.record(db, "brokers", "upstox_login", {"account": acct, "user_id": who.get("user_id")}, info.username)
        return RedirectResponse("/?kite=connected")

    @app.post("/api/accounts/{account_id}/totp-login")
    def totp_login(account_id: str, body: TotpLogin, info=Depends(need("brokers:manage"))):
        """Generate the day's token with PIN + TOTP (Dhan, INDstocks). Neither is stored."""
        with Db() as db:
            _acct(db, account_id)
        a = adapters[account_id]
        if not hasattr(a, "login_with_totp"):
            raise HTTPException(400, "this connection has no PIN + TOTP login")
        try:
            a.login_with_totp(body.pin, body.totp)
        except BrokerError as e:
            with Db() as db, db.begin():
                audit.record(db, "brokers", "token_login_failed", {"account": account_id, "error": e.message[:200]}, info.username)
            raise HTTPException(401, e.message)
        with Db() as db, db.begin():
            audit.record(db, "brokers", "token_login", {"account": account_id}, info.username)
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/upload")
    async def upload(account_id: str, file: UploadFile = File(...), password: str = Form(""),
                     info=Depends(need("brokers:manage"))):
        with Db() as db:
            acct = _acct(db, account_id)
        a = adapters[account_id]
        if not hasattr(a, "parse_upload"):
            raise HTTPException(400, "this connection does not take uploads")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large (15 MB max)")
        try:
            rows = a.parse_upload(data, password or None)
        except BrokerError as e:
            raise HTTPException(400, e.message)
        return save_snapshot(acct, rows, file.filename or "", info.username)

    @app.post("/api/mf/nav-refresh")
    def nav_refresh(info=Depends(need("brokers:manage"))):
        """Revalue the latest CAS upload of every CAS account with today's AMFI NAVs."""
        from ..plugins.cas.adapter import fetch_amfi
        try:
            navs = fetch_amfi()
        except Exception as e:
            raise HTTPException(502, f"AMFI NAV file unavailable: {e}")
        updated = 0
        with Db() as db, db.begin():
            for acct in db.execute(select(Account).where(Account.plugin == "cas")).scalars():
                snap = db.execute(select(Snapshot).where(Snapshot.account_id == acct.id)
                                  .order_by(Snapshot.taken_at.desc()).limit(1)).scalar_one_or_none()
                if not snap:
                    continue
                total = 0.0
                for h in db.execute(select(SnapshotHolding).where(SnapshotHolding.snapshot_id == snap.id)).scalars():
                    if h.isin in navs:
                        h.last_price = navs[h.isin][0]
                        updated += 1
                    total += h.quantity * h.last_price
                snap.value = total
            row = db.get(AppState, "nav_refresh")
            val = {"at": datetime.now().isoformat(timespec="minutes"), "schemes": len(navs), "updated": updated}
            if row:
                row.value = val
            else:
                db.add(AppState(key="nav_refresh", value=val))
            audit.record(db, "portfolio", "nav_refresh", val, info.username)
        return {"updated": updated, "schemes": len(navs)}

    # ------------------------------------------------------------ email source (read-only IMAP)
    scans: dict[str, dict] = {}       # account -> {key: item} from the last scan; nothing is imported without a pick

    def _email(account_id):
        with Db() as db:
            acct = _acct(db, account_id)
        if acct.plugin != "email":
            raise HTTPException(400, "not an email connection")
        return acct, adapters[account_id]

    def _fail(e: BrokerError):
        return HTTPException(401 if e.kind.value == "auth_expired" else 502, e.message)

    @app.post("/api/accounts/{account_id}/email/test")
    def email_test(account_id: str, info=Depends(need("brokers:manage"))):
        _, a = _email(account_id)
        try:
            r = a.test()
        except BrokerError as e:
            raise _fail(e)
        with Db() as db, db.begin():
            audit.record(db, "brokers", "email_test", {"account": account_id, "messages": r["messages"]}, info.username)
        return r

    @app.post("/api/accounts/{account_id}/email/scan")
    def email_scan(account_id: str, body: EmailScan, info=Depends(need("brokers:manage"))):
        """Read recent mail (read-only) and list what could be imported. Imports nothing by itself."""
        _, a = _email(account_id)
        try:
            r = a.scan(days=body.days)
        except BrokerError as e:
            raise _fail(e)
        items = r["alerts"] + r["attachments"]
        with Db() as db:
            keys = [x["key"] for x in items]
            seen = {k for (k,) in db.execute(select(EmailSeen.key).where(EmailSeen.key.in_(keys)))} if keys else set()
        for x in items:
            x["imported"] = x["key"] in seen
        scans[account_id] = {x["key"]: x for x in items}
        with Db() as db, db.begin():
            audit.record(db, "brokers", "email_scan", {"account": account_id, "scanned": r["scanned"],
                                                       "alerts": len(r["alerts"]), "attachments": len(r["attachments"])},
                         info.username)
        return r

    @app.post("/api/accounts/{account_id}/email/import")
    def email_import(account_id: str, body: EmailImport, info=Depends(need("brokers:manage"))):
        """Turn the picked alerts from the last scan into expense / income rows (source 'email')."""
        if not allowed(info.role, "money:read"):
            raise HTTPException(403, "money access needed")
        acct, _ = _email(account_id)
        last = scans.get(account_id) or {}
        picked = [last[k] for k in body.keys if k in last and "amount" in last[k]]
        if not picked:
            raise HTTPException(400, "scan the inbox first, then pick alerts to import")
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        added = skipped = 0
        with Db() as db, db.begin():
            for x in picked:
                if db.get(EmailSeen, x["key"]):
                    skipped += 1
                    continue
                cat = body.categories.get(x["key"], x["category"])
                cat = cat if cat in EMAIL_CATEGORIES else "Other"
                amt = abs(x["amount"]) if (x["direction"] == "credit" or cat == "Income") else -abs(x["amount"])
                on = datetime.fromisoformat(x["on"]) if x.get("on") else now
                e = Expense(on=on.replace(hour=0, minute=0, second=0), merchant=x["merchant"][:128], category=cat,
                            amount=amt, account=(x.get("account_hint") or acct.name)[:64], source="email",
                            note=(x.get("subject") or "")[:200], created_by=info.username)
                db.add(e)
                db.flush()
                db.add(EmailSeen(key=x["key"], account_id=account_id, kind="alert", ref=e.id, at=now))
                added += 1
            audit.record(db, "money", "email_import", {"account": account_id, "added": added, "skipped": skipped},
                         info.username)
        for x in picked:
            x["imported"] = True
        return {"added": added, "skipped": skipped}

    @app.post("/api/accounts/{account_id}/email/attachment")
    def email_attachment(account_id: str, body: EmailAttachment, info=Depends(need("brokers:manage"))):
        """Import one CAS / CSV attachment from the last scan into a CAS or CSV connection."""
        _, a = _email(account_id)
        x = (scans.get(account_id) or {}).get(body.key)
        if not x or "part" not in x:
            raise HTTPException(400, "scan the inbox first, then pick an attachment")
        if x["kind"] not in ("cas", "csv"):
            raise HTTPException(400, "contract-note parsing is not built yet")
        with Db() as db:
            target = _acct(db, body.target)
        if target.plugin != x["kind"]:
            raise HTTPException(400, f"pick a {x['kind'].upper()} connection for this file")
        if x.get("imported"):
            raise HTTPException(409, "this attachment was already imported")
        try:
            fn, data = a.attachment(x["uid"], x["part"])
            rows = adapters[target.id].parse_upload(data, body.password or None)
        except BrokerError as e:
            raise HTTPException(400, e.message)
        r = save_snapshot(target, rows, fn, info.username, via=f"email:{account_id}")
        with Db() as db, db.begin():
            if not db.get(EmailSeen, body.key):
                db.add(EmailSeen(key=body.key, account_id=account_id, kind=x["kind"], ref=r["snapshot"],
                                 at=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)))
        x["imported"] = True
        return r

    @app.delete("/api/accounts/{account_id}")
    def disconnect(account_id: str, info=Depends(need("brokers:manage"))):
        if account_id == "paper":
            raise HTTPException(400, "the paper simulator cannot be removed")
        with Db() as db, db.begin():
            acct = db.get(Account, account_id)
            if not acct:
                raise HTTPException(404, "no account")
            from ..db.models import DeskOrder
            if db.execute(select(DeskOrder.id).where(DeskOrder.account_id == account_id).limit(1)).first():
                raise HTTPException(409, "this account has order history; keep it (turn live orders off instead)")
            m = c.registry.get(acct.plugin)
            for k, v in m.settings_schema.get("properties", {}).items():
                if v.get("secret"):
                    secrets.delete(secret_key(account_id, k))
            for k in ("access_token", "token_issued_at"):
                secrets.delete(secret_key(account_id, k))
            db.delete(acct)
            audit.record(db, "brokers", "account_removed", {"id": account_id, "plugin": acct.plugin}, info.username)
        adapters.pop(account_id, None)
        return {"ok": True}
