import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { duration, fix, pct, signed } from "../lib/format.js";
import { Meter, Tex } from "./bits.jsx";

/* Plain-language line for each factor, built from the model's own numbers. */
function factorLines(ex, p) {
  const sev = ex.severity_detail;
  const cred = ex.credibility_detail;
  const vul = ex.vulnerability_detail;
  let s;
  if (sev.kind === "vital") {
    const bits = [`Band ${sev.band} of 3 (${sev.band_rule}).`];
    if (sev.baseline) bits.push(`${fix(sev.z, 1)} SD from this patient's usual ${fix(sev.baseline.median, 1)}.`);
    else bits.push("No personal baseline yet.");
    if (sev.trend_sd_per_day > 0.05) bits.push(`Worsening ${fix(sev.trend_sd_per_day, 1)} SD a day.`);
    s = bits.join(" ");
  } else {
    s = sev.notes?.length ? sev.notes.join(". ") + "." : "Standard severity for this alert type.";
  }
  const typeN = cred.type_actionable + cred.type_false;
  const c = [
    `${pct(cred.r_type)} of these alerts needed action lately (${cred.type_actionable} of ${typeN} in 30 days).`,
  ];
  const pn = cred.patient_actionable + cred.patient_false;
  if (pn) c.push(`For this patient: ${cred.patient_actionable} of ${pn}.`);
  if (cred.duration_s !== null && cred.duration_s !== undefined) c.push(`Lasted ${Math.round(cred.duration_s)} s.`);
  if (cred.signal_quality < 1) c.push(`Signal quality ${fix(cred.signal_quality, 2)}.`);
  if (ex.corroborators?.length) c.push(`Backed up by ${ex.corroborators.length} other alert${ex.corroborators.length === 1 ? "" : "s"}.`);
  return {
    severity: s,
    credibility: c.join(" "),
    vulnerability: vul.terms.length ? vul.terms.map((t) => t.label).join(", ") + "." : "No added risk factors.",
    urgency: `A real event of this type causes harm in about ${duration(ex.urgency_detail.time_to_harm_min)}.`,
    waiting: ex.waiting_detail.minutes < 1 ? "Just arrived." : `Unacknowledged for ${duration(ex.waiting_detail.minutes)}.`,
  };
}

function Waterfall({ ex }) {
  const parts = ex.contributions;
  let cum = 0;
  const steps = parts.map((c) => {
    const from = cum;
    cum += c.value;
    return { ...c, from, to: cum };
  });
  const z = cum;
  const lo = Math.min(0, ...steps.map((s) => Math.min(s.from, s.to))) - 0.3;
  const hi = Math.max(0, ...steps.map((s) => Math.max(s.from, s.to))) + 0.3;
  const x = (v) => ((v - lo) / (hi - lo)) * 100;
  return (
    <div className="waterfall" role="table" aria-label="How each factor moved the score">
      {steps.map((s) => {
        const left = x(Math.min(s.from, s.to));
        const width = Math.max(0.6, Math.abs(x(s.to) - x(s.from)));
        return (
          <div className="wf-row" role="row" key={s.key}>
            <span className="wf-label" role="cell">
              {s.label}
            </span>
            <span className="wf-track" role="cell">
              <span className="wf-zero" style={{ left: `${x(0)}%` }} />
              <span className={`wf-bar ${s.value >= 0 ? "pos" : "neg"} ${s.key === "baseline" ? "base" : ""}`} style={{ left: `${left}%`, width: `${width}%` }} />
            </span>
            <span className="wf-val num" role="cell">
              {signed(s.value)}
            </span>
          </div>
        );
      })}
      <div className="wf-row total" role="row">
        <span className="wf-label" role="cell">
          Total log-odds z
        </span>
        <span className="wf-track" role="cell">
          <span className="wf-zero" style={{ left: `${x(0)}%` }} />
          <span className="wf-bar total" style={{ left: `${x(Math.min(0, z))}%`, width: `${Math.abs(x(z) - x(0))}%` }} />
        </span>
        <span className="wf-val num" role="cell">
          {signed(z)}
        </span>
      </div>
    </div>
  );
}

