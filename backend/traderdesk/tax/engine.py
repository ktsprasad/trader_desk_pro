"""Tax centre arithmetic from the dated rules in config/tax.yaml. Estimates, not tax advice."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

RULES = Path(__file__).resolve().parent.parent / "config" / "tax.yaml"

KINDS = {
    "equity_stcg": ("Equity STCG", "rate:equity_stcg"),
    "equity_ltcg": ("Equity LTCG", "ltcg"),
    "intraday": ("Intraday equity", "slab"),
    "fo": ("F&O", "slab"),
    "foreign": ("US / foreign stocks", "slab"),
    "vda": ("Crypto (VDA)", "rate:vda"),
    "dividend": ("Dividends", "slab"),
    "other": ("Other", "slab"),
}
TREATMENT = {
    "equity_stcg": "{equity_stcg:.0%} flat", "equity_ltcg": "{equity_ltcg:.1%} above ₹{ltcg_exemption:,.0f}",
    "intraday": "Speculative · slab", "fo": "Non-speculative business · slab",
    "foreign": "Slab (held ≤ {foreign_lt_months} months)", "vda": "{vda:.0%} flat · loss not set off",
    "dividend": "Slab", "other": "Slab",
}


@dataclass
class Line:
    kind: str
    amount: float
    turnover: float = 0.0


def load_rules(path: Path = RULES) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def fy_of(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def fy_bounds(fy: str) -> tuple[date, date]:
    y = int(fy[:4])
    return date(y, 4, 1), date(y + 1, 3, 31)


def rules_for(fy: str, all_rules: list[dict] | None = None) -> dict:
    all_rules = all_rules or load_rules()
    for r in all_rules:
        if r["fy"] == fy:
            return r
    # newest rules carry forward to a year without its own entry, flagged as such
    r = dict(sorted(all_rules, key=lambda x: x["fy"])[-1])
    r["carried_forward_from"] = r["fy"]
    r["fy"] = fy
    return r


def compute(lines: list[Line], r: dict, slab: float, today: date, paid: float = 0.0) -> dict:
    by: dict[str, dict] = {k: {"gain": 0.0, "turnover": 0.0} for k in KINDS}
    for ln in lines:
        k = ln.kind if ln.kind in by else "other"
        by[k]["gain"] += ln.amount
        by[k]["turnover"] += ln.turnover

    # short-term equity losses set off against short-term then long-term equity gains
    stcg, ltcg = by["equity_stcg"]["gain"], by["equity_ltcg"]["gain"]
    if stcg < 0:
        ltcg += stcg
        stcg = 0.0
    exemption_used = min(max(ltcg, 0.0), r["ltcg_exemption"])
    rows, total = [], 0.0
    for k, (label, how) in KINDS.items():
        gain = by[k]["gain"]
        if k == "equity_stcg":
            taxable = max(stcg, 0.0)
        elif k == "equity_ltcg":
            taxable = max(ltcg - r["ltcg_exemption"], 0.0)
        elif k == "vda":
            taxable = max(gain, 0.0)
        else:
            taxable = max(gain, 0.0)
        rate = (r[how.split(":")[1]] if how.startswith("rate:") else r["equity_ltcg"] if how == "ltcg" else slab)
        tax = round(taxable * rate, 2)
        total += tax
        if gain or k in ("equity_stcg", "equity_ltcg"):
            rows.append({"kind": k, "type": label, "treatment": TREATMENT[k].format(**r), "gain": round(gain, 2),
                         "taxable": round(taxable, 2), "tax": tax, "rate": rate})
    cess = round(total * r["cess"], 2)
    total_tax = round(total + cess, 2)

    start, end = fy_bounds(r["fy"])
    elapsed = max(1, (min(today, end) - start).days + 1)
    projected = round(total_tax * 365 / elapsed, 2) if today < end else total_tax
    schedule = []
    for s in r["advance_tax"]:
        due = s["due"] if isinstance(s["due"], date) else date.fromisoformat(str(s["due"]))
        schedule.append({"due": due.isoformat(), "share": s["share"], "cumulative": round(projected * s["share"], 2),
                         "status": "past" if due < today else "upcoming"})
    nxt = next((s for s in schedule if s["status"] == "upcoming"), None)
    if nxt:
        nxt["amount_now"] = round(max(0.0, nxt["cumulative"] - paid), 2)
    fo = by["fo"]
    return {"fy": r["fy"], "law": r.get("law"), "rows": rows, "tax_before_cess": round(total, 2), "cess": cess,
            "total": total_tax, "projected": projected, "paid": round(paid, 2), "advance_tax": schedule,
            "next_due": nxt, "ltcg_exemption": r["ltcg_exemption"], "ltcg_used": round(exemption_used, 2),
            "ltcg_left": round(r["ltcg_exemption"] - exemption_used, 2),
            "fo": {"turnover": round(fo["turnover"], 2), "gain": round(fo["gain"], 2),
                   "below_audit_limit": fo["turnover"] < r["audit_turnover_limit"]},
            "carried_forward_from": r.get("carried_forward_from")}


def holding_term(first_seen: date, today: date, asset_class: str, r: dict) -> dict:
    months_needed = r["foreign_lt_months"] if asset_class in ("us_stock", "gold", "reit_invit") else r["equity_lt_months"]
    held = (today.year - first_seen.year) * 12 + today.month - first_seen.month - (today.day < first_seen.day)
    return {"months_held": held, "long_term": held >= months_needed,
            "months_to_long_term": max(0, months_needed - held), "rule_months": months_needed}
