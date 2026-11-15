import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, istToday, monthOf, signed } from "../api.js";
import { COLORS, Card, Empty, Msg, Tabs, useAction } from "../ui.jsx";

const short = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });

function Donut({ parts }) {
  const total = parts.reduce((s, [, v]) => s + v, 0) || 1;
  let a = -Math.PI / 2;
  const R = 60, r = 38, C = 70;
  return (
    <svg viewBox="0 0 140 140" width="140" height="140" role="img" aria-label="spend by category">
      {parts.map(([k, v], i) => {
        const a2 = a + (v / total) * Math.PI * 2, large = a2 - a > Math.PI ? 1 : 0;
        const p = (ang, rad) => `${C + rad * Math.cos(ang)} ${C + rad * Math.sin(ang)}`;
        const d = `M ${p(a, R)} A ${R} ${R} 0 ${large} 1 ${p(a2, R)} L ${p(a2, r)} A ${r} ${r} 0 ${large} 0 ${p(a, r)} Z`;
        a = a2;
        return <path key={k} d={d} fill={COLORS[i % COLORS.length]} />;
      })}
      <text x="70" y="74" textAnchor="middle" fontSize="11" fill="var(--muted)">spent</text>
    </svg>
  );
}

function Bars({ data, budget }) {
  if (!data.length) return <Empty>No spending recorded yet.</Empty>;
  const max = Math.max(budget || 0, ...data.map((d) => d.spent)) * 1.1 || 1;
  const W = 600, H = 180, bw = W / data.length;
  return (
    <svg viewBox={`0 0 ${W} ${H + 18}`} width="100%" role="img" aria-label="monthly spend vs budget">
      {data.map((d, i) => { const h = (d.spent / max) * H; return (
        <g key={d.month}><rect x={i * bw + bw * 0.18} y={H - h} width={bw * 0.64} height={h} rx="3" fill={budget && d.spent > budget ? "#f0625a" : "#5b8def"} />
          <text x={i * bw + bw / 2} y={H + 14} fontSize="10" fill="var(--muted)" textAnchor="middle">{d.month.slice(5)}</text></g>); })}
      {budget > 0 && <><line x1="0" x2={W} y1={H - (budget / max) * H} y2={H - (budget / max) * H} stroke="#e3b341" strokeDasharray="5 4" />
        <text x={W - 4} y={H - (budget / max) * H - 4} fontSize="11" fill="#e3b341" textAnchor="end">budget</text></>}
    </svg>
  );
}

