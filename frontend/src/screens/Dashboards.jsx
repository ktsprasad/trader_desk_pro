import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, monthOf, signed } from "../api.js";
import { COLORS, Card, Empty, Msg, useAction } from "../ui.jsx";

const WIDGETS = {
  net_worth: ["Net worth", "KPI"], day_pnl: ["Day P/L", "KPI"], tax_so_far: ["Tax so far", "KPI"], bills_due: ["Bills due", "KPI"],
  charges_month: ["Charges this month", "KPI"], equity_curve: ["Equity curve", "Chart"], nifty: ["NIFTY intraday", "Chart"],
  open_positions: ["Open positions", "List"], spend_by_category: ["Spend by category", "Bars"], signals: ["Today's signals", "List"],
};
const month = () => monthOf(0);

function useWidgetData(type, allowed) {
  const on = (k) => type === k;
  const port = useQuery({ queryKey: ["portfolio-ov"], queryFn: () => api("/portfolio/overview"), enabled: (on("net_worth") || on("equity_curve")) && allowed("portfolio") });
  const pos = useQuery({ queryKey: ["positions-pnl"], queryFn: () => api("/positions"), enabled: (on("day_pnl") || on("open_positions")) && allowed("desk") });
  const tax = useQuery({ queryKey: ["tax", "cur"], queryFn: () => api("/tax"), enabled: on("tax_so_far") && allowed("tax") });
  const bills = useQuery({ queryKey: ["bills"], queryFn: () => api("/money/bills"), enabled: on("bills_due") && allowed("bills") });
  const spend = useQuery({ queryKey: ["money-sum", month()], queryFn: () => api(`/money/summary?month=${month()}`), enabled: on("spend_by_category") && allowed("expenses") });
  const ch = useQuery({ queryKey: ["charges-month", month()], queryFn: () => api(`/charges/month?month=${month()}`), enabled: on("charges_month") && allowed("charges") });
  const nifty = useQuery({ queryKey: ["chart", "NIFTY", "5m"], queryFn: () => api("/market/chart?symbol=NIFTY&interval=5m"), enabled: on("nifty") && allowed("charts") });
  const sig = useQuery({ queryKey: ["signals"], queryFn: () => api("/signals"), enabled: on("signals") && allowed("signals") });
  return { port: port.data, pos: pos.data, tax: tax.data, bills: bills.data, spend: spend.data, ch: ch.data, nifty: nifty.data, sig: sig.data };
}

function Spark({ pts, color = "#2f7cf6" }) {
  if (!pts || pts.length < 2) return <Empty>No history yet.</Empty>;
  const min = Math.min(...pts), max = Math.max(...pts), W = 400, H = 110;
  const xy = pts.map((v, i) => `${(i / (pts.length - 1)) * W},${H - 6 - ((v - min) / (max - min || 1)) * (H - 12)}`);
  return <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img" aria-label="trend">
    <polygon points={`0,${H} ${xy.join(" ")} ${W},${H}`} fill={color} opacity=".15" /><polyline points={xy.join(" ")} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>;
}

function Widget({ w, allowed }) {
  const d = useWidgetData(w.type, allowed);
  const [title] = WIDGETS[w.type] || [w.type];
  const kpi = (v, sub, c = "") => <><div className={`mono ${c}`} style={{ fontSize: 24, fontWeight: 600 }}>{v}</div><div className="note">{sub}</div></>;
  let body = <Empty>No access or no data.</Empty>;
  if (w.type === "net_worth" && d.port) body = kpi(inr(d.port.net_worth), `${signed(d.port.pnl)} all-time`);
  if (w.type === "day_pnl" && d.pos) { const v = d.pos.reduce((s, p) => s + (p.pnl || 0), 0); body = kpi(signed(v), `${d.pos.length} open position(s)`, cls(v)); }
  if (w.type === "tax_so_far" && d.tax) body = kpi(inr(d.tax.total), d.tax.next_due ? `next advance tax ${d.tax.next_due.due}` : "no instalment left");
  if (w.type === "bills_due" && d.bills) body = kpi(inr(d.bills.next_10_days.amount), `${d.bills.next_10_days.count} in next 10 days · ${d.bills.overdue.count} overdue`);
  if (w.type === "charges_month" && d.ch) body = kpi(inr(d.ch.charges), `${d.ch.orders} filled orders this month`);
  if (w.type === "equity_curve" && d.port) body = <Spark pts={d.port.history.map((h) => h.value)} />;
  if (w.type === "nifty" && d.nifty) body = <Spark pts={d.nifty.bars.slice(-75).map((b) => b.close)} color="#3fb950" />;
  if (w.type === "open_positions" && d.pos) body = <div className="list">{d.pos.slice(0, 6).map((p) => <div key={p.account_id + p.tradingsymbol}><span>{p.tradingsymbol} {p.product}</span><b className={`mono ${cls(p.pnl)}`}>{signed(p.pnl)}</b></div>)}{!d.pos.length && <div className="note">None.</div>}</div>;
  if (w.type === "signals" && d.sig) body = <div className="list">{d.sig.slice(0, 6).map((s) => <div key={s.id}><span>{s.side} {s.symbol} · {s.strategy}</span><span className="mono muted">{s.at}</span></div>)}{!d.sig.length && <div className="note">None today.</div>}</div>;
  if (w.type === "spend_by_category" && d.spend) {
    const e = Object.entries(d.spend.by_category).slice(0, 5), max = Math.max(1, ...e.map(([, v]) => v));
    body = <div className="stack">{e.map(([k, v], i) => <div key={k} className="row" style={{ flexWrap: "nowrap" }}><span style={{ width: 110 }} className="small">{k}</span><div className="bar" style={{ flex: 1 }}><span style={{ width: `${(v / max) * 100}%`, background: COLORS[i] }} /></div><span className="mono small">{inr(v)}</span></div>)}{!e.length && <div className="note">No spending yet.</div>}</div>;
  }
  return <>{title && <div className="card-h"><h3>{title}</h3></div>}{body}</>;
}

