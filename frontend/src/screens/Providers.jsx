import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api.js";
import { Card, Empty, Modal, Msg, Toggle, useAction } from "../ui.jsx";

export default function Providers() {
  const q = useQuery({ queryKey: ["providers"], queryFn: () => api("/providers") });
  const [p, setP] = useState(null);
  const [yaml, setYaml] = useState(false);
  const [act, msg] = useAction();
  useEffect(() => { if (q.data) setP(q.data.pipeline); }, [q.data]);
  const d = q.data;
  if (!d || !p) return <Empty>Loading…</Empty>;
  const claudeOn = d.providers.find((x) => x.id === "claude")?.on;
  const save = () => act.mutate({ path: "/providers/pipeline", method: "PUT", body: { pipeline: p }, done: "Pipeline saved." });
  return (
    <div className="grid side-l">
      <div className="stack" style={{ gap: 16 }}>
        <Card title="Providers">
          <div className="stack">{d.providers.map((x) => (
            <div key={x.id} className="mini row between" style={{ flexWrap: "nowrap" }}>
              <div className="row" style={{ flexWrap: "nowrap" }}><span className={`tag ${x.kind.toLowerCase()}`}>{x.kind}</span><div><b>{x.name}</b><div className="note">{x.desc}</div></div></div>
              <span className={`small ${x.on ? "gain" : "muted"}`}>{x.status}</span></div>))}</div>
        </Card>
        <Card title="Installed Claude skills">
          <div className="row">{d.skills.map((s) => <span key={s.path} className="pill" title={s.path}>{s.name}</span>)}</div>
          <p className="note">From the repo's skills/ folder. They run in Claude Code or Claude Desktop; the app's own pipeline uses the rules engine.</p>
        </Card>
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <Card title="Job pipeline" action={<span className="pill">{p.name}</span>}>
          <div className="grid g4">
            <label className="f">Pre-market at (IST)<input className="mono" value={p.schedule} onChange={(e) => setP({ ...p, schedule: e.target.value })} /></label>
            <label className="f">Post-market at (IST)<input className="mono" value={p.post_schedule} onChange={(e) => setP({ ...p, post_schedule: e.target.value })} /></label>
            <label className="f">Signal scan every (min)<input className="mono" type="number" min="1" max="60" value={p.scan_every_min} onChange={(e) => setP({ ...p, scan_every_min: Number(e.target.value) })} /></label>
            <label className="f">If a provider fails<select value={p.on_fail} onChange={(e) => setP({ ...p, on_fail: e.target.value })}><option value="rules_only">Publish rules-only brief</option><option value="skip">Skip the brief</option></select></label>
          </div>
          <label className="check" style={{ margin: "10px 0" }}><input type="checkbox" checked={p.enabled} onChange={(e) => setP({ ...p, enabled: e.target.checked })} />Run on NSE trading days</label>
          <div className="stack">{p.steps.map((s, i) => (
            <div key={s.id} className={`mini row between ${s.kind === "Claude" && s.on ? "sel" : ""}`} style={{ flexWrap: "nowrap", opacity: s.on ? 1 : 0.6 }}>
              <div className="row" style={{ flexWrap: "nowrap" }}><span className="muted mono">{i + 1}</span><span className={`tag ${s.kind.toLowerCase()}`}>{s.kind}</span>
                <div><b className="mono">{s.id}</b><div className="note">{s.desc}{s.kind === "Claude" && !claudeOn ? " · needs an API key (AI assistant)" : ""}{s.kind === "Claude" ? " · not wired into the brief yet" : ""}</div></div></div>
              <Toggle on={s.on} label={`${s.id} on/off`} disabled={s.kind === "Claude"} onChange={(v) => setP({ ...p, steps: p.steps.map((x, j) => j === i ? { ...x, on: v } : x) })} />
            </div>))}</div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" onClick={() => act.mutate({ path: "/briefs/run", body: { kind: "pre" }, done: "Brief generated. Open Briefs to read it." })}>Run now</button>
            <button className="btn" onClick={() => setYaml(true)}>View config</button>
            <button className="btn primary" onClick={save}>Save pipeline</button>
          </div>
          <Msg msg={msg} />
          <p className="note">LLM providers can draft prose, never orders. Every price in a brief comes from the rules engine.</p>
        </Card>
        <Card title="Provider scorecard" action={<span className="note">Graded by the post-market review</span>}>
          <div className="table-wrap"><table>
            <thead><tr><th>Provider</th><th className="num">Calls</th><th className="num">Bias correct</th><th className="num">Setups hit target</th><th className="num">Avg R</th><th className="num">Cost</th></tr></thead>
            <tbody>{d.scorecard.map((s) => <tr key={s.provider}><td>{s.provider}</td><td className="num">{s.calls}</td><td className="num">{s.bias_correct != null ? `${s.bias_correct}%` : "—"}</td>
              <td className="num">{s.hit != null ? `${s.hit}%` : "—"}</td><td className="num">{s.avg_r != null ? s.avg_r : "—"}</td><td className="num">₹{s.cost}</td></tr>)}</tbody>
          </table></div>
        </Card>
      </div>
      {yaml && <Modal onClose={() => setYaml(false)}><pre className="mono small" style={{ whiteSpace: "pre-wrap", maxHeight: "60vh", overflow: "auto" }}>{JSON.stringify(p, null, 2)}</pre><button className="btn" onClick={() => setYaml(false)}>Close</button></Modal>}
    </div>
  );
}
