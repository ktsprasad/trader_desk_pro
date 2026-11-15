import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, inr, num } from "../api.js";
import { Card, Empty, Modal, TicketResult } from "../ui.jsx";

const STEP = { NIFTY: 50, BANKNIFTY: 100, FINNIFTY: 50 };
const LOT = { NIFTY: 75, BANKNIFTY: 35, FINNIFTY: 65 };

function preset(name, spot, step) {
  const atm = Math.round(spot / step) * step;
  const L = (side, type, k) => ({ side, type, strike: atm + k * step, premium: 0, lots: 1 });
  return {
    "Iron condor": [L("BUY", "PE", -6), L("SELL", "PE", -4), L("SELL", "CE", 4), L("BUY", "CE", 6)],
    "Bull call spread": [L("BUY", "CE", 0), L("SELL", "CE", 4)],
    "Bear put spread": [L("BUY", "PE", 0), L("SELL", "PE", -4)],
    "Short straddle (hedged)": [L("BUY", "PE", -8), L("SELL", "PE", 0), L("SELL", "CE", 0), L("BUY", "CE", 8)],
    "Long strangle": [L("BUY", "PE", -4), L("BUY", "CE", 4)],
    Custom: [],
  }[name];
}

function Payoff({ p, spot }) {
  if (!p?.points?.length) return <Empty>Add legs with premiums to see the payoff.</Empty>;
  const xs = p.points.map((q) => q.x), ys = p.points.map((q) => q.y);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(0, ...ys), Math.max(0, ...ys)];
  const W = 600, H = 200, px = (x) => ((x - x0) / (x1 - x0 || 1)) * W, py = (y) => H - 10 - ((y - y0) / (y1 - y0 || 1)) * (H - 20);
  return (
    <svg viewBox={`0 0 ${W} ${H + 16}`} width="100%" role="img" aria-label="payoff at expiry" style={{ background: "var(--bg)", borderRadius: 9 }}>
      <line x1="0" x2={W} y1={py(0)} y2={py(0)} stroke="var(--border)" />
      {spot && <><line x1={px(spot)} x2={px(spot)} y1="0" y2={H} stroke="var(--accent)" strokeDasharray="4 4" />
        <text x={px(spot)} y="12" fill="var(--accent)" fontSize="11" textAnchor="middle">spot</text></>}
      <polyline fill="none" stroke="var(--text)" strokeWidth="2" points={p.points.map((q) => `${px(q.x)},${py(q.y)}`).join(" ")} />
      <text x="4" y={H + 13} fontSize="11" fill="var(--muted)">{num(x0, 0)}</text>
      <text x={W - 4} y={H + 13} fontSize="11" fill="var(--muted)" textAnchor="end">{num(x1, 0)}</text>
    </svg>
  );
}

