import { tierColor } from "../lib/format.js";

/*
  Moving analog indicator, after ISA-101 high-performance HMI practice: a grey
  track, the normal band shaded, the patient's own baseline as a tick, and a
  pointer that only takes on colour when the value is abnormal.
*/
export default function RangeBar({ range, tier = "low", large = false }) {
  if (!range || range.value === undefined || range.value === null) return null;
  const { min, max, normal = [], value, baseline, baseline_sd, unit, decimals = 0, note } = range;
  const pos = (v) => `${Math.max(0, Math.min(1, (v - min) / (max - min))) * 100}%`;
  const fmt = (v) => Number(v).toFixed(decimals);
  const color = tier === "low" ? "var(--ink-2)" : tierColor(tier);
  const clipped = value < min || value > max;
  return (
    <div className={`rangebar ${large ? "large" : ""}`} aria-label={`Value ${fmt(value)} ${unit}, normal ${fmt(normal[0])} to ${fmt(normal[1])}`} role="img">
      <div className="rb-track">
        {normal.length === 2 && (
          <div className="rb-normal" style={{ left: pos(normal[0]), width: `calc(${pos(normal[1])} - ${pos(normal[0])})` }} />
        )}
        {large && baseline !== undefined && baseline_sd && (
          <div
            className="rb-sd"
            style={{ left: pos(baseline - 2 * baseline_sd), width: `calc(${pos(baseline + 2 * baseline_sd)} - ${pos(baseline - 2 * baseline_sd)})` }}
          />
        )}
        {baseline !== undefined && <div className="rb-baseline" style={{ left: pos(baseline) }} />}
        <div className="rb-pointer" style={{ left: pos(value), "--c": color }}>
          <span className="rb-needle" />
          <span className="rb-head" />
        </div>
      </div>
      {large && (
        <div className="rb-scale">
          <span style={{ left: "0%" }}>{fmt(min)}</span>
          <span style={{ left: "100%" }}>{fmt(max)}</span>
        </div>
      )}
      {large && (
        <div className="rb-legend">
          <span>
            <i className="lg-pointer" style={{ background: color }} /> Now {fmt(value)} {unit}
            {clipped ? " (off scale)" : ""}
          </span>
          {normal.length === 2 && (
            <span>
              <i className="lg-normal" /> Normal band {fmt(normal[0])}–{fmt(normal[1])}
            </span>
          )}
          {baseline !== undefined && (
            <span>
              <i className="lg-baseline" /> Usual for this patient {fmt(baseline)}
              {baseline_sd ? ` ± ${fmt(2 * baseline_sd)}` : ""}
            </span>
          )}
          {note && <span>{note}</span>}
        </div>
      )}
    </div>
  );
}
