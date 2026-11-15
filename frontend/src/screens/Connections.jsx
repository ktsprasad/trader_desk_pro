import React, { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api.js";
import { Card, Modal, Msg, useAction } from "../ui.jsx";
import { Connect, Import, Mode, PaperPrice, Rules } from "./Settings.jsx";

const CATALOG = [
  ["kite", "Zerodha Kite", "Native", "NSE, BSE, F&O, MCX, Coin · trading"],
  ["dhan", "Dhan", "Native", "NSE, BSE, F&O · read-only"],
  ["upstox", "Upstox", "Native", "NSE, BSE, F&O · read-only"],
  ["indstocks", "INDstocks", "Native", "NSE, BSE, F&O · read-only"],
  ["csv", "INDmoney US / any CSV", "Import", "Holdings CSV or contract-note export"],
  ["cas", "CAS + AMFI NAV", "Import", "All mutual funds via CAMS / KFintech"],
  ["ccxt", "Crypto (CCXT)", "Aggregator", "Binance, Coinbase, Kraken… · read-only"],
  ["email", "Email inbox (any provider)", "Import", "Gmail, Outlook, Yahoo, Zoho, iCloud, any IMAP · read-only"],
];

const CATS = ["Rent and home", "Food and groceries", "Food delivery", "Shopping", "Utilities", "Fuel and travel",
  "Subscriptions", "Health", "Education", "Investing", "Income", "Other"];
const day = (iso) => (iso ? new Date(iso + "Z").toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "—");

function InboxBox({ acct, accounts, onClose }) {
  const [act, msg] = useAction();
  const [days, setDays] = useState(30);
  const [scan, setScan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [pick, setPick] = useState({});
  const [cat, setCat] = useState({});
  const [target, setTarget] = useState({});
  const [pw, setPw] = useState("");
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api(`/accounts/${acct.id}/email/scan`, { method: "POST", body: { days: Number(days) || 30 } });
      setScan(r); setPick(Object.fromEntries(r.alerts.filter((a) => !a.imported).map((a) => [a.key, true]))); setCat({});
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const keys = Object.keys(pick).filter((k) => pick[k]);
  const targets = (kind) => accounts.filter((a) => a.plugin === kind);
  return (
    <Modal onClose={onClose} wide>
      <h3 style={{ marginBottom: 6 }}>{acct.name}: scan the inbox</h3>
      <p className="note" style={{ marginTop: 0 }}>Read-only: mail is opened without marking it read, and nothing is moved, deleted or sent. Nothing is imported until you pick it.</p>
      <div className="row"><label className="f" style={{ width: 160 }}>Last N days<input type="number" min="1" max="400" value={days} onChange={(e) => setDays(e.target.value)} /></label>
        <button className="btn primary" disabled={busy} onClick={run} style={{ alignSelf: "end" }}>{busy ? "Reading mail…" : scan ? "Scan again" : "Scan"}</button>
        <button className="btn" onClick={onClose} style={{ alignSelf: "end" }}>Close</button></div>
      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
      {scan && <div className="stack" style={{ marginTop: 12 }}>
        <div className="note">{scan.scanned} emails since {scan.since} · {scan.alerts.length} transaction alerts · {scan.attachments.length} statements</div>
        <b>Transaction alerts → Money</b>
        {scan.alerts.length === 0 ? <div className="empty">No bank, card or UPI alerts found. Add the bank's sender in the connection's sender filter, or widen the days.</div> :
          <div className="table-wrap"><table>
            <thead><tr><th /><th>Date</th><th>Merchant</th><th>Account</th><th>Category</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
            <tbody>{scan.alerts.map((a) => (
              <tr key={a.key} className={a.imported ? "muted" : ""}>
                <td><input type="checkbox" aria-label={`import ${a.merchant}`} disabled={a.imported} checked={!!pick[a.key]} onChange={(e) => setPick({ ...pick, [a.key]: e.target.checked })} /></td>
                <td>{day(a.on)}</td>
                <td title={a.subject}>{a.merchant}<div className="note">{a.from}</div></td>
                <td className="mono">{a.account_hint || "—"}</td>
                <td>{a.imported ? "imported" : <select value={cat[a.key] || a.category} onChange={(e) => setCat({ ...cat, [a.key]: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select>}</td>
                <td className={`mono ${a.direction === "credit" ? "gain" : "loss"}`} style={{ textAlign: "right" }}>{a.direction === "credit" ? "+" : "−"}₹{a.amount.toLocaleString("en-IN")}</td>
              </tr>))}</tbody></table></div>}
        {scan.alerts.length > 0 && <div><button className="btn primary" disabled={!keys.length}
          onClick={() => act.mutate({ path: `/accounts/${acct.id}/email/import`, body: { keys, categories: cat }, done: (r) => `Added ${r.added} to Money${r.skipped ? `, ${r.skipped} already there` : ""}.`, then: run })}>
          Import {keys.length} selected</button></div>}
        <b>Statements and exports → Portfolio</b>
        {scan.attachments.length === 0 ? <div className="empty">No CAS PDFs, holdings CSVs or contract notes found.</div> :
          scan.attachments.map((f) => (
            <div key={f.key} className="mini"><div className="row between"><span><b>{f.filename}</b> <span className="tag">{f.kind === "contract_note" ? "contract note" : f.kind.toUpperCase()}</span></span><span className="note">{day(f.on)} · {f.from}</span></div>
              {f.imported ? <div className="note">Imported.</div> : f.kind === "contract_note" ? <div className="note">Contract-note parsing is not built yet.</div> :
                targets(f.kind).length === 0 ? <div className="note">Add a {f.kind === "cas" ? "CAS + AMFI NAV" : "Holdings CSV"} connection first, then import this file into it.</div> :
                <div className="row" style={{ marginTop: 6 }}>
                  <select value={target[f.key] || targets(f.kind)[0].id} onChange={(e) => setTarget({ ...target, [f.key]: e.target.value })}>{targets(f.kind).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
                  {f.kind === "cas" && <input type="password" className="in" placeholder="PDF password (not stored)" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="off" />}
                  <button className="btn small primary" onClick={() => act.mutate({ path: `/accounts/${acct.id}/email/attachment`, body: { key: f.key, target: target[f.key] || targets(f.kind)[0].id, password: pw },
                    done: (r) => `Imported ${r.rows} holdings.`, then: () => setScan((sc) => ({ ...sc, attachments: sc.attachments.map((x) => x.key === f.key ? { ...x, imported: true } : x) })) })}>Import</button>
                </div>}
            </div>))}
        <Msg msg={msg} />
      </div>}
    </Modal>
  );
}

function UploadBox({ acct, onClose }) {
  const [act, msg] = useAction();
  const file = useRef(null);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const send = async () => {
    const f = file.current?.files?.[0];
    if (!f) return;
    setBusy(true); setRes(null);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("password", pw);
    try {
      const st = await api("/auth/state");
      const r = await fetch(`/api/accounts/${acct.id}/upload`, { method: "POST", body: fd, credentials: "same-origin", headers: { "X-CSRF": st.user.csrf } });
      const body = await r.json().catch(() => ({}));
      setRes(r.ok ? { ok: true, text: `Imported ${body.rows} holdings · value ₹${Math.round(body.value).toLocaleString("en-IN")}` } : { ok: false, text: body.detail || `HTTP ${r.status}` });
    } catch (e) { setRes({ ok: false, text: e.message }); } finally { setBusy(false); }
  };
  return (
    <Modal onClose={onClose}>
      <h3 style={{ marginBottom: 10 }}>Upload to {acct.name}</h3>
      <div className="stack">
        <input ref={file} type="file" accept={acct.plugin === "cas" ? ".pdf" : ".csv,text/csv"} className="in" aria-label="File" />
        {acct.plugin === "cas" && <label className="f">PDF password (used once, not stored)<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="off" /></label>}
        <div className="row"><button className="btn primary" disabled={busy} onClick={send}>{busy ? "Reading…" : "Upload"}</button>
          {acct.plugin === "cas" && <button className="btn" onClick={() => act.mutate({ path: "/mf/nav-refresh", done: (r) => `NAVs refreshed: ${r.updated} holdings updated from ${r.schemes} schemes.` })}>Refresh NAVs from AMFI</button>}
          <button className="btn" onClick={onClose}>Close</button></div>
        <Msg msg={res} /><Msg msg={msg} />
      </div>
    </Modal>
  );
}

function TokenBox({ acct, mode, onClose }) {
  const [act, msg] = useAction();
  const [tok, setTok] = useState("");
  const [pin, setPin] = useState("");
  const [totp, setTotp] = useState("");
  return (
    <Modal onClose={onClose}>
      <h3 style={{ marginBottom: 10 }}>{acct.name}: {mode === "totp" ? "log in with PIN + TOTP" : "paste today's access token"}</h3>
      {mode === "totp" ? <div className="stack">
        <label className="f">{acct.plugin === "indstocks" ? "MPIN" : "PIN"}<input type="password" inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value)} /></label>
        <label className="f">TOTP code<input inputMode="numeric" autoComplete="one-time-code" value={totp} onChange={(e) => setTotp(e.target.value)} /></label>
        <button className="btn primary" onClick={() => act.mutate({ path: `/accounts/${acct.id}/totp-login`, body: { pin, totp }, done: "Logged in. Token saved to the keychain.", then: () => { setPin(""); setTotp(""); } })}>Log in</button>
        <p className="note">The PIN and code are sent once to the broker to get a 24-hour token; neither is stored.</p>
      </div> : <div className="stack">
        <label className="f">Access token<input type="password" autoComplete="off" value={tok} onChange={(e) => setTok(e.target.value)} /></label>
        <button className="btn primary" disabled={!tok.trim()} onClick={() => act.mutate({ path: `/accounts/${acct.id}/secrets`, body: { secrets: { access_token: tok } }, done: "Token saved to the keychain.", then: () => setTok("") })}>Save token</button>
        <p className="note">{acct.plugin === "dhan" ? "Generate it on web.dhan.co → Profile → Access DhanHQ APIs (valid 24 h)." : "Generate it at indstocks.com → API trading → Access tokens (valid 24 h)."}</p>
      </div>}
      <Msg msg={msg} />
      <button className="btn" style={{ marginTop: 10 }} onClick={onClose}>Close</button>
    </Modal>
  );
}

function SecretBox({ acct, onClose }) {
  const mail = acct.plugin === "email";
  const [act, msg] = useAction();
  const [v, setV] = useState("");
  return (
    <Modal onClose={onClose}>
      <h3 style={{ marginBottom: 10 }}>Replace the {mail ? "app password" : "API secret"} for {acct.name}</h3>
      <input className="in" type="password" autoComplete="off" value={v} onChange={(e) => setV(e.target.value)} style={{ width: "100%" }} aria-label={mail ? "New app password" : "New API secret"} />
      <div className="row" style={{ marginTop: 10 }}><button className="btn primary" disabled={!v.trim()} onClick={() => act.mutate({ path: `/accounts/${acct.id}/secrets`, body: { secrets: { [mail ? "password" : "api_secret"]: v } }, done: mail ? "App password saved. Use Test connection." : "Secret replaced. Log in again.", then: () => setV("") })}>Save</button>
        <button className="btn" onClick={onClose}>Close</button></div>
      <Msg msg={msg} />
    </Modal>
  );
}

export default function Connections({ user }) {
  const owner = user.role === "owner";
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api("/accounts") });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: () => api("/plugins"), enabled: owner });
  const [pick, setPick] = useState("kite");
  const [box, setBox] = useState(null);
  const [act, msg] = useAction();
  const formRef = useRef(null);
  const login = async (id) => { const r = await api(`/accounts/${id}/login-url`); window.location.href = r.url; };
  const kindOf = (p) => (plugins.data || []).find((x) => x.id === p)?.kind;
  const start = (id) => { setPick(id); formRef.current?.scrollIntoView({ behavior: "smooth" }); };
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div><h2>Connections</h2><p className="muted" style={{ margin: "4px 0 0" }}>Every broker and data source is a plugin. Its capabilities decide which buttons appear in the app.</p></div>
      <div className="grid side-r2">
        <div className="stack" style={{ gap: 16 }}>
          <Card title={`Connected · ${(accounts.data || []).length}`}>
            <div className="stack">{(accounts.data || []).map((a) => {
              const oauth = a.plugin === "kite" || a.plugin === "upstox";
              const token = a.plugin === "dhan" || a.plugin === "indstocks";
              const upload = a.plugin === "csv" || a.plugin === "cas";
              const mail = a.plugin === "email";
              return (
                <div key={a.id} className="mini">
                  <div className="row between">
                    <div><b>{a.name}</b> <span className="note mono">{a.id} · {a.plugin}</span></div>
                    <span className={a.connected ? "gain small" : "warnc small"}>● {a.plugin === "paper" ? "Default for orders" : upload ? "Upload-based" : mail ? (a.connected ? "App password saved · read-only" : "Needs an app password") : a.connected ? `Connected${a.live ? " · live allowed" : ""}` : "Needs login"}</span>
                  </div>
                  <div className="row" style={{ marginTop: 6 }}>{a.capabilities.map((c) => <span key={c} className="tag mono">{c}</span>)}
                    {a.capabilities.length === 1 && a.capabilities[0] === "READ" && <span className="note">read-only</span>}</div>
                  {owner && a.plugin !== "paper" && <div className="row" style={{ marginTop: 8 }}>
                    {oauth && <button className="btn small primary" onClick={() => login(a.id)}>{a.connected ? "Log in again" : "Log in (daily)"}</button>}
                    {token && <><button className="btn small primary" onClick={() => setBox({ a, kind: "totp" })}>PIN + TOTP</button>
                      <button className="btn small" onClick={() => setBox({ a, kind: "token" })}>Paste token</button></>}
                    {upload && <button className="btn small primary" onClick={() => setBox({ a, kind: "upload" })}>Upload {a.plugin === "cas" ? "CAS PDF" : "CSV"}</button>}
                    {mail && <><button className="btn small primary" onClick={() => setBox({ a, kind: "inbox" })}>Scan inbox</button>
                      <button className="btn small" onClick={() => act.mutate({ path: `/accounts/${a.id}/email/test`, done: (r) => `${a.name}: signed in to ${r.host} read-only · ${r.messages} emails in the folder.` })}>Test connection</button></>}
                    {(oauth || a.plugin === "ccxt" || mail) && <button className="btn small" onClick={() => setBox({ a, kind: "secret" })}>{mail ? "Replace password" : "Replace secret"}</button>}
                    {a.capabilities.includes("TRADE_EQ") && <label className="check small"><input type="checkbox" checked={a.live} onChange={(e) => act.mutate({ path: `/accounts/${a.id}/live?enabled=${e.target.checked}` })} />live orders allowed</label>}
                    <button className="btn small outline-danger" onClick={() => window.confirm(`Disconnect ${a.name}? Its keys are removed from the keychain.`) && act.mutate({ path: `/accounts/${a.id}`, method: "DELETE", done: "Disconnected." })}>Disconnect</button>
                  </div>}
                  {a.requires_static_ip && a.capabilities.includes("TRADE_EQ") && <div className="note" style={{ marginTop: 6 }}>Live API orders need a whitelisted static IP.</div>}
                </div>);
            })}
              <div className="mini"><div className="row between"><b>Yahoo Finance</b><span className="gain small">● Delayed data</span></div>
                <div className="note">Charts, briefs, signals and prices for brokers whose API has no quotes.</div></div>
            </div>
            <Msg msg={msg} />
          </Card>
          {owner && <Card title="Add a connection">
            <div className="grid g3">{CATALOG.map(([id, n, kind, d]) => (
              <div key={id} className={`mini ${pick === id ? "sel" : ""}`}><div className="row between"><b>{n}</b><span className={`tag ${kind.toLowerCase()}`}>{kind}</span></div>
                <div className="note">{d}</div>
                <button className="btn small primary" style={{ marginTop: 8 }} disabled={!kindOf(id)} onClick={() => start(id)}>{kindOf(id) ? "Connect" : "Plugin missing"}</button></div>))}
              <div className="mini"><b>Build your own</b><div className="note">A folder in backend/traderdesk/plugins with plugin.yaml, settings.schema.json and an adapter class; it appears here on restart.</div></div>
            </div>
          </Card>}
          <Mode owner={owner} />
          <Rules owner={owner} />
        </div>
        <div className="stack" style={{ gap: 16 }} ref={formRef}>
          {owner && plugins.data && <Connect key={pick} plugins={plugins.data} initial={pick} />}
          {owner && <Import />}
          <PaperPrice />
        </div>
      </div>
      {box?.kind === "upload" && <UploadBox acct={box.a} onClose={() => setBox(null)} />}
      {(box?.kind === "token" || box?.kind === "totp") && <TokenBox acct={box.a} mode={box.kind} onClose={() => setBox(null)} />}
      {box?.kind === "secret" && <SecretBox acct={box.a} onClose={() => setBox(null)} />}
      {box?.kind === "inbox" && <InboxBox acct={box.a} accounts={accounts.data || []} onClose={() => setBox(null)} />}
    </div>
  );
}
