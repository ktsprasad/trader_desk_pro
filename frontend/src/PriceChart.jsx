import React, { useEffect, useRef } from "react";
import { createChart, CrosshairMode, LineStyle } from "lightweight-charts";

const IST = 19800; // the chart library shows UTC; shift so the axis reads IST

function css(v) {
  return getComputedStyle(document.documentElement).getPropertyValue(v).trim() || undefined;
}

/**
 * data: /api/market/chart response. levels: {entry, stop, target}. show: {vwap, volume, or, ema}. markers: [{time, side, text}]
 */
export default function PriceChart({ data, levels, show = { vwap: true, volume: true, or: true }, markers, height = 380, type = "candles" }) {
  const el = useRef(null);
  useEffect(() => {
    if (!el.current || !data?.bars?.length) return undefined;
    const text = css("--muted"), grid = css("--border"), bg = css("--bg");
    const chart = createChart(el.current, {
      height, layout: { background: { color: bg }, textColor: text, fontFamily: "JetBrains Mono, monospace" },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } }, crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: grid }, timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    const bars = data.bars.map((b) => ({ ...b, time: b.time + IST }));
    let main;
    if (type === "line") {
      main = chart.addLineSeries({ color: css("--accent"), lineWidth: 2 });
      main.setData(bars.map((b) => ({ time: b.time, value: b.close })));
    } else {
      const ha = type === "heikin";
      main = chart.addCandlestickSeries({ upColor: "#3fb950", downColor: "#f0625a", borderVisible: false,
        wickUpColor: "#3fb950", wickDownColor: "#f0625a" });
      if (ha) {
        let prev = null;
        main.setData(bars.map((b) => {
          const c = (b.open + b.high + b.low + b.close) / 4;
          const o = prev ? (prev.open + prev.close) / 2 : (b.open + b.close) / 2;
          prev = { open: o, close: c };
          return { time: b.time, open: o, close: c, high: Math.max(b.high, o, c), low: Math.min(b.low, o, c) };
        }));
      } else {
        main.setData(bars);
      }
    }
    if (show.volume && bars.some((b) => b.volume)) {
      const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(bars.map((b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? "rgba(63,185,80,.35)" : "rgba(240,98,90,.35)" })));
    }
    if (show.vwap && data.vwap?.length) {
      const v = chart.addLineSeries({ color: "#e3b341", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      v.setData(data.vwap.map((p) => ({ time: p.time + IST, value: p.value })));
    }
    if (show.ema) {
      [[20, "#79b8ff"], [50, "#b392f0"]].forEach(([n, color]) => {
        const k = 2 / (n + 1);
        let e = null;
        const pts = bars.map((b) => { e = e === null ? b.close : b.close * k + e * (1 - k); return { time: b.time, value: e }; });
        chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(pts.slice(n));
      });
    }
    if (show.or && data.opening_range) {
      [["high", "OR high"], ["low", "OR low"]].forEach(([k, t]) => main.createPriceLine({ price: data.opening_range[k],
        color: "#5b7cfa", lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: t }));
    }
    if (levels) {
      [["target", "#3fb950", "Target"], ["entry", "#79b8ff", "Entry"], ["stop", "#f0625a", "Stop"]].forEach(([k, color, t]) => {
        const p = Number(levels[k]);
        if (p) main.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: k === "entry" ? LineStyle.Solid : LineStyle.Dashed, title: t });
      });
    }
    if (markers?.length && main.setMarkers) {
      main.setMarkers(markers.map((m) => ({ time: m.time + IST, position: m.side === "BUY" ? "belowBar" : "aboveBar",
        color: m.side === "BUY" ? "#2f7cf6" : "#e3863a", shape: m.side === "BUY" ? "arrowUp" : "arrowDown", text: m.text }))
        .sort((a, b) => a.time - b.time));
    }
    const visible = Math.min(bars.length, type === "line" ? bars.length : 120);
    chart.timeScale().setVisibleLogicalRange({ from: bars.length - visible, to: bars.length + 3 });
    return () => chart.remove();
  }, [data, levels?.entry, levels?.stop, levels?.target, show.vwap, show.volume, show.or, show.ema, markers, type, height]);
  if (!data?.bars?.length) return <div className="empty" style={{ height }}>No bars for this symbol.</div>;
  return <div ref={el} className="chartbox" style={{ height }} />;
}
