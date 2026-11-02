import { ArrowDown, ArrowUp, Minus, RotateCcw, Save, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Empty, PriorityMark, Switch, Tex } from "../components/bits.jsx";
import { api } from "../lib/api.js";
import { CATEGORY_LABEL, TIERS, TIER_LABEL, pct } from "../lib/format.js";
import { useDebounced } from "../lib/hooks.js";

const FORMULAS = [
  ["The score", [
    "z = \\beta_0 + \\beta_S S + \\beta_C \\ln C + \\beta_V V + \\beta_U\\,U S + \\beta_W W",
    "\\text{TPI} = 100\\,\\sigma(z) = \\frac{100}{1 + e^{-z}}",
  ]],
  ["Severity", [
    "S = 1 - (1 - S_b')(1 - w_z S_z)(1 - w_t S_t)",
    "S_b' = \\tfrac{\\text{band}}{3}\\big(1 - \\tau(1 - S_z)\\big), \\quad S_z = \\sigma\\big(k(z - z_0)\\big), \\quad z = \\frac{|x - \\tilde{x}|}{1.4826\\,\\text{MAD}}",
    "S_t = 1 - e^{-\\max(0,\\ \\dot{x}/\\hat\\sigma)/s}",
  ]],
  ["Credibility", [
    "C = 1 - (1 - C_0)\\prod_j \\big(1 - \\kappa\\,\\text{rel}_j\\,C_{0,j}\\big), \\quad C_0 = r\\,q_n\\,q_s",
    "r_{\\text{type}} = \\frac{\\rho\\,n_0 + a}{n_0 + a + f}, \\quad r = \\frac{r_{\\text{type}}\\,n_p + a_p}{n_p + a_p + f_p}",
    "q_d = 1 - e^{-d/\\tau}, \\quad q_n = 1 - (1 - q_d)(1 - \\rho_{\\text{rep}})^{n-1}",
  ]],
  ["Vulnerability", [
    "V = 1 - e^{-H}",
    "H = \\theta_a \\tfrac{a - 1}{4} + \\theta_{\\text{age}} \\tfrac{\\text{age} - 65}{30} + \\theta_m \\tfrac{m}{6} + \\theta_{\\text{alone}} + \\theta_{\\text{dc}}\\, e^{-d/\\tau_{\\text{dc}}} + \\theta_{\\text{fall}} + \\theta_{\\text{cog}}",
  ]],
  ["Urgency and waiting", ["U = \\frac{T_{\\text{ref}}}{T_{\\text{ref}} + T_{\\text{harm}}}, \\qquad W = 1 - e^{-t/\\tau_{\\text{esc}}}"]],
  ["Claude blend", ["\\text{final} = \\max\\big((1 - \\lambda)\\,\\text{TPI} + \\lambda\\,\\text{Claude},\\ \\text{floor}\\big)"]],
];

function fmtVal(v, step) {
  const d = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  return Number(v).toFixed(d);
}