export default function Options() {
  const [und, setUnd] = useState("NIFTY");
  const [expiry, setExpiry] = useState("");
  const chain = useQuery({ queryKey: ["chain", und, expiry], queryFn: () => api(`/options/chain?underlying=${und}${expiry ? `&expiry=${expiry}` : ""}`) });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api("/accounts") });
  const c = chain.data;
  const spot = c?.spot;
  const lot = c?.lot_size || LOT[und] || 75;
  const [name, setName] = useState("Iron condor");
  const [legs, setLegs] = useState([]);
  const [pay, setPay] = useState(null);
  const [account, setAccount] = useState("paper");
  const [exp, setExp] = useState("");
  const [tickets, setTickets] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => { if (spot && !legs.length && name !== "Custom") setLegs(fillPremiums(preset(name, spot, STEP[und] || 50))); }, [spot]);
  useEffect(() => { if (c?.expiry && !exp) setExp(c.expiry); }, [c?.expiry]);
  function fillPremiums(ls) {
    if (!c?.rows?.length) return ls;
    return ls.map((l) => { const r = c.rows.find((x) => x.strike === l.strike); return { ...l, premium: r?.[l.type]?.ltp ?? l.premium, symbol: r?.[l.type]?.symbol }; });
  }
  useEffect(() => {
    const ok = legs.length && spot;
    if (!ok) { setPay(null); return; }
    const t = setTimeout(() => api("/options/payoff", { method: "POST", body: { legs: legs.map(({ symbol, ...l }) => ({ ...l, premium: Number(l.premium) || 0, lots: Number(l.lots) || 1, strike: Number(l.strike) })), lot_size: lot, spot } })
      .then(setPay).catch((e) => setErr(e.message)), 250);
    return () => clearTimeout(t);
  }, [JSON.stringify(legs), spot, lot]);

  const addLeg = (side, type, strike, premium, symbol) => { setName("Custom"); setLegs([...legs, { side, type, strike, premium: premium ?? 0, lots: 1, symbol }]); };
  const place = async () => {
    setErr(null);
    if (!exp) { setErr("Pick the expiry date."); return; }
    if (legs.some((l) => !Number(l.premium))) { setErr("Every leg needs a limit price (premium)."); return; }
    try {
      const r = await api("/options/basket", { method: "POST", body: { account_id: account, underlying: und, expiry: exp, lot_size: lot,
        legs: legs.map(({ symbol, ...l }) => ({ ...l, premium: Number(l.premium), lots: Number(l.lots), strike: Number(l.strike) })),
        symbols: legs.every((l) => l.symbol) ? legs.map((l) => l.symbol) : null } });
      const rows = await api("/orders?limit=40");
      setTickets(r.tickets.map((id) => rows.find((x) => x.id === id)).filter(Boolean));
    } catch (e) { setErr(e.message); }
  };
  const tradeable = (accounts.data || []).filter((a) => a.capabilities.includes("TRADE_FO"));

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row">
        <select className="in" value={und} onChange={(e) => { setUnd(e.target.value); setLegs([]); setExpiry(""); setExp(""); }} aria-label="Underlying">
          {Object.keys(STEP).map((u) => <option key={u}>{u}</option>)}</select>
        <b style={{ fontSize: 20 }}>{und} options</b>
        <span className="mono">Spot {spot ? num(spot) : "—"}</span>
        {c?.expiries?.length ? <select className="in" value={expiry || c.expiry} onChange={(e) => setExpiry(e.target.value)} aria-label="Expiry">{c.expiries.slice(0, 8).map((x) => <option key={x}>{x}</option>)}</select> : null}
        <span className="chip">Lot {lot}</span>
      </div>
      <div className="grid side-r3">
        <Card title="Option chain" action={<span className="note">Click B or S to add a leg</span>}>
          {!c ? <Empty>Loading…</Empty> : !c.available ? <Empty>{c.reason} You can still build a strategy on the right by typing strikes and premiums.</Empty> : (
            <div className="table-wrap" style={{ maxHeight: 560, overflowY: "auto" }}><table>
              <thead><tr><th /><th className="num">OI</th><th className="num">LTP</th><th className="num" style={{ textAlign: "center" }}>Strike</th><th className="num">LTP</th><th className="num">OI</th><th /></tr></thead>
              <tbody>{c.rows.map((r) => (
                <tr key={r.strike} className={spot && Math.abs(r.strike - spot) < (STEP[und] || 50) / 2 ? "sel" : ""}>
                  <td><span className="row" style={{ flexWrap: "nowrap" }}><button className="btn small" onClick={() => addLeg("BUY", "CE", r.strike, r.CE?.ltp, r.CE?.symbol)}>B</button><button className="btn small" onClick={() => addLeg("SELL", "CE", r.strike, r.CE?.ltp, r.CE?.symbol)}>S</button></span></td>
                  <td className="num">{r.CE?.oi != null ? (r.CE.oi / 1e5).toFixed(1) + "L" : "—"}</td><td className="num"><b>{num(r.CE?.ltp)}</b></td>
                  <td className="num" style={{ textAlign: "center", background: "var(--panel-2)" }}><b>{num(r.strike, 0)}</b></td>
                  <td className="num"><b>{num(r.PE?.ltp)}</b></td><td className="num">{r.PE?.oi != null ? (r.PE.oi / 1e5).toFixed(1) + "L" : "—"}</td>
                  <td><span className="row" style={{ flexWrap: "nowrap" }}><button className="btn small" onClick={() => addLeg("BUY", "PE", r.strike, r.PE?.ltp, r.PE?.symbol)}>B</button><button className="btn small" onClick={() => addLeg("SELL", "PE", r.strike, r.PE?.ltp, r.PE?.symbol)}>S</button></span></td>
                </tr>))}</tbody>
            </table></div>)}
        </Card>
        <Card title="Strategy builder" action={<select className="in" value={name} onChange={(e) => { setName(e.target.value); setLegs(spot ? fillPremiums(preset(e.target.value, spot, STEP[und] || 50)) : []); }}>
          {["Iron condor", "Bull call spread", "Bear put spread", "Short straddle (hedged)", "Long strangle", "Custom"].map((x) => <option key={x}>{x}</option>)}</select>}>
          <div className="table-wrap"><table>
            <thead><tr><th>Leg</th><th className="num">Strike</th><th className="num">Lots</th><th className="num">Price</th><th /></tr></thead>
            <tbody>{legs.map((l, i) => (
              <tr key={i}>
                <td><select className="in" value={`${l.side} ${l.type}`} onChange={(e) => { const [side, type] = e.target.value.split(" "); setLegs(legs.map((x, j) => j === i ? { ...x, side, type, symbol: undefined } : x)); }}>
                  {["BUY CE", "SELL CE", "BUY PE", "SELL PE"].map((x) => <option key={x}>{x}</option>)}</select></td>
                <td className="num"><input className="in mono" style={{ width: 90 }} type="number" step={STEP[und]} value={l.strike} onChange={(e) => setLegs(legs.map((x, j) => j === i ? { ...x, strike: e.target.value, symbol: undefined } : x))} /></td>
                <td className="num"><input className="in mono" style={{ width: 54 }} type="number" min="1" value={l.lots} onChange={(e) => setLegs(legs.map((x, j) => j === i ? { ...x, lots: e.target.value } : x))} /></td>
                <td className="num"><input className="in mono" style={{ width: 80 }} type="number" step="0.05" value={l.premium} onChange={(e) => setLegs(legs.map((x, j) => j === i ? { ...x, premium: e.target.value } : x))} /></td>
                <td><button className="btn small" aria-label="Remove leg" onClick={() => setLegs(legs.filter((_, j) => j !== i))}>×</button></td>
              </tr>))}
              {!legs.length && <tr><td colSpan={5} className="muted">No legs. Pick a preset or add legs from the chain.</td></tr>}</tbody>
          </table></div>
          <button className="btn small" style={{ margin: "8px 0" }} onClick={() => addLeg("BUY", "CE", spot ? Math.round(spot / (STEP[und] || 50)) * (STEP[und] || 50) : 0, 0)}>+ Add leg</button>
          <Payoff p={pay} spot={spot} />
          <div className="grid g3" style={{ marginTop: 10 }}>
            <div><div className="note">Max profit</div><b className="mono gain">{pay ? (pay.max_profit == null ? "Unlimited" : inr(pay.max_profit)) : "—"}</b></div>
            <div><div className="note">Max loss</div><b className="mono loss">{pay ? (pay.max_loss == null ? "Unlimited" : inr(pay.max_loss)) : "—"}</b></div>
            <div><div className="note">Breakevens</div><b className="mono">{pay?.breakevens?.map((b) => num(b, 0)).join(" / ") || "—"}</b></div>
            <div><div className="note">Net premium</div><b className="mono">{pay ? inr(pay.net_premium) : "—"}</b></div>
            <div><div className="note">Lot size</div><b className="mono">{lot}</b></div>
          </div>
          <div className="grid g2" style={{ marginTop: 10 }}>
            <label className="f">Account<select value={account} onChange={(e) => setAccount(e.target.value)}>{tradeable.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
            <label className="f">Expiry<input type="date" value={exp} onChange={(e) => setExp(e.target.value)} /></label>
          </div>
          <p className="note">Legs are ticketed hedge-first: buys before sells. Each leg runs the risk check and needs its own typed confirm. Margin is shown per leg on its ticket.</p>
          {err && <div className="error">{err}</div>}
          <button className="btn primary block" disabled={!legs.length} onClick={place}>Review and place {legs.length} legs</button>
        </Card>
      </div>
      {tickets && <Modal wide onClose={() => setTickets(null)}>
        <h3 style={{ marginBottom: 10 }}>Basket · confirm each leg, hedges first</h3>
        <div className="stack" style={{ maxHeight: "70vh", overflowY: "auto" }}>{tickets.map((t) => <div key={t.id} className="card"><b>{t.order.side} {t.order.quantity} {t.order.tradingsymbol}</b><TicketResult t={t} /></div>)}</div>
        <button className="btn" style={{ marginTop: 10 }} onClick={() => setTickets(null)}>Close</button>
      </Modal>}
    </div>
  );
}
