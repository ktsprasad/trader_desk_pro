import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setCsrf, signed, inr } from "./api.js";
import { Icon, Modal } from "./ui.jsx";
import Desk from "./screens/Desk.jsx";
import Charts from "./screens/Charts.jsx";
import Signals from "./screens/Signals.jsx";
import Trade from "./screens/Trade.jsx";
import OrderBook from "./screens/OrderBook.jsx";
import Options from "./screens/Options.jsx";
import Briefs from "./screens/Briefs.jsx";
import Portfolio from "./screens/Portfolio.jsx";
import Metals from "./screens/Metals.jsx";
import Tax from "./screens/Tax.jsx";
import Charges from "./screens/Charges.jsx";
import Money from "./screens/Money.jsx";
import Dashboards from "./screens/Dashboards.jsx";
import Assistant from "./screens/Assistant.jsx";
import Connections from "./screens/Connections.jsx";
import Providers from "./screens/Providers.jsx";
import UsersAudit from "./screens/UsersAudit.jsx";

const OWNER_ONLY = new Set(["users", "providers"]);
const PERMS = {
  owner: null,
  trader: ["desk", "charts", "signals", "trade", "orders", "options", "briefs", "portfolio", "charges", "dash", "ai", "conn"],
  viewer: ["desk", "charts", "briefs", "portfolio", "dash", "ai", "conn"],
  accountant: ["portfolio", "tax", "charges", "dash", "ai"],
  family: ["expenses", "bills", "dash"],
};
export const NAV = [
  ["Trading", [["desk", "Desk", "desk"], ["charts", "Charts", "charts"], ["signals", "Algo signals", "signals"],
    ["trade", "Trade", "trade"], ["orders", "Order book", "book"], ["options", "Options", "options"], ["briefs", "Briefs", "briefs"]]],
  ["Wealth", [["portfolio", "Portfolio", "portfolio"], ["metals", "Digital metals", "metals"], ["tax", "Tax centre", "tax"],
    ["charges", "Charges", "charges"]]],
  ["Money", [["expenses", "Expenses", "expenses"], ["bills", "Bills", "bills"]]],
  ["Workspace", [["dash", "Dashboards", "dash"], ["ai", "AI assistant", "ai"]]],
  ["Settings", [["conn", "Connections", "conn"], ["providers", "Analysis providers", "prov"], ["users", "Users and audit", "users"]]],
];
const GROUP = Object.fromEntries(NAV.flatMap(([g, items]) => items.map(([id, label]) => [id, [g, label]])));
const SCREENS = { desk: Desk, charts: Charts, signals: Signals, trade: Trade, orders: OrderBook, options: Options,
  briefs: Briefs, portfolio: Portfolio, metals: Metals, tax: Tax, charges: Charges, expenses: Money, bills: Money,
  dash: Dashboards, ai: Assistant, conn: Connections, providers: Providers, users: UsersAudit };

function parseHash() {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [screen, q] = h.split("?");
  return { screen: screen || "desk", params: Object.fromEntries(new URLSearchParams(q || "")) };
}

export function useRoute() {
  const [r, setR] = useState(parseHash);
  useEffect(() => {
    const f = () => setR(parseHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  const go = (screen, params) => {
    const q = params ? "?" + new URLSearchParams(params).toString() : "";
    window.location.hash = `#/${screen}${q}`;
  };
  return [r, go];
}

function useTheme() {
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("tdp-theme") || "auto"; } catch { return "auto"; } });
  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("tdp-theme", theme); } catch { /* private mode */ }
  }, [theme]);
  const next = () => setTheme(theme === "auto" ? "light" : theme === "light" ? "dark" : "auto");
  return [theme, next];
}

