import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, inr } from "../api.js";

const OPEN = ["OPEN", "PENDING", "TRIGGER_PENDING"];

export function OrdersTable({ rows, compact }) {
  const qc = useQueryClient();
  const [err, setErr] = useState(null);
  const act = useMutation({
    mutationFn: ({ path, body }) => api(path, { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e) => setErr(e.message),
  });
  const modify = (r) => {
    const p = window.prompt(`New limit price for ${r.order.tradingsymbol}`, r.order.price ?? "");
    if (p) act.mutate({ path: `/orders/${r.id}/modify`, body: { price: Number(p) } });
  };
  return (
    <>
      {err && <div className="error">{err}</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Tag</th><th>Order</th><th className="num">Price</th><th>Stage</th><th>Status</th>
            {!compact && <th className="num">Max loss</th>}{!compact && <th>Mode</th>}<th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.tag}</td>
                <td>{r.order.side} {r.order.quantity} {r.order.tradingsymbol} <span className="muted">{r.order.product} {r.order.order_type}</span></td>
                <td className="num">{r.order.price ?? "MKT"}</td>
                <td>{r.stage}</td>
                <td>{r.status || "—"}{r.oco_gtt_id ? " · OCO armed" : ""}</td>
                {!compact && <td className="num">{inr(r.max_loss)}</td>}
                {!compact && <td>{r.paper ? "paper" : <span className="pill live">LIVE</span>}</td>}
                <td>
                  {r.stage === "sent" && OPEN.includes(r.status) && (
                    <span className="row">
                      <button className="btn small" onClick={() => modify(r)}>Modify</button>
                      <button className="btn small" onClick={() => act.mutate({ path: `/orders/${r.id}/cancel` })}>Cancel</button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={8} className="muted">No orders.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function OrderBook() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("all");
  const q = useQuery({ queryKey: ["orders"], queryFn: () => api("/orders"), refetchInterval: 5000 });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api("/accounts") });
  const run = useMutation({ mutationFn: ({ path, body }) => api(path, { method: "POST", body }), onSuccess: () => qc.invalidateQueries() });
  const rows = (q.data || []).filter((r) => filter === "all" || (filter === "open" ? r.stage === "sent" && OPEN.includes(r.status) : r.stage === filter));
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>Order book and blotter</h3>
        <div className="row">
          <select className="btn small" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All</option><option value="open">Open</option><option value="sent">Sent</option>
            <option value="blocked">Blocked</option><option value="failed">Failed</option>
          </select>
          <button className="btn small" onClick={() => run.mutate({ path: "/orders/sync" })}>Sync</button>
          <button className="btn small" onClick={() => window.confirm("Cancel every open order?") && run.mutate({ path: "/orders/cancel-all", body: {} })}>Cancel all</button>
          {(accounts.data || []).filter((a) => a.capabilities.includes("TRADE_EQ")).map((a) => (
            <button key={a.id} className="btn small" onClick={() => window.confirm(`Square off every intraday / F&O position on ${a.name}?`) &&
              run.mutate({ path: "/orders/flatten", body: { account_id: a.id } })}>Flatten {a.name}</button>
          ))}
        </div>
      </div>
      {q.error ? <p className="error">{q.error.message}</p> : <OrdersTable rows={rows} />}
    </div>
  );
}