function TheMath({ ex, p }) {
  const sev = ex.severity_detail;
  const cred = ex.credibility_detail;
  const vul = ex.vulnerability_detail;
  const f = ex.factors;
  const b = sev.baseline;
  const blocks = [];

  if (sev.kind === "vital") {
    blocks.push([
      "Severity, vital sign",
      [
        `S_b = \\tfrac{\\text{band}}{3} = \\tfrac{${sev.band}}{3} = ${fix(sev.s_band)}`,
        b
          ? `z = \\frac{|x - \\tilde{x}|}{\\hat\\sigma} = \\frac{|${fix(sev.value, 1)} - ${fix(b.median, 1)}|}{${fix(b.sd, 2)}} = ${fix(sev.z)}`
          : `z \\text{ needs 5 baseline readings; none yet}`,
        `S_z = \\sigma\\big(k(z - z_0)\\big) = \\sigma\\big(${fix(p.deviation_steepness, 1)}(${fix(sev.z || 0)} - ${fix(p.deviation_midpoint, 1)})\\big) = ${fix(sev.s_dev)}`,
        `S_b' = S_b\\,\\big(1 - \\tau(1 - S_z)\\big) = ${fix(sev.s_band)}\\,\\big(1 - ${fix(p.personal_baseline_trust)}(1 - ${fix(sev.s_dev)})\\big) = ${fix(sev.s_band_personal)}`,
        `S_t = 1 - e^{-\\max(0,\\,\\dot{x}/\\hat\\sigma)/s} = ${fix(sev.s_trend)}`,
        `S = 1 - (1 - S_b')(1 - w_z S_z)(1 - w_t S_t)`,
        `\\phantom{S} = 1 - (1 - ${fix(sev.s_band_personal)})(1 - ${fix(p.deviation_weight)}\\cdot${fix(sev.s_dev)})(1 - ${fix(p.trend_weight)}\\cdot${fix(sev.s_trend)}) = \\mathbf{${fix(f.severity)}}`,
      ],
    ]);
  } else {
    blocks.push([
      "Severity, event",
      [`S = \\max\\big(S_{\\text{base}} + \\text{modifiers},\\ \\text{red flags}\\big) = \\mathbf{${fix(f.severity)}}`],
    ]);
  }

  const qd = cred.duration_s === null || cred.duration_s === undefined
    ? `q_d = 1 \\text{ (single measurement)}`
    : `q_d = 1 - e^{-d/\\tau} = 1 - e^{-${Math.round(cred.duration_s)}/${Math.round(p.artifact_time_constant)}} = ${fix(cred.q_duration)}`;
  blocks.push([
    "Credibility",
    [
      `r_{\\text{type}} = \\frac{\\rho\\,n_0 + a}{n_0 + a + f} = \\frac{${fix(cred.prior_rate)}\\cdot${fix(p.prior_strength, 0)} + ${cred.type_actionable}}{${fix(p.prior_strength, 0)} + ${cred.type_actionable} + ${cred.type_false}} = ${fix(cred.r_type)}`,
      `r = \\frac{r_{\\text{type}}\\,n_p + a_p}{n_p + a_p + f_p} = \\frac{${fix(cred.r_type)}\\cdot${fix(p.patient_prior_strength, 1)} + ${cred.patient_actionable}}{${fix(p.patient_prior_strength, 1)} + ${cred.patient_actionable} + ${cred.patient_false}} = ${fix(cred.r_patient)}`,
      qd,
      `q_n = 1 - (1 - q_d)(1 - \\rho_{\\text{rep}})^{n-1} = ${fix(cred.q_repeat)} \\quad (n = ${cred.repeats})`,
      `C_0 = r \\cdot q_n \\cdot q_s = ${fix(cred.r_patient)}\\cdot${fix(cred.q_repeat)}\\cdot${fix(cred.signal_quality)} = ${fix(cred.credibility_base)}`,
      `C = 1 - (1 - C_0)\\prod_j\\big(1 - \\kappa\\,\\text{rel}_j\\,C_{0,j}\\big) = \\mathbf{${fix(f.credibility)}}`,
    ],
  ]);

  blocks.push([
    "Vulnerability",
    [`V = 1 - e^{-H},\\quad H = \\textstyle\\sum_i \\theta_i x_i = ${fix(vul.hazard)} \\;\\Rightarrow\\; V = \\mathbf{${fix(f.vulnerability)}}`],
  ]);
  blocks.push([
    "Urgency and waiting",
    [
      `U = \\frac{T_{\\text{ref}}}{T_{\\text{ref}} + T_{\\text{harm}}} = \\frac{${Math.round(ex.urgency_detail.reference_time_min)}}{${Math.round(ex.urgency_detail.reference_time_min)} + ${Math.round(ex.urgency_detail.time_to_harm_min)}} = \\mathbf{${fix(f.urgency)}}`,
      `W = 1 - e^{-t/\\tau} = 1 - e^{-${Math.round(ex.waiting_detail.minutes)}/${Math.round(ex.waiting_detail.tau)}} = \\mathbf{${fix(f.waiting)}}`,
    ],
  ]);
  blocks.push([
    "Score",
    [
      `z = \\beta_0 + \\beta_S S + \\beta_C \\ln C + \\beta_V V + \\beta_U U S + \\beta_W W = ${fix(ex.z)}`,
      `\\text{TPI} = 100\\,\\sigma(z) = \\frac{100}{1 + e^{-z}} = \\mathbf{${fix(ex.model_raw, 1)}}`,
    ],
  ]);

  return (
    <div className="the-math">
      {blocks.map(([title, lines]) => (
        <div className="math-block" key={title}>
          <h4>{title}</h4>
          {lines.map((t, i) => (
            <Tex key={i} tex={t} display />
          ))}
        </div>
      ))}
    </div>
  );
}