function SignIn({ setupNeeded }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ username: "", password: "", totp: "" });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      const r = await api(setupNeeded ? "/auth/setup" : "/auth/login", { method: "POST", body: f });
      setCsrf(r.csrf);
      qc.invalidateQueries();
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  };
  return (
    <div className="signin">
      <div className="pitch">
        <div className="logo"><span className="mark"><Icon name="logo" /></span>Trader Desk Pro</div>
        <h1>Every broker, every rupee, one desk.</h1>
        <p className="muted" style={{ maxWidth: 520, fontSize: 16 }}>Kite, your portfolio history, metals and your spending, with plans, signals and an assistant that answers from your own data.</p>
        <div className="feature"><span className="ic"><Icon name="shield" /></span><div><b>Runs on your machine</b><div className="muted">Tokens stay in your OS keychain; the server listens on 127.0.0.1 only.</div></div></div>
        <div className="feature"><span className="ic"><Icon name="list" /></span><div><b>Every action is audit-logged</b><div className="muted">Sign-ins, orders, exports and AI tool calls, hash-chained.</div></div></div>
        <div className="feature"><span className="ic"><Icon name="userplus" /></span><div><b>Roles for family and your CA</b><div className="muted">Owner, Trader, Viewer, Accountant and Family.</div></div></div>
        <div className="note" style={{ marginTop: "auto", paddingTop: 30 }}>v0.2 · {window.location.host}</div>
      </div>
      <div className="form">
        <div>
          <h2 style={{ marginBottom: 16 }}>{setupNeeded ? "Create the owner account" : "Sign in"}</h2>
          <form className="stack" onSubmit={submit}>
            <label className="f">Username<input autoComplete="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required /></label>
            <label className="f">Password{setupNeeded ? " (10+ characters)" : ""}<input type="password" autoComplete={setupNeeded ? "new-password" : "current-password"} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required /></label>
            {!setupNeeded && <label className="f">Two-factor code (if on)<input inputMode="numeric" autoComplete="one-time-code" value={f.totp} onChange={(e) => setF({ ...f, totp: e.target.value })} /></label>}
            {err && <div className="error">{err}</div>}
            <button className="btn primary block" disabled={busy}>{busy ? "…" : "Continue"}</button>
            <div className="note">Sessions end after 12 hours idle, or when the server restarts.</div>
          </form>
        </div>
      </div>
    </div>
  );
}

function Search({ go, onClose, allowed }) {
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const items = useMemo(() => {
    const screens = NAV.flatMap(([, it]) => it).filter(([id]) => allowed(id))
      .filter(([, label]) => label.toLowerCase().includes(q.toLowerCase())).map(([id, label]) => ({ label: `Open ${label}`, run: () => go(id) }));
    const sym = q.trim().toUpperCase();
    const syms = /^[A-Z0-9&\-]{2,20}$/.test(sym) ? [
      { label: `Trade ${sym}`, run: () => go("trade", { symbol: sym }) },
      { label: `Chart ${sym}`, run: () => go("charts", { symbol: sym }) }] : [];
    return [...syms, ...screens].slice(0, 9);
  }, [q]);
  const pick = (it) => { it.run(); onClose(); };
  return (
    <Modal onClose={onClose}>
      <div className="search" style={{ maxWidth: "none" }}><Icon name="search" />
        <input autoFocus placeholder="Symbol or screen…" value={q} onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setI(Math.min(i + 1, items.length - 1));
            if (e.key === "ArrowUp") setI(Math.max(i - 1, 0));
            if (e.key === "Enter" && items[i]) pick(items[i]);
            if (e.key === "Escape") onClose();
          }} />
      </div>
      <div className="results" style={{ marginTop: 8 }}>
        {items.map((it, k) => <button key={it.label} className={k === i ? "on" : ""} onClick={() => pick(it)}>{it.label}</button>)}
        {!items.length && <div className="note" style={{ padding: 10 }}>Nothing matches.</div>}
      </div>
    </Modal>
  );
}

function Alerts({ onClose, go }) {
  const q = useQuery({ queryKey: ["alerts"], queryFn: () => api("/alerts") });
  return (
    <Modal onClose={onClose}>
      <div className="card-h"><h3>Alerts</h3><button className="link" onClick={() => { go("desk"); onClose(); }}>Open desk</button></div>
      <div className="list">
        {(q.data || []).map((a, k) => <div key={k}><span className={a.level === "bad" ? "loss" : a.level === "good" ? "gain" : a.level === "warn" ? "warnc" : ""}>● {a.text}</span><span className="mono muted">{a.at}</span></div>)}
        {q.data && !q.data.length && <div className="note">Nothing needs you right now.</div>}
      </div>
    </Modal>
  );
}

