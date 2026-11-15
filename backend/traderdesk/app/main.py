"""Trader Desk Pro API. Binds to 127.0.0.1; remote access goes through Tailscale or a VPN."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..brokers.registry import PluginRegistry
from ..core import audit
from ..core.auth import SessionInfo, SessionStore, allowed, hash_password, new_totp_secret, verify_password, verify_totp
from ..core.calendar import IST, nse_session
from ..core.errors import BrokerError, DeskError
from ..core.models import Cap, OrderChange, OrderRequest, OrderType
from ..core.secrets import KeyringStore, MemoryStore, SecretStore, secret_key
from ..db.models import Account, AuditLog, DeskOrder, Snapshot, SnapshotHolding, User, make_session_factory
from ..orders.desk import OrderDesk, confirm_phrase
from ..plugins.paper.adapter import PaperBroker
from ..portfolio.importer import import_snapshots, latest_snapshot
from ..portfolio.networth import summarise
from ..core.models import Holding
from ..market.yahoo import MarketData
from . import connectors_api, pulse_api, trading_api, wealth_api
from types import SimpleNamespace

log = logging.getLogger("traderdesk")
COOKIE = "tdp_session"
ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- request bodies
class Credentials(BaseModel):
    username: str
    password: str
    totp: str | None = None


class Reauth(BaseModel):
    password: str


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class NewAccount(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_\-]{2,32}$")
    plugin: str
    name: str
    settings: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    live: bool = False


class SecretsBody(BaseModel):
    secrets: dict[str, str]


class TicketBody(BaseModel):
    account_id: str
    order: dict[str, Any]


class ConfirmBody(BaseModel):
    fingerprint: str
    typed: str


class ModifyBody(BaseModel):
    quantity: int | None = None
    price: float | None = None
    trigger_price: float | None = None
    order_type: str | None = None


class CancelAllBody(BaseModel):
    account_id: str | None = None
    symbol: str | None = None


class FlattenBody(BaseModel):
    account_id: str


class ModeBody(BaseModel):
    mode: str


class RulesBody(BaseModel):
    changes: dict[str, Any]
    note: str | None = None


class ImportBody(BaseModel):
    folder: str
    account_id: str = "kite"


class PriceBody(BaseModel):
    account_id: str
    key: str
    price: float


class TotpCode(BaseModel):
    code: str


# ---------------------------------------------------------------- app factory
def create_app(db_url: str | None = None, secrets: SecretStore | None = None,
               clock: Callable[[], datetime] | None = None, adapters_override: dict | None = None,
               serve_frontend: bool = True, market: MarketData | None = None) -> FastAPI:
    db_url = db_url or os.environ.get("TDP_DATABASE_URL") or f"sqlite:///{(ROOT / 'data' / 'traderdesk.db').as_posix()}"
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        Path(db_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if secrets is None:
        secrets = MemoryStore() if os.environ.get("TDP_SECRETS") == "memory" else KeyringStore()
    Db = make_session_factory(db_url)
    registry = PluginRegistry().discover()
    sessions = SessionStore()
    adapters: dict[str, Any] = {}
    session = nse_session()
    desk = OrderDesk(Db, adapters, session=session, clock=clock)

    def build_adapters():
        with Db() as db, db.begin():
            if not db.execute(select(Account)).first():
                db.add(Account(id="paper", plugin="paper", name="Paper simulator", settings={}))
            desk.rules(db)   # seed the default rules once at startup, so concurrent first requests never race
            for acct in db.execute(select(Account)).scalars():
                if adapters_override and acct.id in adapters_override:
                    adapters[acct.id] = adapters_override[acct.id]
                    continue
                try:
                    adapters[acct.id] = make_adapter(acct.plugin, acct.id, acct.settings)
                except Exception as e:
                    log.error("account %s (%s) failed to start: %s", acct.id, acct.plugin, e)

    md = market or MarketData()

    def _kite_live():
        """The first Kite account whose daily login is fresh, or None (then data is delayed)."""
        for a in list(adapters.values()):
            if getattr(a, "name", "") == "kite" and hasattr(a, "quote"):
                try:
                    if a.session_status().get("connected"):
                        return a
                except Exception:
                    continue
        return None

    from ..market.live import LiveQuotes
    md.live = LiveQuotes(_kite_live)

    def _fallback_price(exchange: str, symbol: str) -> float | None:
        if exchange not in ("NSE", "BSE"):
            return None   # no free F&O quotes; set a simulator price instead
        return md.quote(symbol, exchange).get("last")

    desk.price_source = _fallback_price

    def _lookup(exchange: str, symbol: str) -> float | None:
        if exchange == "FX":
            return md.quote(symbol).get("last")
        return _fallback_price(exchange, symbol)

    def make_adapter(plugin: str, account_id: str, settings: dict):
        a = registry.create(plugin, account_id, settings, secrets)
        if hasattr(a, "price_lookup"):
            a.price_lookup = _lookup     # delayed prices for brokers whose holdings carry no last price
        return a

    @asynccontextmanager
    async def lifespan(_app):
        build_adapters()
        if clock is None and os.environ.get("TDP_SCHEDULER", "on") != "off":
            _app.state.start_scheduler()
        yield

    app = FastAPI(title="Trader Desk Pro", version="0.1.0", lifespan=lifespan)
    app.state.desk, app.state.db, app.state.adapters, app.state.secrets = desk, Db, adapters, secrets
    app.state.sessions, app.state.registry = sessions, registry

    # ------------------------------------------------------------ security plumbing
    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin and origin.split("://", 1)[-1] != host:
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    def current(request: Request) -> SessionInfo:
        info = sessions.get(request.cookies.get(COOKIE))
        if info is None:
            raise HTTPException(401, "sign in first")
        if request.method not in ("GET", "HEAD") and request.headers.get("x-csrf") != info.csrf:
            raise HTTPException(403, "missing or wrong CSRF token")
        return info

    def need(perm: str):
        def dep(info: SessionInfo = Depends(current)) -> SessionInfo:
            if not allowed(info.role, perm):
                raise HTTPException(403, f"your role ({info.role}) cannot do {perm}")
            return info
        return dep

    @app.exception_handler(DeskError)
    async def desk_error(_r, e: DeskError):
        return JSONResponse({"detail": e.message, "gate": e.gate, **e.details}, status_code=409)

    @app.exception_handler(BrokerError)
    async def broker_error(_r, e: BrokerError):
        return JSONResponse({"detail": e.message, "kind": e.kind.value, "broker": e.broker}, status_code=502)

    # ------------------------------------------------------------ auth
    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/auth/state")
    def auth_state(request: Request):
        with Db() as db:
            setup = db.execute(select(func.count(User.id))).scalar_one() == 0
        info = sessions.get(request.cookies.get(COOKIE))
        return {"setup_needed": setup,
                "user": {"username": info.username, "role": info.role, "csrf": info.csrf} if info else None}

    def _login_response(resp: Response, user: User) -> dict:
        tok, info = sessions.create(user.username, user.role)
        # Lax, not Strict: the Kite login redirect back to /api/brokers/kite/callback is a cross-site
        # top-level GET and must carry the session. Unsafe methods are guarded by the X-CSRF header.
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", secure=False, max_age=12 * 3600)
        return {"username": user.username, "role": user.role, "csrf": info.csrf}

    @app.post("/api/auth/setup")
    def setup(body: Credentials, resp: Response):
        with Db() as db, db.begin():
            if db.execute(select(func.count(User.id))).scalar_one():
                raise HTTPException(409, "already set up; sign in")
            try:
                u = User(username=body.username, password_hash=hash_password(body.password), role="owner")
            except ValueError as e:
                raise HTTPException(400, str(e))
            db.add(u)
            audit.record(db, "auth", "owner_created", {"username": u.username}, u.username)
            return _login_response(resp, u)

    failures: dict[str, list[float]] = {}
    LOCK_WINDOW, LOCK_AFTER = 300.0, 5

    @app.post("/api/auth/login")
    def login(body: Credentials, resp: Response):
        import time as _t
        key = body.username.strip().lower()[:64]
        recent = [x for x in failures.get(key, []) if _t.monotonic() - x < LOCK_WINDOW]
        failures[key] = recent
        if len(recent) >= LOCK_AFTER:
            with Db() as db, db.begin():
                audit.record(db, "auth", "login_locked", {"username": key}, None)
            raise HTTPException(429, "too many failed sign-ins; wait 5 minutes")
        why = None
        with Db() as db, db.begin():
            u = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
            ok = bool(u and u.active and verify_password(u.password_hash, body.password))
            if ok and u.totp_secret and not verify_totp(u.totp_secret, body.totp):
                why = "totp"
            elif not ok:
                why = "password"
            else:
                failures.pop(key, None)
                audit.record(db, "auth", "login", {}, u.username)
                return _login_response(resp, u)
        failures.setdefault(key, []).append(_t.monotonic())
        # failures are recorded in their own transaction so the 401 never rolls the audit row back
        with Db() as db, db.begin():
            audit.record(db, "auth", "login_failed", {"username": body.username[:64], "why": why}, None)
        raise HTTPException(401, "two-factor code needed or wrong" if why == "totp" else "wrong username or password")

    @app.post("/api/auth/logout")
    def logout(request: Request, resp: Response, info: SessionInfo = Depends(current)):
        sessions.drop(request.cookies.get(COOKIE))
        resp.delete_cookie(COOKIE)
        return {"ok": True}

    @app.post("/api/auth/reauth")
    def reauth(body: Reauth, info: SessionInfo = Depends(current)):
        with Db() as db, db.begin():
            u = db.execute(select(User).where(User.username == info.username)).scalar_one()
            good = verify_password(u.password_hash, body.password)
            if good:
                info.reauth_on = datetime.now(IST).date()
            audit.record(db, "auth", "reauth" if good else "reauth_failed", {}, info.username)
        if not good:
            raise HTTPException(401, "wrong password")
        return {"ok": True}

    @app.post("/api/auth/totp/start")
    def totp_start(info: SessionInfo = Depends(current)):
        secret = new_totp_secret()
        info.__dict__["pending_totp"] = secret
        import pyotp
        return {"secret": secret, "uri": pyotp.TOTP(secret).provisioning_uri(info.username, "Trader Desk Pro")}

    @app.post("/api/auth/totp/verify")
    def totp_verify(body: TotpCode, info: SessionInfo = Depends(current)):
        secret = info.__dict__.get("pending_totp")
        if not secret or not verify_totp(secret, body.code):
            raise HTTPException(400, "code does not match")
        with Db() as db, db.begin():
            u = db.execute(select(User).where(User.username == info.username)).scalar_one()
            u.totp_secret = secret
            audit.record(db, "auth", "totp_enabled", {}, info.username)
        return {"ok": True}

    @app.get("/api/users")
    def users(info: SessionInfo = Depends(need("users:manage"))):
        with Db() as db:
            return [{"username": u.username, "role": u.role, "active": u.active, "totp": bool(u.totp_secret)}
                    for u in db.execute(select(User)).scalars()]

    @app.post("/api/users")
    def add_user(body: NewUser, info: SessionInfo = Depends(need("users:manage"))):
        from ..core.auth import ROLES
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {sorted(ROLES)}")
        with Db() as db, db.begin():
            if db.execute(select(User).where(User.username == body.username)).first():
                raise HTTPException(409, "user exists")
            try:
                db.add(User(username=body.username, password_hash=hash_password(body.password), role=body.role))
            except ValueError as e:
                raise HTTPException(400, str(e))
            audit.record(db, "users", "user_created", {"username": body.username, "role": body.role}, info.username)
        return {"ok": True}

    # ------------------------------------------------------------ plugins and accounts
    @app.get("/api/plugins")
    def plugins(info: SessionInfo = Depends(need("brokers:read"))):
        return registry.list()

    def _account_status(acct: Account) -> dict:
        a = adapters.get(acct.id)
        status = {"connected": a is not None}
        if a is not None and hasattr(a, "session_status"):
            status = a.session_status()
        caps = sorted(c.value for c in a.capabilities) if a is not None else []
        m = registry.manifests.get(acct.plugin)
        return {"id": acct.id, "plugin": acct.plugin, "name": acct.name, "live": acct.live, "capabilities": caps,
                "requires_static_ip": bool(m and m.requires_static_ip), **status}

    @app.get("/api/accounts")
    def accounts(info: SessionInfo = Depends(need("brokers:read"))):
        with Db() as db:
            return [_account_status(a) for a in db.execute(select(Account)).scalars()]

    @app.post("/api/accounts")
    def add_account(body: NewAccount, info: SessionInfo = Depends(need("brokers:manage"))):
        if body.plugin not in registry.manifests:
            raise HTTPException(400, f"no plugin {body.plugin!r}")
        m = registry.get(body.plugin)
        secret_fields = {k for k, v in m.settings_schema.get("properties", {}).items() if v.get("secret")}
        leaked = secret_fields & set(body.settings)
        if leaked:
            raise HTTPException(400, f"send {sorted(leaked)} under 'secrets', not 'settings'")
        settings = {k: v.strip() if isinstance(v, str) else v for k, v in body.settings.items()}
        with Db() as db, db.begin():
            if db.get(Account, body.id):
                raise HTTPException(409, "account id exists")
            db.add(Account(id=body.id, plugin=body.plugin, name=body.name, settings=settings, live=body.live))
            for k, v in body.secrets.items():
                secrets.set(secret_key(body.id, k), v.strip())
            audit.record(db, "brokers", "account_added", {"id": body.id, "plugin": body.plugin, "live": body.live,
                                                          "secret_fields": sorted(body.secrets)}, info.username)
        adapters[body.id] = make_adapter(body.plugin, body.id, settings)
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/secrets")
    def replace_secrets(account_id: str, body: SecretsBody, info: SessionInfo = Depends(need("brokers:manage"))):
        """Replace stored secrets (e.g. after rotating the API secret). Values go to the keychain only."""
        with Db() as db, db.begin():
            acct = db.get(Account, account_id)
            if not acct:
                raise HTTPException(404, "no account")
            m = registry.get(acct.plugin)
            allowed_fields = {k for k, v in m.settings_schema.get("properties", {}).items() if v.get("secret")}
            unknown = set(body.secrets) - allowed_fields
            if unknown or not body.secrets:
                raise HTTPException(400, f"secret fields for {acct.plugin}: {sorted(allowed_fields)}")
            for k, v in body.secrets.items():
                if not v.strip():
                    raise HTTPException(400, f"{k} is empty")
                secrets.set(secret_key(account_id, k), v.strip())
            if "access_token" in body.secrets:   # a pasted daily token: start its clock
                secrets.set(secret_key(account_id, "token_issued_at"), datetime.now(IST).isoformat())
            else:                                   # a new API secret invalidates the old session token
                secrets.delete(secret_key(account_id, "access_token"))
                secrets.delete(secret_key(account_id, "token_issued_at"))
            audit.record(db, "brokers", "secrets_replaced", {"id": account_id, "fields": sorted(body.secrets)},
                         info.username)
            adapters[account_id] = make_adapter(acct.plugin, account_id, acct.settings)
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/live")
    def set_live(account_id: str, enabled: bool, info: SessionInfo = Depends(need("mode:live"))):
        with Db() as db, db.begin():
            acct = db.get(Account, account_id)
            if not acct:
                raise HTTPException(404, "no account")
            acct.live = enabled
            audit.record(db, "brokers", "account_live", {"id": account_id, "live": enabled}, info.username)
        return {"ok": True}

    @app.get("/api/accounts/{account_id}/login-url")
    def login_url(account_id: str, info: SessionInfo = Depends(need("brokers:manage"))):
        a = adapters.get(account_id)
        if not a or not hasattr(a, "login_url"):
            raise HTTPException(400, "this account has no browser login")
        return {"url": a.login_url()}

    @app.get("/api/brokers/kite/callback")
    def kite_callback(request_token: str, request: Request, account: str | None = None,
                      status: str | None = None):
        info = sessions.get(request.cookies.get(COOKIE))
        if info is None or not allowed(info.role, "brokers:manage"):
            raise HTTPException(401, "sign in as the owner, then log in to Kite again")
        with Db() as db:
            ids = [a.id for a in db.execute(select(Account).where(Account.plugin == "kite")).scalars()]
        acct = account or (ids[0] if ids else None)
        if not acct or acct not in adapters:
            raise HTTPException(404, "no Kite account")
        try:
            who = adapters[acct].complete_login(request_token)
        except BrokerError as e:
            from urllib.parse import quote
            hint = ("Kite rejected the API secret for this API key. If you regenerated it, enter the new one "
                    "under Connections > Replace secret, then log in again."
                    if "checksum" in e.message.lower() else e.message)
            with Db() as db, db.begin():
                audit.record(db, "brokers", "kite_login_failed", {"account": acct, "error": e.message}, info.username)
            return RedirectResponse(f"/?kite_error={quote(hint)}")
        with Db() as db, db.begin():
            audit.record(db, "brokers", "kite_login", {"account": acct, "user_id": who.get("user_id")}, info.username)
        return RedirectResponse("/?kite=connected")

    # ------------------------------------------------------------ portfolio
    @app.get("/api/portfolio")
    def portfolio(info: SessionInfo = Depends(need("portfolio:read"))):
        holdings: list[Holding] = []
        sources = []
        with Db() as db:
            accts = list(db.execute(select(Account)).scalars())
            for acct in accts:
                a = adapters.get(acct.id)
                if a is None or Cap.READ not in a.capabilities or isinstance(a, PaperBroker):
                    continue
                try:
                    hs = a.holdings()
                    holdings += hs
                    sources.append({"account": acct.id, "source": "live", "rows": len(hs)})
                except BrokerError as e:
                    snap = latest_snapshot(db, acct.id)
                    if snap:
                        hs = _snap_holdings(db, snap)
                        holdings += hs
                        sources.append({"account": acct.id, "source": "snapshot", "as_of": snap.taken_at.isoformat(),
                                        "rows": len(hs), "error": e.message})
                    else:
                        sources.append({"account": acct.id, "source": "unavailable", "error": e.message})
            if not holdings:
                snap = latest_snapshot(db)
                if snap:
                    holdings = _snap_holdings(db, snap)
                    sources.append({"account": snap.account_id, "source": "snapshot",
                                    "as_of": snap.taken_at.isoformat(), "rows": len(holdings)})
        fx = {}
        if any(h.currency == "USD" for h in holdings):
            try:
                fx["USD"] = float(md.quote("USDINR")["last"])
            except Exception as e:
                log.info("no USD/INR rate: %s", e)
        out = summarise(holdings, fx)
        out["sources"] = sources
        return out

    def _snap_holdings(db, snap: Snapshot) -> list[Holding]:
        rows = db.execute(select(SnapshotHolding).where(SnapshotHolding.snapshot_id == snap.id)).scalars()
        return [Holding(snap.account_id, r.exchange, r.tradingsymbol, r.quantity, r.average_price, r.last_price,
                        isin=r.isin, asset_class=r.asset_class, name=r.name,
                        currency="USD" if r.asset_class in ("us_stock", "crypto") else "INR") for r in rows]

    @app.get("/api/portfolio/history")
    def history(info: SessionInfo = Depends(need("portfolio:read"))):
        with Db() as db:
            rows = db.execute(select(Snapshot).order_by(Snapshot.taken_at)).scalars()
            return [{"taken_at": s.taken_at.isoformat(), "account": s.account_id, "invested": s.invested,
                     "value": s.value} for s in rows]

    @app.post("/api/portfolio/import")
    def do_import(body: ImportBody, info: SessionInfo = Depends(need("brokers:manage"))):
        folder = Path(body.folder)
        if not folder.is_dir():
            raise HTTPException(400, "not a folder")
        with Db() as db, db.begin():
            res = import_snapshots(db, folder, body.account_id)
            audit.record(db, "portfolio", "import", {"folder": str(folder), **{k: v for k, v in res.items()
                                                                               if k != "errors"}}, info.username)
        return res

    # ------------------------------------------------------------ order desk
    def _row(r: DeskOrder) -> dict:
        req = r.request or {}
        o = OrderRequest.from_dict(req) if req else None
        return {"id": r.id, "tag": r.client_tag, "account_id": r.account_id, "broker_order_id": r.broker_order_id,
                "stage": r.stage, "status": r.status, "kind": r.kind, "paper": r.paper, "order": req,
                "fingerprint": r.fingerprint, "verdict": r.verdict, "charges": r.charges, "margin": r.margin,
                "confirm_text": r.confirm_text, "confirm_phrase": confirm_phrase(o) if o else None,
                "max_loss": r.max_loss, "oco_gtt_id": r.oco_gtt_id, "error": r.error,
                "created_at": r.created_at.isoformat() + "Z", "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None}

    @app.get("/api/orders")
    def orders(limit: int = 200, info: SessionInfo = Depends(need("orders:read"))):
        with Db() as db:
            rows = db.execute(select(DeskOrder).order_by(DeskOrder.id.desc()).limit(min(limit, 1000))).scalars()
            return [_row(r) for r in rows]

    @app.post("/api/orders/ticket")
    def ticket(body: TicketBody, info: SessionInfo = Depends(need("orders:ticket"))):
        try:
            o = OrderRequest.from_dict(body.order)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, f"bad order: {e}")
        return _row(desk.ticket(body.account_id, o, info.username))

    @app.post("/api/orders/{desk_id}/confirm")
    def confirm(desk_id: int, body: ConfirmBody, info: SessionInfo = Depends(need("orders:send"))):
        with Db() as db:
            row = db.get(DeskOrder, desk_id)
            if row is None:
                raise HTTPException(404, "no ticket")
            if not row.paper and info.reauth_on != datetime.now(IST).date():
                raise HTTPException(403, "reauth_required: enter your password before the first live order today")
        return _row(desk.confirm_and_send(desk_id, body.fingerprint, body.typed, info.username))

    @app.post("/api/orders/{desk_id}/cancel")
    def cancel(desk_id: int, info: SessionInfo = Depends(need("orders:cancel"))):
        return _row(desk.cancel(desk_id, info.username))

    @app.post("/api/orders/{desk_id}/modify")
    def modify(desk_id: int, body: ModifyBody, info: SessionInfo = Depends(need("orders:send"))):
        ch = OrderChange(body.quantity, body.price, body.trigger_price,
                         OrderType(body.order_type) if body.order_type else None)
        return _row(desk.modify(desk_id, ch, info.username))

    @app.post("/api/orders/cancel-all")
    def cancel_all(body: CancelAllBody, info: SessionInfo = Depends(need("orders:cancel"))):
        return {"cancelled": desk.cancel_all(body.account_id, body.symbol, info.username)}

    @app.post("/api/orders/flatten")
    def flatten(body: FlattenBody, info: SessionInfo = Depends(need("orders:flatten"))):
        return {"orders": desk.flatten(body.account_id, info.username)}

    @app.post("/api/orders/sync")
    def sync(info: SessionInfo = Depends(need("orders:read"))):
        return {"updated": desk.sync(info.username)}

    @app.post("/api/kill")
    def kill(info: SessionInfo = Depends(need("orders:kill"))):
        return {"cancelled": desk.kill(info.username), "kill_switch": True}

    @app.delete("/api/kill")
    def release(info: SessionInfo = Depends(need("kill:release"))):
        desk.release_kill(info.username)
        return {"kill_switch": False}

    @app.get("/api/mode")
    def get_mode(info: SessionInfo = Depends(current)):
        with Db() as db:
            return {"mode": desk.mode(db)}

    @app.post("/api/mode")
    def set_mode(body: ModeBody, info: SessionInfo = Depends(need("mode:live"))):
        with Db() as db, db.begin():
            try:
                desk.set_mode(db, body.mode, info.username)
            except ValueError as e:
                raise HTTPException(400, str(e))
        return {"mode": body.mode}

    @app.post("/api/paper/price")
    def paper_price(body: PriceBody, info: SessionInfo = Depends(need("orders:ticket"))):
        with Db() as db:
            a, paper = desk.broker_for(db, body.account_id)
        if not paper:
            raise HTTPException(400, "prices can only be set on a paper account")
        a.set_price(body.key.upper(), body.price)
        desk.sync(info.username)
        return {"ok": True}

    # ------------------------------------------------------------ rules and audit
    @app.get("/api/rules")
    def get_rules(info: SessionInfo = Depends(need("rules:read"))):
        with Db() as db, db.begin():
            v, r = desk.rules(db)
        return {"version": v, "rules": r}

    @app.put("/api/rules")
    def put_rules(body: RulesBody, info: SessionInfo = Depends(need("rules:edit"))):
        with Db() as db, db.begin():
            v = desk.update_rules(db, body.changes, info.username, body.note)
        return {"version": v}

    @app.get("/api/audit")
    def audit_log(module: str | None = None, limit: int = 200, info: SessionInfo = Depends(need("audit:read"))):
        with Db() as db:
            q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 2000))
            if module:
                q = q.where(AuditLog.module == module)
            return [{"id": r.id, "ts": r.ts.isoformat() + "Z", "user": r.username, "module": r.module,
                     "action": r.action, "detail": r.detail} for r in db.execute(q).scalars()]

    @app.get("/api/audit/verify")
    def audit_verify(info: SessionInfo = Depends(need("audit:read"))):
        with Db() as db:
            return audit.verify(db)

    # ------------------------------------------------------------ top bar
    @app.get("/api/topbar")
    def topbar(info: SessionInfo = Depends(current)):
        now = desk.clock()
        with Db() as db, db.begin():
            _, rules = desk.rules(db)
            today = desk._today(db)
            mode = desk.mode(db)
            kill_on = desk.kill_switch_on(db)
            accts = [_account_status(a) for a in db.execute(select(Account)).scalars()]
        ny = datetime.now(ZoneInfo("America/New_York"))
        return {"ist": now.strftime("%Y-%m-%d %H:%M:%S"), "nse_open": session.is_open(now),
                "ny": ny.strftime("%H:%M"), "mode": mode, "kill_switch": kill_on,
                "risk_budget_left": round(max(0.0, (rules.get("max_daily_loss") or 0) - today.loss_at_risk), 2),
                "trades_today": today.entries_placed, "max_trades": rules.get("max_trades_per_day"),
                "accounts": [{"id": a["id"], "name": a["name"], "connected": a["connected"]} for a in accts],
                "data": md.live.status() if md.live else {"live": False, "reason": "Delayed"},
                "user": {"username": info.username, "role": info.role}}

    # ------------------------------------------------------------ the other screens
    app.state.portfolio_fn = lambda info: portfolio(info=info)
    ctx = SimpleNamespace(Db=Db, need=need, current=current, desk=desk, adapters=adapters, secrets=secrets, md=md,
                          registry=registry, sessions=sessions, session=session, make_adapter=make_adapter)
    trading_api.register(app, ctx)
    wealth_api.register(app, ctx)
    connectors_api.register(app, ctx)
    pulse_api.register(app, ctx)

    # ------------------------------------------------------------ frontend
    dist = Path(os.environ.get("TDP_FRONTEND_DIST", ROOT / "frontend" / "dist"))
    if serve_frontend and dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            root = dist.resolve()
            f = (root / path).resolve()
            # never serve anything outside the built UI folder (blocks ../ and encoded traversal)
            if path and f.is_file() and f.is_relative_to(root):
                return FileResponse(f)
            # index.html names the hashed bundle: never let the browser keep an old copy after a rebuild
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def run() -> None:  # pragma: no cover
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("TDP_PORT", "8766")))


if __name__ == "__main__":  # pragma: no cover
    run()
