import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, num, pct, signed } from "../api.js";
import { COLORS, Card, Empty } from "../ui.jsx";

const CLASS = { equity: "Indian equity", etf: "ETFs", mutual_fund: "Mutual funds", us_stock: "US equity", gold_silver: "Gold, silver",
  reit_invit: "REIT, InvIT", reit: "REIT", invit: "InvIT", crypto: "Crypto", cash: "Cash", gold: "Gold" };
const FILTER = { all: () => true, stocks: (h) => ["equity", "etf", "reit_invit"].includes(h.asset_class), mfs: (h) => h.asset_class === "mutual_fund",
  us: (h) => h.asset_class === "us_stock", other: (h) => !["equity", "etf", "reit_invit", "mutual_fund", "us_stock"].includes(h.asset_class) };

function held(h) {
  if (h.months_held == null) return "—";
  const y = Math.floor(h.months_held / 12), m = h.months_held % 12;
  return `${y ? `${y}y ` : ""}${m}m · ${h.long_term ? "LTCG" : "STCG"}`;
}

export default function Portfolio({ go }) {
  const q = useQuery({ queryKey: ["portfolio-ov"], queryFn: () => api("/portfolio/overview"), refetchInterval: 120000 });
  const [f, setF] = useState("all");
  const p = q.data;
  if (q.error) return <div className="error">{q.error.message}</div>;
  if (!p) return <Empty>Loading portfolio…</Empty>;
  const alloc = Object.entries(p.allocation).filter(([, v]) => v.value > 0).sort((a, b) => b[1].value - a[1].value);
  const invested = p.invested + (p.metals?.cost || 0);
  const nw = p.net_worth;
  const pnl = p.pnl + (p.metals?.pnl || 0);
  const src = p.sources?.[0];
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card title="Net worth" action={src && <span className="chip">{src.source === "live" ? "Live from broker" : `Snapshot ${src.as_of?.slice(0, 10) || ""}`} · {p.sources.length} source(s)</span>}>
        <div className="row between" style={{ alignItems: "flex-end" }}>
          <div><div className="big">{inr(nw)}</div><div className={`mono ${cls(pnl)}`}>{signed(pnl)} ({pct(invested ? (pnl / invested) * 100 : null, 1)}) all-time</div></div>
          <div className="row" style={{ gap: 28 }}>
            <div><div className="note">Invested</div><b className="mono" style={{ fontSize: 18 }}>{inr(invested)}</b></div>
            <div><div className="note">Today</div><b className={`mono ${cls(p.day_change)}`} style={{ fontSize: 18 }}>{signed(p.day_change)}</b></div>
            <div><div className="note">LTCG used this FY</div><b className="mono" style={{ fontSize: 18 }}>{inr(p.ltcg)} / {inr(p.ltcg_exemption)}</b></div>
          </div>
        </div>
        <div className="stackbar" style={{ marginTop: 14 }}>{alloc.map(([k, v], i) => <span key={k} title={CLASS[k] || k} style={{ width: `${v.weight_pct}%`, background: COLORS[i % COLORS.length] }} />)}</div>
        <div className="legend">{alloc.map(([k, v], i) => <span key={k}><i style={{ background: COLORS[i % COLORS.length] }} />{CLASS[k] || k} {Math.round(v.weight_pct)}%</span>)}</div>
      </Card>

      <div className="grid g5">
        {Object.entries(p.by_account || {}).map(([k, v]) => (
          <div className="card kpi" key={k}><div className="label">{k === "kite" ? "Zerodha Kite · equity + Coin" : k}</div><div className="value" style={{ fontSize: 20 }}>{inr(v.value)}</div>
            <div className={`sub ${cls(v.pnl)}`}>{pct(v.invested ? (v.pnl / v.invested) * 100 : null, 1)}</div></div>))}
        {p.metals?.holdings?.length > 0 && <div className="card kpi"><div className="label">Digital metals</div><div className="value" style={{ fontSize: 20 }}>{inr(p.metals.value)}</div>
          <div className={`sub ${cls(p.metals.pnl)}`}>{p.metals.value != null ? pct((p.metals.pnl / p.metals.cost) * 100, 1) : "set rates"}</div></div>}
        <button className="card kpi" style={{ textAlign: "left", cursor: "pointer", borderStyle: "dashed" }} onClick={() => go("conn")}>
          <div className="label">Add a source</div><div className="note" style={{ marginTop: 6 }}>Kite is connected here. INDstocks, INDmoney and CAS plugins are not built yet.</div></button>
      </div>

      <div className="grid side-r">
        <Card title="Holdings" action={<div className="seg">{[["all", "All"], ["stocks", "Stocks"], ["mfs", "MFs"], ["us", "US"], ["other", "Other"]].map(([k, l]) =>
          <button key={k} className={f === k ? "on" : ""} onClick={() => setF(k)}>{l}</button>)}</div>}>
          <div className="table-wrap"><table>
            <thead><tr><th>Instrument</th><th>Source</th><th className="num">Qty</th><th className="num">Avg</th><th className="num">Value</th><th className="num">P/L</th><th className="num">P/L %</th><th>Held</th></tr></thead>
            <tbody>{p.holdings.filter(FILTER[f]).map((h) => (
              <tr key={h.account + h.symbol}>
                <td><b>{h.name && h.asset_class === "mutual_fund" ? h.name : h.symbol}</b></td><td><span className="tag">{h.account}</span></td>
                <td className="num">{num(h.quantity, h.quantity % 1 ? 3 : 0)}</td><td className="num">{num(h.average_price)}</td><td className="num">{inr(h.value_inr)}</td>
                <td className={`num ${cls(h.pnl_inr)}`}>{signed(h.pnl_inr)}</td><td className={`num ${cls(h.pnl_pct)}`}>{pct(h.pnl_pct, 1)}</td>
                <td className={h.long_term ? "gain" : ""}>{held(h)}</td></tr>))}
              {!p.holdings.length && <tr><td colSpan={8} className="muted">No holdings. Log in to Kite or import snapshots in Connections.</td></tr>}</tbody>
          </table></div>
          <p className="note">“Held” counts from the first snapshot that contains the holding, so it can understate older positions.</p>
        </Card>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="GTT dip-ladders">
            {p.gtt_ladders.length ? p.gtt_ladders.map((g) => {
              const lo = Math.min(...g.triggers) * 0.97;
              return (
                <div key={g.symbol} style={{ marginBottom: 12 }}>
                  <div className="row between"><b>{g.symbol}</b><span className="mono note">CMP {num(g.ltp, 0)} · {g.triggers.length} GTTs</span></div>
                  <div className="bar" style={{ position: "relative", height: 12, margin: "6px 0" }}>{g.triggers.map((t) => <i key={t} style={{ position: "absolute", left: `${((t - lo) / (g.ltp - lo)) * 100}%`, top: 2, width: 8, height: 8, borderRadius: "50%", background: "var(--accent)" }} />)}</div>
                  <div className="mono note">{g.triggers.map((t) => num(t, 0)).join(" · ")}</div>
                </div>);
            }) : <div className="note">Single-leg buy GTTs from a logged-in broker show here as ladders.</div>}
          </Card>
          <Card title="Tax lots turning long-term">
            <div className="list">{p.turning_long_term.map((t) => <div key={t.symbol}><span>{t.symbol} · {num(t.quantity, 0)}</span><span className="mono muted">in {t.months} month{t.months === 1 ? "" : "s"}{t.rule_months === 24 ? " · 24-month rule" : ""}</span></div>)}
              {!p.turning_long_term.length && <div className="note">Nothing within 12 months.</div>}</div>
          </Card>
        </div>
      </div>
    </div>
  );
}
