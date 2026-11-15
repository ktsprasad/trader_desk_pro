import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, inr, num } from "./api.js";

const P = {
  desk: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  charts: "M3 3v18h18M7 15l4-4 3 3 5-6",
  pulse: "M2 12h4l3-8 4 16 3-8h6",
  signals: "M12 3 2 20h20L12 3zm0 6v5m0 3v.5",
  trade: "M7 7h13l-3-3M17 17H4l3 3",
  options: "M4 6h16M4 12h16M4 18h16M9 3v18",
  briefs: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7",
  portfolio: "M12 3a9 9 0 1 0 9 9h-9z M14 3.3A9 9 0 0 1 20.7 10H14z",
  metals: "M5 20h14l-3-8H8zM9 12l3-8 3 8",
  tax: "M7 3h10v18l-2.5-1.5L12 21l-2.5-1.5L7 21zM10 8h4M10 12h4",
  charges: "M7 5h10M7 9h10M9 5c4 0 6 2 6 4s-2 4-6 4l7 6",
  expenses: "M3 6h18v12H3zM3 10h18M7 15h3",
  bills: "M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4",
  dash: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  ai: "M4 5h16v11H8l-4 4zM8 9h8M8 12h5",
  conn: "M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0zM12 18v3",
  prov: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
  users: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  theme: "M12 3a9 9 0 1 0 0 18V3z",
  bell: "M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  menu: "M4 6h16M4 12h16M4 18h16",
  shield: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  userplus: "M15 19a6 6 0 0 0-12 0M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M16 11h6",
  logo: "M4 16l5-5 4 4 7-7M15 8h5v5",
  rules: "M4 4h16v16H4zM8 9h8M8 13h8M8 17h5",
  book: "M4 4h11a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z",
};

export function Icon({ name, size }) {
  return (
    <svg viewBox="0 0 24 24" width={size || 17} height={size || 17} fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={P[name] || P.list} /></svg>
  );
}