function Expenses() {
  const [month, setMonth] = useState(monthOf(0));
  const [open, setOpen] = useState(false);
  const s = useQuery({ queryKey: ["money-sum", month], queryFn: () => api(`/money/summary?month=${month}`) });
  const rows = useQuery({ queryKey: ["expenses", month], queryFn: () => api(`/money/expenses?month=${month}`) });
  const [act, msg] = useAction();
  const [f, setF] = useState({ on: istToday(), merchant: "", amount: "", category: "Food and groceries", account: "Cash" });
  const d = s.data;
  if (!d) return <Empty>Loading…</Empty>;
  const parts = Object.entries(d.by_category);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between">
        <div className="seg">{[[0, "This month"], [-1, "Last month"], [-2, monthOf(-2)]].map(([o, l]) => <button key={o} className={month === monthOf(o) ? "on" : ""} onClick={() => setMonth(monthOf(o))}>{l}</button>)}</div>
        <div className="row"><button className="btn primary" onClick={() => setOpen(!open)}>+ Add expense</button><a className="btn" href={`/api/money/export?month=${month}`}>Export CSV</a></div>
      </div>
      {open && <Card title="Manual entry" className="hl" action={<button className="btn small" onClick={() => setOpen(false)}>Close</button>}>
        <form className="fields" onSubmit={(e) => { e.preventDefault(); act.mutate({ path: "/money/expenses", body: { ...f, amount: Number(f.amount) }, done: "Added.", then: () => setF({ ...f, merchant: "", amount: "" }) }); }}>
          <label className="f">Merchant or note<input value={f.merchant} onChange={(e) => setF({ ...f, merchant: e.target.value })} required /></label>
          <label className="f">Amount (₹)<input className="mono" type="number" step="0.01" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required /></label>
          <label className="f">Category<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{d.categories.map((c) => <option key={c}>{c}</option>)}</select></label>
          <label className="f">Paid with<input value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })} /></label>
          <label className="f">Date<input type="date" value={f.on} onChange={(e) => setF({ ...f, on: e.target.value })} /></label>
          <div style={{ alignSelf: "end" }}><button className="btn primary">Add</button></div>
        </form><Msg msg={msg} />
        <p className="note">Income goes in with category Income. Investing categories count as invested, not spent.</p>
      </Card>}
      <div className="grid g4">
        <div className="card kpi"><div className="label">Spent · {month}</div><div className="value">{inr(d.spent)}</div><div className="sub">{d.budget ? `of ${inr(d.budget)} budget · ${Math.round((d.spent / d.budget) * 100)}%` : "set budgets in Budgets"}</div></div>
        <div className="card kpi"><div className="label">Income</div><div className="value gain">{inr(d.income)}</div></div>
        <div className="card kpi"><div className="label">Invested</div><div className="value">{inr(d.invested)}</div><div className="sub">categories starting “Invest”</div></div>
        <div className="card kpi"><div className="label">Saved + invested</div><div className="value gain">{d.saved_pct != null ? `${d.saved_pct}%` : "—"}</div><div className="sub">of income this month</div></div>
      </div>
      <div className="grid side-r">
        <Card title={`Monthly spend vs ${inr(d.budget)} budget`}><Bars data={d.monthly} budget={d.budget} /></Card>
        <Card title="Where it went">
          {!parts.length ? <Empty>No spending this month.</Empty> : <div className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}><Donut parts={parts} />
            <div className="list" style={{ flex: 1 }}>{parts.map(([k, v], i) => <div key={k}><span><span className="dot" style={{ background: COLORS[i % COLORS.length] }} /> {k}</span><b className="mono">{inr(v)}</b></div>)}</div></div>}
        </Card>
      </div>
      <div className="grid side-r">
        <Card title="Transactions">
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Merchant</th><th>Category</th><th>Account</th><th>Source</th><th className="num">Amount</th><th /></tr></thead>
            <tbody>{(rows.data || []).map((e) => (
              <tr key={e.id}><td className="mono">{short(e.on)}</td><td><b>{e.merchant}</b></td><td><span className="pill">{e.category}</span></td><td>{e.account}</td><td><span className="tag">{e.source}</span></td>
                <td className={`num ${cls(e.amount)}`}>{signed(e.amount)}</td><td><button className="btn small" aria-label="Delete" onClick={() => act.mutate({ path: `/money/expenses/${e.id}`, method: "DELETE" })}>×</button></td></tr>))}
              {rows.data && !rows.data.length && <tr><td colSpan={7} className="muted">No transactions this month.</td></tr>}</tbody>
          </table></div>
        </Card>
        <Card title="Budgets">
          <div className="stack">{Object.entries(d.budgets).map(([k, b]) => { const v = d.by_category[k] || 0, over = v > b; return (
            <div key={k}><div className="row between"><span>{k}</span><span className={`mono ${over ? "loss" : ""}`}>{inr(v)} / {inr(b)}{over ? " · over" : ""}</span></div>
              <div className="bar"><span style={{ width: `${Math.min(100, (v / b) * 100)}%`, background: over ? "var(--loss)" : "var(--accent)" }} /></div></div>); })}
            {!Object.keys(d.budgets).length && <div className="note">No budgets yet (Budgets tab).</div>}</div>
        </Card>
      </div>
    </div>
  );
}

