import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, hm, num, pct } from "../api.js";
import { Card, Empty, Msg, Seg, useAction } from "../ui.jsx";

const dayLabel = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
const gradeCls = (g) => (!g ? "muted" : g[0] === "A" || g[0] === "B" ? "gain" : g[0] === "C" ? "warnc" : "loss");

export default function Briefs({ go }) {
  const [kind, setKind] = useState("pre");
  const [sel, setSel] = useState(null);
  const [act, msg] = useAction();
  const list = useQuery({ queryKey: ["briefs"], queryFn: () => api("/briefs") });
  const rows = (list.data || []).filter((b) => b.kind === kind);
  const id = sel && rows.some((r) => r.id === sel) ? sel : rows[0]?.id;
  const b = useQuery({ queryKey: ["brief", id], queryFn: () => api(`/briefs/${id}`), enabled: !!id });
  const body = b.data?.body;
  const run = () => act.mutate({ path: "/briefs/run", body: { kind }, done: "Done.", then: (r) => setSel(r.id) });

  return (
    <div className="grid side-l">
      <Card title="Briefs">
        <Seg className="full" options={[["pre", "Pre-market"], ["post", "Post-market"]]} value={kind} onChange={(v) => { setKind(v); setSel(null); }} />
        <div className="stack" style={{ marginTop: 10, gap: 4 }}>
          {rows.map((r) => (
            <button key={r.id} className={`nav ${r.id === id ? "active" : ""}`} onClick={() => setSel(r.id)} style={{ justifyContent: "space-between" }}>
              <span>{dayLabel(r.day)} · {r.market}</span><b className={gradeCls(r.grade)}>{r.grade || ""}</b></button>))}
          {!rows.length && <div className="note">None yet.</div>}
        </div>
        <button className="btn small" style={{ marginTop: 10 }} disabled={act.isPending} onClick={run}>{act.isPending ? "Working…" : kind === "pre" ? "Generate pre-market brief" : "Run post-market review"}</button>
        <Msg msg={msg} />
        <p className="note">Pre-market runs at 08:45 IST and the review at 15:45 on trading days (Settings → Analysis providers). Grades come from the review: how the morning's bias played out.</p>
      </Card>

      {!body ? <Empty>{id ? "Loading…" : "Generate the first brief on the left."}</Empty> : kind === "post" ? (
        <Card title={`Post-market review · ${dayLabel(b.data.day)}`}>
          {body.error ? <div className="warn">{body.error}</div> : <>
            <h2 className={gradeCls(body.grade)} style={{ fontSize: 40 }}>{body.grade}</h2>
            <p>{body.note}</p>
            <div className="grid g4"><div><div className="note">NIFTY open</div><b className="mono">{num(body.nifty_open)}</b></div>
              <div><div className="note">Close</div><b className="mono">{num(body.nifty_close)}</b></div>
              <div><div className="note">Open → close</div><b className={`mono ${cls(body.change_pct)}`}>{pct(body.change_pct)}</b></div>
              <div><div className="note">Range</div><b className="mono">{body.range_pct}%</b></div></div>
          </>}
        </Card>
      ) : (
        <div className="stack" style={{ gap: 16 }}>
          <Card className="hl">
            <div className="note">Pre-market brief · {dayLabel(b.data.day)} · generated {hm(b.data.created_at)} IST</div>
            <h2 style={{ fontSize: 26, margin: "4px 0 8px" }}>{body.headline}</h2>
            <p className="muted" style={{ margin: 0 }}>{body.summary}</p>
            <div className="row" style={{ margin: "10px 0" }}><span className="tag rules">Rules engine</span>{(body.sources || []).filter((s) => s !== "Rules engine").map((s) => <span key={s} className="pill">{s}</span>)}
              <button className="btn small" onClick={run}>Regenerate</button></div>
            {body.what_would_change && <div className="mini"><b>What would change this view:</b> {body.what_would_change}.</div>}
          </Card>
          <div className="grid g3">
            <Card title="Global cues"><div className="list">{body.global_cues.map((g) => <div key={g.name}><span>{g.name}</span>
              <b className={`mono ${g.name.startsWith("USD") ? "" : cls(g.change_pct)}`}>{g.error ? "—" : g.name.startsWith("USD") ? num(g.last) : pct(g.change_pct)}</b></div>)}</div></Card>
            <Card title="India context"><div className="list">
              <div><span>NIFTY</span><b className="mono">{num(body.indices?.NIFTY?.last, 0)} <span className={cls(body.indices?.NIFTY?.change_pct)}>{pct(body.indices?.NIFTY?.change_pct)}</span></b></div>
              <div><span>India VIX</span><b className="mono">{num(body.indices?.["INDIA VIX"]?.last, 1)}</b></div>
              <div><span>Regime score</span><b className="mono">{body.regime?.score ?? "—"} · {body.regime?.label}</b></div>
              {(body.unavailable || []).map((u) => <div key={u}><span className="muted">{u}</span><span className="note">no free source</span></div>)}
            </div></Card>
            <Card title="NIFTY levels"><div className="list">
              {[["previous_high", "Previous high"], ["r1", "Pivot R1"], ["pivot", "Pivot"], ["s1", "Pivot S1"], ["previous_low", "Previous low"], ["previous_close", "Previous close"]].map(([k, l]) =>
                <div key={k}><span>{l}</span><b className="mono">{num(body.levels?.[k], 0)}</b></div>)}</div></Card>
          </div>
          <Card title="Planned setups" action={<span className="note">From your watchlist. Odds from history.</span>}>
            <div className="table-wrap"><table>
              <thead><tr><th>Symbol</th><th>Setup</th><th>Side</th><th className="num">Entry</th><th className="num">Stop</th><th className="num">Target</th><th className="num">Hit rate</th><th>Source</th><th /></tr></thead>
              <tbody>{body.setups.map((s, i) => (
                <tr key={i}><td><b>{s.symbol}</b></td><td>{s.strategy}{s.note ? <div className="note">{s.note}</div> : null}</td>
                  <td className={s.side === "BUY" ? "gain" : ""}>{s.side === "BUY" ? "Long" : s.side}</td>
                  <td className="num">{num(s.entry)}</td><td className="num">{num(s.stop)}</td><td className="num">{num(s.target)}</td>
                  <td className="num">{s.hit_rate != null ? `${s.hit_rate}%` : "—"} <span className="note">n={s.sample}</span></td>
                  <td><span className="tag rules">{s.source}</span></td>
                  <td>{s.entry ? <button className="btn small" onClick={() => go("trade", { symbol: s.symbol, side: "BUY", entry: s.entry, stop: s.stop, target: s.target, plan: "pb20" })}>Arm plan</button>
                    : <button className="btn small" onClick={() => go("charts", { symbol: s.symbol })}>Watch</button>}</td></tr>))}
                {!body.setups.length && <tr><td colSpan={9} className="muted">No setups met the rules on the watchlist today.</td></tr>}</tbody>
            </table></div>
          </Card>
          <Card title="No-trade conditions today"><ul style={{ margin: 0, paddingLeft: 18 }}>{body.no_trade.map((x) => <li key={x}>{x}</li>)}</ul></Card>
        </div>
      )}
    </div>
  );
}
