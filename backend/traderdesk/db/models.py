"""Database schema. SQLite by default (zero setup); set TDP_DATABASE_URL to a Postgres URL
for the docker-compose stack. Same models either way."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Account(Base):
    """A broker connection: one plugin instance with its (non-secret) settings."""
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plugin: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    live: Mapped[bool] = mapped_column(Boolean, default=False)   # live orders allowed on this account
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeskOrder(Base):
    """Every order the desk builds, from ticket to final state."""
    __tablename__ = "desk_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_tag: Mapped[str] = mapped_column(String(32), unique=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(16), default="ticket")   # ticket|checked|sent|blocked|failed
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(8), nullable=True)   # entry | exit
    verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    charges: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    margin: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirm_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    fo_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    oco_gtt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # stop+target armed after fill
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RiskRules(Base):
    __tablename__ = "risk_rules"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    rules: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """Append-only, hash-chained: editing or deleting a row breaks every hash after it."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    module: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Snapshot(Base):
    """A dated portfolio snapshot (live fetch or imported history)."""
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("source", "taken_at", name="uq_snapshot_source_time"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(64))
    invested: Mapped[float] = mapped_column(Float, default=0)
    value: Mapped[float] = mapped_column(Float, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class SnapshotHolding(Base):
    __tablename__ = "snapshot_holdings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"), index=True)
    exchange: Mapped[str] = mapped_column(String(16))
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    isin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float] = mapped_column(Float)
    average_price: Mapped[float] = mapped_column(Float)
    last_price: Mapped[float] = mapped_column(Float)


class AppState(Base):
    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


# ---------------------------------------------------------------- money
class Expense(Base):
    """One spend or income row. Manual today; email parsers add rows with source='email'."""
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    on: Mapped[datetime] = mapped_column(DateTime, index=True)
    merchant: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    amount: Mapped[float] = mapped_column(Float)            # negative = spend, positive = income
    account: Mapped[str] = mapped_column(String(64), default="Cash")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EmailSeen(Base):
    """An email item already imported (alert -> expense, attachment -> snapshot), so a rescan never doubles it."""
    __tablename__ = "email_seen"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))           # alert | cas | csv
    ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime)


class Budget(Base):
    __tablename__ = "budgets"
    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    monthly: Mapped[float] = mapped_column(Float)


class Bill(Base):
    __tablename__ = "bills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[float] = mapped_column(Float)
    due: Mapped[datetime] = mapped_column(DateTime)
    recurrence: Mapped[str] = mapped_column(String(16), default="none")   # none|monthly|yearly
    category: Mapped[str] = mapped_column(String(64), default="Utilities")
    pay_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paid_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- wealth
class MetalHolding(Base):
    __tablename__ = "metal_holdings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metal: Mapped[str] = mapped_column(String(16))          # gold | silver | platinum
    app: Mapped[str] = mapped_column(String(64))
    vault: Mapped[str] = mapped_column(String(64))
    purity: Mapped[str] = mapped_column(String(16), default="99.9%")
    grams: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)              # INR incl. GST
    bought_on: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32), default="manual")


class TaxEntry(Base):
    """A realised gain/income line for the tax centre (manual, Console P&L import or live fills)."""
    __tablename__ = "tax_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    on: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(24))   # equity_stcg|equity_ltcg|intraday|fo|foreign|vda|dividend|other
    amount: Mapped[float] = mapped_column(Float)    # gain (+) or loss (-), INR
    turnover: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaxPayment(Base):
    __tablename__ = "tax_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fy: Mapped[str] = mapped_column(String(9))
    on: Mapped[datetime] = mapped_column(DateTime)
    amount: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(16), default="advance")   # advance | tds


# ---------------------------------------------------------------- analysis
class Brief(Base):
    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)      # YYYY-MM-DD (IST)
    market: Mapped[str] = mapped_column(String(8), default="NSE")
    kind: Mapped[str] = mapped_column(String(8), default="pre")   # pre | post
    body: Mapped[dict] = mapped_column(JSON)
    grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("day", "symbol", "strategy", "side", name="uq_signal_once"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    at: Mapped[str] = mapped_column(String(5))
    symbol: Mapped[str] = mapped_column(String(32))
    strategy: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="waiting")   # waiting|ignored|ticketed|alerted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread: Mapped[str] = mapped_column(String(36), index=True)
    username: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))     # user | assistant
    content: Mapped[dict] = mapped_column(JSON)       # {"text": ..., "tools": [...], "model": ...}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def make_session_factory(url: str):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, future=True, **kw)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
