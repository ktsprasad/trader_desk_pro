import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, num, pct } from "../api.js";
import { Card, Empty, Kpi, Tabs } from "../ui.jsx";

const TABS = [["money", "Money Flow"], ["options", "Option Pulse"], ["radar", "Stock Radar"], ["calm", "Calm Zone"], ["assets", "Crypto & Gold"],
  ["liquidity", "Liquidity Map"], ["oi", "OI Flow"], ["rsi", "RSI Screener"], ["academy", "F&O Academy"]];
const big = (n) => (n == null ? "—" : Math.abs(n) >= 1e7 ? `${(n / 1e7).toFixed(2)} Cr` : Math.abs(n) >= 1e5 ? `${(n / 1e5).toFixed(2)} L` : Math.round(n).toLocaleString("en-IN"));

function SortTable({ rows, cols, initial, empty = "Nothing to show.", go }) {
  const [sort, setSort] = useState(initial || [cols[0][0], 1]);
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const [k, d] = sort, x = a[k], y = b[k];
    if (x == null) return 1;
    if (y == null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * d;
  }), [rows, sort]);
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap"><table>
      <thead><tr>{cols.map(([k, label, , right]) => (
        <th key={k} style={{ cursor: "pointer", textAlign: right ? "right" : "left" }} onClick={() => setSort([k, sort[0] === k ? -sort[1] : -1])}>
          {label}{sort[0] === k ? (sort[1] > 0 ? " ▲" : " ▼") : ""}</th>))}{go && <th />}</tr></thead>
      <tbody>{sorted.map((r, i) => (
        <tr key={r.symbol + i}>{cols.map(([k, , fmt, right]) => <td key={k} className={right ? "mono" : ""} style={right ? { textAlign: "right" } : null}>{fmt ? fmt(r[k], r) : r[k]}</td>)}
          {go && <td style={{ whiteSpace: "nowrap" }}><button className="btn small" onClick={() => go("charts", { symbol: r.symbol })}>Chart</button> <button className="btn small" onClick={() => go("trade", { symbol: r.symbol })}>Ticket</button></td>}</tr>))}</tbody>
    </table></div>
  );
}

const sym = (v) => <b>{v}</b>;
const chg = (v) => <span className={cls(v)}>{pct(v)}</span>;
const rsiCell = (v) => <span className={v == null ? "" : v >= 70 ? "loss" : v <= 30 ? "gain" : ""}>{v ?? "—"}</span>;

function Source({ s, extra, live, at, reason }) {
  return (<p className="note" style={{ marginTop: 10 }}>
    <span className="chip" style={{ marginRight: 8 }}><span className={`dot ${live ? "on" : "amber"}`} />{live ? `Live${at ? ` · ${at}` : ""}` : "Delayed / end of day"}</span>
    {s}{extra ? ` · ${extra}` : ""}.{!live && reason ? ` ${reason}.` : ""} Analysis only: nothing here places an order, and a ticket opens only for you to check and confirm.</p>);
}
const every = (ms) => (query) => (query.state.data?.live ? ms : false);

function Unavailable({ q, what }) {
  if (q.isLoading) return <Empty>Loading {what}…</Empty>;
  return <Empty>{what} is not available right now: {q.error?.message}. It needs NSE's end-of-day F&amp;O file (published each evening after the close) and an internet connection.</Empty>;
}

function useScreener() {
  return useQuery({ queryKey: ["pulse-screener"], queryFn: () => api("/pulse/screener"), staleTime: 15000, retry: false, refetchInterval: every(20000) });
}

function ScreenerState({ q, children }) {
  if (q.isLoading) return <Card><Empty>Reading daily bars for the stock list… the first load takes a few seconds.</Empty></Card>;
  if (q.error) return <Card><Empty>Screener unavailable: {q.error.message}</Empty></Card>;
  return children(q.data);
}

function Market({ d }) {
  const qs = d.market.quotes || {};
  const reg = d.market.regime || {};
  return (
    <div className="grid g4">
      {["NIFTY", "BANKNIFTY", "INDIAVIX"].map((k) => <Kpi key={k} label={k === "INDIAVIX" ? "India VIX" : k} value={num(qs[k]?.last)} sub={qs[k]?.error ? "unavailable" : pct(qs[k]?.change_pct)} />)}
      <Kpi label="Market regime" value={reg.score != null ? `${reg.score}/100` : "—"} sub={reg.label} />
    </div>
  );
}