export function Card({ title, action, children, className = "", style }) {
  return (
    <section className={`card ${className}`} style={style}>
      {(title || action) && <div className="card-h"><h3>{title}</h3>{action}</div>}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, valueClass = "", className = "" }) {
  return (
    <div className={`card kpi ${className}`}>
      <div className="label">{label}</div>
      <div className={`value ${valueClass}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([id, label]) => <button key={id} role="tab" aria-selected={value === id} className={value === id ? "on" : ""} onClick={() => onChange(id)}>{label}</button>)}
    </div>
  );
}

export function Seg({ options, value, onChange, className = "" }) {
  return (
    <div className={`seg ${className}`}>
      {options.map(([id, label, extra]) => <button key={id} type="button" className={`${value === id ? "on" : ""} ${extra || ""}`} onClick={() => onChange(id)}>{label}</button>)}
    </div>
  );
}

export function Toggle({ on, onChange, label, disabled }) {
  return <button type="button" className={`toggle ${on ? "on" : ""}`} aria-pressed={!!on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)} />;
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function Msg({ msg }) {
  if (!msg) return null;
  return <div className={msg.ok ? "note" : "error"} style={{ marginTop: 6 }}>{msg.text}</div>;
}

export function useAction() {
  const qc = useQueryClient();
  const [msg, setMsg] = useState(null);
  const m = useMutation({
    mutationFn: ({ path, method = "POST", body }) => api(path, { method, body }),
    onSuccess: (r, v) => { qc.invalidateQueries(); setMsg(v.done ? { ok: true, text: typeof v.done === "function" ? v.done(r) : v.done } : null); v.then && v.then(r); },
    onError: (e) => setMsg({ ok: false, text: e.message }),
  });
  return [m, msg, setMsg];
}

export function Verdict({ t }) {
  const v = t.verdict;
  if (!v) return null;
  return (
    <div className={`verdict ${v.verdict}`}>
      <b>{v.verdict === "PASS" ? "✓ Risk check: PASS" : "✕ Risk check: BLOCK"}</b> <span className="note">· {v.kind} · rules v{v.rules_version}</span>
      <ul>
        {v.reasons.map((r) => <li key={r}>{r}</li>)}
        {(v.info || []).map((x) => <li key={x} className="muted">{x}</li>)}
        {(v.notes || []).map((x) => <li key={x} className="muted">{x}</li>)}
      </ul>
      {(v.warnings || []).map((w) => <div key={w} className="warn" style={{ marginTop: 6 }}>{w}</div>)}
    </div>
  );
}

/** The confirm step every order goes through: type the order back; password before the first live order. */
export function Confirm({ t, onDone }) {
  const qc = useQueryClient();
  const [typed, setTyped] = useState("");
  const [pw, setPw] = useState("");
  const [needPw, setNeedPw] = useState(false);
  const [err, setErr] = useState(null);
  const send = useMutation({
    mutationFn: async () => {
      if (needPw) await api("/auth/reauth", { method: "POST", body: { password: pw } });
      return api(`/orders/${t.id}/confirm`, { method: "POST", body: { fingerprint: t.fingerprint, typed } });
    },
    onSuccess: (r) => { qc.invalidateQueries(); onDone && onDone(r); },
    onError: (e) => {
      if (e.status === 403 && String(e.message).startsWith("reauth_required")) setNeedPw(true);
      setErr(e.message);
    },
  });
  const ok = typed.trim().toUpperCase() === (t.confirm_phrase || "").toUpperCase();
  return (
    <div className="stack" style={{ marginTop: 10 }}>
      <div className="note">{t.confirm_text}</div>
      <label className="f">Type <b className="mono" style={{ color: "var(--text)" }}>{t.confirm_phrase}</b> to confirm
        <input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus aria-label="Type the order to confirm" />
      </label>
      {needPw && <label className="f">Password (first live order today)<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>}
      {err && <div className="error">{err}</div>}
      <button className="btn primary block" disabled={!ok || send.isPending} onClick={() => send.mutate()}>
        {send.isPending ? "Sending…" : `Place ${t.order?.side?.toLowerCase() || ""} order`}
      </button>
      <div className="note">{t.paper ? "Paper mode: this goes to the simulator. Switch to live in Settings → Connections." : "LIVE: this sends a real order."} The ticket expires 5 minutes after it was built; the risk check runs again on send.</div>
    </div>
  );
}

export function TicketResult({ t, onClose }) {
  const [sent, setSent] = useState(null);
  if (!t) return null;
  return (
    <div className="stack">
      <div className="row between"><b className="mono">{t.tag}</b>{t.paper ? <span className="chip paper">PAPER</span> : <span className="chip live">LIVE</span>}</div>
      {t.charges && (
        <div className="grid g3">
          <div><div className="note">Max loss</div><b className="mono">{inr(t.max_loss)}</b></div>
          <div><div className="note">Margin</div><b className="mono">{inr(t.margin?.required)}</b></div>
          <div><div className="note">Charges</div><b className="mono">{inr(t.charges.total)}</b></div>
          <div><div className="note">Breakeven</div><b className="mono">{num(t.charges.breakeven)}</b></div>
          {t.charges.pct_of_expected_profit != null && <div><div className="note">Charges / profit</div><b className="mono">{t.charges.pct_of_expected_profit}%</b></div>}
        </div>
      )}
      <Verdict t={t} />
      {t.stage === "checked" && !sent && <Confirm t={t} onDone={setSent} />}
      {t.stage === "blocked" && <div className="note">Blocked orders never reach the broker. Change the order, or (owner) the rule in Settings; the change is logged.</div>}
      {sent && (
        <div className={`verdict ${sent.stage === "sent" ? "PASS" : "BLOCK"}`}>
          <b>{sent.stage === "sent" ? `Sent · ${sent.status}` : `Not sent · ${sent.stage}`}</b>
          {sent.error && <div className="error">{sent.error}</div>}
          {sent.oco_gtt_id && <div className="note">Stop + target armed at the broker as OCO GTT {sent.oco_gtt_id}.</div>}
        </div>
      )}
      {onClose && <button className="btn" onClick={onClose}>Close</button>}
    </div>
  );
}

export function Modal({ children, onClose, wide }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={wide ? { width: "min(760px, 100%)" } : null} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">{children}</div>
    </div>
  );
}

export const COLORS = ["#3b82f6", "#a855f7", "#f97316", "#eab308", "#22d3ee", "#ec4899", "#6b7280", "#22c55e"];
