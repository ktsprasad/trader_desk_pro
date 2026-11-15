import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, monthOf, num, signed } from "../api.js";
import { COLORS, Card, Empty, Seg } from "../ui.jsx";

const LABEL = { brokerage: "Brokerage", stt: "STT", exchange_txn: "Exchange transaction", sebi: "SEBI fee", stamp: "Stamp duty", gst: "GST", dp: "DP charges" };
const months = () => Array.from({ length: 12 }, (_, i) => monthOf(-i));

export default function Charges() {
  const [seg, setSeg] = useState("eq_intraday");
  const [f, setF] = useState({ broker: "kite", exchange: "NSE", quantity: 23, buy: 1013.5, sell: 1029.5 });
  const [calc, setCalc] = useState(null);
  const [err, setErr] = useState(null);
  const [month, setMonth] = useState(months()[0]);
  const m = useQuery({ queryKey: ["charges-month", month], queryFn: () => api(`/charges/month?month=${month}`) });
  useEffect(() => {
    const t = setTimeout(() => api("/charges/calc", { method: "POST", body: { ...f, segment: seg, quantity: Number(f.quantity), buy: Number(f.buy), sell: Number(f.sell) } })
      .then((r) => { setCalc(r); setErr(null); }).catch((e) => setErr(e.message)), 200);
    return () => clearTimeout(t);
  }, [JSON.stringify(f), seg]);
  const d = m.data;
  const maxPart = d ? Math.max(1, ...Object.values(d.parts)) : 1;
  return (
    <div className="grid g2" style={{ alignItems: "start" }}>
      <Card title="Charges calculator" action={<select className="in" value={f.broker} onChange={(e) => setF({ ...f, broker: e.target.value })} aria-label="Broker">
        {(calc?.brokers || ["kite"]).filter((b) => b !== "paper").map((b) => <option key={b}>{b}</option>)}</select>}>
        <Seg className="full" options={[["eq_intraday", "Equity intraday"], ["eq_delivery", "Delivery"], ["futures", "Futures"], ["options", "Options"]]} value={seg} onChange={setSeg} />
        <div className="grid g4" style={{ marginTop: 12 }}>
          <label className="f">Exchange<select value={f.exchange} onChange={(e) => setF({ ...f, exchange: e.target.value })}>{(seg.startsWith("eq") ? ["NSE", "BSE"] : ["NFO", "BFO"]).map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="f">Qty<input className="mono" type="number" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></label>
          <label className="f">Buy<input className="mono" type="number" step="0.05" value={f.buy} onChange={(e) => setF({ ...f, buy: e.target.value })} /></label>
          <label className="f">Sell<input className="mono" type="number" step="0.05" value={f.sell} onChange={(e) => setF({ ...f, sell: e.target.value })} /></label>
        </div>
        {err && <div className="error">{err}</div>}
        {calc && <>
          <div className="table-wrap" style={{ marginTop: 12 }}><table>
            <thead><tr><th>Charge</th><th>Rule</th><th className="num">₹</th></tr></thead>
            <tbody>{calc.rows.map((r) => <tr key={r.charge}><td>{LABEL[r.charge]}</td><td className="muted">{r.rule}</td><td className="num">{num(r.amount)}</td></tr>)}
              <tr><td><b>Total charges</b></td><td /><td className="num"><b>{inr(calc.total, 2)}</b></td></tr></tbody></table></div>
          <div className="grid g3 mini" style={{ marginTop: 10 }}>
            <div><div className="note">Gross P/L</div><b className={`mono ${cls(calc.gross)}`}>{signed(calc.gross, 2)}</b></div>
            <div><div className="note">Net P/L</div><b className={`mono ${cls(calc.net)}`}>{signed(calc.net, 2)}</b></div>
            <div><div className="note">Charges / gross</div><b className="mono">{calc.charges_pct_of_gross != null ? `${calc.charges_pct_of_gross}%` : "—"}</b></div>
          </div>
          <p className="note">Breakeven move: {num(calc.breakeven_move)} per unit. The same numbers appear on the order ticket before you confirm. Rates come from config/charges.yaml by date.</p>
        </>}
      </Card>
      <div className="stack" style={{ gap: 16 }}>
        <Card title={`${month} · all brokers`} action={<select className="in" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month">{months().map((x) => <option key={x}>{x}</option>)}</select>}>
          {!d ? <Empty>Loading…</Empty> : <>
            <div className="grid g4">
              <div><div className="note">Gross P/L</div><b className={`mono ${cls(d.gross)}`} style={{ fontSize: 18 }}>{signed(d.gross)}</b></div>
              <div><div className="note">Total charges</div><b className="mono loss" style={{ fontSize: 18 }}>{d.charges ? "−" : ""}{inr(d.charges)}</b></div>
              <div><div className="note">Net P/L</div><b className={`mono ${cls(d.net)}`} style={{ fontSize: 18 }}>{signed(d.net)}</b></div>
              <div><div className="note">Costs ate</div><b className="mono" style={{ fontSize: 18 }}>{d.costs_pct != null ? `${d.costs_pct}%` : "—"}</b></div>
            </div>
            <div className="stack" style={{ marginTop: 14 }}>{Object.entries(d.parts).map(([k, v], i) => (
              <div key={k} className="row" style={{ flexWrap: "nowrap" }}><span style={{ width: 160 }}>{LABEL[k] || k}</span>
                <div className="bar" style={{ flex: 1 }}><span style={{ width: `${(v / maxPart) * 100}%`, background: COLORS[i % COLORS.length] }} /></div>
                <span className="mono" style={{ width: 80, textAlign: "right" }}>{inr(v)}</span></div>))}
              {!Object.keys(d.parts).length && <div className="note">No filled orders this month.</div>}</div>
            <p className="note">{d.orders} filled orders{d.paper_orders ? `, ${d.paper_orders} of them paper` : ""}. Entry-leg charges as computed at ticket time.</p>
          </>}
        </Card>
        <div className="grid g2">
          <Card title="Same trades at other brokers" action={<span className="note">Estimate</span>}>
            <div className="table-wrap"><table><thead><tr><th>Broker</th><th className="num">Charges</th><th className="num">vs now</th></tr></thead>
              <tbody>{(d?.compare || []).map((c) => {
                const cur = d.compare.find((x) => x.broker === (d.current_broker === "paper" ? "kite" : d.current_broker))?.charges || 0;
                const diff = c.charges - cur;
                return <tr key={c.broker}><td>{c.broker}</td><td className="num">{inr(c.charges)}</td><td className={`num ${diff < 0 ? "gain" : diff > 0 ? "loss" : ""}`}>{diff ? signed(diff) : "—"}</td></tr>;
              })}</tbody></table></div>
            <p className="note">STT, stamp duty and SEBI fees are the same everywhere; only brokerage and DP differ.</p>
          </Card>
          <Card title="Contract note check"><Empty>{d?.contract_notes?.reason || "Upload contract notes to match them against expected charges."}</Empty></Card>
        </div>
      </div>
    </div>
  );
}
