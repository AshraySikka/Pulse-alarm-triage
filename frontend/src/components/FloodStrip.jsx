import { clock } from "../lib/format.js";

/*
  Every raw alert of the shift as one small block, in ten-minute columns.
  Grey blocks are noise or closed; colour appears only on alerts that belong
  to an open incident ranked medium or above. The dashed line is the ISA-18.2
  alarm-flood rate: more than 10 alarms in 10 minutes for one operator.
*/
const ORDER = ["closed", "low", "medium", "high", "critical"];
const CAP = 13;

export default function FloodStrip({ timeline }) {
  if (!timeline) return null;
  const { buckets, flood_threshold: flood, bucket_minutes: bm } = timeline;
  const unit = 7; // px per block including gap
  return (
    <figure className="flood" aria-label="Alerts per ten minutes over the shift">
      <div className="flood-plot" style={{ height: CAP * unit + 2 }}>
        <div className="flood-line" style={{ bottom: flood * unit }}>
          <span>Flood line: {flood} in {bm} min</span>
        </div>
        {buckets.map((b, i) => {
          const blocks = [];
          ORDER.forEach((t) => {
            for (let k = 0; k < b.tiers[t]; k++) blocks.push(t);
          });
          const shown = blocks.slice(0, CAP);
          const extra = blocks.length - shown.length;
          const parts = ORDER.filter((t) => b.tiers[t])
            .reverse()
            .map((t) => `${b.tiers[t]} ${t === "closed" ? "closed" : t}`)
            .join(", ");
          return (
            <div
              key={i}
              className="flood-col"
              title={`${clock(b.start)}: ${b.count} alert${b.count === 1 ? "" : "s"}${parts ? ` (${parts})` : ""}`}
            >
              {shown.map((t, k) => (
                <span key={k} className={`fb fb-${t}`} />
              ))}
              {extra > 0 && <span className="fb-more">+{extra}</span>}
            </div>
          );
        })}
      </div>
      <div className="flood-axis">
        {buckets.map((b, i) =>
          i % 6 === 0 ? (
            <span key={i} style={{ left: `${(i / buckets.length) * 100}%` }}>
              {clock(b.start)}
            </span>
          ) : null
        )}
        <span className="flood-now" style={{ left: "100%" }}>
          now
        </span>
      </div>
    </figure>
  );
}
