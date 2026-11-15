import React, { useMemo, useState } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { api, cls, num, pct } from "../api.js";
import { Card, Empty, Seg } from "../ui.jsx";
import PriceChart from "../PriceChart.jsx";

const TEMPLATES = [
  ["orb", "Intraday ORB", "5m candles, opening range, VWAP, volume, signals"],
  ["swing", "Swing trend", "Daily candles with EMA 20 / 50"],
  ["index", "Index dashboard", "Nifty, Bank Nifty, India VIX, USD/INR"],
  ["oi", "Options OI", "Strike-wise call and put open interest"],
  ["rs", "Relative strength", "Your holdings vs Nifty 50, rebased to 100"],
  ["portfolio", "Portfolio", "Net worth and invested capital over time"],
  ["metals", "Metals", "Gold and silver futures (USD) vs time"],
];

function Line({ series, height = 300 }) {
  // series: [{name, color, points:[{t, v}]}] - simple SVG lines, rebased upstream
  const all = series.flatMap((s) => s.points);
  if (!all.length) return <Empty>No data.</Empty>;
  const xs = all.map((p) => p.t), ys = all.map((p) => p.v);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const W = 1000, H = height;
  const px = (t) => ((t - x0) / (x1 - x0 || 1)) * W, py = (v) => H - 14 - ((v - y0) / (y1 - y0 || 1)) * (H - 28);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={height} preserveAspectRatio="none" role="img" aria-label="line chart">
        {series.map((s) => <polyline key={s.name} fill="none" stroke={s.color} strokeWidth="2" vectorEffect="non-scaling-stroke"
          points={s.points.map((p) => `${px(p.t)},${py(p.v)}`).join(" ")} />)}
      </svg>
      <div className="legend">{series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name} {s.points.length ? num(s.points[s.points.length - 1].v, 1) : ""}</span>)}</div>
    </div>
  );
}

