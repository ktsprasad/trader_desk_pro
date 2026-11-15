import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, hm, inr, num, pct, signed } from "../api.js";
import { Card, Empty, Modal, TicketResult, useAction, Msg } from "../ui.jsx";

const lvl = { bad: "loss", good: "gain", warn: "warnc", info: "" };

export function IndexStrip({ indices }) {
  return (
    <div className="row" style={{ gap: 22, marginTop: 10 }}>
      {[["NIFTY", "NIFTY"], ["BANKNIFTY", "BANKNIFTY"], ["INDIAVIX", "INDIA VIX"]].map(([k, label]) => {
        const q = indices?.[k] || {};
        return (
          <div key={k}><div className="note">{label}</div>
            <b className="mono">{q.last != null ? num(q.last, k === "INDIAVIX" ? 1 : 0) : "—"}</b>{" "}
            <span className={`mono small ${cls(q.change_pct)}`}>{pct(q.change_pct)}</span></div>
        );
      })}
    </div>
  );
}

export default function Desk({ go }) {
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api("/desk/overview"), refetchInterval: 15000 });
  const [act, msg] = useAction();
  const [ticket, setTicket] = useState(null);
  const [sel, setSel] = useState({});
  const d = q.data;
  if (q.error) return <div className="error">{q.error.message}</div>;
  if (!d) return <Empty>Loading the desk…</Empty>;
  const pos = d.positions.filter((p) => !p.error);
  const exit = async (p) => {
    const r = await api("/positions/exit", { method: "POST", body: { account_id: p.account_id, exchange: p.exchange, tradingsymbol: p.tradingsymbol, product: p.product, quantity: p.quantity } });
    const rows = await api("/orders?limit=20");
    setTicket(rows.find((x) => x.id === r.id));
  };
  const intraday = (p) => p.product !== "CNC";
  const flatten = () => {
    const accts = [...new Set(pos.filter(intraday).map((p) => p.account_id))];
    if (!accts.length) return;
    if (!window.confirm(`Square off every intraday / F&O position on ${accts.join(", ")}? Short legs go first.`)) return;
    accts.forEach((a) => act.mutate({ path: "/orders/flatten", body: { account_id: a }, done: "Flatten orders sent." }));
  };
  const exitSelected = async () => { for (const p of pos.filter((x) => sel[x.account_id + x.tradingsymbol])) await exit(p); };

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g3">
        <Card title={d.brief ? `Today's brief · ${hm(d.brief.created_at)}` : "Today's brief"} action={<button className="link" onClick={() => go("briefs")}>Open brief</button>}>
          {d.brief ? <>
            <div className="row"><h2 style={{ fontSize: 22 }}>{d.brief.headline?.split(",")[0]}</h2>{d.brief.day_type && <span className="pill">{d.brief.day_type}</span>}</div>
            <p className="muted" style={{ margin: "8px 0 0" }}>{d.brief.summary}</p>
          </> : <p className="muted">No brief yet today. It runs at 08:45 IST on trading days, or <button className="link btn small" onClick={() => act.mutate({ path: "/briefs/run", body: { kind: "pre" }, done: "Brief ready." })}>run it now</button>.</p>}
          <IndexStrip indices={d.indices} />
          <Msg msg={msg} />
        </Card>
        <Card title="Armed plans" action={<button className="link" onClick={() => go("signals")}>New plan</button>}>
          <div className="stack">
            {d.plans.map((p) => (
              <div className="mini" key={p.id}>
                <div className="row between"><b>{p.symbol} · {p.strategy} {p.side === "BUY" ? "long" : "short"}</b>
                  <span className={`pill ${p.status === "ticketed" ? "good" : "info"}`}>{p.status === "ticketed" ? "Ticketed" : "Waiting"}</span></div>
                <div className="mono note">entry {num(p.entry)} · stop {num(p.stop)} · tgt {num(p.target)}</div>
              </div>
            ))}
            {!d.plans.length && <Empty>No armed plans. Signals and brief setups show up here.</Empty>}
          </div>
        </Card>
        <Card title="Accounts" action={<button className="link" onClick={() => go("conn")}>Manage</button>}>
          <div className="table-wrap"><table>
            <thead><tr><th>Broker</th><th className="num">Cash</th><th className="num">Margin used</th><th>Status</th></tr></thead>
            <tbody>{d.accounts.map((a) => (
              <tr key={a.id}><td>{a.name}</td><td className="num">{a.cash != null ? inr(a.cash) : "—"}</td><td className="num">{a.used != null ? inr(a.used) : "—"}</td>
                <td className={a.connected ? "gain" : "warnc"}>● {a.connected ? (a.plugin === "paper" ? "Simulator" : "Connected") : "Log in"}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      </div>

      <Card title="Positions · all brokers" action={<div className="row">
        <button className="btn small" disabled={!Object.values(sel).some(Boolean)} onClick={exitSelected}>Exit selected</button>
        <button className="btn small outline-danger" disabled={!pos.some(intraday)} onClick={flatten}>Flatten intraday</button></div>}>
        <div className="table-wrap"><table>
          <thead><tr><th /><th>Symbol</th><th>Broker</th><th>Product</th><th className="num">Qty</th><th className="num">Avg</th><th className="num">LTP</th>
            <th className="num">P/L</th><th className="num">Stop</th><th className="num">Target</th><th className="num">R</th><th /></tr></thead>
          <tbody>
            {pos.map((p) => {
              const k = p.account_id + p.tradingsymbol;
              return (
                <tr key={k}>
                  <td><input type="checkbox" aria-label={`select ${p.tradingsymbol}`} checked={!!sel[k]} onChange={(e) => setSel({ ...sel, [k]: e.target.checked })} /></td>
                  <td><b>{p.tradingsymbol}</b></td><td>{p.broker}</td><td>{p.product} · {p.exchange}</td>
                  <td className="num">{p.quantity}</td><td className="num">{num(p.average_price)}</td><td className="num">{num(p.last_price)}</td>
                  <td className={`num ${cls(p.pnl)}`}>{signed(p.pnl)}</td><td className="num">{p.stop ? num(p.stop) : "—"}</td>
                  <td className="num">{p.target ? num(p.target) : "—"}</td><td className={`num ${cls(p.r)}`}>{p.r != null ? (p.r > 0 ? "+" : "") + p.r : "—"}</td>
                  <td><span className="row"><button className="btn small" onClick={() => go("trade", { symbol: p.tradingsymbol, exchange: p.exchange })}>Modify</button>
                    <button className="btn small" onClick={() => exit(p)}>Exit</button></span></td>
                </tr>
              );
            })}
            {!pos.length && <tr><td colSpan={12} className="muted">No open positions.</td></tr>}
          </tbody>
        </table></div>
        {d.positions.filter((p) => p.error).map((p) => <div key={p.account_id} className="warn" style={{ marginTop: 8 }}>{p.broker}: {p.error}</div>)}
        <Msg msg={msg} />
      </Card>

      <div className="grid g2">
        <Card title="Open orders" action={d.open_orders.length ? <button className="btn small outline-danger" onClick={() => window.confirm("Cancel every open order?") && act.mutate({ path: "/orders/cancel-all", body: {}, done: (r) => `${r.cancelled} cancelled.` })}>Cancel all</button> : null}>
          <div className="table-wrap"><table>
            <thead><tr><th>Time</th><th>Order</th><th className="num">Qty</th><th className="num">Price</th><th>Status</th><th /></tr></thead>
            <tbody>{d.open_orders.map((o) => (
              <tr key={o.id}><td className="mono">{hm(o.time)}</td>
                <td>{o.oco ? `${o.order.side === "BUY" ? "SELL" : "BUY"} ${o.order.tradingsymbol} · OCO` : `${o.order.side} ${o.order.tradingsymbol} · ${o.order.order_type} · ${o.order.product}`}</td>
                <td className="num">{o.order.quantity}</td>
                <td className="num">{o.oco ? `${num(o.order.stop, 0)} / ${num(o.order.target, 0)}` : num(o.order.price)}</td>
                <td><span className="pill">{o.oco ? "GTT active" : o.status === "TRIGGER_PENDING" ? "Trigger pending" : o.status}</span></td>
                <td>{!o.oco && <button className="btn small" onClick={() => act.mutate({ path: `/orders/${o.id}/cancel`, done: "Cancelled." })}>Cancel</button>}</td></tr>
            ))}
            {!d.open_orders.length && <tr><td colSpan={6} className="muted">No open orders.</td></tr>}</tbody>
          </table></div>
        </Card>
        <Card title="Alerts and GTTs" action={<button className="link" onClick={() => go("portfolio")}>All GTTs</button>}>
          <div className="list">
            {d.alerts.map((a, i) => <div key={i}><span className={lvl[a.level]}>{a.level === "good" ? "▲" : a.level === "bad" ? "▼" : "●"} {a.text}</span><span className="mono muted">{a.at}</span></div>)}
            {!d.alerts.length && <div className="note">Nothing needs you right now.</div>}
          </div>
        </Card>
      </div>
      <div className="mobile-actions">
        <button className="btn outline-danger" onClick={flatten}>Flatten intraday</button>
        <button className="btn danger" onClick={() => window.confirm("Kill switch: cancel every open order and block new entries today?") && act.mutate({ path: "/kill", done: "Kill switch on." })}>Kill switch</button>
      </div>
      {ticket && <Modal onClose={() => setTicket(null)}><h3 style={{ marginBottom: 10 }}>Exit {ticket.order.tradingsymbol}</h3><TicketResult t={ticket} onClose={() => setTicket(null)} /></Modal>}
    </div>
  );
}
