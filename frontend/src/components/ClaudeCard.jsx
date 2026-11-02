import { FileText, RefreshCw } from "lucide-react";
import { AGREEMENT, TIER_LABEL, ago } from "../lib/format.js";
import { ClaudeMark, PriorityMark } from "./bits.jsx";

export default function ClaudeCard({ detail, status, busy, onJudge, onShowPrompt, onSetup }) {
  const j = detail.claude_full;
  const configured = status?.configured;
  const enabled = status?.enabled !== false;
  const stale = detail.claude?.stale;

  const promptLink = (
    <button className="link" onClick={onShowPrompt}>
      <FileText size={14} aria-hidden="true" /> See exactly what Claude {j ? "saw" : "would be sent"}
    </button>
  );

  if (busy) {
    return (
      <div className="claude-card thinking" aria-live="polite">
        <div className="claude-head">
          <ClaudeMark size={16} />
          <span>Claude is reading this incident, the patient's history and the model's numbers.</span>
        </div>
        <div className="skeleton" />
        <div className="skeleton short" />
      </div>
    );
  }

  if (!j) {
    return (
      <div className="claude-card empty-state">
        <div className="claude-head">
          <ClaudeMark size={16} />
          <span>Claude hasn't reviewed this incident.</span>
        </div>
        {!configured ? (
          <p className="muted">
            Add your Claude API key to <code>backend/.env</code> and restart the server to turn on second opinions. The model's
            ranking works without it.
          </p>
        ) : !enabled ? (
          <p className="muted">Claude is turned off on the Model page.</p>
        ) : (
          <p className="muted">It reads the alert, the patient's care plan and the model's evidence, then agrees, escalates or de-escalates and names the next step.</p>
        )}
        {detail.claude_error && <p className="error-text">Last attempt: {detail.claude_error}</p>}
        <div className="claude-foot">
          {configured && enabled ? (
            <button className="btn claude" onClick={onJudge}>
              <ClaudeMark size={14} /> Ask Claude
            </button>
          ) : !configured ? (
            <button className="btn" onClick={onSetup}>
              How to connect Claude
            </button>
          ) : null}
          {promptLink}
        </div>
      </div>
    );
  }

  return (
    <div className={`claude-card agreement-${j.agreement}`}>
      <div className="claude-verdict">
        <span className={`claude-chip big ${j.agreement !== "agree" ? "moved" : ""}`}>
          <ClaudeMark size={14} />
          {AGREEMENT[j.agreement]}
        </span>
        <span className="claude-prio">
          <PriorityMark tier={j.priority} size={12} /> {TIER_LABEL[j.priority]}, <span className="num">{j.score}</span>
        </span>
        <span className="muted">{j.confidence} confidence</span>
        {j.likely_false_alarm && <span className="flag quiet">Likely false alarm</span>}
      </div>
      <p className="claude-headline">{j.headline}</p>
      <p className="claude-rationale">{j.rationale}</p>
      <div className="next-step">
        <span className="next-label">Next step</span>
        <p>{j.recommended_action}</p>
        <div className="next-meta">
          <span>
            <span className="muted">Who:</span> {j.notify}
          </span>
          <span>
            <span className="muted">Within:</span> <span className="num">{j.respond_within_minutes}</span> min
          </span>
        </div>
      </div>
      {j.missing_info?.length > 0 && (
        <div className="missing">
          <span className="muted">Would change the call</span>
          <ul>
            {j.missing_info.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      {stale && <p className="stale-note">New alerts arrived after this review. Ask again for an updated read.</p>}
      <div className="claude-foot">
        <span className="muted small">
          {detail.claude_model} {detail.claude_judged_at ? ago(detail.claude_judged_at) : ""}
        </span>
        <button className="btn ghost small" onClick={onJudge} disabled={!configured || !enabled}>
          <RefreshCw size={13} /> Ask again
        </button>
        {promptLink}
      </div>
    </div>
  );
}
