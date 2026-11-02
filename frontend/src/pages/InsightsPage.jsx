import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ClaudeMark, Empty, PriorityMark } from "../components/bits.jsx";
import { api } from "../lib/api.js";
import { TIER_LABEL, clock, pct, plural } from "../lib/format.js";

function Status({ ok }) {
  return <PriorityMark tier={ok ? "low" : "high"} size={14} className="status-dot" />;
}

function HourTip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="chart-tip">
      <strong className="num">{plural(d.total, "alert")}</strong>
      <span>
        {d.label} to {d.end}
      </span>
      <span>
        {d.vital} vital, {d.device} device, {d.safety + d.care + d.report} other
      </span>
    </div>
  );
}

export default function InsightsPage({ version }) {
  const [s, setS] = useState(null);
  const [error, setError] = useState(null);
  const [table, setTable] = useState(false);

  useEffect(() => {
    api.stats().then(setS).catch((e) => setError(e.message));
  }, [version]);

  if (error) return <Empty title="Insights didn't load">{error}</Empty>;
  if (!s) return <div className="loading">Loading insights</div>;

  const maxF = Math.max(...s.funnel.map((f) => f.value), 1);
  const hours = s.hours.map((h) => ({
    ...h,
    label: clock(h.start),
    end: clock(new Date(new Date(h.start).getTime() + 3600e3).toISOString()),
  }));
  const peak = Math.max(...hours.map((h) => h.total));
  const totalOutcomes = s.outcomes.actionable + s.outcomes.false_alarm;
  const top = s.bad_actors[0];
  const learned = [...s.learned].sort((a, b) => b.learned - a.learned);
  const ag = s.claude.agreement || {};
  const maxAck = Math.max(...s.ack_times.map((a) => a.median_min), 1);
  const maxBad = Math.max(...s.bad_actors.map((b) => b.count), 1);

  return (
    <div className="page insights-page">
      <header className="page-head">
        <div>
          <h1>Alarm health</h1>
          <p className="lede">
            How noisy the last {s.window_hours} hours were, what the model has learned from your decisions, and where Claude
            disagreed. The health checks borrow the ISA-18.2 alarm-management standard used in industrial control rooms.
          </p>
        </div>
      </header>

      <div className="insights-grid">
        <section className="panel span-2">
          <h2>From noise to a short list</h2>
          <p className="muted">
            Grouping removed {pct(s.noise_reduction)} of the separate things to look at. Ranking does the rest.
          </p>
          <div className="funnel">
            {s.funnel.map((f) => (
              <div className="funnel-row" key={f.key}>
                <span className="funnel-label">{f.label}</span>
                <span className="funnel-bar" style={{ width: `${(f.value / maxF) * 100}%` }} />
                <span className="funnel-val num">{f.value}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>Health checks</h2>
          <ul className="health">
            <li>
              <Status ok={s.flood.flood_buckets === 0} />
              <span>
                <strong>
                  {s.flood.flood_buckets === 0
                    ? "No alarm floods"
                    : `${plural(s.flood.flood_buckets, "ten-minute window")} over the flood line`}
                </strong>
                <span className="muted">
                  Busiest ten minutes had {s.flood.peak} alerts. The ISA-18.2 flood line is {s.flood.threshold} per operator.
                </span>
              </span>
            </li>
            <li>
              <Status ok={s.chattering === 0} />
              <span>
                <strong>{s.chattering === 0 ? "No chattering alerts" : `${plural(s.chattering, "incident")} chattering`}</strong>
                <span className="muted">Three or more alerts inside ten minutes. Pulse groups them, but the device needs a look.</span>
              </span>
            </li>
            <li>
              <Status ok={s.stale === 0} />
              <span>
                <strong>{s.stale === 0 ? "Nothing stale" : `${plural(s.stale, "incident")} open over ${s.stale_hours} hours`}</strong>
                <span className="muted">Old alarms train people to ignore the queue. Close or fix them.</span>
              </span>
            </li>
            {top && (
              <li>
                <Status ok={top.share < 0.2} />
                <span>
                  <strong>
                    One source made {pct(top.share)} of all alerts
                  </strong>
                  <span className="muted">
                    {top.patient}, {top.alert_type.toLowerCase()}. Fixing the top few bad actors usually cuts load the most.
                  </span>
                </span>
              </li>
            )}
            <li>
              <Status ok={totalOutcomes === 0 || s.outcomes.false_alarm / totalOutcomes < 0.5} />
              <span>
                <strong>
                  {totalOutcomes ? `${pct(s.outcomes.false_alarm / totalOutcomes)} of closed alerts were false alarms` : "No closed alerts yet"}
                </strong>
                <span className="muted">
                  {s.outcomes.false_alarm} of {totalOutcomes} in 30 days. Every dismiss teaches the credibility model.
                </span>
              </span>
            </li>
          </ul>
        </section>

        <section className="panel">
          <h2>Alerts per hour</h2>
          <p className="muted">Busiest hour in the darker bar.</p>
          {!table ? (
            <div className="chart-box">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={hours} margin={{ top: 8, right: 4, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="var(--rule-soft)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "var(--ink-3)", fontSize: 11 }} stroke="var(--rule)" tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fill: "var(--ink-3)", fontSize: 11 }} stroke="var(--rule)" tickLine={false} />
                  <Tooltip content={<HourTip />} cursor={{ fill: "var(--sunken)" }} />
                  <Bar dataKey="total" barSize={22} radius={[4, 4, 0, 0]} isAnimationActive={false}>
                    {hours.map((h, i) => (
                      <Cell key={i} fill={h.total === peak ? "var(--ink)" : "var(--ink-4)"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Hour</th>
                    <th>Total</th>
                    <th>Vital</th>
                    <th>Device</th>
                    <th>Other</th>
                  </tr>
                </thead>
                <tbody>
                  {hours.map((h) => (
                    <tr key={h.label}>
                      <td>{h.label}</td>
                      <td className="num">{h.total}</td>
                      <td className="num">{h.vital}</td>
                      <td className="num">{h.device}</td>
                      <td className="num">{h.safety + h.care + h.report}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <button className="link small" onClick={() => setTable(!table)}>
            {table ? "Show chart" : "Show as table"}
          </button>
        </section>

        <section className="panel">
          <h2>Noisiest sources</h2>
          <p className="muted">Alert count by patient and alert type, last {s.window_hours} hours.</p>
          <div className="hbars">
            {s.bad_actors.map((b, i) => (
              <div className="hbar" key={i}>
                <span className="hbar-label" title={`${b.patient}, ${b.alert_type}`}>
                  {b.patient.split(" ")[0]}, {b.alert_type.toLowerCase()}
                </span>
                <span className="hbar-track">
                  <span className="hbar-fill" style={{ width: `${(b.count / maxBad) * 100}%`, display: "block" }} />
                </span>
                <span className="hbar-val num">{b.count}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>Time to acknowledge</h2>
          <p className="muted">Median minutes from first alert to someone picking it up, last 30 days.</p>
          <div className="hbars">
            {s.ack_times.map((a) => (
              <div className="hbar" key={a.tier}>
                <span className="hbar-label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <PriorityMark tier={a.tier} size={12} /> {TIER_LABEL[a.tier]}
                </span>
                <span className="hbar-track">
                  <span className="hbar-fill" style={{ width: `${(a.median_min / maxAck) * 100}%`, display: "block", background: "var(--ink-3)" }} />
                </span>
                <span className="hbar-val num">{Math.round(a.median_min)}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel span-2">
          <h2>What the model has learned</h2>
          <p className="muted">
            Each alert type starts from a prior belief about how often it needs action. Thirty days of resolve and dismiss
            decisions move it. Hollow is where it started, solid is where it is now.
          </p>
          <div className="dumbbell" role="table" aria-label="Prior and learned actionable rate per alert type">
            <div className="db-axis" role="row">
              <span role="columnheader">Alert type</span>
              <span className="db-scale" role="columnheader">
                {[0, 25, 50, 75, 100].map((v) => (
                  <span key={v} style={{ left: `${v}%` }}>
                    {v}%
                  </span>
                ))}
              </span>
              <span role="columnheader" style={{ textAlign: "right" }}>
                Learned
              </span>
            </div>
            {learned.map((r) => {
              const a = Math.min(r.prior, r.learned) * 100;
              const b = Math.max(r.prior, r.learned) * 100;
              return (
                <div className="db-row" role="row" key={r.code}>
                  <span role="cell">{r.name}</span>
                  <span className="db-track" role="cell" title={`Prior ${pct(r.prior)}, learned ${pct(r.learned)} from ${r.actionable} real and ${r.false_alarms} false`}>
                    {[25, 50, 75].map((v) => (
                      <span key={v} className="db-grid" style={{ left: `${v}%` }} />
                    ))}
                    <span className="db-line" style={{ left: `${a}%`, width: `${b - a}%` }} />
                    <span className="db-prior" style={{ left: `${r.prior * 100}%` }} />
                    <span className="db-learned" style={{ left: `${r.learned * 100}%` }} />
                  </span>
                  <span className="db-val num" role="cell">
                    {pct(r.learned)}{" "}
                    <span className="muted small">
                      ({r.actionable}/{r.actionable + r.false_alarms})
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
          <div className="legend-row">
            <span>
              <i className="lg-prior" /> Prior
            </span>
            <span>
              <i className="lg-learned" /> Learned from outcomes
            </span>
          </div>
        </section>

        <section className="panel span-2">
          <h2 style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ClaudeMark size={18} /> Claude and the model
          </h2>
          {s.claude.judged === 0 ? (
            <p className="muted">
              No Claude reviews yet. On the queue, use Ask Claude to review the top 6, or open an incident and ask there.
            </p>
          ) : (
            <>
              <p className="muted">
                Claude reviewed {plural(s.claude.judged, "incident")}.
                {s.claude.mean_gap !== null && ` On average its score sat ${s.claude.mean_gap} points from the model's.`}
              </p>
              <div className="stat-row">
                <div className="stat">
                  <span className="stat-v">{ag.agree || 0}</span>
                  <span className="stat-l">Agreed with the model</span>
                </div>
                <div className="stat">
                  <span className="stat-v">{ag.escalate || 0}</span>
                  <span className="stat-l">Escalated</span>
                </div>
                <div className="stat">
                  <span className="stat-v">{ag["de-escalate"] || 0}</span>
                  <span className="stat-l">De-escalated</span>
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
