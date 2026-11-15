let csrf = null;

export function setCsrf(token) {
  csrf = token;
}

export class ApiError extends Error {
  constructor(status, body) {
    const d = body?.detail;
    super(Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ")
      : typeof d === "string" ? d : `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET" && csrf) headers["X-CSRF"] = csrf;
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const inr = (n, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : (n < 0 ? "−" : "") + "₹" + Math.abs(Number(n)).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const signed = (n, digits = 0) =>
  n === null || n === undefined ? "—" : (n > 0 ? "+" : n < 0 ? "−" : "") + "₹" + Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const num = (n, digits = 2) =>
  n === null || n === undefined || n === "" ? "—" : Number(n).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const cls = (n) => (n > 0 ? "gain" : n < 0 ? "loss" : "");

export const pct = (n, d = 2) => (n === null || n === undefined ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(d)}%`);

export const hm = (iso) => (iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }) : "");

export const istToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
// "YYYY-MM" of the IST month `offset` months from now. Never toISOString(): that is UTC, and the 1st of a
// month at midnight IST is still the previous month in UTC.
export const monthOf = (offset = 0) => {
  const [y, m] = istToday().split("-").map(Number);
  const t = y * 12 + (m - 1) + offset;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
};
