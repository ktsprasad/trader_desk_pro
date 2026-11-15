"""Wealth, money and workspace screens: portfolio overview, digital metals, tax centre, expenses,
bills, dashboards, users and sessions, and the read-only AI assistant."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..core import audit
from ..core.auth import ROLES
from ..db.models import (Account, Bill, Budget, ChatMessage, DeskOrder, Expense, MetalHolding, Snapshot, SnapshotHolding,
                         TaxEntry, TaxPayment, User)
from ..orders.charges import ChargesEngine
from ..orders.pnl import fills_from_rows, match
from ..tax import engine as tax
from .trading_api import state_get, state_set

log = logging.getLogger("traderdesk.wealth")
METALS = ("gold", "silver", "platinum")
EXPENSE_CATEGORIES = ["Rent and home", "Food and groceries", "Food delivery", "Shopping", "Utilities",
                      "Fuel and travel", "Subscriptions", "Health", "Education", "Investing", "Income", "Other"]


class ExpenseBody(BaseModel):
    on: date
    merchant: str = Field(min_length=1, max_length=128)
    category: str
    amount: float
    account: str = "Cash"
    note: str | None = None


class BudgetsBody(BaseModel):
    budgets: dict[str, float]


class BillBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    amount: float
    due: date
    recurrence: str = "none"
    category: str = "Utilities"
    pay_hint: str | None = None


class SnoozeBody(BaseModel):
    days: int = 3


class MetalBody(BaseModel):
    metal: str
    app: str
    vault: str
    purity: str = "99.9%"
    grams: float = Field(gt=0)
    cost: float = Field(gt=0)
    bought_on: date


class RatesBody(BaseModel):
    rates: dict[str, dict[str, float]]   # {"gold": {"sell": 12560, "buy": 13180}, ...}


class TaxEntryBody(BaseModel):
    on: date
    kind: str
    amount: float
    turnover: float = 0
    note: str | None = None


class TaxPaymentBody(BaseModel):
    on: date
    amount: float
    kind: str = "advance"


class TaxSettingsBody(BaseModel):
    slab: float = Field(ge=0, le=0.5)


class DashBody(BaseModel):
    dashboards: list[dict]


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None


class AskBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    thread: str | None = None
    provider: str = "claude"     # claude | ollama


class AssistantSettings(BaseModel):
    api_key: str | None = None
    local_for_money: bool | None = None
    log_tools: bool | None = None


def register(app, c) -> None:
    Db, need, current, desk, md = c.Db, c.need, c.current, c.desk, c.md

    def today() -> date:
        return desk.clock().date()

    # ------------------------------------------------------------ portfolio overview
    def first_seen(db) -> dict[str, date]:
        rows = db.execute(select(SnapshotHolding.tradingsymbol, func.min(Snapshot.taken_at))
                          .join(Snapshot, Snapshot.id == SnapshotHolding.snapshot_id)
                          .group_by(SnapshotHolding.tradingsymbol)).all()
        return {s: t.date() for s, t in rows}

    def metals_summary(db) -> dict:
        rates = state_get(db, "metal_rates", {"rates": {}, "as_of": None})
        rows = []
        for h in db.execute(select(MetalHolding).order_by(MetalHolding.metal, MetalHolding.app)).scalars():
            sell = (rates["rates"].get(h.metal) or {}).get("sell")
            val = round(h.grams * sell, 2) if sell else None
            rows.append({"id": h.id, "metal": h.metal, "app": h.app, "vault": h.vault, "purity": h.purity,
                         "grams": h.grams, "cost": h.cost, "value": val,
                         "pnl": round(val - h.cost, 2) if val is not None else None,
                         "bought_on": h.bought_on.date().isoformat(), "source": h.source})
        by = {m: {"grams": round(sum(r["grams"] for r in rows if r["metal"] == m), 4),
                  **(rates["rates"].get(m) or {})} for m in METALS}
        cost = round(sum(r["cost"] for r in rows), 2)
        valued = [r for r in rows if r["value"] is not None]
        value = round(sum(r["value"] for r in valued), 2) if valued else None
        return {"holdings": rows, "by_metal": by, "cost": cost, "value": value,
                "pnl": round(value - sum(r["cost"] for r in valued), 2) if value is not None else None,
                "rates_as_of": rates.get("as_of"), "unpriced": len(rows) - len(valued)}

    @app.get("/api/portfolio/overview")
    def portfolio_overview(info=Depends(need("portfolio:read"))):
        base = app.state.portfolio_fn(info)
        with Db() as db:
            seen = first_seen(db)
            metals = metals_summary(db)
            hist = [{"t": s.taken_at.isoformat(), "value": s.value, "invested": s.invested}
                    for s in db.execute(select(Snapshot).order_by(Snapshot.taken_at)).scalars()]
            funds = app.state.funds_all(db)
        r = tax.rules_for(tax.fy_of(today()))
        alloc = dict(base["by_asset_class"])
        total = base["value"] + (metals["value"] or 0) + sum(f.get("cash", 0) or 0 for f in funds if f["plugin"] != "paper")
        if metals["value"]:
            alloc["gold_silver"] = {"value": metals["value"], "invested": metals["cost"]}
        cash = sum(f.get("cash", 0) or 0 for f in funds if f["plugin"] != "paper")
        if cash:
            alloc["cash"] = {"value": cash, "invested": cash}
        for v in alloc.values():
            v["weight_pct"] = round(v["value"] / total * 100, 1) if total else 0
        turning = []
        for h in base["holdings"]:
            fs = seen.get(h["symbol"])
            if fs:
                term = tax.holding_term(fs, today(), h["asset_class"], r)
                h["held_since"] = fs.isoformat()
                h.update(term)
                if not term["long_term"] and term["months_to_long_term"] <= 12:
                    label = h["name"] if h.get("asset_class") == "mutual_fund" and h.get("name") else h["symbol"]
                    turning.append({"symbol": label[:40], "quantity": h["quantity"],
                                    "months": term["months_to_long_term"], "rule_months": term["rule_months"]})
        ladders = []
        for acct_id, a in c.adapters.items():
            try:
                gtts = a.gtts() if hasattr(a, "gtts") else []
            except Exception:
                continue
            items = gtts.values() if isinstance(gtts, dict) else gtts
            for g in items:
                sym = getattr(g, "tradingsymbol", None) or (g.get("condition") or {}).get("tradingsymbol")
                trig = getattr(g, "trigger_values", None) or (g.get("condition") or {}).get("trigger_values") or []
                ltp = getattr(g, "last_price", None) or (g.get("condition") or {}).get("last_price")
                kind = getattr(g, "kind", None) or g.get("type")
                if kind in ("single",) and sym:
                    ladders.append({"account": acct_id, "symbol": sym, "trigger": trig[0] if trig else None, "ltp": ltp})
        grouped: dict[str, dict] = {}
        for l in ladders:
            g = grouped.setdefault(l["symbol"], {"symbol": l["symbol"], "ltp": l["ltp"], "triggers": []})
            g["triggers"].append(l["trigger"])
        base.update({"metals": metals, "allocation": alloc, "net_worth": round(total, 2), "history": hist,
                     "turning_long_term": sorted(turning, key=lambda x: x["months"])[:8],
                     "gtt_ladders": list(grouped.values()),
                     "ltcg": compute_tax(today(), tax.fy_of(today()))["ltcg_used"],
                     "ltcg_exemption": r["ltcg_exemption"]})
        return base

    # ------------------------------------------------------------ metals
    @app.get("/api/metals")
    def metals(info=Depends(need("portfolio:read"))):
        with Db() as db:
            return metals_summary(db)

    @app.post("/api/metals")
    def add_metal(body: MetalBody, info=Depends(need("brokers:manage"))):
        if body.metal not in METALS:
            raise HTTPException(400, f"metal is one of {METALS}")
        with Db() as db, db.begin():
            h = MetalHolding(metal=body.metal, app=body.app, vault=body.vault, purity=body.purity, grams=body.grams,
                             cost=body.cost, bought_on=datetime.combine(body.bought_on, datetime.min.time()))
            db.add(h)
            audit.record(db, "portfolio", "metal_added", body.model_dump(mode="json"), info.username)
        return {"ok": True}

    @app.delete("/api/metals/{hid}")
    def del_metal(hid: int, info=Depends(need("brokers:manage"))):
        with Db() as db, db.begin():
            h = db.get(MetalHolding, hid)
            if h:
                db.delete(h)
                audit.record(db, "portfolio", "metal_deleted", {"id": hid}, info.username)
        return {"ok": True}

    @app.put("/api/metals/rates")
    def put_rates(body: RatesBody, info=Depends(need("brokers:manage"))):
        rates = {m: {k: float(v) for k, v in r.items() if k in ("sell", "buy") and v and v > 0}
                 for m, r in body.rates.items() if m in METALS}
        with Db() as db, db.begin():
            state_set(db, "metal_rates", {"rates": rates, "as_of": datetime.now().isoformat(timespec="minutes")})
        return {"ok": True}

    # ------------------------------------------------------------ tax
    def compute_tax(on: date, fy: str) -> dict:
        start, end = tax.fy_bounds(fy)
        r = tax.rules_for(fy)
        with Db() as db:
            settings = state_get(db, "tax_settings", {"slab": r["default_slab"]})
            lines = [tax.Line(e.kind, e.amount, e.turnover) for e in db.execute(
                select(TaxEntry).where(TaxEntry.on >= datetime.combine(start, datetime.min.time()),
                                       TaxEntry.on <= datetime.combine(end, datetime.max.time()))).scalars()]
            rows = list(db.execute(select(DeskOrder).where(DeskOrder.stage == "sent", DeskOrder.status == "COMPLETE",
                                                           DeskOrder.paper.is_(False))).scalars())
            paid = sum(p.amount for p in db.execute(select(TaxPayment).where(TaxPayment.fy == fy)).scalars())
        live = [cl for cl in match(fills_from_rows(rows)) if start <= cl.closed.date() <= end]
        lines += [tax.Line(cl.kind, cl.pnl, abs(cl.pnl)) for cl in live]
        out = tax.compute(lines, r, settings["slab"], on, paid)
        out["slab"] = settings["slab"]
        out["live_trades"] = len(live)
        return out

    @app.get("/api/tax")
    def tax_summary(fy: str | None = None, info=Depends(need("tax:read"))):
        fy = fy or tax.fy_of(today())
        out = compute_tax(today(), fy)
        # harvesting ideas from the portfolio
        ideas = []
        try:
            port = portfolio_overview(info)
        except Exception:
            port = {"holdings": []}
        r = tax.rules_for(fy)
        left = out["ltcg_left"]
        stcg_gain = next((x["gain"] for x in out["rows"] if x["kind"] == "equity_stcg"), 0)
        ch = ChargesEngine()
        from ..core.models import OrderRequest, Product, Side
        for h in port["holdings"]:
            if h.get("asset_class") not in ("equity", "etf") or "long_term" not in h:
                continue
            o = OrderRequest(h["exchange"], h["symbol"], Side.SELL, int(h["quantity"]) or 1, product=Product.CNC)
            cost = ch.round_trip("kite", o, h["last_price"], h["last_price"], today())["total"]
            if h["long_term"] and h["pnl_inr"] > 0 and left > 0:
                book = min(h["pnl_inr"], left)
                ideas.append({"kind": "ltcg_reset", "symbol": h["symbol"], "gain": h["pnl_inr"],
                              "saves_later": round(book * r["equity_ltcg"], 2), "charges": round(cost, 2),
                              "worth_it": book * r["equity_ltcg"] > cost,
                              "text": f"Book LTCG on {h['symbol']} and buy back: ₹{h['pnl_inr']:,.0f} gain stays inside the exemption."})
            elif not h["long_term"] and h["pnl_inr"] < 0 and stcg_gain > 0:
                saved = min(-h["pnl_inr"], stcg_gain) * r["equity_stcg"]
                ideas.append({"kind": "stcl_offset", "symbol": h["symbol"], "loss": h["pnl_inr"],
                              "saves_now": round(saved, 2), "charges": round(cost, 2), "worth_it": saved > cost,
                              "text": f"Sell {h['symbol']} at a loss to offset short-term gains."})
        out["ideas"] = sorted(ideas, key=lambda i: -(i.get("saves_later") or i.get("saves_now") or 0))[:6]
        with Db() as db:
            out["entries"] = [{"id": e.id, "on": e.on.date().isoformat(), "kind": e.kind, "amount": e.amount,
                               "turnover": e.turnover, "source": e.source, "note": e.note}
                              for e in db.execute(select(TaxEntry).order_by(TaxEntry.on.desc()).limit(200)).scalars()]
            out["payments"] = [{"id": p.id, "on": p.on.date().isoformat(), "amount": p.amount, "kind": p.kind}
                               for p in db.execute(select(TaxPayment).where(TaxPayment.fy == fy)).scalars()]
        out["kinds"] = {k: v[0] for k, v in tax.KINDS.items()}
        return out

    @app.post("/api/tax/entries")
    def add_tax_entry(body: TaxEntryBody, info=Depends(need("tax:read"))):
        if body.kind not in tax.KINDS:
            raise HTTPException(400, f"kind is one of {sorted(tax.KINDS)}")
        with Db() as db, db.begin():
            db.add(TaxEntry(on=datetime.combine(body.on, datetime.min.time()), kind=body.kind, amount=body.amount,
                            turnover=body.turnover, note=body.note, source="manual"))
            audit.record(db, "tax", "entry_added", body.model_dump(mode="json"), info.username)
        return {"ok": True}

    @app.delete("/api/tax/entries/{eid}")
    def del_tax_entry(eid: int, info=Depends(need("tax:read"))):
        with Db() as db, db.begin():
            e = db.get(TaxEntry, eid)
            if e:
                db.delete(e)
                audit.record(db, "tax", "entry_deleted", {"id": eid}, info.username)
        return {"ok": True}

    @app.post("/api/tax/payments")
    def add_payment(body: TaxPaymentBody, info=Depends(need("tax:read"))):
        with Db() as db, db.begin():
            db.add(TaxPayment(fy=tax.fy_of(body.on), on=datetime.combine(body.on, datetime.min.time()),
                              amount=body.amount, kind=body.kind))
            audit.record(db, "tax", "payment_added", body.model_dump(mode="json"), info.username)
        return {"ok": True}

    @app.put("/api/tax/settings")
    def tax_settings(body: TaxSettingsBody, info=Depends(need("tax:read"))):
        with Db() as db, db.begin():
            state_set(db, "tax_settings", {"slab": body.slab})
        return {"ok": True}

    @app.get("/api/tax/export")
    def tax_export(fy: str | None = None, info=Depends(need("exports:run"))):
        fy = fy or tax.fy_of(today())
        out = compute_tax(today(), fy)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["FY", fy])
        w.writerow(["Type", "Treatment", "Gain", "Taxable", "Tax"])
        for r in out["rows"]:
            w.writerow([r["type"], r["treatment"], r["gain"], r["taxable"], r["tax"]])
        w.writerow(["Cess", "", "", "", out["cess"]])
        w.writerow(["Total", "", "", "", out["total"]])
        with Db() as db, db.begin():
            audit.record(db, "tax", "export", {"fy": fy}, info.username)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="tax-{fy}.csv"'})

    # ------------------------------------------------------------ money: expenses, budgets, bills
    def month_range(month: str) -> tuple[datetime, datetime]:
        y, m = map(int, month.split("-"))
        return datetime(y, m, 1), datetime(y + (m == 12), m % 12 + 1, 1)

    @app.get("/api/money/expenses")
    def expenses(month: str | None = None, info=Depends(need("money:read"))):
        month = month or today().strftime("%Y-%m")
        a, b = month_range(month)
        with Db() as db:
            return [{"id": e.id, "on": e.on.date().isoformat(), "merchant": e.merchant, "category": e.category,
                     "amount": e.amount, "account": e.account, "source": e.source, "note": e.note}
                    for e in db.execute(select(Expense).where(Expense.on >= a, Expense.on < b)
                                        .order_by(Expense.on.desc(), Expense.id.desc())).scalars()]

    @app.post("/api/money/expenses")
    def add_expense(body: ExpenseBody, info=Depends(need("money:read"))):
        amt = body.amount
        if body.category != "Income" and amt > 0:
            amt = -amt          # spends are stored negative
        with Db() as db, db.begin():
            db.add(Expense(on=datetime.combine(body.on, datetime.min.time()), merchant=body.merchant.strip(),
                           category=body.category, amount=amt, account=body.account, note=body.note,
                           created_by=info.username))
            audit.record(db, "money", "expense_added", {"merchant": body.merchant, "amount": amt}, info.username)
        return {"ok": True}

    @app.delete("/api/money/expenses/{eid}")
    def del_expense(eid: int, info=Depends(need("money:read"))):
        with Db() as db, db.begin():
            e = db.get(Expense, eid)
            if e:
                db.delete(e)
                audit.record(db, "money", "expense_deleted", {"id": eid}, info.username)
        return {"ok": True}

    @app.get("/api/money/summary")
    def money_summary(month: str | None = None, info=Depends(need("money:read"))):
        month = month or today().strftime("%Y-%m")
        a, b = month_range(month)
        with Db() as db:
            rows = list(db.execute(select(Expense).where(Expense.on >= a, Expense.on < b)).scalars())
            budgets = {x.category: x.monthly for x in db.execute(select(Budget)).scalars()}
            start12 = (a.replace(day=1) - timedelta(days=330)).replace(day=1)
            hist = list(db.execute(select(Expense).where(Expense.on >= start12, Expense.on < b)).scalars())
            mailboxes = [(x.name, (x.settings or {}).get("provider", "IMAP"))
                         for x in db.execute(select(Account).where(Account.plugin == "email")).scalars()]
        spend = [e for e in rows if e.amount < 0 and not e.category.startswith("Invest")]
        invested = -sum(e.amount for e in rows if e.amount < 0 and e.category.startswith("Invest"))
        income = sum(e.amount for e in rows if e.amount > 0)
        by_cat: dict[str, float] = {}
        for e in spend:
            by_cat[e.category] = by_cat.get(e.category, 0) - e.amount
        monthly: dict[str, float] = {}
        for e in hist:
            if e.amount < 0 and not e.category.startswith("Invest"):
                k = e.on.strftime("%Y-%m")
                monthly[k] = monthly.get(k, 0) - e.amount
        total_budget = sum(budgets.values())
        spent = round(sum(by_cat.values()), 2)
        return {"month": month, "spent": spent, "income": round(income, 2), "invested": round(invested, 2),
                "saved_pct": round((income - spent) / income * 100) if income else None,
                "budget": total_budget, "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
                "budgets": budgets, "monthly": [{"month": k, "spent": round(v, 2)} for k, v in sorted(monthly.items())],
                "categories": EXPENSE_CATEGORIES,
                "sources": [{"name": "Manual entries", "status": f"{len([e for e in rows if e.source == 'manual'])} this month"},
                            *([{"name": f"{n} ({p}, read-only)", "status": f"{len([e for e in rows if e.source == 'email'])} from email this month"}
                               for n, p in mailboxes] or
                              [{"name": "Email inbox (Gmail, Outlook, Yahoo, Zoho, any IMAP; read-only)",
                                "status": "not connected: add it in Connections"}])]}

    @app.put("/api/money/budgets")
    def put_budgets(body: BudgetsBody, info=Depends(need("money:read"))):
        with Db() as db, db.begin():
            for k, v in body.budgets.items():
                row = db.get(Budget, k)
                if v <= 0:
                    if row:
                        db.delete(row)
                elif row:
                    row.monthly = v
                else:
                    db.add(Budget(category=k, monthly=v))
        return {"ok": True}

    @app.get("/api/money/export")
    def money_export(month: str | None = None, info=Depends(need("money:read"))):
        rows = expenses(month, info)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["on", "merchant", "category", "amount", "account", "source", "note"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="expenses-{month or today():%Y-%m}.csv"'})

    def bill_row(b: Bill) -> dict:
        d = (b.due.date() - today()).days
        status = "paid" if b.paid_on else "overdue" if d < 0 else "due"
        return {"id": b.id, "name": b.name, "amount": b.amount, "due": b.due.date().isoformat(), "days": d,
                "recurrence": b.recurrence, "category": b.category, "pay_hint": b.pay_hint, "status": status,
                "paid_on": b.paid_on.date().isoformat() if b.paid_on else None,
                "snoozed": bool(b.snoozed_until and b.snoozed_until.date() >= today())}

    @app.get("/api/money/bills")
    def bills(info=Depends(need("money:read"))):
        with Db() as db:
            rows = [bill_row(b) for b in db.execute(select(Bill).order_by(Bill.due)).scalars()]
        month = today().strftime("%Y-%m")
        unpaid = [r for r in rows if r["status"] != "paid"]
        paid_m = [r for r in rows if r["paid_on"] and r["paid_on"].startswith(month)]
        due_m = [r for r in unpaid if r["due"].startswith(month)]
        soon = [r for r in unpaid if 0 <= r["days"] <= 10]
        over = [r for r in unpaid if r["days"] < 0]
        s = lambda xs: {"count": len(xs), "amount": round(sum(x["amount"] for x in xs), 2)}  # noqa: E731
        return {"bills": rows, "overdue": s(over), "next_10_days": s(soon), "this_month": s(due_m),
                "paid_this_month": s(paid_m),
                "reminders": state_get_safe("bill_reminders", {"days_before": [7, 3, 1, 0], "channels": ["app"]})}

    def state_get_safe(key, default):
        with Db() as db:
            return state_get(db, key, default)

    @app.post("/api/money/bills")
    def add_bill(body: BillBody, info=Depends(need("money:read"))):
        if body.recurrence not in ("none", "monthly", "yearly"):
            raise HTTPException(400, "recurrence is none, monthly or yearly")
        with Db() as db, db.begin():
            db.add(Bill(name=body.name, amount=body.amount, due=datetime.combine(body.due, datetime.min.time()),
                        recurrence=body.recurrence, category=body.category, pay_hint=body.pay_hint))
            audit.record(db, "money", "bill_added", body.model_dump(mode="json"), info.username)
        return {"ok": True}

    @app.post("/api/money/bills/{bid}/paid")
    def bill_paid(bid: int, info=Depends(need("money:read"))):
        with Db() as db, db.begin():
            b = db.get(Bill, bid)
            if not b:
                raise HTTPException(404, "no bill")
            b.paid_on = datetime.combine(today(), datetime.min.time())
            if b.recurrence != "none":
                d = b.due.date()
                nxt = (date(d.year + (d.month == 12), d.month % 12 + 1, min(d.day, 28)) if b.recurrence == "monthly"
                       else date(d.year + 1, d.month, min(d.day, 28)))
                db.add(Bill(name=b.name, amount=b.amount, due=datetime.combine(nxt, datetime.min.time()),
                            recurrence=b.recurrence, category=b.category, pay_hint=b.pay_hint))
            audit.record(db, "money", "bill_paid", {"id": bid, "name": b.name}, info.username)
        return {"ok": True}

    @app.post("/api/money/bills/{bid}/snooze")
    def bill_snooze(bid: int, body: SnoozeBody, info=Depends(need("money:read"))):
        with Db() as db, db.begin():
            b = db.get(Bill, bid)
            if not b:
                raise HTTPException(404, "no bill")
            b.snoozed_until = datetime.combine(today() + timedelta(days=max(1, body.days)), datetime.min.time())
        return {"ok": True}

    @app.delete("/api/money/bills/{bid}")
    def del_bill(bid: int, info=Depends(need("money:read"))):
        with Db() as db, db.begin():
            b = db.get(Bill, bid)
            if b:
                db.delete(b)
        return {"ok": True}

    @app.put("/api/money/reminders")
    def put_reminders(body: dict, info=Depends(need("money:read"))):
        with Db() as db, db.begin():
            state_set(db, "bill_reminders", {"days_before": [int(x) for x in body.get("days_before", [])][:6],
                                             "channels": [x for x in body.get("channels", []) if x in ("app",)]})
        return {"ok": True}

    # ------------------------------------------------------------ dashboards
    @app.get("/api/dashboards")
    def dashboards(info=Depends(current)):
        with Db() as db:
            return state_get(db, f"dash:{info.username}", {"dashboards": [
                {"name": "My overview", "widgets": [{"type": "net_worth", "w": 3}, {"type": "day_pnl", "w": 3},
                                                    {"type": "tax_so_far", "w": 3}, {"type": "bills_due", "w": 3},
                                                    {"type": "equity_curve", "w": 8},
                                                    {"type": "spend_by_category", "w": 4},
                                                    {"type": "open_positions", "w": 6}]}]})

    @app.put("/api/dashboards")
    def put_dashboards(body: DashBody, info=Depends(current)):
        if len(json.dumps(body.dashboards)) > 50_000:
            raise HTTPException(400, "layout too large")
        with Db() as db, db.begin():
            state_set(db, f"dash:{info.username}", {"dashboards": body.dashboards})
        return {"ok": True}

    # ------------------------------------------------------------ users and sessions
    @app.patch("/api/users/{username}")
    def patch_user(username: str, body: UserPatch, info=Depends(need("users:manage"))):
        with Db() as db, db.begin():
            u = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if not u:
                raise HTTPException(404, "no user")
            if username == info.username and (body.active is False or (body.role and body.role != "owner")):
                raise HTTPException(400, "you cannot demote or disable yourself")
            if body.role is not None:
                if body.role not in ROLES:
                    raise HTTPException(400, f"role must be one of {sorted(ROLES)}")
                u.role = body.role
            if body.active is not None:
                u.active = body.active
            audit.record(db, "users", "user_changed", {"username": username, **body.model_dump(exclude_none=True)},
                         info.username)
        if body.active is False or body.role is not None:
            c.sessions.drop_user(username)
        return {"ok": True}

    @app.get("/api/sessions")
    def sessions_list(request: Request, info=Depends(need("users:manage"))):
        mine = request.cookies.get("tdp_session")
        return [{"id": hashlib.sha256(t.encode()).hexdigest()[:12], "username": s.username, "role": s.role,
                 "last_seen": s.last_seen.isoformat(timespec="minutes"), "current": t == mine}
                for t, s in c.sessions.all()]

    @app.delete("/api/sessions/{sid}")
    def session_drop(sid: str, info=Depends(need("users:manage"))):
        for t, s in c.sessions.all():
            if hashlib.sha256(t.encode()).hexdigest()[:12] == sid:
                c.sessions.drop(t)
                with Db() as db, db.begin():
                    audit.record(db, "auth", "session_revoked", {"username": s.username}, info.username)
                return {"ok": True}
        raise HTTPException(404, "no such session")

    @app.get("/api/me")
    def me(info=Depends(current)):
        with Db() as db:
            u = db.execute(select(User).where(User.username == info.username)).scalar_one()
            last = db.execute(select(audit.AuditLog).where(audit.AuditLog.username == info.username,
                                                           audit.AuditLog.action == "login")
                              .order_by(audit.AuditLog.id.desc()).limit(2)).scalars().all()
        return {"username": u.username, "role": u.role, "totp": bool(u.totp_secret),
                "previous_login": last[1].ts.isoformat() + "Z" if len(last) > 1 else None}

    # ------------------------------------------------------------ AI assistant (read-only tools)
    TOOLS = [
        {"name": "portfolio_summary", "description": "Net worth, invested, P/L, allocation and top holdings.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "tax_summary", "description": "This financial year's estimated tax by income type, advance tax "
                                               "schedule and LTCG exemption used.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "spending_summary", "description": "Spending by category, income and budgets for a month.",
         "input_schema": {"type": "object", "properties": {"month": {"type": "string", "description": "YYYY-MM"}},
                          "additionalProperties": False}},
        {"name": "bills_due", "description": "Upcoming and overdue bills.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "orders_today", "description": "Today's tickets and orders with their risk verdicts.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "signals_today", "description": "Today's strategy signals and the market regime.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "latest_brief", "description": "The latest pre-market brief.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "charges_month", "description": "Charges and realised P/L for a month.",
         "input_schema": {"type": "object", "properties": {"month": {"type": "string", "description": "YYYY-MM"}},
                          "additionalProperties": False}},
        {"name": "metals_summary", "description": "Digital gold/silver/platinum holdings.",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    ]

    def run_tool(name: str, args: dict, info) -> Any:
        if name == "portfolio_summary":
            p = portfolio_overview(info)
            return {k: p[k] for k in ("net_worth", "invested", "value", "pnl", "pnl_pct", "allocation")} | \
                {"top_holdings": p["holdings"][:10]}
        if name == "tax_summary":
            t = compute_tax(today(), tax.fy_of(today()))
            return {k: t[k] for k in ("fy", "rows", "total", "projected", "next_due", "ltcg_used", "ltcg_left")}
        if name == "spending_summary":
            return money_summary(args.get("month"), info)
        if name == "bills_due":
            return bills(info)
        if name == "orders_today":
            with Db() as db:
                start = datetime.combine(today(), datetime.min.time()) - timedelta(hours=5, minutes=30)
                return [{"tag": r.client_tag, "stage": r.stage, "status": r.status, "order": r.request,
                         "reasons": (r.verdict or {}).get("reasons")}
                        for r in db.execute(select(DeskOrder).where(DeskOrder.created_at >= start)).scalars()]
        if name == "signals_today":
            return app.state.signals_summary_fn(info)
        if name == "latest_brief":
            from ..db.models import Brief
            with Db() as db:
                b = db.execute(select(Brief).where(Brief.kind == "pre").order_by(Brief.id.desc()).limit(1)).scalar_one_or_none()
                return {"day": b.day, **{k: b.body.get(k) for k in ("headline", "summary", "levels", "setups")}} if b else None
        if name == "charges_month":
            return app.state.charges_month_fn(args.get("month"), info)
        if name == "metals_summary":
            with Db() as db:
                return metals_summary(db)
        raise ValueError(f"unknown tool {name}")

    SYSTEM = ("You are the read-only assistant inside Trader Desk Pro, a local app for one Indian household's "
              "investments, trading and spending. Answer from the user's own data by calling the tools; never "
              "invent numbers. Amounts are INR unless stated. You cannot place orders, move money or change "
              "settings; if asked, explain how the user can do it in the app. Keep answers short and concrete. "
              "End with one line noting this is not investment, tax or financial advice when you give figures.")

    def settings_get() -> dict:
        with Db() as db:
            s = state_get(db, "assistant", {"local_for_money": False, "log_tools": True})
        s["key_set"] = bool(c.secrets.get("assistant:anthropic_api_key"))
        return s

    @app.get("/api/assistant/settings")
    def assistant_settings(info=Depends(current)):
        return settings_get() | {"model": "claude-sonnet-5-5"}

    @app.put("/api/assistant/settings")
    def put_assistant_settings(body: AssistantSettings, info=Depends(need("users:manage"))):
        if body.api_key is not None:
            if body.api_key.strip():
                c.secrets.set("assistant:anthropic_api_key", body.api_key.strip())
            else:
                c.secrets.delete("assistant:anthropic_api_key")
        with Db() as db, db.begin():
            s = state_get(db, "assistant", {"local_for_money": False, "log_tools": True})
            for k in ("local_for_money", "log_tools"):
                if getattr(body, k) is not None:
                    s[k] = getattr(body, k)
            state_set(db, "assistant", s)
            audit.record(db, "ai", "settings", {"key_changed": body.api_key is not None, **s}, info.username)
        return {"ok": True}

    @app.get("/api/assistant/threads")
    def threads(info=Depends(current)):
        with Db() as db:
            rows = db.execute(select(ChatMessage).where(ChatMessage.username == info.username, ChatMessage.role == "user")
                              .order_by(ChatMessage.id)).scalars()
            first: dict[str, dict] = {}
            for m in rows:
                first.setdefault(m.thread, {"id": m.thread, "title": m.content.get("text", "")[:60],
                                            "at": m.created_at.isoformat() + "Z"})
        return sorted(first.values(), key=lambda t: t["at"], reverse=True)

    @app.get("/api/assistant/threads/{tid}")
    def thread(tid: str, info=Depends(current)):
        with Db() as db:
            return [{"role": m.role, **m.content, "at": m.created_at.isoformat() + "Z"}
                    for m in db.execute(select(ChatMessage).where(ChatMessage.thread == tid,
                                                                  ChatMessage.username == info.username)
                                        .order_by(ChatMessage.id)).scalars()]

    def history(tid: str, username: str) -> list[dict]:
        out = []
        with Db() as db:
            for m in db.execute(select(ChatMessage).where(ChatMessage.thread == tid, ChatMessage.username == username)
                                .order_by(ChatMessage.id)).scalars():
                out.append({"role": m.role, "content": m.content.get("text", "")})
        return out[-20:]

    def ask_claude(msgs: list[dict], info) -> tuple[str, list[str], str]:
        import anthropic
        key = c.secrets.get("assistant:anthropic_api_key")
        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        used: list[str] = []
        log_tools = settings_get().get("log_tools", True)
        for _ in range(8):
            resp = client.beta.messages.create(
                model="claude-sonnet-5-5", max_tokens=4000, system=SYSTEM, tools=TOOLS, messages=msgs,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            if resp.stop_reason == "refusal":
                return "I can't help with that request.", used, resp.model
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                return text or "(no answer)", used, resp.model
            msgs.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                used.append(b.name)
                try:
                    data = run_tool(b.name, dict(b.input or {}), info)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(data, default=str)[:60_000]})
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": str(e)})
                if log_tools:
                    with Db() as db, db.begin():
                        audit.record(db, "ai", "tool_call", {"tool": b.name, "provider": "claude"}, info.username)
            msgs.append({"role": "user", "content": results})
        return "I stopped after too many tool calls. Try a narrower question.", used, "claude-sonnet-5-5"

    def ask_ollama(msgs: list[dict], info) -> tuple[str, list[str], str]:
        """Local model: no tool calling; a compact read-only data summary goes in the prompt."""
        import httpx
        used = ["portfolio_summary", "tax_summary", "spending_summary", "bills_due"]
        ctx = {t: run_tool(t, {}, info) for t in used}
        try:
            tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1).json().get("models", [])
        except Exception:
            raise HTTPException(503, "Ollama is not running on 127.0.0.1:11434")
        if not tags:
            raise HTTPException(503, "Ollama has no models; run `ollama pull llama3.1:8b`")
        model = tags[0]["name"]
        sys = SYSTEM + "\n\nUser data (JSON):\n" + json.dumps(ctx, default=str)[:24_000]
        r = httpx.post("http://127.0.0.1:11434/api/chat", timeout=120,
                       json={"model": model, "stream": False, "messages": [{"role": "system", "content": sys}] + msgs})
        r.raise_for_status()
        return r.json().get("message", {}).get("content", ""), used, f"ollama:{model}"

    @app.post("/api/assistant/ask")
    def ask(body: AskBody, info=Depends(need("portfolio:read"))):
        tid = body.thread or str(uuid.uuid4())
        msgs = history(tid, info.username) + [{"role": "user", "content": body.text}]
        s = settings_get()
        provider = body.provider
        if provider == "claude" and s.get("local_for_money") and any(
                w in body.text.lower() for w in ("spend", "expense", "bill", "tax", "salary", "budget")):
            provider = "ollama"
        try:
            if provider == "ollama":
                text, used, model = ask_ollama(msgs, info)
            else:
                text, used, model = ask_claude(msgs, info)
        except HTTPException:
            raise
        except Exception as e:
            name = type(e).__name__
            hint = " Add an Anthropic API key in AI assistant settings." if "Authentication" in name or "api_key" in str(e) else ""
            raise HTTPException(502, f"{name}: {e}.{hint}")
        with Db() as db, db.begin():
            db.add(ChatMessage(thread=tid, username=info.username, role="user", content={"text": body.text}))
            db.add(ChatMessage(thread=tid, username=info.username, role="assistant",
                               content={"text": text, "tools": used, "model": model}))
        return {"thread": tid, "text": text, "tools": used, "model": model}
