import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, cls, inr, num, pct } from "../api.js";
import { Card, Seg, TicketResult } from "../ui.jsx";
import PriceChart from "../PriceChart.jsx";

const n = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

export default function Trade({ params, go }) {
  const [symbol, setSymbol] = useState((params.symbol || "HDFCBANK").toUpperCase());
  const [input, setInput] = useState(symbol);
  const [iv, setIv] = useState("5m");
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api("/accounts") });
  const chart = useQuery({ queryKey: ["chart", symbol, iv], queryFn: () => api(`/market/chart?symbol=${encodeURIComponent(symbol)}&interval=${iv}`), refetchInterval: (qq) => (qq.state.data?.live ? 5000 : 60000), retry: false });
  const odds = useQuery({ queryKey: ["odds", symbol], queryFn: () => api(`/market/odds?symbol=${encodeURIComponent(symbol)}`), retry: false });
  const [f, setF] = useState({ side: params.side || "BUY", kind: "EQ", account: "paper", product: "MIS", order_type: "LIMIT", quantity: params.qty || 1,
    trigger_price: "", price: params.entry || "", stop: params.stop || "", target: params.target || "", exchange: params.exchange || "NSE",
    lot_size: 1, expiry: "", instrument_type: "EQ", amo: false });
  const [t, setT] = useState(null);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  useEffect(() => { setT(null); }, [symbol, JSON.stringify(f)]);   // any edit makes the built ticket stale
  useEffect(() => {
    if (chart.data?.last && !f.price && !params.entry) setF((x) => ({ ...x, price: chart.data.last.toFixed(2) }));
  }, [chart.data?.last]);

  const tradeable = (accounts.data || []).filter((a) => a.capabilities.includes("TRADE_EQ"));
  const acct = tradeable.find((a) => a.id === f.account);
  const entry = n(f.order_type === "SL-M" ? f.trigger_price : f.price) || chart.data?.last;
  const risk = entry && n(f.stop) ? Math.abs(entry - n(f.stop)) : null;
  const reward = entry && n(f.target) ? Math.abs(n(f.target) - entry) : null;
  const fo = f.kind === "FO";

  const build = async (e) => {
    e.preventDefault();
    setErr(null); setT(null);
    const order = { exchange: fo ? "NFO" : f.exchange, tradingsymbol: symbol, side: f.side, quantity: Number(f.quantity),
      order_type: f.order_type, product: f.product, price: n(f.price), trigger_price: n(f.trigger_price), stop: n(f.stop), target: n(f.target),
      instrument_type: fo ? f.instrument_type : "EQ", lot_size: Number(f.lot_size) || 1, expiry: f.expiry || null, plan_id: params.plan || null, amo: !!f.amo };
    try {
      setT(await api("/orders/ticket", { method: "POST", body: { account_id: f.account, order } }));
      if (params.signal) api(`/signals/${params.signal}/status`, { method: "POST", body: { status: "ticketed" } }).catch(() => {});
    } catch (e2) { setErr(e2.message); }
  };
  const d = chart.data;
  const o = odds.data;
  return (
    <div className="grid side-r2">
      <Card>
        <div className="row between" style={{ marginBottom: 10 }}>
          <form className="row" onSubmit={(e) => { e.preventDefault(); setSymbol(input.trim().toUpperCase()); }}>
            <input className="in" style={{ width: 200, fontWeight: 700 }} value={input} onChange={(e) => setInput(e.target.value)} aria-label="Symbol" />
            <b className="mono" style={{ fontSize: 22 }}>{d?.last != null ? num(d.last) : "—"}</b>
            <span className={`mono ${cls(d?.change_pct)}`}>{pct(d?.change_pct)}</span>
          </form>
          <Seg options={[["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1d", "1D"]]} value={iv} onChange={setIv} />
        </div>
        <div className="row" style={{ marginBottom: 10 }}>
          {n(f.target) && <span className="chip"><span className="dot on" />Target {num(n(f.target))}</span>}
          {entry && <span className="chip"><span className="dot" style={{ background: "#79b8ff" }} />Entry {num(entry)}</span>}
          {n(f.stop) && <span className="chip"><span className="dot" />Stop {num(n(f.stop))}</span>}
          {d?.opening_range && <span className="chip">Opening range {num(d.opening_range.low)}–{num(d.opening_range.high)}</span>}
          <span className="chip">{d?.source || "delayed data"}</span>
        </div>
        {chart.error ? <div className="empty">{chart.error.message}</div> :
          <PriceChart data={d} levels={{ entry, stop: f.stop, target: f.target }} />}
        <div className="grid g4" style={{ marginTop: 14 }}>
          <div className="mini"><div className="note">Setup</div><b>Opening-range breakout</b></div>
          <div className="mini"><div className="note">Back-test · {o?.sessions ?? "…"} sessions</div>
            {o?.trades ? <b className="mono"><span className="gain">{o.target_pct}%</span> tgt · <span className="loss">{o.stop_pct}%</span> stop</b> : <b className="muted">{o ? "no trades" : "…"}</b>}</div>
          <div className="mini"><div className="note">Expectancy</div><b className="mono">{o?.expectancy_r != null ? `${o.expectancy_r > 0 ? "+" : ""}${o.expectancy_r} R` : "—"}</b></div>
          <div className="mini"><div className="note">Reward : risk</div><b className="mono">{risk && reward ? `${(reward / risk).toFixed(1)} : 1` : "—"}</b></div>
        </div>
      </Card>

      <Card title="Order ticket" action={<span className="chip">via {acct?.name || "—"}</span>}>
        <form className="stack" onSubmit={build}>
          <Seg className="full" options={[["BUY", "Buy", "buy"], ["SELL", "Sell", "sell"]]} value={f.side} onChange={(v) => setF({ ...f, side: v })} />
          <Seg className="full" options={[["EQ", "Equity"], ["FO", "F&O"], ["BASKET", "Basket"]]} value={f.kind}
            onChange={(v) => v === "BASKET" ? go("options") : setF({ ...f, kind: v, product: v === "FO" ? "NRML" : "MIS", instrument_type: v === "FO" ? "CE" : "EQ" })} />
          <div className="grid g2">
            <label className="f">Broker<select value={f.account} onChange={set("account")}>{tradeable.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
            <label className="f">Product<select value={f.product} onChange={set("product")}>
              {(fo ? [["NRML", "NRML (carry)"], ["MIS", "MIS (intraday)"]] : [["MIS", "MIS (intraday)"], ["CNC", "CNC (delivery)"]]).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            {fo && <label className="f">Instrument<select value={f.instrument_type} onChange={set("instrument_type")}><option value="CE">Call (CE)</option><option value="PE">Put (PE)</option><option value="FUT">Future</option></select></label>}
            {fo && <label className="f">Lot size<input type="number" min="1" value={f.lot_size} onChange={set("lot_size")} /></label>}
            <label className="f">Order type<select value={f.order_type} onChange={set("order_type")}>
              {[["LIMIT", "Limit"], ["MARKET", "Market"], ["SL", "SL-Limit"], ["SL-M", "SL-Market"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="f">Quantity<input className="mono" type="number" min="1" value={f.quantity} onChange={set("quantity")} /></label>
            <label className="f">Trigger<input className="mono" type="number" step="any" value={f.trigger_price} onChange={set("trigger_price")} disabled={!f.order_type.startsWith("SL")} /></label>
            <label className="f">Limit price<input className="mono" type="number" step="any" value={f.price} onChange={set("price")} disabled={f.order_type === "MARKET" || f.order_type === "SL-M"} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={!!f.amo} onChange={(e) => setF({ ...f, amo: e.target.checked })} />After-market order (AMO): queue for the next open</label>
          <fieldset className="box"><legend>Exit on fill (OCO)</legend>
            <div className="grid g2">
              <label className="f">Stop-loss<input className="mono" type="number" step="any" value={f.stop} onChange={set("stop")} /></label>
              <label className="f">Target<input className="mono" type="number" step="any" value={f.target} onChange={set("target")} /></label>
            </div>
          </fieldset>
          <div className="grid g3">
            <div><div className="note">Max loss</div><b className="mono">{risk ? inr(risk * Number(f.quantity)) : "—"}</b></div>
            <div><div className="note">Margin</div><b className="mono">{t?.margin ? inr(t.margin.required) : "on check"}</b></div>
            <div><div className="note">Charges</div><b className="mono">{t?.charges ? inr(t.charges.total) : "on check"}</b></div>
          </div>
          {err && <div className="error">{err}</div>}
          {!t && <button className="btn primary block" type="submit">Check risk &amp; cost</button>}
        </form>
        {t && <div style={{ marginTop: 12 }}><TicketResult t={t} />
          <button className="btn small" style={{ marginTop: 8 }} onClick={() => setT(null)}>Edit order</button></div>}
      </Card>
    </div>
  );
}
