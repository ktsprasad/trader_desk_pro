import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api.js";

function useAction() {
  const qc = useQueryClient();
  const [msg, setMsg] = useState(null);
  const m = useMutation({
    mutationFn: ({ path, method = "POST", body }) => api(path, { method, body }),
    onSuccess: (r, v) => { qc.invalidateQueries(); setMsg({ ok: true, text: v.done ? v.done(r) : "Saved." }); },
    onError: (e) => setMsg({ ok: false, text: e.message }),
  });
  return [m, msg];
}

function Msg({ msg }) {
  if (!msg) return null;
  return <div className={msg.ok ? "muted" : "error"} style={{ marginTop: 6 }}>{msg.text}</div>;
}

export function Connect({ plugins, initial = "kite" }) {
  const [act, msg] = useAction();
  const [pid, setPid] = useState(initial);
  const first = plugins.find((x) => x.id === initial);
  const [f, setF] = useState({ id: initial === "csv" ? "csv-import" : initial === "email" ? "mail" : initial, name: first?.name || initial });
  const [vals, setVals] = useState({});
  const p = plugins.find((x) => x.id === pid);
  const props = p?.settings_schema?.properties || {};
  const submit = (e) => {
    e.preventDefault();
    const settings = {}, secrets = {};
    Object.entries(props).forEach(([k, s]) => {
      const v = vals[k] ?? s.default;
      if (v === undefined || v === "") return;
      (s.secret ? secrets : settings)[k] = s.type === "number" || s.type === "integer" ? Number(v) : v;
    });
    act.mutate({ path: "/accounts", body: { ...f, plugin: pid, settings, secrets }, done: () => "Connected. Secrets went to the OS keychain." });
  };
  return (
    <div className="card">
      <h3>Connect a broker or source</h3>
      <form className="fields" onSubmit={submit}>
        <label className="f">Plugin<select value={pid} onChange={(e) => { const id = e.target.value; setPid(id); setVals({}); setF({ id: id === "csv" ? "csv-import" : id === "email" ? "mail" : id, name: plugins.find((x) => x.id === id)?.name || id }); }}>
          {plugins.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.kind})</option>)}</select></label>
        <label className="f">Account id<input value={f.id} onChange={(e) => setF({ ...f, id: e.target.value })} pattern="[a-z0-9_\-]{2,32}" /></label>
        <label className="f">Display name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        {Object.entries(props).map(([k, s]) => (
          <label key={k} className="f">{s.title || k}{s.secret ? " (stored in keychain)" : ""}
            {s.enum ? (
              <select value={vals[k] ?? s.default ?? ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>
                {s.enum.map((o) => <option key={o}>{o}</option>)}</select>
            ) : (
              <input type={s.secret ? "password" : s.type === "number" || s.type === "integer" ? "number" : "text"}
                value={vals[k] ?? ""} placeholder={s.default !== undefined ? String(s.default) : ""}
                onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
            )}
          </label>
        ))}
        <div style={{ alignSelf: "end" }}><button className="btn primary">Connect</button></div>
      </form>
      {p && <p className="muted">Can do: {p.capabilities.join(", ")}.{p.requires_static_ip ? " Live API orders need a static IP whitelisted with the broker (SEBI rule from 1 Apr 2026)." : ""}</p>}
      {pid === "kite" && <p className="muted">In the Kite developer console set the redirect URL to <span className="mono">http://127.0.0.1:8766/api/brokers/kite/callback</span>.</p>}
      {pid === "upstox" && <p className="muted">In the Upstox developer console set the redirect URL to <span className="mono">http://127.0.0.1:8766/api/brokers/upstox/callback</span>. Upstox is connected read-only.</p>}
      {(pid === "dhan" || pid === "indstocks") && <p className="muted">Read-only. Paste the day's access token here, or connect without it and use “PIN + TOTP” on the account each morning. The PIN is never stored.</p>}
      {pid === "ccxt" && <p className="muted">Create an API key with read permission only (no trading, no withdrawal). Needs <span className="mono">pip install ccxt</span>.</p>}
      {pid === "csv" && <p className="muted">After connecting, upload a holdings CSV from INDmoney US, INDstocks, Kite Console or any broker. Re-upload to update.</p>}
      {pid === "email" && <div className="muted"><p style={{ margin: "6px 0" }}>Works with any mailbox that offers IMAP. Use an <b>app password</b>, not your normal password; it goes to the OS keychain. The inbox is opened read-only: nothing is marked read, moved, deleted or sent.</p>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li><b>Gmail:</b> myaccount.google.com → Security → turn on 2-Step Verification → App passwords → create one named “Trader Desk”. Paste the 16 letters (spaces are fine).</li>
          <li><b>Outlook / Hotmail:</b> account.microsoft.com → Security → Advanced security options → App passwords (needs two-step verification). Work Microsoft 365 accounts need IMAP allowed by the admin.</li>
          <li><b>Yahoo / AOL:</b> Account security → Generate app password.</li>
          <li><b>Zoho:</b> Mail settings → enable IMAP, then Security → App passwords. Pick “Zoho (India)” for zoho.in accounts.</li>
          <li><b>iCloud:</b> appleid.apple.com → App-specific passwords. <b>Other:</b> enter your provider's IMAP host (port 993, SSL).</li>
        </ul>
        <p style={{ margin: "6px 0" }}>Tip: put your banks in “Only these senders” (e.g. <span className="mono">hdfcbank.net, axisbank.com, camsonline.com</span>) to scan faster.</p></div>}
      {pid === "cas" && <p className="muted">After connecting, upload your CAMS / KFintech CAS PDF (password = PAN, as set on the statement). The password is used once and never stored.</p>}
      <Msg msg={msg} />
    </div>
  );
}

export function Accounts({ owner }) {
  const q = useQuery({ queryKey: ["accounts"], queryFn: () => api("/accounts") });
  const [act, msg] = useAction();
  const [rotate, setRotate] = useState(null);
  const [secret, setSecret] = useState("");
  const login = async (id) => {
    const r = await api(`/accounts/${id}/login-url`);
    window.location.href = r.url;
  };
  return (
    <div className="card">
      <h3>Connections</h3>
      <div className="table-wrap"><table>
        <thead><tr><th>Account</th><th>Plugin</th><th>Session</th><th>Capabilities</th><th>Live orders</th><th /></tr></thead>
        <tbody>{(q.data || []).map((a) => (
          <tr key={a.id}>
            <td>{a.name} <span className="muted mono">{a.id}</span></td>
            <td>{a.plugin}</td>
            <td><span className={`dot ${a.connected ? "on" : ""}`} style={{ display: "inline-block" }} /> {a.connected ? "connected" : "needs login"}</td>
            <td className="muted" style={{ whiteSpace: "normal" }}>{a.capabilities.join(" ")}</td>
            <td>{a.plugin === "paper" ? "—" : owner
              ? <label><input type="checkbox" checked={a.live} onChange={(e) => act.mutate({ path: `/accounts/${a.id}/live?enabled=${e.target.checked}` })} /> allowed</label>
              : (a.live ? "allowed" : "off")}</td>
            <td><span className="row">
              {a.needs_login && owner && <button className="btn small primary" onClick={() => login(a.id)}>Log in (daily)</button>}
              {a.plugin !== "paper" && owner && <button className="btn small" onClick={() => setRotate(a.id)}>Replace secret</button>}
            </span></td>
          </tr>
        ))}</tbody>
      </table></div>
      {rotate && (
        <form className="row" style={{ marginTop: 8 }} onSubmit={(e) => {
          e.preventDefault();
          act.mutate({ path: `/accounts/${rotate}/secrets`, body: { secrets: { api_secret: secret } },
            done: () => { setRotate(null); setSecret(""); return "Secret replaced in the keychain. Log in again."; } });
        }}>
          <span className="muted">New API secret for <b>{rotate}</b></span>
          <input className="btn" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
          <button className="btn primary" disabled={!secret.trim()}>Save</button>
          <button type="button" className="btn" onClick={() => { setRotate(null); setSecret(""); }}>Cancel</button>
        </form>
      )}
      <Msg msg={msg} />
    </div>
  );
}

export function Mode({ owner }) {
  const q = useQuery({ queryKey: ["mode"], queryFn: () => api("/mode") });
  const bar = useQuery({ queryKey: ["topbar"], queryFn: () => api("/topbar") });
  const [act, msg] = useAction();
  const mode = q.data?.mode;
  return (
    <div className="card">
      <h3>Paper / live</h3>
      <p>Now: <b>{mode?.toUpperCase()}</b>. In paper mode every account routes to a simulator with the same API.
        Live mode sends real orders only for accounts with live orders allowed.</p>
      {owner && <div className="row">
        <button className="btn" disabled={mode === "paper"} onClick={() => act.mutate({ path: "/mode", body: { mode: "paper" } })}>Switch to paper</button>
        <button className="btn danger" disabled={mode === "live"} onClick={() => window.confirm("Go LIVE? Orders on live-allowed accounts will use real money.") && act.mutate({ path: "/mode", body: { mode: "live" } })}>Go live</button>
        {bar.data?.kill_switch && <button className="btn" onClick={() => act.mutate({ path: "/kill", method: "DELETE" })}>Release kill switch</button>}
      </div>}
      <Msg msg={msg} />
    </div>
  );
}

const RULE_HELP = {
  capital: "INR the position-size % is measured against", max_loss_per_trade: "INR at the stop",
  max_daily_loss: "INR across today's entries", no_entries_before: "IST, HH:MM", no_entries_after: "IST, HH:MM",
  no_new_mis_after: "IST, HH:MM", expiry_day_no_new_fo_after: "IST, HH:MM", blocked: "comma-separated symbols",
};

export function Rules({ owner }) {
  const q = useQuery({ queryKey: ["rules"], queryFn: () => api("/rules") });
  const [act, msg] = useAction();
  const [draft, setDraft] = useState({});
  const [note, setNote] = useState("");
  useEffect(() => { if (q.data) setDraft(q.data.rules); }, [q.data]);
  if (!q.data) return null;
  const save = (e) => {
    e.preventDefault();
    const changes = {};
    Object.entries(draft).forEach(([k, v]) => {
      const old = q.data.rules[k];
      let nv = v;
      if (typeof old === "number") nv = Number(v);
      if (typeof old === "boolean") nv = v === true || v === "true";
      if (Array.isArray(old)) nv = Array.isArray(v) ? v : String(v).split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      if (JSON.stringify(nv) !== JSON.stringify(old)) changes[k] = nv;
    });
    if (!Object.keys(changes).length) return;
    act.mutate({ path: "/rules", method: "PUT", body: { changes, note }, done: (r) => `Saved as version ${r.version}.` });
  };
  return (
    <div className="card">
      <h3>Risk rules (version {q.data.version})</h3>
      <form className="fields" onSubmit={save}>
        {Object.entries(draft).map(([k, v]) => (
          <label key={k} className="f" title={RULE_HELP[k] || ""}>{k.replaceAll("_", " ")}
            {typeof q.data.rules[k] === "boolean" ? (
              <select disabled={!owner} value={String(v)} onChange={(e) => setDraft({ ...draft, [k]: e.target.value === "true" })}>
                <option value="true">on</option><option value="false">off</option></select>
            ) : (
              <input disabled={!owner} value={Array.isArray(v) ? v.join(", ") : v ?? ""} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
            )}
          </label>
        ))}
        {owner && <label className="f">Why (logged)<input value={note} onChange={(e) => setNote(e.target.value)} /></label>}
        {owner && <div style={{ alignSelf: "end" }}><button className="btn primary">Save new version</button></div>}
      </form>
      <Msg msg={msg} />
    </div>
  );
}

export function Import() {
  const [act, msg] = useAction();
  const [folder, setFolder] = useState("D:\\code\\kite\\kite\\portfolio_data");
  return (
    <div className="card">
      <h3>Import portfolio history</h3>
      <p className="muted">Loads the dated portfolio_*.json snapshots once. Running it again skips what is already in.</p>
      <div className="row">
        <input className="btn" style={{ flex: 1, minWidth: 220 }} value={folder} onChange={(e) => setFolder(e.target.value)} />
        <button className="btn primary" onClick={() => act.mutate({ path: "/portfolio/import", body: { folder },
          done: (r) => `Imported ${r.imported}, already there ${r.skipped}, failed ${r.failed}.` })}>Import</button>
      </div>
      <Msg msg={msg} />
    </div>
  );
}

export function PaperPrice() {
  const [act, msg] = useAction();
  const [f, setF] = useState({ key: "NSE:RELIANCE", price: "" });
  return (
    <div className="card">
      <h3>Paper simulator price</h3>
      <p className="muted">Set a last price for an instrument on the paper account. Resting orders and GTTs fill against it.</p>
      <div className="row">
        <input className="btn" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} />
        <input className="btn" type="number" step="any" placeholder="price" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} />
        <button className="btn" onClick={() => act.mutate({ path: "/paper/price", body: { account_id: "paper", key: f.key, price: Number(f.price) },
          done: () => `${f.key.toUpperCase()} = ${f.price}` })}>Set</button>
      </div>
      <Msg msg={msg} />
    </div>
  );
}

export default function Settings({ user }) {
  const owner = user.role === "owner";
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: () => api("/plugins") });
  return (
    <div className="grid">
      <Accounts owner={owner} />
      {owner && plugins.data && <Connect plugins={plugins.data} />}
      <Mode owner={owner} />
      <Rules owner={owner} />
      {owner && <Import />}
      <PaperPrice />
    </div>
  );
}
