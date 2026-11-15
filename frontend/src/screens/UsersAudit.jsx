import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import QRCode from "qrcode";
import { api } from "../api.js";
import { Card, Empty, Msg, Tabs, useAction } from "../ui.jsx";

const ROLES = ["owner", "trader", "viewer", "accountant", "family"];
const PERMS = [
  ["See portfolio, orders, connections", ["owner", "trader", "viewer", "accountant"]],
  ["Build tickets, send, cancel, flatten, kill switch", ["owner", "trader"]],
  ["Tax centre and exports", ["owner", "accountant"]],
  ["Money: expenses and bills", ["owner", "family"]],
  ["Manage users, brokers, risk rules, go live, release kill switch, audit", ["owner"]],
];
const MODULES = ["", "auth", "orders", "risk", "brokers", "portfolio", "users", "signals", "briefs", "tax", "money", "ai", "settings"];

function TwoFactor({ me }) {
  const [step, setStep] = useState(null);
  const [code, setCode] = useState("");
  const [act, msg] = useAction();
  const start = async () => {
    const r = await api("/auth/totp/start", { method: "POST" });
    setStep({ ...r, qr: await QRCode.toDataURL(r.uri, { margin: 1, width: 180 }) });
  };
  if (me?.totp) return <div className="verdict PASS">✓ Two-factor sign-in is on for your account.</div>;
  return (
    <div className="stack">
      {!step ? <button className="btn primary" onClick={start}>Turn on two-factor sign-in</button> : <>
        <div className="row" style={{ alignItems: "flex-start" }}><img src={step.qr} alt="Two-factor QR code" width="180" height="180" style={{ background: "#fff", borderRadius: 8 }} />
          <div className="stack" style={{ flex: 1, minWidth: 200 }}><div className="note">Scan with an authenticator app, or type this key:</div><code className="mono" style={{ wordBreak: "break-all" }}>{step.secret}</code>
            <label className="f">6-digit code<input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></label>
            <button className="btn primary" disabled={code.length < 6} onClick={() => act.mutate({ path: "/auth/totp/verify", body: { code }, done: "Two-factor is on. You'll need a code at sign-in." })}>Verify and turn on</button></div></div>
        <Msg msg={msg} /></>}
    </div>
  );
}

function Users({ me, user }) {
  const q = useQuery({ queryKey: ["users"], queryFn: () => api("/users") });
  const [act, msg] = useAction();
  const [f, setF] = useState({ username: "", password: "", role: "viewer" });
  return (
    <div className="grid side-r">
      <Card title="Users">
        <div className="table-wrap"><table>
          <thead><tr><th>User</th><th>Role</th><th>2FA</th><th>Status</th><th /></tr></thead>
          <tbody>{(q.data || []).map((u) => (
            <tr key={u.username}><td><b>{u.username}</b>{u.username === user.username ? <span className="note"> (you)</span> : ""}</td>
              <td><select className="in" value={u.role} disabled={u.username === user.username} onChange={(e) => act.mutate({ path: `/users/${u.username}`, method: "PATCH", body: { role: e.target.value }, done: "Role changed; their sessions were signed out." })}>
                {ROLES.map((r) => <option key={r}>{r}</option>)}</select></td>
              <td className={u.totp ? "gain" : "muted"}>{u.totp ? "on" : "off"}</td>
              <td className={u.active ? "gain" : "loss"}>{u.active ? "active" : "disabled"}</td>
              <td>{u.username !== user.username && <button className="btn small" onClick={() => act.mutate({ path: `/users/${u.username}`, method: "PATCH", body: { active: !u.active } })}>{u.active ? "Disable" : "Enable"}</button>}</td></tr>))}</tbody>
        </table></div>
        <Msg msg={msg} />
      </Card>
      <div className="stack" style={{ gap: 16 }}>
        <Card title="Add a user">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); act.mutate({ path: "/users", body: f, done: `Added ${f.username}.`, then: () => setF({ username: "", password: "", role: "viewer" }) }); }}>
            <label className="f">Username<input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required /></label>
            <label className="f">Temporary password (10+ characters)<input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required minLength={10} /></label>
            <label className="f">Role<select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.filter((r) => r !== "owner").map((r) => <option key={r}>{r}</option>)}</select></label>
            <button className="btn primary">Add user</button>
          </form>
        </Card>
        <Card title="Your two-factor sign-in"><TwoFactor me={me} /></Card>
      </div>
    </div>
  );
}