export default function Dashboards({ allowed }) {
  const q = useQuery({ queryKey: ["dashboards"], queryFn: () => api("/dashboards") });
  const [boards, setBoards] = useState(null);
  const [i, setI] = useState(0);
  const [edit, setEdit] = useState(false);
  const [selW, setSelW] = useState(null);
  const [act, msg] = useAction();
  useEffect(() => { if (q.data && !boards) setBoards(q.data.dashboards); }, [q.data]);
  if (!boards) return <Empty>Loading…</Empty>;
  const b = boards[i] || boards[0];
  const upd = (nb) => setBoards(boards.map((x, k) => (k === i ? nb : x)));
  const mobile = window.matchMedia("(max-width: 860px)").matches;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between">
        <div className="row">{boards.map((x, k) => <button key={k} className={`btn small ${k === i ? "primary" : ""}`} onClick={() => { setI(k); setSelW(null); }}>{x.name}</button>)}
          <button className="btn small" onClick={() => { const name = window.prompt("Dashboard name"); if (name) { setBoards([...boards, { name, widgets: [] }]); setI(boards.length); setEdit(true); } }}>New dashboard</button></div>
        <div className="row">
          <button className="btn" onClick={() => setEdit(!edit)}>{edit ? "Done editing" : "Edit"}</button>
          <button className="btn" onClick={() => { const blob = new Blob([JSON.stringify(boards, null, 2)], { type: "application/json" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "dashboards.json"; a.click(); }}>Export layout</button>
          <button className="btn primary" onClick={() => act.mutate({ path: "/dashboards", method: "PUT", body: { dashboards: boards }, done: "Saved." })}>Save dashboard</button>
        </div>
      </div>
      <Msg msg={msg} />
      <div className={edit ? "grid side-l" : ""}>
        {edit && <div className="stack" style={{ gap: 16 }}>
          <Card title="Widgets" action={<span className="note">click to add</span>}>
            <div className="stack" style={{ gap: 6 }}>{Object.entries(WIDGETS).map(([k, [l, t]]) => (
              <button key={k} className="mini row between" style={{ cursor: "pointer" }} onClick={() => upd({ ...b, widgets: [...b.widgets, { type: k, w: t === "KPI" ? 3 : 6 }] })}><span>{l}</span><span className="note">{t}</span></button>))}</div>
          </Card>
          {selW != null && b.widgets[selW] && <Card title="Widget settings">
            <b>{WIDGETS[b.widgets[selW].type]?.[0]}</b>
            <label className="f" style={{ marginTop: 8 }}>Width<select value={b.widgets[selW].w} onChange={(e) => upd({ ...b, widgets: b.widgets.map((x, k) => k === selW ? { ...x, w: Number(e.target.value) } : x) })}>
              {[3, 4, 6, 8, 12].map((n) => <option key={n} value={n}>{n} columns</option>)}</select></label>
            <label className="check" style={{ marginTop: 8 }}><input type="checkbox" checked={!!b.widgets[selW].hide_phone} onChange={(e) => upd({ ...b, widgets: b.widgets.map((x, k) => k === selW ? { ...x, hide_phone: e.target.checked } : x) })} />Hide on phone</label>
          </Card>}
        </div>}
        <div style={{ display: "grid", gridTemplateColumns: mobile ? "1fr" : "repeat(12, minmax(0, 1fr))", gap: 16, alignContent: "start",
          ...(edit ? { outline: "1px dashed var(--border)", outlineOffset: 6, borderRadius: 10 } : {}) }}>
          {b.widgets.map((w, k) => (mobile && w.hide_phone ? null :
            <div key={k} className={`card ${edit && selW === k ? "hl" : ""}`} style={{ gridColumn: mobile ? "auto" : `span ${w.w}`, position: "relative", cursor: edit ? "pointer" : "default" }} onClick={() => edit && setSelW(k)}>
              {edit && <button className="btn small" style={{ position: "absolute", top: 8, right: 8 }} aria-label="Remove widget" onClick={(e) => { e.stopPropagation(); upd({ ...b, widgets: b.widgets.filter((_, j) => j !== k) }); setSelW(null); }}>×</button>}
              <Widget w={w} allowed={allowed} />
            </div>))}
          {!b.widgets.length && <div style={{ gridColumn: "1 / -1" }}><Empty>Empty dashboard. Click Edit and add widgets.</Empty></div>}
        </div>
      </div>
    </div>
  );
}
