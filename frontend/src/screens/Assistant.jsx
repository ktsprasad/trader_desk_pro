import React, { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api.js";
import { Card, Msg, useAction } from "../ui.jsx";

const TOOLS = ["portfolio", "tax", "spending", "bills", "orders", "signals", "brief", "charges", "metals"];
const SUGGEST = ["How much tax have I booked this year, and what is due next?", "Where did my money go this month?",
  "Is my ORB strategy still working?", "Summarise my portfolio allocation and the biggest risks."];

export default function Assistant({ user }) {
  const qc = useQueryClient();
  const threads = useQuery({ queryKey: ["threads"], queryFn: () => api("/assistant/threads") });
  const settings = useQuery({ queryKey: ["ai-settings"], queryFn: () => api("/assistant/settings") });
  const [tid, setTid] = useState(null);
  const msgs = useQuery({ queryKey: ["thread", tid], queryFn: () => api(`/assistant/threads/${tid}`), enabled: !!tid });
  const [text, setText] = useState("");
  const [provider, setProvider] = useState("claude");
  const [pending, setPending] = useState(null);
  const [err, setErr] = useState(null);
  const [key, setKey] = useState("");
  const [act, msg] = useAction();
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs.data, pending]);
  const s = settings.data;
  const owner = user.role === "owner";

  const ask = async (q) => {
    const t = (q ?? text).trim();
    if (!t) return;
    setErr(null); setPending(t); setText("");
    try {
      const r = await api("/assistant/ask", { method: "POST", body: { text: t, thread: tid, provider } });
      setTid(r.thread);
      qc.invalidateQueries({ queryKey: ["threads"] });
      qc.invalidateQueries({ queryKey: ["thread", r.thread] });
    } catch (e) { setErr(e.message); setText(t); } finally { setPending(null); }
  };
  const list = tid ? msgs.data || [] : [];
  return (
    <div className="grid ai-grid">
      <Card>
        <button className="btn primary block" onClick={() => { setTid(null); setErr(null); }}>+ New chat</button>
        <div className="stack" style={{ marginTop: 10, gap: 4 }}>{(threads.data || []).map((t) => (
          <button key={t.id} className={`nav ${t.id === tid ? "active" : ""}`} style={{ flexDirection: "column", alignItems: "flex-start" }} onClick={() => setTid(t.id)}>
            <b style={{ fontSize: 13 }}>{t.title}</b><span className="note">{new Date(t.at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span></button>))}
          {threads.data && !threads.data.length && <div className="note">No chats yet.</div>}</div>
      </Card>
      <div className="stack" style={{ gap: 12, minWidth: 0 }}>
        <div className="chat" style={{ flex: 1 }}>
          {!list.length && !pending && <div className="empty">Ask about your portfolio, trades, tax, spending or a plan. Answers come from your own data through read-only tools.</div>}
          {list.map((m, i) => m.role === "user" ? <div key={i} className="msg-u">{m.text}</div> : (
            <div key={i} className="msg-a">
              <div className="row" style={{ marginBottom: 8 }}><span className="tag claude">{m.model?.startsWith("ollama") ? m.model : "Claude Sonnet"}</span>{m.tools?.length ? <span className="note">{m.tools.length} tool call(s)</span> : null}</div>
              {m.text}
              {m.tools?.length > 0 && <div className="row" style={{ marginTop: 8 }}>{[...new Set(m.tools)].map((t) => <span key={t} className="tag llm">{t}</span>)}</div>}
            </div>))}
          {pending && <><div className="msg-u">{pending}</div><div className="msg-a muted">Thinking…</div></>}
          <div ref={end} />
        </div>
        {err && <div className="error">{err}</div>}
        {!list.length && <div className="row">{SUGGEST.map((q) => <button key={q} className="btn small" onClick={() => ask(q)}>{q}</button>)}</div>}
        <div className="card">
          <textarea className="in" style={{ width: "100%", minHeight: 60, border: 0, background: "transparent" }} placeholder="Ask about your portfolio, trades, tax, spending or a plan…"
            value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }} aria-label="Message" />
          <div className="row between">
            <select className="in" value={provider} onChange={(e) => setProvider(e.target.value)} aria-label="Model"><option value="claude">Claude Sonnet · cloud</option><option value="ollama">Ollama · local</option></select>
            <button className="btn primary" disabled={!!pending || !text.trim()} onClick={() => ask()}>Send</button>
          </div>
        </div>
        <p className="note">Not investment, tax or financial advice. Claude receives only the tool results it asks for; set "local for money questions" to keep spending and tax on this computer.</p>
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <Card title="Read-only tools it can call">
          <div className="row">{TOOLS.map((t) => <span key={t} className="pill">{t}</span>)}</div>
          <p className="note">It can draft plans. It cannot place orders, move money or change settings.</p>
        </Card>
        <Card title="Privacy">
          <div className="stack">
            <label className="check"><input type="checkbox" checked={!!s?.local_for_money} disabled={!owner} onChange={(e) => act.mutate({ path: "/assistant/settings", method: "PUT", body: { local_for_money: e.target.checked } })} />Use Ollama (local) for spending and tax questions</label>
            <label className="check"><input type="checkbox" checked={!!s?.log_tools} disabled={!owner} onChange={(e) => act.mutate({ path: "/assistant/settings", method: "PUT", body: { log_tools: e.target.checked } })} />Log every tool call to the audit log</label>
          </div>
        </Card>
        <Card title="Claude API key">
          <div className="note" style={{ marginBottom: 8 }}>{s?.key_set ? "● Key stored in the OS keychain." : "No key stored. The SDK also uses ANTHROPIC_API_KEY or an `ant auth login` profile if present."} Model: {s?.model}.</div>
          {owner && <div className="row"><input className="in" type="password" autoComplete="off" placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} style={{ flex: 1, minWidth: 120 }} aria-label="Anthropic API key" />
            <button className="btn small primary" disabled={!key.trim()} onClick={() => act.mutate({ path: "/assistant/settings", method: "PUT", body: { api_key: key }, done: "Key saved to the keychain.", then: () => setKey("") })}>Save</button>
            {s?.key_set && <button className="btn small" onClick={() => act.mutate({ path: "/assistant/settings", method: "PUT", body: { api_key: "" }, done: "Key removed." })}>Remove</button>}</div>}
          <Msg msg={msg} />
        </Card>
      </div>
    </div>
  );
}