function Roles() {
  return (
    <Card title="What each role can do">
      <div className="table-wrap"><table><thead><tr><th>Permission</th>{ROLES.map((r) => <th key={r} className="num" style={{ textAlign: "center" }}>{r}</th>)}</tr></thead>
        <tbody>{PERMS.map(([p, rs]) => <tr key={p}><td>{p}</td>{ROLES.map((r) => <td key={r} style={{ textAlign: "center" }} className={rs.includes(r) ? "gain" : "muted"}>{rs.includes(r) ? "✓" : "–"}</td>)}</tr>)}</tbody></table></div>
    </Card>
  );
}

function Audit() {
  const [module, setModule] = useState("");
  const q = useQuery({ queryKey: ["audit", module], queryFn: () => api(`/audit?limit=500${module ? `&module=${module}` : ""}`) });
  const chain = useQuery({ queryKey: ["audit-verify"], queryFn: () => api("/audit/verify") });
  const exportCsv = () => {
    const rows = [["id", "ts_utc", "user", "module", "action", "detail"], ...(q.data || []).map((r) => [r.id, r.ts, r.user ?? "", r.module, r.action, JSON.stringify(r.detail)])];
    const csv = rows.map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(",")).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "audit-log.csv"; a.click();
  };
  return (
    <Card title="Audit log" action={<div className="row">
      {chain.data && (chain.data.ok ? <span className="chip"><span className="dot on" />Chain verified · {chain.data.rows} rows</span> : <span className="chip live">Chain broken at row {chain.data.broken_at}</span>)}
      <select className="in" value={module} onChange={(e) => setModule(e.target.value)} aria-label="Module">{MODULES.map((m) => <option key={m} value={m}>{m || "All modules"}</option>)}</select>
      <button className="btn small" onClick={() => chain.refetch()}>Verify chain</button><button className="btn small" onClick={exportCsv}>Export CSV</button></div>}>
      {q.error ? <div className="error">{q.error.message}</div> : <div className="table-wrap"><table>
        <thead><tr><th>Time (IST)</th><th>User</th><th>Module</th><th>Action</th><th>Detail</th></tr></thead>
        <tbody>{(q.data || []).map((r) => (
          <tr key={r.id}><td className="mono">{new Date(r.ts).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</td>
            <td>{r.user || "system"}</td><td><span className="pill">{r.module}</span></td><td><b>{r.action}</b></td>
            <td className="mono small" style={{ whiteSpace: "normal", maxWidth: 520 }}>{JSON.stringify(r.detail)}</td></tr>))}</tbody>
      </table></div>}
    </Card>
  );
}

function Sessions() {
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => api("/sessions") });
  const [act, msg] = useAction();
  return (
    <Card title="Active sessions">
      <div className="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Last seen</th><th /></tr></thead>
        <tbody>{(q.data || []).map((s) => <tr key={s.id}><td>{s.username}{s.current ? <span className="note"> (this browser)</span> : ""}</td><td>{s.role}</td><td className="mono">{s.last_seen.replace("T", " ")}</td>
          <td>{!s.current && <button className="btn small" onClick={() => act.mutate({ path: `/sessions/${s.id}`, method: "DELETE", done: "Signed out." })}>Sign out</button>}</td></tr>)}</tbody></table></div>
      <Msg msg={msg} />
      <p className="note">Sessions live in memory: a server restart signs everyone out. Idle sessions end after 12 hours.</p>
    </Card>
  );
}

export default function UsersAudit({ me, user }) {
  const [t, setT] = useState("users");
  return (
    <>
      <Tabs tabs={[["users", "Users"], ["roles", "Roles"], ["audit", "Audit log"], ["sessions", "Sessions"]]} value={t} onChange={setT} />
      {t === "users" && <Users me={me} user={user} />}{t === "roles" && <Roles />}{t === "audit" && <Audit />}{t === "sessions" && <Sessions />}
    </>
  );
}
