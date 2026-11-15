import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, num } from "../api.js";
import { Card, Empty, Msg, Seg, Toggle, useAction } from "../ui.jsx";
import PriceChart from "../PriceChart.jsx";

function Ring({ score }) {
  const r = 26, c = 2 * Math.PI * r, v = score ?? 0;
  return (
    <svg width="68" height="68" viewBox="0 0 68 68" role="img" aria-label={`regime score ${score ?? "unknown"}`}>
      <circle cx="34" cy="34" r={r} fill="none" stroke="var(--panel-3)" strokeWidth="7" />
      <circle cx="34" cy="34" r={r} fill="none" stroke={v >= 60 ? "var(--gain)" : v >= 40 ? "var(--warn)" : "var(--loss)"} strokeWidth="7"
        strokeDasharray={`${(c * v) / 100} ${c}`} transform="rotate(-90 34 34)" strokeLinecap="round" />
      <text x="34" y="39" textAnchor="middle" fontSize="16" fontWeight="700" fill="var(--text)">{score ?? "—"}</text>
    </svg>
  );
}

export default function Signals({ go, user }) {
  const sum = useQuery({ queryKey: ["sig-summary"], queryFn: () => api("/signals/summary"), refetchInterval: 60000 });
  const list = useQuery({ queryKey: ["signals"], queryFn: () => api("/signals"), refetchInterval: 30000 });
  const [sel, setSel] = useState(null);
  const [act, msg] = useAction();
  const [wl, setWl] = useState("");
  const s = sum.data;
  const rows = list.data || [];
  const cur = rows.find((x) => x.id === sel) || rows[0];
  const gate = useQuery({ queryKey: ["gate", cur?.id], queryFn: () => api(`/signals/${cur.id}/gate`), enabled: !!cur });
  const chart = useQuery({ queryKey: ["chart", cur?.symbol, "5m"], queryFn: () => api(`/market/chart?symbol=${cur.symbol}&interval=5m`), enabled: !!cur, retry: false });
  useEffect(() => { if (s && !wl) setWl(s.watchlist.join(", ")); }, [s]);
  const owner = user.role === "owner";
  if (!s) return <Empty>{sum.error ? sum.error.message : "Loading signals…"}</Empty>;
  const bt = (k) => s.strategies.find((x) => x.key === k)?.backtest;
  const allBt = s.strategies.filter((x) => x.enabled && x.backtest?.trades);
  const hit = allBt.length ? (allBt.reduce((a, x) => a + x.backtest.target_pct * x.backtest.trades, 0) / allBt.reduce((a, x) => a + x.backtest.trades, 0)).toFixed(0) : null;

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid g4">
        <div className="card row" style={{ gap: 14, flexWrap: "nowrap" }}><Ring score={s.regime.score} />
          <div><div className="note">Market regime · NSE</div><b style={{ fontSize: 18 }}>{s.regime.label}</b>
            <div className="note">{s.regime.exposure_ceiling ? `Exposure ceiling ${s.regime.exposure_ceiling}%` : s.regime.error || ""}</div></div></div>
        <div className="card kpi"><div className="label">Signals today</div><div className="value">{s.today.total}</div>
          <div className="sub"><span className="gain">{s.today.buy} buy</span> · <span className="warnc">{s.today.sell} sell</span> · {s.today.waiting} waiting for you</div></div>
        <div className="card kpi"><div className="label">Back-test hit rate</div><div className="value">{hit ? `${hit}%` : "—"}</div>
          <div className="sub">{s.backtest_day ? `watchlist, last ~60 sessions · ${s.backtest_day}` : "run a back-test"}</div></div>
        <div className="card kpi"><div className="label">Circuit breaker</div>
          <div className={`value ${s.breaker.open ? "gain" : "loss"}`} style={{ fontSize: 18 }}>{s.breaker.open ? "Open for trading" : "Tripped"}</div>
          <div className="sub">day loss {s.breaker.used_pct}% of limit</div></div>
      </div>

      <div className="grid side-l">
        <Card title="Strategies">
          <div className="stack">
            {s.strategies.map((x) => (
              <div className="mini" key={x.key}>
                <div className="row between"><b>{x.name}</b><Toggle on={x.enabled} label={`${x.name} on/off`} disabled={!owner}
                  onChange={(v) => act.mutate({ path: "/signals/strategies", method: "PUT", body: { strategies: { [x.key]: { enabled: v } } } })} /></div>
                <div className="note">{x.origin}</div>
                <div className="row" style={{ marginTop: 6 }}>
                  <Seg options={[["one_click", "One-click"], ["alert_only", "Alert only"]]} value={x.mode}
                    onChange={(v) => owner && act.mutate({ path: "/signals/strategies", method: "PUT", body: { strategies: { [x.key]: { mode: v } } } })} />
                </div>
                <div className="row" style={{ marginTop: 6 }}>
                  <span className="pill">BT {bt(x.key)?.target_pct ?? "—"}% · {bt(x.key)?.expectancy_r != null ? `${bt(x.key).expectancy_r > 0 ? "+" : ""}${bt(x.key).expectancy_r}R` : "—"}</span>
                  <span className="pill">{x.market}</span></div>
              </div>
            ))}
            <div className="note">Auto-execution is not offered: every signal becomes a ticket you confirm.</div>
            <div className="row"><button className="btn small" onClick={() => act.mutate({ path: "/signals/scan", done: (r) => `${r.added} new signal(s).` })}>Scan now</button>
              <button className="btn small" onClick={() => act.mutate({ path: "/signals/backtest", done: "Back-test refreshed." })}>Run back-test</button></div>
            <Msg msg={msg} />
          </div>
        </Card>

        <div className="stack" style={{ gap: 16 }}>
          <Card title="Live signal feed" action={<span className="note">scans every 5 min, 09:20–14:35 IST</span>}>
            <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}><table>
              <thead><tr><th>Time</th><th>Signal</th><th>Symbol</th><th>Strategy</th><th className="num">Entry</th><th className="num">Stop</th><th className="num">Target</th><th>History</th><th>Status</th></tr></thead>
              <tbody>{rows.map((x) => (
                <tr key={x.id} className={`click ${cur?.id === x.id ? "sel" : ""}`} onClick={() => setSel(x.id)}>
                  <td className="mono">{x.at}</td><td><span className={x.side === "BUY" ? "badge-buy" : "badge-sell"}>{x.side === "BUY" ? "▲ BUY" : "▼ SELL"}</span></td>
                  <td><b>{x.symbol}</b></td><td>{x.strategy}</td><td className="num">{num(x.entry)}</td><td className="num">{num(x.stop)}</td><td className="num">{num(x.target)}</td>
                  <td><div className="row" style={{ flexWrap: "nowrap" }}><div className="bar" style={{ width: 60 }}><span style={{ width: `${x.confidence}%` }} /></div><span className="mono small">{x.confidence ? `${x.confidence}%` : "—"}</span></div></td>
                  <td>{x.status === "waiting" ? "Waiting for you" : x.status}</td></tr>))}
                {!rows.length && <tr><td colSpan={9} className="muted">No signals yet today. Strategies scan the watchlist during the session.</td></tr>}</tbody>
            </table></div>
          </Card>

          {cur && <Card title="Selected signal" action={<span className={cur.side === "BUY" ? "badge-buy" : "badge-sell"}>{cur.side}</span>}>
            <div className="grid g2">
              <div>
                <h2 style={{ fontSize: 20 }}>{cur.symbol} <span className="note">{cur.strategy} · {cur.at}</span></h2>
                <p className="muted">{cur.reason}</p>
                {chart.data && <PriceChart data={chart.data} levels={{ entry: cur.entry, stop: cur.stop, target: cur.target }} show={{ vwap: true }} height={200} />}
                <div className="grid g4" style={{ marginTop: 8 }}>
                  <div><div className="note">Entry</div><b className="mono">{num(cur.entry)}</b></div>
                  <div><div className="note">Stop</div><b className="mono">{num(cur.stop)}</b></div>
                  <div><div className="note">Target</div><b className="mono">{num(cur.target)}</b></div>
                  <div><div className="note">Size (regime-adjusted)</div><b className="mono">{gate.data?.size ?? "…"} sh</b></div>
                </div>
              </div>
              <div>
                <div className="row between"><b className="note">PRE-TRADE DISCIPLINE GATE</b><span className="gain small">{gate.data ? `${gate.data.passed} of ${gate.data.checks.length} pass` : ""}</span></div>
                <div className="stack" style={{ marginTop: 8 }}>
                  {(gate.data?.checks || []).map((c) => <div key={c.text} className={c.ok ? "" : "loss"}>{c.ok ? "✓" : "✕"} {c.text}</div>)}
                  {(gate.data?.unchecked || []).map((c) => <div key={c} className="muted">– {c}: not checked</div>)}
                </div>
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="btn primary" onClick={() => go("trade", { symbol: cur.symbol, side: cur.side, entry: cur.entry, stop: cur.stop, target: cur.target, qty: gate.data?.size || 1, signal: cur.id, plan: cur.strategy.slice(0, 3).toLowerCase() })}>Open pre-filled ticket</button>
                  <button className="btn" onClick={() => act.mutate({ path: `/signals/${cur.id}/status`, body: { status: "ignored" } })}>Ignore</button>
                </div>
                <p className="note">The ticket still runs the risk check and needs your typed confirmation.</p>
              </div>
            </div>
          </Card>}

          <Card title="Watchlist" action={owner && <button className="btn small primary" onClick={() => act.mutate({ path: "/watchlist", method: "PUT", body: { symbols: wl.split(/[\s,]+/).filter(Boolean) }, done: "Watchlist saved." })}>Save</button>}>
            <textarea className="in" style={{ width: "100%", minHeight: 60 }} value={wl} onChange={(e) => setWl(e.target.value)} disabled={!owner} aria-label="Watchlist symbols" />
            <p className="note">NSE symbols, comma separated (up to 40). Strategies scan these; the brief picks setups from them.</p>
          </Card>
        </div>
      </div>
      <p className="note">Signals are rule outputs with their historical hit rate on delayed data, not investment advice.</p>
    </div>
  );
}