export default function ScoreBreakdown({ detail }) {
  const ex = detail.explanation;
  const p = detail.params;
  const [math, setMath] = useState(false);
  const lines = factorLines(ex, p);
  const factors = [
    ["severity", "Severity"],
    ["credibility", "Credibility"],
    ["vulnerability", "Vulnerability"],
    ["urgency", "Urgency"],
    ["waiting", "Waiting"],
  ];
  return (
    <div className="breakdown">
      <div className="factors">
        {factors.map(([k, label]) => (
          <div className="factor" key={k}>
            <div className="factor-top">
              <span className="factor-name">{label}</span>
              <Meter value={ex.factors[k]} label={label} />
              <span className="num factor-val">{fix(ex.factors[k])}</span>
            </div>
            <p className="factor-why">{lines[k]}</p>
          </div>
        ))}
      </div>

      <h4 className="sub">How each factor moved the score</h4>
      <Waterfall ex={ex} />
      <p className="score-line">
        Model score <span className="num">{fix(ex.model_raw, 1)}</span>
        {ex.floor_applied && (
          <>
            , raised to <span className="num">{fix(ex.model_score, 0)}</span> by the safety floor
          </>
        )}
        {ex.final_notes.length > 0 && (
          <>
            . Final <span className="num">{fix(ex.score, 1)}</span>: {ex.final_notes.join("; ").toLowerCase()}
          </>
        )}
        .
      </p>
      {ex.corroborators.length > 0 && (
        <div className="corro">
          <h4 className="sub">Backed up by</h4>
          <ul>
            {ex.corroborators.map((c) => (
              <li key={c.incident_id}>
                <span className="corro-title">{c.title}</span>
                <span className="muted corro-val" title={c.value_text}>
                  {c.value_text}
                </span>
                <span className="num muted corro-lift">+{fix(c.lift)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <button className="disclosure" onClick={() => setMath(!math)} aria-expanded={math}>
        {math ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        Show the math with this incident's numbers
      </button>
      {math && <TheMath ex={ex} p={p} />}
    </div>
  );
}