const crf = (v) => (v == null ? "—" : `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })} cr`);
const reduceMotion = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function FlowLines({ sectors, rowH, height, max }) {
  // dots travel from each sector bar to the inflow (top) or outflow (bottom) box; faster and denser = bigger flow
  const W = 190, inY = 60, outY = height - 60;
  return (
    <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} aria-hidden="true" className="hide-m" style={{ flex: "none" }}>
      {sectors.map((s, i) => {
        const y = i * rowH + rowH / 2, toY = s.flow_cr >= 0 ? inY : outY;
        const k = Math.min(1, Math.abs(s.flow_cr) / max);
        const path = `M0 ${y} C ${W * 0.55} ${y}, ${W * 0.45} ${toY}, ${W} ${toY}`;
        const color = s.flow_cr >= 0 ? "var(--gain)" : "var(--loss)";
        const dots = reduceMotion || k < 0.02 ? 0 : 1 + Math.round(k * 4);
        const dur = (6 - 4.5 * k).toFixed(2);
        return (<g key={s.sector}>
          <path d={path} fill="none" stroke={color} strokeOpacity={0.12 + 0.3 * k} strokeWidth={1 + 2 * k} />
          {Array.from({ length: dots }, (_, j) => (
            <circle key={j} r={1.6 + 1.4 * k} fill={color} opacity={0.85}>
              <animateMotion dur={`${dur}s`} repeatCount="indefinite" begin={`${(j * dur) / dots}s`} path={path} />
            </circle>))}
        </g>);
      })}
    </svg>
  );
}