function Knob({ s, value, saved, onChange }) {
  const changed = Math.abs(value - saved) > 1e-9;
  const isDefault = Math.abs(value - s.default) < 1e-9;
  const id = `k-${s.key}`;
  if (s.kind === "bool") {
    return (
      <div className={`knob bool ${changed ? "changed" : ""}`}>
        <div className="knob-top">
          <label htmlFor={id}>{s.label}</label>
          <Switch id={id} checked={value >= 0.5} onChange={(on) => onChange(on ? 1 : 0)} label={s.label} />
        </div>
        <p className="knob-help">{s.help}</p>
      </div>
    );
  }
  const defPos = ((s.default - s.min) / (s.max - s.min)) * 100;
  return (
    <div className={`knob ${changed ? "changed" : ""}`}>
      <div className="knob-top">
        <label htmlFor={id}>{s.label}</label>
        <span className="num knob-val">{fmtVal(value, s.step)}</span>
      </div>
      <div className="knob-range">
        <input
          id={id}
          type="range"
          min={s.min}
          max={s.max}
          step={s.step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
        />
        <span className="knob-default" style={{ left: `calc(${defPos}% + ${8 - defPos * 0.16}px)` }} title={`Default ${s.default}`} />
      </div>
      <p className="knob-help">
        {s.help}
        {!isDefault && (
          <button className="link small" onClick={() => onChange(s.default)}>
            Default {fmtVal(s.default, s.step)}
          </button>
        )}
      </p>
    </div>
  );
}

function Movement({ row }) {
  const d = row.before_rank - row.after_rank;
  if (d === 0) return <span className="move same" aria-label="No change"><Minus size={12} /></span>;
  return d > 0 ? (
    <span className="move up" aria-label={`Up ${d}`}>
      <ArrowUp size={12} />
      {d}
    </span>
  ) : (
    <span className="move down" aria-label={`Down ${-d}`}>
      <ArrowDown size={12} />
      {-d}
    </span>
  );
}

function CatalogRow({ t, onSave }) {
  const [v, setV] = useState({ base_severity: t.base_severity, time_to_harm_min: t.time_to_harm_min, prior_actionable_rate: t.prior_actionable_rate });
  useEffect(() => setV({ base_severity: t.base_severity, time_to_harm_min: t.time_to_harm_min, prior_actionable_rate: t.prior_actionable_rate }), [t]);
  const field = (k, step, min, max) => (
    <input
      type="number"
      className="num-input"
      step={step}
      min={min}
      max={max}
      value={v[k]}
      onChange={(e) => setV({ ...v, [k]: e.target.value })}
      onBlur={() => Number(v[k]) !== t[k] && onSave(t.code, { [k]: Number(v[k]) })}
      aria-label={`${t.name} ${k.replaceAll("_", " ")}`}
      disabled={k === "base_severity" && !!t.param}
      title={k === "base_severity" && t.param ? "Vital signs are scored from their value, not a base severity" : undefined}
    />
  );
  const L = t.learned;
  return (
    <tr>
      <td>
        <strong>{t.name}</strong>
        <span className="muted block">{CATEGORY_LABEL[t.category]}</span>
      </td>
      <td>{field("base_severity", 0.05, 0, 1)}</td>
      <td>{field("time_to_harm_min", 5, 1, 2880)}</td>
      <td>{field("prior_actionable_rate", 0.01, 0.01, 0.99)}</td>
      <td className="learned-cell">
        <span className="num">{pct(L.learned)}</span>
        <span className="muted small">
          {L.actionable} real, {L.false_alarms} false
        </span>
      </td>
    </tr>
  );
}

export default function ModelPage({ toast, bump }) {
  const [cfg, setCfg] = useState(null);
  const [draft, setDraft] = useState(null);
  const [preview, setPreview] = useState(null);
  const [types, setTypes] = useState([]);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const debounced = useDebounced(draft, 280);

  async function load() {
    try {
      const c = await api.config();
      setCfg(c);
      setDraft(c.params);
      setTypes(await api.alertTypes());
    } catch (e) {
      setError(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!debounced) return;
    api.preview(debounced).then(setPreview).catch(() => {});
  }, [debounced]);

  const dirty = useMemo(
    () => cfg && draft && Object.keys(draft).some((k) => Math.abs(draft[k] - cfg.params[k]) > 1e-9),
    [cfg, draft]
  );
  const activePreset = useMemo(() => {
    if (!cfg || !draft) return null;
    return cfg.presets.find((p) => Object.keys(draft).every((k) => Math.abs(draft[k] - (p.params[k] ?? cfg.defaults[k])) < 1e-9))?.key;
  }, [cfg, draft]);

  if (error) return <Empty title="Model settings didn't load">{error}</Empty>;
  if (!cfg || !draft) return <div className="loading">Loading model settings</div>;

  async function save() {
    setSaving(true);
    try {
      const c = await api.saveConfig(draft);
      setCfg(c);
      setDraft(c.params);
      toast({ tone: "success", title: "Saved", body: "The queue now ranks with these settings." });
      bump();
    } catch (e) {
      toast({ title: "Couldn't save", body: e.message, tone: "error" });
    } finally {
      setSaving(false);
    }
  }

  async function resetAll() {
    const c = await api.resetConfig();
    setCfg(c);
    setDraft(c.params);
    toast({ title: "Back to defaults" });
    bump();
  }

  async function saveType(code, data) {
    try {
      await api.updateAlertType(code, data);
      setTypes(await api.alertTypes());
      setPreview(await api.preview(draft));
      bump();
      toast({ title: "Catalog updated", body: "Applies to the queue right away." });
    } catch (e) {
      toast({ title: "Couldn't update", body: e.message, tone: "error" });
    }
  }

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));
  const tb = preview?.tiers_before || {};
  const ta = preview?.tiers_after || {};

  return (
    <div className="page model-page">
      <header className="page-head">
        <div>
          <h1>Tune the model</h1>
          <p className="lede">
            Every number the scorer uses is on this page. Move a slider and the live queue re-ranks on the right before you save.
          </p>
        </div>
      </header>

      <div className="presets" role="group" aria-label="Presets">
        {cfg.presets.map((p) => (
          <button
            key={p.key}
            className={`preset ${activePreset === p.key ? "on" : ""}`}
            onClick={() => setDraft({ ...cfg.defaults, ...p.params })}
          >
            <strong>{p.name}</strong>
            <span>{p.help}</span>
          </button>
        ))}
      </div>

      <div className="model-grid">
        <div className="model-left">
          <section className="panel formulas">
            <h2>How a score is built</h2>
            <p className="muted">
              Five factors between 0 and 1 feed a logistic scorecard. Credibility enters as a log, so the odds scale with the
              chance the alarm is real. A safety floor sits on top, and Claude can be blended in last.
            </p>
            {FORMULAS.map(([label, lines]) => (
              <div className="formula" key={label}>
                <span className="formula-label">{label}</span>
                <div className="formula-lines">
                  {lines.map((tex, i) => (
                    <Tex key={i} tex={tex} display />
                  ))}
                </div>
              </div>
            ))}
          </section>

          {cfg.groups.map((g) => (
            <section className="panel knob-group" key={g.key}>
              <h2>{g.name}</h2>
              <p className="muted">{g.help}</p>
              <div className="knobs">
                {cfg.schema
                  .filter((s) => s.group === g.key)
                  .map((s) => (
                    <Knob key={s.key} s={s} value={draft[s.key]} saved={cfg.params[s.key]} onChange={(v) => set(s.key, v)} />
                  ))}
              </div>
            </section>
          ))}

          <section className="panel">
            <h2>Alert catalog</h2>
            <p className="muted">
              Starting beliefs per alert type. The learned rate is what 30 days of resolve and dismiss decisions have taught the model.
            </p>
            <div className="table-wrap">
              <table className="catalog">
                <thead>
                  <tr>
                    <th>Alert type</th>
                    <th>Base severity</th>
                    <th>Time to harm (min)</th>
                    <th>Prior actionable rate</th>
                    <th>Learned rate</th>
                  </tr>
                </thead>
                <tbody>
                  {types.map((t) => (
                    <CatalogRow key={t.code} t={t} onSave={saveType} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <aside className="model-right">
          <div className="panel preview">
            <div className="preview-head">
              <h2>Live re-rank</h2>
              <span className="muted small">{preview ? `${preview.moved} of ${preview.rows.length} moved` : "Working"}</span>
            </div>
            <div className="tier-delta">
              {TIERS.map((t) => (
                <div key={t} className="td">
                  <PriorityMark tier={t} size={12} />
                  <span>{TIER_LABEL[t]}</span>
                  <span className="num">
                    {tb[t] || 0}
                    {(tb[t] || 0) !== (ta[t] || 0) && <> → {ta[t] || 0}</>}
                  </span>
                </div>
              ))}
            </div>
            <ol className="preview-list">
              {(preview?.rows || []).slice(0, 14).map((r) => (
                <li key={r.id} className={`pv tier-${r.after_tier}`}>
                  <span className="pv-rank num">{r.after_rank}</span>
                  <PriorityMark tier={r.after_tier} size={12} />
                  <span className="pv-main">
                    <strong>{r.patient}</strong>
                    <span className="muted">{r.title}</span>
                  </span>
                  <span className="pv-score num">
                    {Math.abs(r.after_score - r.before_score) >= 0.5 ? (
                      <>
                        <span className="muted">{Math.round(r.before_score)}</span> {Math.round(r.after_score)}
                      </>
                    ) : (
                      Math.round(r.after_score)
                    )}
                  </span>
                  <Movement row={r} />
                </li>
              ))}
            </ol>
            <div className="preview-actions">
              <button className="btn primary" onClick={save} disabled={!dirty || saving}>
                <Save size={15} /> {saving ? "Saving" : "Save settings"}
              </button>
              <button className="btn" onClick={() => setDraft(cfg.params)} disabled={!dirty}>
                <Undo2 size={15} /> Discard
              </button>
              <button className="btn ghost" onClick={resetAll}>
                <RotateCcw size={15} /> Defaults
              </button>
            </div>
            {dirty && <p className="unsaved">Unsaved changes. The queue still uses the saved settings.</p>}
          </div>
        </aside>
      </div>
    </div>
  );
}
