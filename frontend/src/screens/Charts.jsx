import React, { useMemo, useState } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { api, cls, num, pct } from "../api.js";
import { Card, Empty, Kpi, Seg } from "../ui.jsx";
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

const when = (t, intraday) => (t ? new Date(t * 1000).toLocaleString("en-IN", intraday
  ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }
  : { day: "numeric", month: "short", year: "2-digit", timeZone: "Asia/Kolkata" }) : "—");

function SignalPanel({ d, go }) {
  const s = d.stats || {};
  const intraday = d.style === "intraday" || d.style === "options";
  const nowCls = d.now.state === "in" ? "gain" : d.now.state === "watch" ? "warnc" : "muted";
  const t = d.now.trade;
  const losing = s.trades >= 5 && (s.total_return_pct ?? 0) < 0;
  const lagging = d.style === "longterm" && s.total_return_pct != null && d.buy_and_hold_pct != null && s.total_return_pct < d.buy_and_hold_pct;
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Card title={`What the ${d.style_name.toLowerCase()} rule says now`}>
        <div className="row between">
          <b className={nowCls} style={{ fontSize: 17 }}>{d.now.state === "in" ? "● In a trade" : d.now.state === "watch" ? "● Trend up, waiting for an entry" : "● No position"}</b>
          {t && d.now.state === "in" && <button className="btn small primary" onClick={() => go("trade", { symbol: d.symbol, side: t.side, entry: t.entry, stop: t.stop ?? "", target: t.target ?? "" })}>Open pre-filled ticket</button>}
        </div>
        <p style={{ margin: "6px 0 0" }}>{d.now.text}</p>
        {d.style === "options" && <p className="note">{d.strike_step ? `Strike step ${d.strike_step} · nearest expiry ${d.expiry} (NSE file of ${d.options_day}).` : d.note}</p>}
      </Card>
      <div className="grid g4">
        <Kpi label={`Trades back-tested · ${d.interval}`} value={s.trades ?? 0} sub={`${when(d.period.from, false)} to ${when(d.period.to, false)}`} />
        <Kpi label="Winning trades" value={s.win_pct != null ? `${s.win_pct}%` : "—"} sub={s.avg_r != null ? `average ${s.avg_r > 0 ? "+" : ""}${s.avg_r} R` : `average ${pct(s.avg_return_pct)}`} />
        <Kpi label="Total, compounded" value={pct(s.total_return_pct)} valueClass={cls(s.total_return_pct)} sub={`best ${pct(s.best_pct)} · worst ${pct(s.worst_pct)}`} />
        <Kpi label="Buy and hold, same period" value={pct(d.buy_and_hold_pct)} valueClass={cls(d.buy_and_hold_pct)} sub={d.style === "longterm" ? "the rule aims to avoid big falls, not to beat holding" : "for comparison"} />
      </div>
      {(losing || lagging) && <div className="warn">{losing ? `This rule lost money on ${d.symbol} over the period shown.` : `Holding ${d.symbol} did better than this rule over the period shown.`} Treat its signals with caution here, or pick another style.</div>}
      <Card title="Rules">
        <ul style={{ margin: 0, paddingLeft: 18 }}>{d.rules.map((r) => <li key={r}>{r}</li>)}</ul>
        <p className="note">Back-tested on {d.source?.toLowerCase().includes("kite") ? "Kite prices" : "Yahoo bars"}: past results, before charges and slippage. Not advice; nothing is sent without your typed confirm.</p>
      </Card>
      <Card title={`Recent signals · ${d.trades.length}`}>
        {d.trades.length ? <div className="table-wrap"><table>
          <thead><tr><th>Entry</th><th>Signal</th><th style={{ textAlign: "right" }}>Price</th><th>Exit</th><th style={{ textAlign: "right" }}>Price</th><th>Why it exited</th><th style={{ textAlign: "right" }}>Result</th></tr></thead>
          <tbody>{d.trades.map((x) => (
            <tr key={x.entry_time}>
              <td className="mono">{when(x.entry_time, intraday)}</td>
              <td><b className={x.side === "BUY" ? "gain" : "loss"}>{x.option || (x.side === "BUY" ? "Buy" : "Sell short")}</b>{x.strategy ? <span className="note"> · {x.strategy}</span> : null}</td>
              <td className="mono" style={{ textAlign: "right" }}>{num(x.entry)}</td>
              <td className="mono">{x.exit_time ? when(x.exit_time, intraday) : "open"}</td>
              <td className="mono" style={{ textAlign: "right" }}>{x.exit != null ? num(x.exit) : "—"}</td>
              <td>{x.exit_reason}</td>
              <td className={`mono ${cls(x.return_pct)}`} style={{ textAlign: "right" }}>{x.return_pct != null ? pct(x.return_pct) : "—"}{x.r != null ? <span className="note"> · {x.r > 0 ? "+" : ""}{x.r}R</span> : null}</td>
            </tr>))}</tbody></table></div> : <Empty>The rule did not trigger on {d.symbol} in this period.</Empty>}
      </Card>
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
    enabled: tpl === "orb" || tpl === "swing", refetchInterval: (qq) => (qq.state.data?.live ? 5000 : 60000) });
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
  const [sig, setSig] = useState(params.style || "intraday");
  const [strat, setStrat] = useState("orb");
  const sq = useQuery({ queryKey: ["chart-signals", symbol, sig, strat], enabled: sig !== "off" && (tpl === "orb" || tpl === "swing"),
    queryFn: () => api(`/market/signals?symbol=${encodeURIComponent(symbol)}&style=${sig}&strategy=${strat}`), retry: false,
    refetchInterval: (qq) => (qq.state.data?.live ? 15000 : 120000) });
  const sigOn = sig !== "off" && (tpl === "orb" || tpl === "swing");
  const view = sigOn ? sq.data : chart.data;
  const viewShow = sigOn ? { volume: show.volume, ema: sig === "swing", sma: sig === "longterm" } : show;
  const viewMarkers = sigOn ? sq.data?.markers : markers;

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
              {sigOn ? <span className="chip">{sq.data?.interval || (sig === "swing" || sig === "longterm" ? "1d" : "5m")} · set by {sig} signals</span> : <Seg options={[["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1h", "1H"], ["1d", "1D"], ["1w", "1W"]]} value={iv} onChange={setIv} />}
              <Seg options={[["candles", "Candles"], ["line", "Line"], ["heikin", "Heikin-Ashi"]]} value={type} onChange={setType} />
            </form>
            <div className="row"><button className="btn small" onClick={() => go("trade", { symbol })}>Open ticket</button></div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <span className="note">BUY / EXIT SIGNALS</span>
            <Seg options={[["off", "Off"], ["intraday", "Intraday"], ["swing", "Swing"], ["longterm", "Long-term"], ["options", "Options"]]} value={sig} onChange={setSig} />
            {(sig === "intraday" || sig === "options") && <Seg options={[["orb", "Opening-range breakout"], ["vwap", "VWAP reclaim"]]} value={strat} onChange={setStrat} />}
          </div>
        </Card>}
        <div className="row"><span className="note">TEMPLATE</span>{TEMPLATES.map(([id, l]) => <button key={id} className={`btn small ${tpl === id ? "primary" : ""}`} onClick={() => pickTpl(id)}>{l}</button>)}</div>
        {(tpl === "orb" || tpl === "swing") && <Card title={`${symbol} · ${sigOn ? `${sq.data?.style_name || ""} signals · ${sq.data?.interval || ""}` : iv} · NSE`}
          action={view && <span className="mono">{num(view.last)} {!sigOn && <span className={cls(chart.data?.change_pct)}>{pct(chart.data?.change_pct)}</span>}</span>}>
          {(sigOn ? sq.error : chart.error) ? <Empty>{(sigOn ? sq.error : chart.error).message}</Empty>
            : sigOn && sq.isLoading ? <Empty>Back-testing the {sig} rules on {symbol}…</Empty>
            : <PriceChart data={view} show={viewShow} markers={viewMarkers} type={type} height={440} visibleBars={sig === "longterm" ? 750 : sig === "swing" ? 250 : 150} />}
          {sigOn ? <p className="note">Blue ▲ buy · orange ▼ sell (short) · green ● exit in profit · red ● exit at a loss. {sig === "longterm" ? "Lines: 50-DMA (blue) and 200-DMA (purple)." : sig === "swing" ? "Lines: EMA 20 / 50." : ""} {view?.source}.</p>
            : <p className="note">BUY / SELL tags come from strategies switched on in Algo signals. Nothing is sent without your confirm.</p>}
        </Card>}
        {sigOn && sq.data && <SignalPanel d={sq.data} go={go} />}
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