export default function Charts({ params, go }) {
  const [tpl, setTpl] = useState("orb");
  const [symbol, setSymbol] = useState((params.symbol || "HDFCBANK").toUpperCase());
  const [input, setInput] = useState(symbol);
  const [iv, setIv] = useState("5m");
  const [type, setType] = useState("candles");
  const [show, setShow] = useState({ signals: true, vwap: true, volume: true, or: true, ema: false });
  const chart = useQuery({ queryKey: ["chart", symbol, iv], queryFn: () => api(`/market/chart?symbol=${encodeURIComponent(symbol)}&interval=${iv}`), retry: false,
    enabled: tpl === "orb" || tpl === "swing" });
  const sigs = useQuery({ queryKey: ["signals"], queryFn: () => api("/signals") });
  const markers = useMemo(() => {
    if (!show.signals || !chart.data?.bars?.length) return [];
    return (sigs.data || []).filter((s) => s.symbol === symbol).map((s) => {
      const [h, m] = s.at.split(":").map(Number);
      const last = chart.data.bars[chart.data.bars.length - 1].time;
      const d = new Date((last + 19800) * 1000); d.setUTCHours(h, m - 5, 0, 0);
      return { time: Math.floor(d.getTime() / 1000) - 19800, side: s.side, text: s.strategy };
    });
  }, [sigs.data, chart.data, show.signals, symbol]);

  const pickTpl = (id) => {
    setTpl(id);
    if (id === "orb") { setIv("5m"); setShow({ ...show, vwap: true, or: true, ema: false }); }
    if (id === "swing") { setIv("1d"); setShow({ ...show, vwap: false, or: false, ema: true }); }
  };
  const idx = useQueries({ queries: (tpl === "index" ? ["NIFTY", "BANKNIFTY", "INDIAVIX", "USDINR"] : tpl === "metals" ? ["GOLD", "SILVER"] : [])
    .map((s) => ({ queryKey: ["chart", s, "1d"], queryFn: () => api(`/market/chart?symbol=${s}&interval=1d`), retry: false })) });
  const port = useQuery({ queryKey: ["portfolio-ov"], queryFn: () => api("/portfolio/overview"), enabled: tpl === "rs" || tpl === "portfolio" });
  const rsSyms = (port.data?.holdings || []).filter((h) => h.asset_class === "equity").slice(0, 5).map((h) => h.symbol);
  const rs = useQueries({ queries: (tpl === "rs" ? ["NIFTY", ...rsSyms] : []).map((s) => ({ queryKey: ["chart", s, "1d"], queryFn: () => api(`/market/chart?symbol=${s}&interval=1d`), retry: false })) });
  const COL = ["#e6e9ee", "#2f7cf6", "#3fb950", "#e3b341", "#b392f0", "#f0625a"];
  const rebased = (d, name, color, n = 180) => {
    const b = (d?.bars || []).slice(-n);
    return { name, color, points: b.length ? b.map((x) => ({ t: x.time, v: (x.close / b[0].close) * 100 })) : [] };
  };

  return (
    <div className="grid side-r">
      <div className="stack" style={{ gap: 12 }}>
        {(tpl === "orb" || tpl === "swing") && <Card>
          <div className="row between">
            <form className="row" onSubmit={(e) => { e.preventDefault(); setSymbol(input.trim().toUpperCase()); }}>
              <input className="in" style={{ width: 180, fontWeight: 700 }} value={input} onChange={(e) => setInput(e.target.value)} aria-label="Symbol" />
              <Seg options={[["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1h", "1H"], ["1d", "1D"], ["1w", "1W"]]} value={iv} onChange={setIv} />
              <Seg options={[["candles", "Candles"], ["line", "Line"], ["heikin", "Heikin-Ashi"]]} value={type} onChange={setType} />
            </form>
            <div className="row"><button className="btn small" onClick={() => go("trade", { symbol })}>Open ticket</button></div>
          </div>
        </Card>}
        <div className="row"><span className="note">TEMPLATE</span>{TEMPLATES.map(([id, l]) => <button key={id} className={`btn small ${tpl === id ? "primary" : ""}`} onClick={() => pickTpl(id)}>{l}</button>)}</div>
        {(tpl === "orb" || tpl === "swing") && <Card title={`${symbol} · ${iv} · NSE`} action={chart.data && <span className="mono">{num(chart.data.last)} <span className={cls(chart.data.change_pct)}>{pct(chart.data.change_pct)}</span></span>}>
          {chart.error ? <Empty>{chart.error.message}</Empty> : <PriceChart data={chart.data} show={show} markers={markers} type={type} height={440} />}
          <p className="note">BUY / SELL tags come from strategies switched on in Algo signals. Open a pre-filled ticket from there; nothing is sent without your confirm. Data is delayed.</p>
        </Card>}
        {tpl === "index" && <div className="grid g2">{idx.map((r, i) => {
          const d = r.data; const name = ["NIFTY 50", "BANK NIFTY", "INDIA VIX", "USD/INR"][i];
          return <Card key={name} title={name} action={d && <span className="mono">{num(d.last)} <span className={cls(d.change_pct)}>{pct(d.change_pct)}</span></span>}>
            {r.error ? <Empty>{r.error.message}</Empty> : <PriceChart data={d} show={{}} type="line" height={220} />}</Card>;
        })}</div>}
        {tpl === "metals" && <div className="grid g2">{idx.map((r, i) => <Card key={i} title={["Gold futures (USD/oz)", "Silver futures (USD/oz)"][i]}>
          {r.error ? <Empty>{r.error.message}</Empty> : <PriceChart data={r.data} show={{}} type="line" height={260} />}</Card>)}
          <p className="note">Your digital metal holdings and rates are in Wealth → Digital metals.</p></div>}
        {tpl === "rs" && <Card title="Relative strength · rebased to 100 · 180 sessions">
          {rsSyms.length ? <Line series={rs.map((r, i) => rebased(r.data, i ? rsSyms[i - 1] : "NIFTY 50", COL[i]))} /> : <Empty>No equity holdings to compare yet.</Empty>}</Card>}
        {tpl === "portfolio" && <Card title="Portfolio value and invested">
          {(port.data?.history || []).length ? <Line series={[
            { name: "Value", color: "#2f7cf6", points: port.data.history.map((h) => ({ t: Date.parse(h.t), v: h.value })) },
            { name: "Invested", color: "#8b95a5", points: port.data.history.map((h) => ({ t: Date.parse(h.t), v: h.invested })) }]} />
            : <Empty>Import your snapshots in Settings → Connections to see history.</Empty>}</Card>}
        {tpl === "oi" && <Card title="Options OI"><Empty>Strike-wise open interest needs a broker quote feed (Kite Connect plan). The Options screen shows it when Kite is logged in on that plan.</Empty></Card>}
      </div>
      <div className="stack" style={{ gap: 12 }}>
        <Card title="Preloaded templates">
          <div className="stack">{TEMPLATES.map(([id, l, desc]) => (
            <button key={id} className={`mini ${tpl === id ? "sel" : ""}`} style={{ textAlign: "left", cursor: "pointer" }} onClick={() => pickTpl(id)}>
              <b>{l}</b><div className="note">{desc}</div></button>))}</div>
        </Card>
        <Card title="Indicators">
          <div className="stack">
            {[["signals", "Algo BUY / SELL signals"], ["vwap", "VWAP"], ["volume", "Volume"], ["or", "Opening range band"], ["ema", "EMA 20 / 50"]].map(([k, l]) => (
              <label key={k} className="check"><input type="checkbox" checked={!!show[k]} onChange={(e) => setShow({ ...show, [k]: e.target.checked })} />{l}</label>))}
          </div>
        </Card>
      </div>
    </div>
  );
}
