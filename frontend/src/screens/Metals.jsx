import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, istToday, num, pct, signed } from "../api.js";
import { Card, Empty, Msg, useAction } from "../ui.jsx";

const DOT = { gold: "#eab308", silver: "#9ca3af", platinum: "#22d3ee" };

export default function Metals() {
  const q = useQuery({ queryKey: ["metals"], queryFn: () => api("/metals") });
  const [act, msg] = useAction();
  const [f, setF] = useState({ metal: "gold", app: "PhonePe", vault: "SafeGold", purity: "99.5%", grams: "", cost: "", bought_on: istToday() });
  const [rates, setRates] = useState({});
  const m = q.data;
  useEffect(() => { if (m) setRates(Object.fromEntries(Object.entries(m.by_metal).map(([k, v]) => [k, { sell: v.sell ?? "", buy: v.buy ?? "" }]))); }, [m?.rates_as_of]);
  if (!m) return <Empty>Loading…</Empty>;
  const add = (e) => { e.preventDefault(); act.mutate({ path: "/metals", body: { ...f, grams: Number(f.grams), cost: Number(f.cost) }, done: "Added.", then: () => setF({ ...f, grams: "", cost: "" }) }); };
  const saveRates = () => act.mutate({ path: "/metals/rates", method: "PUT", done: "Rates saved.",
    body: { rates: Object.fromEntries(Object.entries(rates).map(([k, v]) => [k, { sell: Number(v.sell) || 0, buy: Number(v.buy) || 0 }])) } });
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card warnbox"><b>⚠ Unregulated product.</b> Digital gold sold on apps is not a SEBI-regulated security or commodity derivative, so SEBI investor protection does not apply. Regulated alternatives: gold ETFs, gold mutual funds and SGBs.</div>
      <div className="grid g4">
        <div className="card kpi"><div className="label">Metals value</div><div className="value">{inr(m.value)}</div>
          <div className={`sub ${cls(m.pnl)}`}>{m.value != null ? `${signed(m.pnl)} · ${pct((m.pnl / m.cost) * 100, 1)} on ${inr(m.cost)}` : "enter today's sell rates →"}</div></div>
        {["gold", "silver", "platinum"].map((k) => (
          <div className="card kpi" key={k}><div className="label"><span className="dot" style={{ background: DOT[k] }} /> {k[0].toUpperCase() + k.slice(1)}{k === "gold" ? " · 24K" : ""}</div>
            <div className="value">{num(m.by_metal[k].grams, 2)} g</div>
            <div className="row" style={{ marginTop: 6 }}>
              <input className="in mono" style={{ width: 90 }} placeholder="sell ₹/g" value={rates[k]?.sell ?? ""} onChange={(e) => setRates({ ...rates, [k]: { ...rates[k], sell: e.target.value } })} aria-label={`${k} sell rate`} />
              <input className="in mono" style={{ width: 90 }} placeholder="buy ₹/g" value={rates[k]?.buy ?? ""} onChange={(e) => setRates({ ...rates, [k]: { ...rates[k], buy: e.target.value } })} aria-label={`${k} buy rate`} />
            </div></div>))}
      </div>
      <div className="row"><button className="btn small" onClick={saveRates}>Save rates</button><span className="note">Rates are the provider's sell price per gram, entered by you{m.rates_as_of ? ` · last ${m.rates_as_of.replace("T", " ")}` : ""}. No app exposes a public price API.</span><Msg msg={msg} /></div>

      <div className="grid side-r">
        <Card title="Holdings by app and vault">
          <div className="table-wrap"><table>
            <thead><tr><th>Holding</th><th>App · vault</th><th>Purity</th><th className="num">Grams</th><th className="num">Cost incl. GST</th><th className="num">Value</th><th className="num">P/L</th><th>Since</th><th /></tr></thead>
            <tbody>{m.holdings.map((h) => (
              <tr key={h.id}><td><span className="dot" style={{ background: DOT[h.metal] }} /> <b>{h.metal}</b></td><td>{h.app} · {h.vault}</td><td>{h.purity}</td>
                <td className="num">{num(h.grams, 4)}</td><td className="num">{inr(h.cost)}</td><td className="num">{inr(h.value)}</td>
                <td className={`num ${cls(h.pnl)}`}>{signed(h.pnl)}</td><td className="mono">{h.bought_on}</td>
                <td><button className="btn small" aria-label="Delete" onClick={() => window.confirm("Delete this holding?") && act.mutate({ path: `/metals/${h.id}`, method: "DELETE" })}>×</button></td></tr>))}
              {!m.holdings.length && <tr><td colSpan={9} className="muted">No metal holdings yet. Add one on the right.</td></tr>}</tbody>
          </table></div>
        </Card>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Add holding">
            <form className="fields" onSubmit={add}>
              <label className="f">Metal<select value={f.metal} onChange={(e) => setF({ ...f, metal: e.target.value })}><option>gold</option><option>silver</option><option>platinum</option></select></label>
              <label className="f">App<input value={f.app} onChange={(e) => setF({ ...f, app: e.target.value })} required /></label>
              <label className="f">Vault<input value={f.vault} onChange={(e) => setF({ ...f, vault: e.target.value })} required /></label>
              <label className="f">Purity<input value={f.purity} onChange={(e) => setF({ ...f, purity: e.target.value })} /></label>
              <label className="f">Grams<input className="mono" type="number" step="0.0001" value={f.grams} onChange={(e) => setF({ ...f, grams: e.target.value })} required /></label>
              <label className="f">Cost incl. GST (₹)<input className="mono" type="number" step="0.01" value={f.cost} onChange={(e) => setF({ ...f, cost: e.target.value })} required /></label>
              <label className="f">Bought on<input type="date" value={f.bought_on} onChange={(e) => setF({ ...f, bought_on: e.target.value })} /></label>
              <div style={{ alignSelf: "end" }}><button className="btn primary">Add</button></div>
            </form>
            <p className="note">Invoice-email and statement-PDF capture are not built yet; manual entry works for PhonePe, Paytm, Google Pay, Jar and others.</p>
          </Card>
          <Card title="Compare with regulated gold">
            <div className="table-wrap"><table><thead><tr><th>Option</th><th>Costs</th><th>Regulator</th></tr></thead>
              <tbody><tr><td>Digital gold (apps)</td><td>3% GST + buy/sell spread</td><td className="loss">None</td></tr>
                <tr><td>Gold ETF (e.g. on Kite)</td><td>Expense ratio + brokerage</td><td className="gain">SEBI</td></tr>
                <tr><td>Gold fund of funds</td><td>Expense ratio</td><td className="gain">SEBI</td></tr></tbody></table></div>
            <p className="note">Tax: non-equity; long-term after 24 months per the dated rules file. Verify with your CA.</p>
          </Card>
        </div>
      </div>
    </div>
  );
}
