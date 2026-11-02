import { Check, CheckCheck, CircleX, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";
import { CATEGORY_LABEL, TIER_LABEL, ago, clock, dayClock, pct } from "../lib/format.js";
import { Modal, PriorityMark, TierPill } from "./bits.jsx";
import ClaudeCard from "./ClaudeCard.jsx";
import RangeBar from "./RangeBar.jsx";
import ScoreBreakdown from "./ScoreBreakdown.jsx";
import VitalsChart from "./VitalsChart.jsx";

const EVENT_LABEL = {
  created: "First alert",
  acknowledged: "Acknowledged",
  resolved: "Resolved as a real alert",
  dismissed: "Dismissed as a false alarm",
  reopened: "Reopened",
  claude: "Claude",
};

function Section({ title, children, aside }) {
  return (
    <section className="d-section">
      <header className="d-section-head">
        <h3>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

export default function IncidentDrawer({ id, onClose, onChanged, onOpen, toast, claudeStatus, version, onClaudeSetup }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [prompt, setPrompt] = useState(null);
  const [allAlerts, setAllAlerts] = useState(false);
  const panel = useRef(null);

  const load = useCallback(async () => {
    try {
      setD(await api.incident(id));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [id]);

  useEffect(() => {
    setD(null);
    setAllAlerts(false);
    load();
    panel.current?.focus();
  }, [id, load]);

  useEffect(() => {
    if (version) load();
  }, [version, load]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !prompt && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, prompt]);

  async function act(action) {
    setBusy(action);
    try {
      const r = await api.action(id, action, { actor: "You" });
      setD(r);
      onChanged();
      if (r.learning && action !== "reopen") {
        const L = r.learning;
        toast({
          tone: "success",
          title: action === "resolve" ? "Resolved. The model learned it was real." : "Dismissed. The model learned it was noise.",
          body: `${L.alert_type} alerts now look ${pct(L.type_after, 1)} likely to need action (was ${pct(L.type_before, 1)}). For ${L.patient}: ${pct(L.patient_after, 1)} (was ${pct(L.patient_before, 1)}).`,
          ttl: 9000,
        });
      } else {
        toast({ title: action === "acknowledge" ? "Acknowledged. It's yours." : "Reopened" });
      }
    } catch (e) {
      toast({ title: "That didn't work", body: e.message, tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  async function judge() {
    setBusy("judge");
    try {
      const r = await api.judge(id);
      setD(r);
      onChanged();
      const c = r.claude_full;
      toast({ tone: "claude", title: `Claude ${c.agreement === "agree" ? "agrees" : c.agreement === "escalate" ? "escalates" : "de-escalates"}`, body: c.headline, ttl: 7000 });
    } catch (e) {
      if (e.code === "not_configured") onClaudeSetup();
      else toast({ title: "Claude couldn't review this", body: e.message, tone: "error", ttl: 9000 });
      load();
    } finally {
      setBusy(null);
    }
  }

  async function showPrompt() {
    try {
      setPrompt(await api.prompt(id));
    } catch (e) {
      toast({ title: "Couldn't load the prompt", body: e.message, tone: "error" });
    }
  }

  const active = d && (d.status === "open" || d.status === "acknowledged");
  const p = d?.patient_full;
  const alerts = d ? (allAlerts ? d.alerts : d.alerts.slice(0, 6)) : [];

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Incident detail" tabIndex={-1} ref={panel}>
        {!d && !error && <div className="loading">Loading incident</div>}
        {error && (
          <div className="d-error">
            <p>{error}</p>
            <button className="btn" onClick={load}>
              Try again
            </button>
          </div>
        )}
        {d && (
          <>
            <header className={`d-head tier-${d.tier}`}>
              <div className="d-head-top">
                <TierPill tier={d.tier} />
                <span className="d-status">{d.status === "open" ? "Not yet picked up" : d.status === "acknowledged" ? `With ${d.assigned_to || "a coordinator"}` : d.status === "resolved" ? "Resolved" : "Dismissed"}</span>
                <button className="icon-btn" onClick={onClose} aria-label="Close">
                  <X size={19} />
                </button>
              </div>
              <div className="d-title">
                <div className="d-score">
                  <PriorityMark tier={d.tier} size={22} acknowledged={d.status !== "open"} />
                  <span className="num">{Math.round(d.score)}</span>
                </div>
                <div>
                  <h2>{d.title}</h2>
                  <p className="d-sub">
                    <strong>{p.name}</strong>, {p.age}. {d.value_text}
                  </p>
                </div>
              </div>
              <p className="d-scoreline">
                Model <span className="num">{Math.round(d.model_score)}</span>
                {d.claude && (
                  <>
                    <span className="sep" /> Claude <span className="num">{d.claude.score}</span>
                  </>
                )}
                <span className="sep" /> {CATEGORY_LABEL[d.alert_type.category]}
                <span className="sep" /> first alert {ago(d.first_seen)}, {d.alert_count} grouped
              </p>
            </header>

            <div className="d-body">
              {d.range && (
                <Section title="Reading">
                  <RangeBar range={d.range} tier={d.tier} large />
                </Section>
              )}

              <Section title="Claude's read">
                <ClaudeCard
                  detail={d}
                  status={claudeStatus}
                  busy={busy === "judge"}
                  onJudge={judge}
                  onShowPrompt={showPrompt}
                  onSetup={onClaudeSetup}
                />
              </Section>

              <Section title="Why it ranks here">
                <ScoreBreakdown detail={d} />
              </Section>

              {d.vitals && (
                <Section title="Trend">
                  <VitalsChart vitals={d.vitals} tier={d.tier} />
                </Section>
              )}

              <Section title="Patient">
                <dl className="facts">
                  <dt>Living</dt>
                  <dd>
                    {p.lives_alone ? "Lives alone" : "Not alone"}, {p.neighbourhood}
                  </dd>
                  <dt>Acuity</dt>
                  <dd>{p.acuity} of 5</dd>
                  <dt>Goals of care</dt>
                  <dd>{p.goals_of_care === "comfort" ? "Comfort-focused (palliative)" : "Full treatment"}</dd>
                  <dt>Conditions</dt>
                  <dd>{p.conditions.join("; ")}</dd>
                  <dt>Medications</dt>
                  <dd>{p.medications.join("; ")}</dd>
                  {p.discharge_date && (
                    <>
                      <dt>Discharged</dt>
                      <dd>{new Date(p.discharge_date + "T12:00").toLocaleDateString([], { month: "short", day: "numeric" })}</dd>
                    </>
                  )}
                  <dt>Care team</dt>
                  <dd>{p.care_team}</dd>
                  <dt>Care plan</dt>
                  <dd>{p.care_notes}</dd>
                </dl>
              </Section>

              {d.related.length > 0 && (
                <Section title="Also open for this patient">
                  <ul className="related">
                    {d.related.map((r) => (
                      <li key={r.id}>
                        <button className="related-row" onClick={() => onOpen(r.id)}>
                          <PriorityMark tier={r.tier} size={12} />
                          <span className="num">{Math.round(r.score)}</span>
                          <span>{r.title}</span>
                          <span className="muted">{r.value_text}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              <Section title={`Raw alerts (${d.alerts.length})`}>
                <ol className="raw-alerts">
                  {alerts.map((a) => (
                    <li key={a.id}>
                      <span className="num muted">{clock(a.raised_at)}</span>
                      <span className="raw-main">
                        {a.value !== null && <span className="num">{a.value}</span>}
                        {a.message && <span>{a.message}</span>}
                      </span>
                      <span className="raw-meta muted">
                        {a.duration_s !== null && `${Math.round(a.duration_s)} s`}
                        {a.signal_quality < 1 && ` q ${a.signal_quality.toFixed(2)}`}
                        {a.source && ` ${a.source}`}
                      </span>
                    </li>
                  ))}
                </ol>
                {d.alerts.length > 6 && (
                  <button className="link" onClick={() => setAllAlerts(!allAlerts)}>
                    {allAlerts ? "Show fewer" : `Show all ${d.alerts.length}`}
                  </button>
                )}
              </Section>

              <Section title="Activity">
                <ol className="timeline">
                  {d.events.map((e, i) => (
                    <li key={i} className={`ev-${e.kind}`}>
                      <span className="num muted">{dayClock(e.created_at)}</span>
                      <span>
                        <strong>{EVENT_LABEL[e.kind] || e.kind}</strong>
                        {e.actor && e.actor !== "System" ? `, ${e.actor}` : ""}
                        {e.message ? `. ${e.message}` : ""}
                      </span>
                    </li>
                  ))}
                </ol>
              </Section>
            </div>

            <footer className="d-foot">
              {active ? (
                <>
                  {d.status === "open" && (
                    <button className="btn primary" onClick={() => act("acknowledge")} disabled={!!busy}>
                      <Check size={16} /> Acknowledge
                    </button>
                  )}
                  <button className="btn" onClick={() => act("resolve")} disabled={!!busy}>
                    <CheckCheck size={16} /> Resolve: it was real
                  </button>
                  <button className="btn" onClick={() => act("dismiss")} disabled={!!busy}>
                    <CircleX size={16} /> Dismiss: false alarm
                  </button>
                  <span className="foot-hint">Resolve and dismiss teach the credibility model.</span>
                </>
              ) : (
                <>
                  <span className="foot-hint">
                    {d.status === "resolved" ? "Closed as a real alert" : "Closed as a false alarm"}
                    {d.closed_at ? ` at ${clock(d.closed_at)}` : ""}. Score at the time: {Math.round(d.score_snapshot ?? d.score)} ({TIER_LABEL[d.tier_snapshot] || "–"}).
                  </span>
                  <button className="btn" onClick={() => act("reopen")} disabled={!!busy}>
                    <RotateCcw size={16} /> Reopen
                  </button>
                </>
              )}
            </footer>
          </>
        )}
      </aside>
      {prompt && (
        <Modal title="What Claude sees" onClose={() => setPrompt(null)} wide>
          <p className="muted">
            Sent to <code>{prompt.model}</code> with a JSON schema, so the answer always has the same fields. Every number below comes from the model.
          </p>
          <h4 className="sub">System prompt</h4>
          <pre className="prompt">{prompt.system}</pre>
          <h4 className="sub">Incident</h4>
          <pre className="prompt">{prompt.user}</pre>
        </Modal>
      )}
    </>
  );
}