function Budgets() {
  const s = useQuery({ queryKey: ["money-sum", monthOf(0)], queryFn: () => api(`/money/summary?month=${monthOf(0)}`) });
  const [b, setB] = useState({});
  const [act, msg] = useAction();
  useEffect(() => { if (s.data) setB(Object.fromEntries(s.data.categories.filter((c) => c !== "Income").map((c) => [c, s.data.budgets[c] ?? ""]))); }, [s.data]);
  if (!s.data) return <Empty>Loading…</Empty>;
  return (
    <Card title="Monthly budgets" action={<button className="btn primary small" onClick={() => act.mutate({ path: "/money/budgets", method: "PUT", body: { budgets: Object.fromEntries(Object.entries(b).map(([k, v]) => [k, Number(v) || 0])) }, done: "Budgets saved." })}>Save</button>}>
      <div className="fields">{Object.entries(b).map(([k, v]) => <label key={k} className="f">{k}<input className="mono" type="number" min="0" value={v} onChange={(e) => setB({ ...b, [k]: e.target.value })} placeholder="no budget" /></label>)}</div>
      <Msg msg={msg} />
    </Card>
  );
}

function Bills() {
  const q = useQuery({ queryKey: ["bills"], queryFn: () => api("/money/bills") });
  const [act, msg] = useAction();
  const [f, setF] = useState({ name: "", amount: "", due: istToday(), recurrence: "monthly", pay_hint: "Pay at biller" });
  const d = q.data;
  if (!d) return <Empty>Loading…</Empty>;
  const now = new Date(); const y = now.getFullYear(), m = now.getMonth();
  const first = new Date(y, m, 1).getDay(), days = new Date(y, m + 1, 0).getDate();
  const byDay = {};
  d.bills.forEach((b) => { const dt = new Date(b.due + "T00:00:00"); if (dt.getFullYear() === y && dt.getMonth() === m) byDay[dt.getDate()] = b.status; });
  const kpi = (label, v, c) => <div className={`card kpi ${c || ""}`}><div className="label">{label}</div><div className="value">{v.count} · {inr(v.amount)}</div></div>;
  return (
    <div className="grid side-r" style={{ alignItems: "start" }}>
      <div className="stack" style={{ gap: 16 }}>
        <div className="grid g4">{kpi("Overdue", d.overdue, d.overdue.count ? "badbox" : "")}{kpi("Due in next 10 days", d.next_10_days, d.next_10_days.count ? "warnbox" : "")}{kpi("Due this month", d.this_month)}{kpi("Paid this month", d.paid_this_month)}</div>
        <Card title="Upcoming and overdue">
          <div className="stack">{d.bills.filter((b) => b.status !== "paid" || b.paid_on?.startsWith(monthOf(0))).map((b) => (
            <div key={b.id} className={`mini ${b.status === "overdue" ? "badbox" : ""}`} style={b.status === "overdue" ? { borderColor: "var(--loss)" } : b.status === "due" && b.days <= 10 ? { borderColor: "var(--warn)" } : null}>
              <div className="row between">
                <div><b>{b.name}</b><div className="note">Due {short(b.due)}{b.recurrence !== "none" ? ` · recurring ${b.recurrence}` : ""}{b.pay_hint ? ` · ${b.pay_hint}` : ""}</div></div>
                <div className="row"><b className="mono">{inr(b.amount)}</b>
                  {b.status === "paid" ? <span className="pill good">✓ Paid {b.paid_on && short(b.paid_on)}</span> : <>
                    <span className={`pill ${b.status === "overdue" ? "bad" : b.days <= 10 ? "warn" : ""}`}>{b.status === "overdue" ? `Overdue ${-b.days} days` : b.days === 0 ? "Today" : `In ${b.days} days`}</span>
                    <button className="btn small" onClick={() => act.mutate({ path: `/money/bills/${b.id}/paid` })}>Mark paid</button>
                    <button className="btn small" disabled={b.snoozed} onClick={() => act.mutate({ path: `/money/bills/${b.id}/snooze`, body: { days: 3 } })}>{b.snoozed ? "Snoozed" : "Snooze"}</button></>}
                  <button className="btn small" aria-label="Delete bill" onClick={() => window.confirm(`Delete ${b.name}?`) && act.mutate({ path: `/money/bills/${b.id}`, method: "DELETE" })}>×</button>
                </div></div></div>))}
            {!d.bills.length && <Empty>No bills yet. Add one below.</Empty>}</div>
          <p className="note">The app never pays bills. Mark a bill paid when you've paid it; a recurring bill then gets its next due date.</p>
        </Card>
        <Card title="Add bill">
          <form className="fields" onSubmit={(e) => { e.preventDefault(); act.mutate({ path: "/money/bills", body: { ...f, amount: Number(f.amount) }, done: "Bill added.", then: () => setF({ ...f, name: "", amount: "" }) }); }}>
            <label className="f">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></label>
            <label className="f">Amount (₹)<input className="mono" type="number" step="0.01" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required /></label>
            <label className="f">Due<input type="date" value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} /></label>
            <label className="f">Repeats<select value={f.recurrence} onChange={(e) => setF({ ...f, recurrence: e.target.value })}><option value="none">No</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option></select></label>
            <label className="f">How to pay<input value={f.pay_hint} onChange={(e) => setF({ ...f, pay_hint: e.target.value })} /></label>
            <div style={{ alignSelf: "end" }}><button className="btn primary">Add bill</button></div>
          </form><Msg msg={msg} />
        </Card>
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <Card title={now.toLocaleDateString("en-IN", { month: "long", year: "numeric" })}>
          <div className="cal">{["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((x) => <b key={x} className="note">{x}</b>)}
            {Array.from({ length: first }, (_, i) => <span key={`b${i}`} />)}
            {Array.from({ length: days }, (_, i) => i + 1).map((n) => (
              <div key={n} className={n === now.getDate() ? "today" : ""}>{n}{byDay[n] && <i style={{ background: byDay[n] === "paid" ? "var(--gain)" : byDay[n] === "overdue" ? "var(--loss)" : "var(--warn)" }} />}</div>))}
          </div>
          <div className="legend"><span><i style={{ background: "var(--loss)" }} />Overdue</span><span><i style={{ background: "var(--warn)" }} />Due</span><span><i style={{ background: "var(--gain)" }} />Paid</span></div>
        </Card>
        <Card title="Reminder settings">
          <div className="stack">
            <b className="small">Remind me</b>
            <div className="grid g2">{[[7, "7 days before"], [3, "3 days before"], [1, "1 day before"], [0, "On the due date"]].map(([n, l]) => (
              <label key={n} className="check"><input type="checkbox" checked={d.reminders.days_before.includes(n)}
                onChange={(e) => act.mutate({ path: "/money/reminders", method: "PUT", body: { ...d.reminders, days_before: e.target.checked ? [...d.reminders.days_before, n] : d.reminders.days_before.filter((x) => x !== n) } })} />{l}</label>))}</div>
            <b className="small">Send to</b>
            <label className="check"><input type="checkbox" checked readOnly />In the app (alerts bell, Bills count)</label>
            <div className="note">Telegram, email and calendar delivery are not built yet.</div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Sources() {
  const s = useQuery({ queryKey: ["money-sum", monthOf(0)], queryFn: () => api(`/money/summary?month=${monthOf(0)}`) });
  return (
    <Card title="Sources">
      <div className="list">{(s.data?.sources || []).map((x) => <div key={x.name}><span>{x.name}</span><span className="muted">● {x.status}</span></div>)}</div>
      <p className="note">Card, bank and UPI alert emails come in through an email connection: Gmail, Outlook, Yahoo, Zoho, iCloud or any IMAP mailbox, read-only. Add one in Connections, then use “Scan inbox” to review the alerts it found and import the ones you pick.</p>
    </Card>
  );
}

export default function Money({ screen, go }) {
  const tab = screen === "bills" ? "bills" : screen === "budgets" ? "budgets" : screen;
  const [t, setT] = useState(tab === "bills" ? "bills" : "expenses");
  useEffect(() => setT(tab === "bills" ? "bills" : "expenses"), [tab]);
  return (
    <>
      <Tabs tabs={[["expenses", "Expenses"], ["bills", "Bills and reminders"], ["budgets", "Budgets"], ["sources", "Sources"]]} value={t}
        onChange={(v) => { setT(v); if (v === "bills" || v === "expenses") go(v); }} />
      {t === "expenses" && <Expenses />}{t === "bills" && <Bills />}{t === "budgets" && <Budgets />}{t === "sources" && <Sources />}
    </>
  );
}
