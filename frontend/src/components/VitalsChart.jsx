import {
  CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from "recharts";
import { tierColor } from "../lib/format.js";

const fmtDay = (t) => new Date(t).toLocaleDateString([], { weekday: "short", day: "numeric" });

function midnights(lo, hi) {
  const out = [];
  const d = new Date(lo);
  d.setHours(24, 0, 0, 0);
  while (d.getTime() <= hi) {
    out.push(d.getTime());
    d.setDate(d.getDate() + 1);
  }
  return out;
}

function Tip({ active, payload, unit, decimals }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="chart-tip">
      <strong className="num">
        {Number(p.v).toFixed(decimals)} {unit}
      </strong>
      <span>{new Date(p.t).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</span>
      <span>{p.kind === "alert" ? "Alert reading" : p.source === "alert" ? "From an alert" : "Routine reading"}</span>
    </div>
  );
}

export default function VitalsChart({ vitals, tier }) {
  if (!vitals) return null;
  const { display, series, alert_points: alerts, baseline, param } = vitals;
  const decimals = display.decimals ?? 1;
  const data = series.map((r) => ({ t: new Date(r.taken_at).getTime(), v: r.value, source: r.source }));
  const pts = alerts.map((a) => ({ t: new Date(a.taken_at).getTime(), v: a.value, kind: "alert" }));
  const all = [...data, ...pts].map((d) => d.v);
  if (!all.length) return null;
  const lo = Math.min(...all, baseline ? baseline.median - 2.5 * baseline.sd : Infinity);
  const hi = Math.max(...all, baseline ? baseline.median + 2.5 * baseline.sd : -Infinity);
  const pad = (hi - lo) * 0.12 || 1;
  const domain = [Number((lo - pad).toFixed(decimals)), Number((hi + pad).toFixed(decimals))];
  const color = tier === "low" ? "var(--ink-2)" : tierColor(tier);
  const times = [...data, ...pts].map((d) => d.t);
  const ticks = midnights(Math.min(...times), Math.max(...times));
  const showNormal = param !== "weight" && display.normal;

  return (
    <div className="vitals-chart">
      <ResponsiveContainer width="100%" height={210}>
        <ComposedChart margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="var(--rule-soft)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            scale="time"
            ticks={ticks}
            tickFormatter={fmtDay}
            tick={{ fill: "var(--ink-3)", fontSize: 11 }}
            stroke="var(--rule)"
          />
          <YAxis
            domain={domain}
            tick={{ fill: "var(--ink-3)", fontSize: 11 }}
            stroke="var(--rule)"
            width={44}
            tickFormatter={(v) => Number(v).toFixed(decimals)}
          />
          {showNormal && (
            <ReferenceArea y1={display.normal[0]} y2={display.normal[1]} fill="var(--band)" fillOpacity={0.28} ifOverflow="hidden" />
          )}
          {baseline && (
            <ReferenceArea
              y1={baseline.median - 2 * baseline.sd}
              y2={baseline.median + 2 * baseline.sd}
              fill="var(--ink-3)"
              fillOpacity={0.1}
              ifOverflow="hidden"
            />
          )}
          {baseline && <ReferenceLine y={baseline.median} stroke="var(--ink-3)" strokeDasharray="4 4" />}
          <Tooltip content={<Tip unit={display.unit} decimals={decimals} />} cursor={{ stroke: "var(--rule)" }} />
          <Line data={data} dataKey="v" stroke="var(--ink-2)" strokeWidth={1.6} dot={{ r: 2, fill: "var(--ink-2)", strokeWidth: 0 }} isAnimationActive={false} />
          <Scatter data={pts} dataKey="v" fill={color} shape="circle" isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="chart-legend">
        <span>
          <i className="lg-line" /> Readings, last 7 days
        </span>
        <span>
          <i className="lg-dot" style={{ background: color }} /> Alert readings
        </span>
        {baseline && (
          <span>
            <i className="lg-area" /> Usual for this patient (median ± 2 SD)
          </span>
        )}
        {showNormal && (
          <span>
            <i className="lg-band" /> Normal band
          </span>
        )}
      </div>
    </div>
  );
}
