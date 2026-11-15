import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, istToday, signed } from "../api.js";
import { Card, Empty, Msg, useAction } from "../ui.jsx";

const fyList = () => { const d = new Date(); const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; return [y, y - 1].map((x) => `${x}-${String(x + 1).slice(2)}`); };
const dl = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });

export default function Tax({ user }) {
  const [fy, setFy] = useState(fyList()[0]);
  const q = useQuery({ queryKey: ["tax", fy], queryFn: () => api(`/tax?fy=${fy}`) });
  const [act, msg] = useAction();
  const [e, setE] = useState({ on: istToday(), kind: "equity_stcg", amount: "", turnover: "", note: "" });
  const [pay, setPay] = useState({ on: istToday(), amount: "" });
  const t = q.data;
  if (q.error) return <div className="error">{q.error.message}</div>;
  if (!t) return <Empty>Loading…</Empty>;
  const used = t.ltcg_used / t.ltcg_exemption * 100;
  const paidBy = (due) => t.payments.filter((p) => p.on <= due).reduce((s, p) => s + p.amount, 0);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between">
        <div className="row"><select className="in" value={fy} onChange={(x) => setFy(x.target.value)} aria-label="Financial year">{fyList().map((x) => <option key={x} value={x}>FY {x}</option>)}</select>
          <span className="chip">Slab rate {Math.round(t.slab * 100)}%</span>
          {user.role === "owner" && <select className="in" value={t.slab} onChange={(x) => act.mutate({ path: "/tax/settings", method: "PUT", body: { slab: Number(x.target.value) } })} aria-label="Slab rate">
            {[0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3].map((r) => <option key={r} value={r}>{r * 100}% slab</option>)}</select>}</div>
        <a className="btn" href={`/api/tax/export?fy=${fy}`}>Export capital gains (CSV)</a>
      </div>
      <div className="grid g4">
        <div className="card kpi"><div className="label">Tax on investments so far</div><div className="value">{inr(t.total)}</div><div className="sub">incl. 4% cess</div></div>
        <div className="card kpi"><div className="label">Projected for full year</div><div className="value">{inr(t.projected)}</div><div className="sub">at current pace</div></div>
        <div className={`card kpi ${t.next_due ? "warnbox" : ""}`}><div className="label">Next advance tax{t.next_due ? ` · ${dl(t.next_due.due)}` : ""}</div>
          <div className="value warnc">{t.next_due ? inr(t.next_due.amount_now) : "—"}</div><div className="sub">{t.next_due ? `to reach ${Math.round(t.next_due.share * 100)}% of projected tax` : "none left this year"}</div></div>
        <div className="card kpi"><div className="label">LTCG exemption used</div><div className="value">{inr(t.ltcg_used)}</div>
          <div className="bar" style={{ margin: "6px 0" }}><span style={{ width: `${Math.min(100, used)}%`, background: "var(--gain)" }} /></div>
          <div className="sub">of {inr(t.ltcg_exemption)} · {inr(t.ltcg_left)} still tax-free</div></div>
      </div>
      <div className="grid side-r">
        <Card title="By income type" action={<span className="note">Slab income at {Math.round(t.slab * 100)}% · {t.live_trades} live trades + manual entries</span>}>
          <div className="table-wrap"><table>
            <thead><tr><th>Type</th><th>Treatment</th><th className="num">Gain / income</th><th className="num">Taxable</th><th className="num">Tax</th></tr></thead>
            <tbody>{t.rows.map((r) => <tr key={r.kind}><td><b>{r.type}</b></td><td>{r.treatment}</td><td className={`num ${cls(r.gain)}`}>{signed(r.gain)}</td><td className="num">{inr(r.taxable)}</td><td className="num">{inr(r.tax)}</td></tr>)}
              <tr><td><b>Total</b></td><td className="muted">+ 4% cess {inr(t.cess)}</td><td /><td /><td className="num"><b>{inr(t.total)}</b></td></tr></tbody>
          </table></div>
        </Card>
        <Card title="Advance tax">
          <div className="stack">{t.advance_tax.map((s) => {
            const paid = paidBy(s.due), ok = paid >= s.cumulative - 1;
            const due = s === t.next_due || (t.next_due && s.due === t.next_due.due);
            return <div key={s.due} className={`mini ${due ? "sel" : ""}`}><div className="row between"><b>{dl(s.due)} · {Math.round(s.share * 100)}%</b>
              <span className={s.cumulative < 1 ? "muted" : ok ? "gain" : s.status === "past" ? "loss" : due ? "warnc" : "muted"}>
                {s.cumulative < 1 ? "Nothing due" : ok ? "✓ Paid" : s.status === "past" ? "Short" : due ? "Due" : "Upcoming"}</span></div>
              <div className="mono note">{inr(s.cumulative)} cumulative · paid {inr(paid)}</div></div>;
          })}</div>
          <form className="row" style={{ marginTop: 10 }} onSubmit={(x) => { x.preventDefault(); act.mutate({ path: "/tax/payments", body: { on: pay.on, amount: Number(pay.amount) }, done: "Payment recorded." }); }}>
            <input className="in" type="date" value={pay.on} onChange={(x) => setPay({ ...pay, on: x.target.value })} aria-label="Paid on" />
            <input className="in mono" style={{ width: 110 }} type="number" placeholder="₹ paid" value={pay.amount} onChange={(x) => setPay({ ...pay, amount: x.target.value })} required aria-label="Amount paid" />
            <button className="btn small">Record payment</button></form>
          <p className="note">Enter salary TDS as a payment too, so the projection only counts tax not already deducted.</p>
        </Card>
      </div>
      <div className="grid g2">
        <Card title="Before 31 March: harvesting ideas">
          <div className="stack">{t.ideas.map((i) => (
            <div key={i.symbol + i.kind} className={`mini ${i.worth_it ? "sel" : ""}`}>
              <div className="row between"><b>{i.text}</b><span className={`mono ${i.worth_it ? "gain" : "muted"}`}>{i.saves_later != null ? `saves ${inr(i.saves_later)} later` : `saves ${inr(i.saves_now)}`}</span></div>
              <div className="note">Charges about {inr(i.charges)}.{i.worth_it ? "" : " Charges are more than the tax saved; not worth it."}</div></div>))}
            {!t.ideas.length && <div className="note">No ideas from current holdings.</div>}</div>
        </Card>
        <Card title="F&O business income">
          <div className="grid g2"><div><div className="note">Turnover (absolute P/L sum)</div><b className="mono" style={{ fontSize: 18 }}>{inr(t.fo.turnover)}</b></div>
            <div><div className="note">Net F&O income</div><b className={`mono ${cls(t.fo.gain)}`} style={{ fontSize: 18 }}>{signed(t.fo.gain)}</b></div></div>
          <div className={`verdict ${t.fo.below_audit_limit ? "PASS" : "BLOCK"}`} style={{ marginTop: 10 }}>{t.fo.below_audit_limit ? "✓ Below tax-audit turnover limits. File ITR-3 with the business-income schedule." : "Above the audit turnover limit: talk to your CA."}</div>
        </Card>
      </div>
      <Card title="Realised gains and income (manual / Console P&L)">
        <form className="fields" onSubmit={(x) => { x.preventDefault(); act.mutate({ path: "/tax/entries", body: { ...e, amount: Number(e.amount), turnover: Number(e.turnover) || 0 }, done: "Added.", then: () => setE({ ...e, amount: "", turnover: "", note: "" }) }); }}>
          <label className="f">Date<input type="date" value={e.on} onChange={(x) => setE({ ...e, on: x.target.value })} /></label>
          <label className="f">Type<select value={e.kind} onChange={(x) => setE({ ...e, kind: x.target.value })}>{Object.entries(t.kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="f">Gain (− for loss)<input className="mono" type="number" step="0.01" value={e.amount} onChange={(x) => setE({ ...e, amount: x.target.value })} required /></label>
          <label className="f">Turnover (F&O)<input className="mono" type="number" step="0.01" value={e.turnover} onChange={(x) => setE({ ...e, turnover: x.target.value })} /></label>
          <label className="f">Note<input value={e.note} onChange={(x) => setE({ ...e, note: x.target.value })} /></label>
          <div style={{ alignSelf: "end" }}><button className="btn primary">Add</button></div>
        </form>
        <Msg msg={msg} />
        <div className="table-wrap" style={{ marginTop: 10 }}><table><thead><tr><th>Date</th><th>Type</th><th className="num">Amount</th><th>Note</th><th /></tr></thead>
          <tbody>{t.entries.map((x) => <tr key={x.id}><td className="mono">{x.on}</td><td>{t.kinds[x.kind]}</td><td className={`num ${cls(x.amount)}`}>{signed(x.amount)}</td><td>{x.note}</td>
            <td><button className="btn small" onClick={() => act.mutate({ path: `/tax/entries/${x.id}`, method: "DELETE" })}>×</button></td></tr>)}
            {!t.entries.length && <tr><td colSpan={5} className="muted">Live Kite fills are counted automatically; paper trades never are. Add realised gains from Kite Console's tax P&L here.</td></tr>}</tbody></table></div>
      </Card>
      <p className="note">Rates: FY {t.fy} from the dated rules file ({t.law}).{t.carried_forward_from ? ` No entry for this year yet; using ${t.carried_forward_from}.` : ""} Estimates only, not tax advice. Check with your CA before filing.</p>
    </div>
  );
}
