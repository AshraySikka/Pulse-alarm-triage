export const TIERS = ["critical", "high", "medium", "low"];

export const TIER_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

// CSS token prefixes per tier: --crit-fill, --high-fill, --med-fill, --low-fill
const TIER_VAR = { critical: "crit", high: "high", medium: "med", low: "low" };
export const tierColor = (tier, part = "fill") => `var(--${TIER_VAR[tier] || "low"}-${part})`;

export function minutesSince(iso, now = Date.now()) {
  return Math.max(0, (now - new Date(iso).getTime()) / 60000);
}

export function duration(mins) {
  const m = Math.round(mins);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (h < 24) return r ? `${h} h ${r} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return `${d} d ${h % 24} h`;
}

export function ago(iso, now) {
  const m = minutesSince(iso, now);
  return m < 1 ? "just now" : `${duration(m)} ago`;
}

export function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function dayClock(iso) {
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const t = clock(iso);
  return sameDay ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${t}`;
}

export const pct = (x, digits = 0) => `${(x * 100).toFixed(digits)}%`;
export const fix = (x, d = 2) => (x === null || x === undefined ? "–" : Number(x).toFixed(d));
export const signed = (x, d = 2) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(d)}`;
export const round = (x) => Math.round(x);

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || `${one}s`}`;
}

export const AGREEMENT = {
  agree: "Agrees",
  escalate: "Escalates",
  "de-escalate": "De-escalates",
};

export const CATEGORY_LABEL = {
  vital: "Vital sign",
  safety: "Safety",
  care: "Care delivery",
  report: "Reported symptom",
  device: "Device",
};