function TopBar({ screen, user, go, next, openSearch, openMenu, openAlerts, allowed }) {
  const qc = useQueryClient();
  const bar = useQuery({ queryKey: ["topbar"], queryFn: () => api("/topbar"), refetchInterval: 15000, enabled: allowed("desk") || user.role === "owner" });
  const pnl = useQuery({ queryKey: ["positions-pnl"], queryFn: () => api("/positions"), refetchInterval: 20000, enabled: allowed("desk") });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api("/alerts"), refetchInterval: 30000, enabled: allowed("desk") });
  const kill = useMutation({ mutationFn: () => api("/kill", { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const b = bar.data;
  const [g, label] = GROUP[screen] || ["", ""];
  const dayPnl = (pnl.data || []).reduce((s, p) => s + (p.pnl || 0), 0);
  const trading = ["Trading"].includes(g);
  return (
    <header className="topbar">
      <button className="btn icon only-mobile" aria-label="Menu" onClick={openMenu}><Icon name="menu" /></button>
      <div className="crumb"><small>{g}</small><b>{label}</b></div>
      <div className="search" onClick={openSearch} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openSearch()}>
        <Icon name="search" /><span style={{ flex: 1 }}>Search symbols, screens</span><kbd>Ctrl K</kbd>
      </div>
      <div className="chips">
        {b && (trading || screen === "desk") && <>
          <span className="chip"><span className={`dot ${b.nse_open ? "on" : "amber"}`} />NSE {b.nse_open ? "open" : "closed"} · {b.ist.slice(11, 16)} IST</span>
          <span className="chip hide-m">New York {b.ny}</span>
          {allowed("desk") && <span className="chip hide-m">Day P/L <b className={dayPnl > 0 ? "gain" : dayPnl < 0 ? "loss" : ""}>{signed(dayPnl)}</b></span>}
          <span className="chip hide-m">Risk left <b>{inr(b.risk_budget_left)}</b></span>
          <span className="chip hide-m">Trades <b>{b.trades_today} / {b.max_trades ?? "∞"}</b></span>
        </>}
        {b && <span className={`chip ${b.mode === "live" ? "live" : "paper"}`}>{b.mode.toUpperCase()}</span>}
        {b && allowed("trade") && <button className="btn danger" disabled={b.kill_switch || kill.isPending}
          onClick={() => window.confirm("Kill switch: cancel every open order and block new entries for the rest of today?") && kill.mutate()}>
          {b.kill_switch ? "Killed for today" : "Kill switch"}</button>}
        {allowed("ai") && <button className="btn ai" onClick={() => go("ai")}><Icon name="spark" size={15} /> Ask AI</button>}
        <button className="btn icon" aria-label="Switch theme" title="Theme: auto → light → dark" onClick={next}><Icon name="theme" /></button>
        {allowed("desk") && <button className="btn icon" aria-label="Alerts" onClick={openAlerts} style={{ position: "relative" }}><Icon name="bell" />
          {(alerts.data || []).some((a) => a.level === "bad" || a.level === "warn") && <span className="dot" style={{ position: "absolute", top: 4, right: 4 }} />}</button>}
        <span className="avatar" title={user.username}>{user.username.slice(0, 2).toUpperCase()}</span>
      </div>
    </header>
  );
}

export default function App() {
  const [, nextTheme] = useTheme();
  const [route, go] = useRoute();
  const [search, setSearch] = useState(false);
  const [alerts, setAlerts] = useState(false);
  const [menu, setMenu] = useState(false);
  const qc = useQueryClient();
  const [kiteNote] = useState(() => {
    const q = new URLSearchParams(window.location.search);
    const note = q.get("kite_error") ? { ok: false, text: `Kite login failed: ${q.get("kite_error")}` }
      : q.get("kite") === "connected" ? { ok: true, text: "Kite connected." } : null;
    if (note) { window.history.replaceState(null, "", "/#/conn"); }
    return note;
  });
  const state = useQuery({ queryKey: ["auth"], queryFn: () => api("/auth/state") });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api("/me"), enabled: !!state.data?.user });
  const counts = useQuery({ queryKey: ["navcounts"], enabled: !!state.data?.user, refetchInterval: 60000,
    queryFn: async () => {
      const role = state.data.user.role;
      const out = {};
      if (!PERMS[role] || PERMS[role].includes("signals")) out.signals = (await api("/signals").catch(() => [])).filter((s) => s.status === "waiting").length;
      if (!PERMS[role] || PERMS[role].includes("bills")) { const b = await api("/money/bills").catch(() => null); out.bills = b ? b.overdue.count + b.next_10_days.count : 0; }
      return out;
    } });
  useEffect(() => {
    const f = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearch(true); } };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  useEffect(() => setMenu(false), [route.screen]);

  if (state.isLoading) return <div className="empty" style={{ margin: 40 }}>Loading…</div>;
  if (state.error) return <div className="error" style={{ margin: 40 }}>Cannot reach the server: {state.error.message}</div>;
  const s = state.data;
  if (!s.user) return <SignIn setupNeeded={s.setup_needed} />;
  setCsrf(s.user.csrf);
  const role = s.user.role;
  const allowed = (id) => (PERMS[role] ? PERMS[role].includes(id) : true) && (!OWNER_ONLY.has(id) || role === "owner");
  const first = NAV.flatMap(([, it]) => it).find(([id]) => allowed(id))?.[0] || "dash";
  const screen = allowed(route.screen) && SCREENS[route.screen] ? route.screen : first;
  const Screen = SCREENS[screen];
  const logout = async () => { await api("/auth/logout", { method: "POST" }).catch(() => {}); setCsrf(null); qc.invalidateQueries(); };

  return (
    <div className="app">
      <nav className={`sidebar ${menu ? "open" : ""}`} aria-label="Main">
        <div className="logo"><span className="mark"><Icon name="logo" /></span>Trader Desk Pro</div>
        {NAV.map(([group, items]) => {
          const vis = items.filter(([id]) => allowed(id));
          if (!vis.length) return null;
          return (
            <div key={group}>
              <h4>{group}</h4>
              {vis.map(([id, label, icon]) => (
                <button key={id} className={`nav ${screen === id ? "active" : ""}`} onClick={() => go(id)} aria-current={screen === id ? "page" : undefined}>
                  <Icon name={icon} />{label}{counts.data?.[id] ? <span className="count">{counts.data[id]}</span> : null}
                </button>
              ))}
            </div>
          );
        })}
        <div className="usercard" style={{ marginTop: 16 }}>
          <span className="avatar">{s.user.username.slice(0, 2).toUpperCase()}</span>
          <div style={{ minWidth: 0, flex: 1 }}><b>{s.user.username}</b><div className="note">{role} · 2FA {me.data?.totp ? "on" : "off"}</div></div>
          <button className="link btn small" onClick={logout}>Sign out</button>
        </div>
      </nav>
      <div className="content">
        <TopBar screen={screen} user={s.user} go={go} next={nextTheme} openSearch={() => setSearch(true)} openMenu={() => setMenu(true)}
          openAlerts={() => setAlerts(true)} allowed={allowed} />
        <main>
          {kiteNote && <div className={`card ${kiteNote.ok ? "" : "badbox"}`} role="status" style={{ marginBottom: 16 }}>{kiteNote.text}</div>}
          <Screen go={go} params={route.params} user={s.user} me={me.data} screen={screen} allowed={allowed} />
        </main>
      </div>
      {search && <Search go={go} onClose={() => setSearch(false)} allowed={allowed} />}
      {alerts && <Alerts go={go} onClose={() => setAlerts(false)} />}
    </div>
  );
}