function SectorMonitor({ go }) {
  const q = useQuery({ queryKey: ["pulse-sectors"], queryFn: () => api("/pulse/sectors"), retry: false, staleTime: 15000, refetchInterval: every(20000) });
  if (q.isLoading) return <Card><Empty>Reading today's value traded for about 130 stocks across 20 sectors…</Empty></Card>;
  if (q.error) return <Card><Empty>Sector flow unavailable: {q.error.message}</Empty></Card>;
  const d = q.data;
  const max = Math.max(1, ...d.sectors.map((s) => Math.abs(s.flow_cr)));
  const rowH = 26, height = Math.max(240, d.sectors.length * rowH);
  const List = ({ title, total, rows, cls: c }) => (
    <div className="mini" style={{ borderColor: c === "gain" ? "var(--gain)" : "var(--loss)" }}>
      <div className="row between"><b className={c}>{title}</b><b className={`mono ${c}`}>{crf(total)}</b></div>
      {rows.map((r) => (
        <div key={r.symbol} className="row between" style={{ padding: "3px 0", cursor: "pointer" }} onClick={() => go("charts", { symbol: r.symbol })}>
          <span><b>{r.symbol}</b> <span className="note">{r.sector}</span></span>
          <span className="mono"><span className={cls(r.change_pct)}>{pct(r.change_pct)}</span> · <span className={c}>{crf(r.flow_cr)}</span></span></div>))}
    </div>);
  return (
    <Card title={<span>Sector money flow <span className="chip" style={{ marginLeft: 6 }}><span className={`dot ${d.live ? "on" : "amber"}`} />{d.live ? `Live · ${d.as_of_time}` : `Delayed · ${d.as_of}`}</span></span>}>
      <div style={{ fontSize: 17, fontWeight: 700, margin: "2px 0 10px" }}>{d.headline}{d.surge?.length ? <>; surge: <span className={cls(d.surge[0].cr_per_min)}>{d.surge[0].symbol} ({d.surge[0].cr_per_min > 0 ? "+" : ""}{d.surge[0].cr_per_min} cr/min)</span></> : null}</div>
      <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap", gap: 0 }}>
        <div style={{ flex: "1 1 0", minWidth: 0 }}>
          {d.sectors.map((s) => {
            const w = (Math.abs(s.flow_cr) / max) * 50;
            return (
              <div key={s.sector} style={{ display: "grid", gridTemplateColumns: "118px 1fr 92px", alignItems: "center", height: rowH, gap: 6 }} title={`${s.up} up / ${s.down} down of ${s.stocks}; value traded ₹${Math.round(s.turnover_cr)} cr`}>
                <span className="note" style={{ textAlign: "right", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.sector}</span>
                <div style={{ position: "relative", height: 12 }}>
                  <div style={{ position: "absolute", left: "50%", top: -6, bottom: -6, borderLeft: "1px solid var(--border)" }} />
                  <div style={{ position: "absolute", top: 0, height: 12, borderRadius: 3, background: s.flow_cr >= 0 ? "var(--gain)" : "var(--loss)",
                    left: s.flow_cr >= 0 ? "50%" : `${50 - w}%`, width: `${w}%`, opacity: 0.85 }} />
                </div>
                <span className={`mono small ${cls(s.flow_cr)}`}>{crf(s.flow_cr)}</span>
              </div>);
          })}
        </div>
        <FlowLines sectors={d.sectors} rowH={rowH} height={height} max={max} />
        <div className="stack" style={{ flex: "1 1 0", minWidth: 0, gap: 12 }}>
          <List title="Inflow" total={d.inflow_total_cr} rows={d.inflow.slice(0, 8)} cls="gain" />
          <List title="Outflow" total={d.outflow_total_cr} rows={d.outflow.slice(0, 8)} cls="loss" />
        </div>
      </div>
      <div className="row" style={{ marginTop: 10, gap: 8 }}>
        <span className="chip">Breadth <b className="gain">{d.breadth.up}</b> / <b className="loss">{d.breadth.down}</b></span>
        {d.surge?.length ? d.surge.slice(0, 6).map((x) => <span key={x.symbol} className="chip">{x.symbol} <b className={cls(x.cr_per_min)}>{x.cr_per_min > 0 ? "+" : ""}{x.cr_per_min} cr/min</b></span>)
          : <span className="note">{d.live ? "Surge rates appear after about 30 seconds of live data." : "Surge (₹ cr per minute) needs live Kite data; it is not estimated from delayed prices."}</span>}
      </div>
      <p className="note">Money flow = value traded × where the close sits in the day's range (Chaikin): closing at the high counts fully as inflow, at the low fully as outflow. So a sector can show inflow on a down day if it closed near its high. Dot speed and density show the size of each sector's flow.{d.failed?.length ? ` Not read: ${d.failed.join(", ")}.` : ""}</p>
      <Source s={d.source} extra="stock and sector list: config/universe.yaml" live={d.live} at={d.as_of_time} reason={d.live_reason} />
    </Card>
  );
}

function Spark({ v }) {
  if (!v || v.length < 2) return null;
  const lo = Math.min(...v), hi = Math.max(...v), W = 110, H = 28;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * W},${H - ((x - lo) / (hi - lo || 1)) * H}`).join(" ");
  return <svg width={W} height={H} aria-hidden="true"><polyline points={pts} fill="none" stroke={v[v.length - 1] >= v[0] ? "var(--gain)" : "var(--loss)"} strokeWidth="1.5" /></svg>;
}

function CryptoGold() {
  const q = useQuery({ queryKey: ["pulse-assets"], queryFn: () => api("/pulse/assets"), retry: false, staleTime: 30000, refetchInterval: 60000 });
  if (q.isLoading) return <Card><Empty>Loading…</Empty></Card>;
  if (q.error) return <Card><Empty>Unavailable: {q.error.message}</Empty></Card>;
  const d = q.data;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g4">{d.rows.slice(0, 4).map((r) => <Kpi key={r.symbol} label={r.name} value={num(r.last, r.last < 10 ? 4 : 2)} sub={<span className={cls(r.change_pct)}>{pct(r.change_pct)}{r.live ? " · live" : ""}</span>} />)}</div>
      <Card title="Crypto, gold, silver and crude">
        <div className="table-wrap"><table>
          <thead><tr><th>Asset</th><th style={{ textAlign: "right" }}>Last</th><th style={{ textAlign: "right" }}>Day</th><th style={{ textAlign: "right" }}>1 month</th><th style={{ textAlign: "right" }}>1 year</th><th style={{ textAlign: "right" }}>RSI 14</th><th>52-week range</th><th>3 months</th></tr></thead>
          <tbody>{d.rows.map((r) => (
            <tr key={r.symbol}><td><b>{r.name}</b> <span className="note mono">{r.symbol}</span></td>
              <td className="mono" style={{ textAlign: "right" }}>{num(r.last, r.last < 10 ? 4 : 2)} <span className="note">{r.currency}</span></td>
              <td className={`mono ${cls(r.change_pct)}`} style={{ textAlign: "right" }}>{pct(r.change_pct)}</td>
              <td className={`mono ${cls(r.ret_1m)}`} style={{ textAlign: "right" }}>{pct(r.ret_1m)}</td>
              <td className={`mono ${cls(r.ret_1y)}`} style={{ textAlign: "right" }}>{pct(r.ret_1y)}</td>
              <td className="mono" style={{ textAlign: "right" }}>{rsiCell(r.rsi)}</td>
              <td className="mono note">{num(r.low_52w)} – {num(r.high_52w)}</td>
              <td><Spark v={r.spark} /></td></tr>))}</tbody></table></div>
        {d.derived.length > 0 && <div className="row" style={{ marginTop: 10, gap: 8 }}>{d.derived.map((x) => <span key={x.name} className="chip">{x.name}: <b>₹{x.value.toLocaleString("en-IN")}</b></span>)}</div>}
        <p className="note">Parity prices convert the COMEX price at today's USD/INR, before import duty and GST, so Indian retail and MCX prices are higher. Edit the list in config/universe.yaml (assets).{d.failed.length ? ` Not read: ${d.failed.map((x) => x.symbol).join(", ")}.` : ""}</p>
        <Source s={d.source} live={d.live} at={d.as_of_time} />
      </Card>
    </div>
  );
}

function MoneyFlow({ go }) {
  const q = useScreener();
  return <div className="stack" style={{ gap: 16 }}><SectorMonitor go={go} /><ScreenerState q={q}>{(d) => {
    const inflow = d.rows.filter((r) => r.flow === "Inflow"), outflow = d.rows.filter((r) => r.flow === "Outflow");
    return (<div className="stack" style={{ gap: 16 }}>
      <Market d={d} />
      <div className="grid g3">
        <Kpi label="Inflow (up on ≥1.5× turnover)" value={inflow.length} valueClass="gain" sub={inflow.slice(0, 4).map((r) => r.symbol).join(", ") || "none today"} />
        <Kpi label="Outflow (down on ≥1.5× turnover)" value={outflow.length} valueClass="loss" sub={outflow.slice(0, 4).map((r) => r.symbol).join(", ") || "none today"} />
        <Kpi label="Stocks read" value={`${d.rows.length} / ${d.universe}`} sub={`as of ${d.as_of || "—"}`} />
      </div>
      <Card title="Money Flow Index and turnover">
        <SortTable rows={d.rows} go={go} initial={["turnover_ratio", -1]} cols={[["symbol", "Symbol", sym], ["last", "Last", (v) => num(v), true],
          ["change_pct", "Day", chg, true], ["mfi", "MFI 14", rsiCell, true], ["turnover_cr", "Turnover ₹ Cr", (v) => num(v), true],
          ["turnover_ratio", "vs 20-day avg", (v) => (v == null ? "—" : `${v}×`), true], ["flow", "Flow", (v) => <span className={v === "Inflow" ? "gain" : v === "Outflow" ? "loss" : "muted"}>{v}</span>]]} />
        <p className="note">MFI is RSI weighted by volume: above 80 money has been pouring in (stretched), below 20 draining out. Turnover = close × volume for the day.</p>
        <Source s={d.source} extra={`stock list: ${d.list_file} + your watchlist`} live={d.live} at={d.as_of_time} reason={d.live_reason} />
      </Card>
    </div>);
  }}</ScreenerState></div>;
}

function OptionPulse() {
  const [s, setS] = useState("NIFTY");
  const [input, setInput] = useState("NIFTY");
  const [exp, setExp] = useState("");
  const q = useQuery({ queryKey: ["pulse-options", s, exp], queryFn: () => api(`/pulse/options?symbol=${encodeURIComponent(s)}${exp ? `&expiry=${exp}` : ""}`), retry: false, staleTime: 10000, refetchInterval: every(30000) });
  if (!q.data) return <Card><Unavailable q={q} what="Option Pulse" /></Card>;
  const d = q.data, p = d.pulse;
  const maxOi = Math.max(1, ...p.strikes.map((x) => Math.max(x.call_oi, x.put_oi)));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g4">{Object.entries(d.indices).map(([k, v]) => (
        <div key={k} className="card kpi" style={{ cursor: "pointer" }} onClick={() => { setS(k); setInput(k); setExp(""); }}>
          <div className="label">{k} · {v.expiry}</div><div className="value">PCR {v.pcr ?? "—"}</div>
          <div className="sub">Max pain {num(v.max_pain, 0)} · walls {num(v.put_wall, 0)} / {num(v.call_wall, 0)}</div></div>))}</div>
      <Card title={`${p.symbol} options · ${d.live ? `live ${d.as_of}` : d.date}`} action={<form className="row" onSubmit={(e) => { e.preventDefault(); setS(input.trim().toUpperCase()); setExp(""); }}>
        <input className="in" list="fo-syms" value={input} onChange={(e) => setInput(e.target.value)} style={{ width: 150 }} aria-label="Option symbol" />
        <datalist id="fo-syms">{d.symbols.map((x) => <option key={x} value={x} />)}</datalist>
        <select value={p.expiry} onChange={(e) => setExp(e.target.value)} aria-label="Expiry">{p.expiries.map((x) => <option key={x}>{x}</option>)}</select></form>}>
        <div className="grid g4" style={{ marginBottom: 12 }}>
          <div className="mini"><div className="note">Underlying</div><b className="mono">{num(p.underlying)}</b></div>
          <div className="mini"><div className="note">Put / call OI ratio</div><b className="mono">{p.pcr ?? "—"}</b></div>
          <div className="mini"><div className="note">Max pain</div><b className="mono">{num(p.max_pain, 0)}</b></div>
          <div className="mini"><div className="note">Put wall · call wall</div><b className="mono">{num(p.put_wall, 0)} · {num(p.call_wall, 0)}</b></div>
        </div>
        <div className="table-wrap"><table>
          <thead><tr><th style={{ textAlign: "right" }}>Call OI chg</th><th style={{ textAlign: "right" }}>Call OI</th><th style={{ textAlign: "center" }}>Strike</th><th>Put OI</th><th>Put OI chg</th></tr></thead>
          <tbody>{p.strikes.map((x) => (
            <tr key={x.strike} style={x.strike === p.max_pain ? { outline: "1px dashed var(--accent)" } : null}>
              <td className={`mono ${cls(x.call_chg)}`} style={{ textAlign: "right" }}>{big(x.call_chg)}</td>
              <td style={{ textAlign: "right" }}><div style={{ display: "inline-block", height: 8, width: `${(x.call_oi / maxOi) * 120}px`, background: "var(--loss)", borderRadius: 3, marginRight: 6 }} /><span className="mono">{big(x.call_oi)}</span></td>
              <td className="mono" style={{ textAlign: "center", fontWeight: Math.abs(x.strike - p.underlying) === Math.min(...p.strikes.map((y) => Math.abs(y.strike - p.underlying))) ? 800 : 400 }}>{num(x.strike, x.strike % 1 ? 2 : 0)}</td>
              <td><span className="mono">{big(x.put_oi)}</span><div style={{ display: "inline-block", height: 8, width: `${(x.put_oi / maxOi) * 120}px`, background: "var(--gain)", borderRadius: 3, marginLeft: 6 }} /></td>
              <td className={`mono ${cls(x.put_chg)}`}>{big(x.put_chg)}</td></tr>))}</tbody></table></div>
        <Source s={d.source} live={d.live} at={d.as_of} reason={d.live_reason} />
        <p className="note">OI in units (shares or index units), nearest 15 strikes to the underlying. The call wall often acts as resistance and the put wall as support; max pain is where option buyers would collect least at expiry.</p>
      </Card>
      <Card title="Most active contracts (by volume)">
        <SortTable rows={d.most_active.map((r) => ({ ...r, contract: `${r.strike} ${r.opt}` }))} initial={["volume", -1]} cols={[["symbol", "Symbol", sym], ["expiry", "Expiry"], ["contract", "Contract"],
          ["close", "Close", (v) => num(v), true], ["lots", "Lots traded", (v) => big(v), true], ["oi_chg", "OI chg", (v) => <span className={cls(v)}>{big(v)}</span>, true],
          ["notional_cr", "Notional ₹ Cr", (v) => num(v, 0), true]]} />
        <Source s={`NSE F&O bhavcopy, ${d.most_active_date}`} extra="most active list is end of day" live={false} />
      </Card>
    </div>
  );
}

function Radar({ go }) {
  const q = useScreener();
  return <ScreenerState q={q}>{(d) => {
    const hits = d.rows.filter((r) => r.setups.length).map((r) => ({ ...r, n: r.setups.length }));
    return (<Card title={`Stock Radar · ${hits.length} of ${d.rows.length} stocks with a setup`}>
      <SortTable rows={hits} go={go} initial={["n", -1]} empty="No setups in the list today." cols={[["symbol", "Symbol", sym], ["last", "Last", (v) => num(v), true], ["change_pct", "Day", chg, true],
        ["rsi", "RSI", rsiCell, true], ["from_52w_high_pct", "From 52w high", (v) => pct(v), true],
        ["setups", "Setups", (v) => <div className="row" style={{ gap: 4 }}>{v.map((x) => <span key={x} className="tag">{x}</span>)}</div>],
        ["plan", "Pullback plan", (v) => (v ? <span className="mono note">entry {num(v.entry)} · stop {num(v.stop)} · target {num(v.target)}</span> : "")]]} />
      <p className="note">Setups are mechanical checks on daily bars. Check the chart and the back-test odds on the Trade screen before acting.</p>
      <Source s={d.source} live={d.live} at={d.as_of_time} reason={d.live_reason} />
    </Card>);
  }}</ScreenerState>;
}

function Calm({ go }) {
  const q = useScreener();
  return <ScreenerState q={q}>{(d) => {
    const vix = d.market.quotes?.INDIAVIX?.last;
    const calm = d.rows.filter((r) => r.squeeze_rank != null && r.squeeze_rank <= 20);
    const reg = d.market.regime || {};
    return (<div className="stack" style={{ gap: 16 }}>
      <div className="grid g3">
        <Kpi label="India VIX" value={num(vix)} sub={vix == null ? "unavailable" : vix < 13 ? "calm: options are cheap" : vix < 18 ? "normal" : "elevated: size down"} valueClass={vix >= 18 ? "loss" : vix < 13 ? "gain" : ""} />
        <Kpi label="Market regime" value={reg.score != null ? `${reg.score}/100` : "—"} sub={`${reg.label || ""}${reg.exposure_ceiling ? ` · exposure ceiling ${reg.exposure_ceiling}%` : ""}`} />
        <Kpi label="Stocks in a squeeze" value={calm.length} sub="Bollinger width in its narrowest 20% of 6 months" />
      </div>
      <Card title="Regime checklist">
        <div className="list">{(reg.parts || []).map((p) => <div key={p.rule}><span>{p.ok ? "✓" : "✗"} {p.rule}</span><span className={p.ok ? "gain" : "muted"}>{p.ok ? `+${p.points}` : "0"}</span></div>)}</div>
      </Card>
      <Card title="Calm stocks (volatility squeeze)">
        <SortTable rows={calm} go={go} initial={["squeeze_rank", 1]} empty="No stock in the list is in a squeeze today." cols={[["symbol", "Symbol", sym], ["last", "Last", (v) => num(v), true], ["change_pct", "Day", chg, true],
          ["squeeze_rank", "Width rank (0 = narrowest)", (v) => v, true], ["atr_pct", "ATR % of price", (v) => (v == null ? "—" : `${v}%`), true],
          ["above_50dma", "Trend", (v, r) => (r.above_200dma ? (v ? "above 50 and 200 DMA" : "above 200 DMA") : "below 200 DMA")]]} />
        <p className="note">Quiet ranges often end in a strong move; the squeeze does not say which way. Wait for the break and use the range for the stop.</p>
        <Source s={d.source} live={d.live} at={d.as_of_time} reason={d.live_reason} />
      </Card>
    </div>);
  }}</ScreenerState>;
}

function Liquidity() {
  const [s, setS] = useState("NIFTY");
  const [input, setInput] = useState("NIFTY");
  const q = useQuery({ queryKey: ["pulse-liq", s], queryFn: () => api(`/pulse/liquidity?symbol=${encodeURIComponent(s)}`), retry: false, staleTime: 15000, refetchInterval: every(30000) });
  const d = q.data;
  const Row = ({ x }) => (<div className="row between" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
    <span>{x.kind}</span><span className="mono">{num(x.price)} <span className={`note ${cls(x.distance_pct)}`}>{pct(x.distance_pct)}</span></span></div>);
  return (
    <Card title="Liquidity Map" action={<form className="row" onSubmit={(e) => { e.preventDefault(); setS(input.trim().toUpperCase()); }}>
      <input className="in" value={input} onChange={(e) => setInput(e.target.value)} style={{ width: 160 }} aria-label="Symbol" /><button className="btn small primary">Map</button></form>}>
      {q.isLoading ? <Empty>Loading…</Empty> : q.error ? <Empty>{q.error.message}</Empty> : <>
        <div className="grid g3" style={{ marginBottom: 12 }}>
          <div className="mini"><div className="note">{d.symbol} last close</div><b className="mono">{num(d.last)}</b></div>
          <div className="mini"><div className="note">Day turnover</div><b className="mono">{d.turnover_cr ? `₹${num(d.turnover_cr)} Cr` : "—"}</b></div>
          <div className="mini"><div className="note">Option levels</div><b>{d.options_day ? `from NSE F&O, ${d.options_day}` : "no F&O data for this symbol"}</b></div>
        </div>
        <div className="grid g2">
          <div><b className="loss">Above: buy stops and sellers rest here</b>{d.above.length ? d.above.map((x) => <Row key={x.kind + x.price} x={x} />) : <Empty>No level above: price is at a high.</Empty>}</div>
          <div><b className="gain">Below: sell stops and buyers rest here</b>{d.below.length ? d.below.map((x) => <Row key={x.kind + x.price} x={x} />) : <Empty>No level below.</Empty>}</div>
        </div>
        <p className="note">Untaken swing highs and lows are where stop orders cluster; price often runs to them before turning.</p>
        <Source s={d.live ? "Kite real-time price and today's candle on Yahoo daily history; option levels from the latest NSE F&O file" : "Yahoo daily bars (delayed); option levels from the latest NSE F&O file"} live={d.live} />
      </>}
    </Card>
  );
}

function OiFlow({ go }) {
  const q = useQuery({ queryKey: ["pulse-oi"], queryFn: () => api("/pulse/oi-flow"), retry: false, staleTime: 10000, refetchInterval: every(30000) });
  const [k, setK] = useState("Long buildup");
  if (!q.data) return <Card><Unavailable q={q} what="OI Flow" /></Card>;
  const d = q.data;
  const help = { "Long buildup": "price up, OI up: fresh buying", "Short buildup": "price down, OI up: fresh selling",
    "Short covering": "price up, OI down: shorts exiting", "Long unwinding": "price down, OI down: longs exiting" };
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g4">{Object.keys(help).map((x) => (
        <div key={x} className={`card kpi ${k === x ? "sel" : ""}`} style={{ cursor: "pointer", outline: k === x ? "2px solid var(--accent)" : "none" }} onClick={() => setK(x)}>
          <div className="label">{x}</div><div className={`value ${x === "Long buildup" || x === "Short covering" ? "gain" : "loss"}`}>{d.counts[x]}</div><div className="sub">{help[x]}</div></div>))}</div>
      <Card title={`${k} · futures · ${d.live ? `live ${d.as_of}` : d.date}`}>
        <SortTable rows={d.groups[k]} go={go} initial={["oi_chg_pct", -1]} cols={[["symbol", "Symbol", (v, r) => <b>{v}{r.index ? " (index)" : ""}</b>],
          ["price", "Fut close", (v) => num(v), true], ["price_chg_pct", "Price chg", chg, true], ["oi", "OI", (v) => big(v), true],
          ["oi_chg", "OI chg", (v) => <span className={cls(v)}>{big(v)}</span>, true], ["oi_chg_pct", "OI chg %", (v) => pct(v), true]]} />
        <p className="note">All expiries are summed per symbol, so the monthly rollover does not look like unwinding. Price change is the nearest expiry's close vs its previous close.</p>
        <Source s={d.source} extra={`${d.symbols} symbols`} live={d.live} at={d.as_of} reason={d.live_reason} />
      </Card>
    </div>
  );
}

function RsiScreener({ go }) {
  const q = useScreener();
  const [f, setF] = useState("all");
  return <ScreenerState q={q}>{(d) => {
    const rows = d.rows.filter((r) => f === "all" || (f === "ob" ? r.rsi >= 70 : f === "os" ? r.rsi <= 30 : f === "bull" ? r.rsi >= 50 && r.rsi_weekly >= 50 : r.rsi < 50 && r.rsi_weekly < 50));
    return (<Card title="RSI Screener" action={<select value={f} onChange={(e) => setF(e.target.value)} aria-label="Filter">
      <option value="all">All</option><option value="ob">Overbought (≥ 70)</option><option value="os">Oversold (≤ 30)</option>
      <option value="bull">Daily and weekly ≥ 50</option><option value="bear">Daily and weekly &lt; 50</option></select>}>
      <SortTable rows={rows} go={go} initial={["rsi", -1]} empty="No stock matches this filter today." cols={[["symbol", "Symbol", sym], ["last", "Last", (v) => num(v), true],
        ["change_pct", "Day", chg, true], ["rsi", "RSI 14 daily", rsiCell, true], ["rsi_weekly", "RSI 14 weekly", rsiCell, true],
        ["above_200dma", "200 DMA", (v) => (v ? <span className="gain">above</span> : <span className="loss">below</span>)]]} />
      <p className="note">RSI 70+ is stretched and 30- is washed out; in a strong trend RSI can stay there for weeks. Combine with the weekly reading and the 200-day average.</p>
      <Source s={d.source} live={d.live} at={d.as_of_time} reason={d.live_reason} />
      {d.failed.length > 0 && <p className="note">Not read: {d.failed.map((x) => x.symbol).join(", ")}.</p>}
    </Card>);
  }}</ScreenerState>;
}

const LESSONS = [
  ["Futures in one minute", "A future is an agreement to buy or sell at a set price on expiry. You post margin, not the full value, so gains and losses are on the whole contract: a 1% move on a ₹15 lakh contract is ₹15,000. Profit and loss is settled daily (mark to market). Check the current lot size before every trade; NSE revises them."],
  ["Calls and puts", "A call gains when price rises above the strike; a put gains when it falls below. Buyers pay a premium and can lose only that. Sellers collect the premium but carry large, sometimes unlimited risk, which is why selling needs much more margin."],
  ["Open interest (OI)", "OI is the number of contracts still open. Rising OI means new money is entering, falling OI means positions are closing. On its own it does not say who is right: read it with price (see OI Flow)."],
  ["The four OI signals", "Price up with OI up is long buildup; price down with OI up is short buildup; price up with OI down is short covering, often fast but short-lived; price down with OI down is long unwinding. Buildups show conviction; covering and unwinding show exits."],
  ["Put / call ratio (PCR)", "Put OI divided by call OI for an expiry. Very high readings (above about 1.3) often show heavy put writing, which traders read as support; very low readings (below about 0.7) show call writing above. It works best at extremes, as a contrarian sign."],
  ["Max pain and OI walls", "Max pain is the strike at which option buyers would get least at expiry. The strike with the most call OI often caps rallies (call wall), and the one with the most put OI often holds dips (put wall). Walls move as positions change, so recheck them each day."],
  ["Time decay and volatility", "An option's premium loses time value every day and fastest in the final week. Higher implied volatility (India VIX for Nifty) makes options dearer. Buying options needs a quick, large move; selling them profits from calm, until a big move arrives."],
  ["Expiry day", "On expiry, premiums collapse to their intrinsic value and moves can be sharp near big OI strikes. Many traders avoid fresh positions late on expiry day; the risk rules here can block new F&O entries after a set time."],
  ["Sizing and the stop", "Decide the stop first, then size so a hit costs a fixed part of capital (1% or less). For options, count the premium you can lose; for futures and sold options, use the stop distance x lot size x lots. The ticket shows max loss before you confirm."],
  ["Costs add up", "Every round trip pays brokerage, STT, exchange and SEBI fees, stamp duty and GST. On options, STT on sale is charged on the premium. Small, frequent scalps can lose to costs alone: see the Charges screen for your real numbers."],
  ["What SEBI's study found", "SEBI's FY22 to FY24 studies found that roughly 9 in 10 individual F&O traders lost money over those three years. Treat F&O as a risk-managed tool, not a shortcut."],
];

function Academy() {
  const [open, setOpen] = useState(0);
  return (
    <Card title="F&O Academy">
      <div className="stack">{LESSONS.map(([t, body], i) => (
        <div key={t} className="mini" style={{ cursor: "pointer" }} onClick={() => setOpen(open === i ? -1 : i)}>
          <div className="row between"><b>{i + 1}. {t}</b><span className="muted">{open === i ? "−" : "+"}</span></div>
          {open === i && <p style={{ margin: "8px 0 0" }}>{body}</p>}
        </div>))}</div>
      <p className="note" style={{ marginTop: 10 }}>Education, not advice. Rules and lot sizes change; check NSE and SEBI circulars for current values.</p>
    </Card>
  );
}

export default function Pulse({ params, go }) {
  const [t, setT] = useState(TABS.some(([id]) => id === params.tab) ? params.tab : "money");
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div><h2>Market pulse</h2><p className="muted" style={{ margin: "4px 0 0" }}>Money flow, options, setups, volatility, liquidity, open interest and RSI in one place, from Yahoo daily bars and NSE's end-of-day F&amp;O file.</p></div>
      <Tabs tabs={TABS} value={t} onChange={(v) => { setT(v); go("pulse", { tab: v }); }} />
      {t === "money" && <MoneyFlow go={go} />}
      {t === "options" && <OptionPulse />}
      {t === "radar" && <Radar go={go} />}
      {t === "calm" && <Calm go={go} />}
      {t === "liquidity" && <Liquidity />}
      {t === "oi" && <OiFlow go={go} />}
      {t === "rsi" && <RsiScreener go={go} />}
      {t === "academy" && <Academy />}
      {t === "assets" && <CryptoGold />}
    </div>
  );
}
