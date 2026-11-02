import katex from "katex";
import { X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { TIER_LABEL, tierColor } from "../lib/format.js";

/* The Pulse mark: rows of grey noise, then the one signal that matters. */
export function Logo({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo-mark">
      <rect width="32" height="32" rx="7" fill="var(--ink)" />
      <g fill="var(--ink-4)">
        {[6, 11, 16, 21, 26].map((x) => (
          <circle key={x} cx={x} cy="8" r="2" />
        ))}
        {[11, 16, 21].map((x) => (
          <circle key={x} cx={x} cy="15" r="2" />
        ))}
      </g>
      <circle cx="16" cy="23" r="3" fill="var(--crit-fill)" />
    </svg>
  );
}

/*
  Priority mark. Shape and colour both encode the tier (ISA-18.2 practice), so
  it reads without colour vision. Filled = not yet acknowledged, outline = someone has it.
*/
export function PriorityMark({ tier, size = 14, acknowledged = false, className = "" }) {
  const filled = !acknowledged && tier !== "low";
  const common = {
    fill: filled ? tierColor(tier) : "none",
    stroke: tierColor(tier, "edge"),
    strokeWidth: filled ? 1 : 1.8,
    strokeLinejoin: "round",
  };
  let shape;
  if (tier === "critical") shape = <rect x="2.2" y="2.2" width="11.6" height="11.6" rx="1.4" {...common} />;
  else if (tier === "high") shape = <path d="M8 1.9 L14.4 13.6 H1.6 Z" {...common} />;
  else if (tier === "medium") shape = <path d="M8 1.6 L14.4 8 L8 14.4 L1.6 8 Z" {...common} />;
  else shape = <circle cx="8" cy="8" r="4.6" {...common} />;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      role="img"
      aria-label={`${TIER_LABEL[tier]} priority${acknowledged ? ", acknowledged" : ""}`}
      className={`pmark ${className}`}
    >
      {shape}
    </svg>
  );
}

export function TierPill({ tier, children }) {
  return (
    <span className={`tier-pill tier-${tier}`}>
      <PriorityMark tier={tier} size={11} />
      {children || TIER_LABEL[tier]}
    </span>
  );
}

/* Claude's mark: a small four-point spark in violet, used only for the second opinion. */
export function ClaudeMark({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" className="claude-mark">
      <path
        d="M8 1.2c.5 3.3 1.5 4.3 4.8 4.8-3.3.5-4.3 1.5-4.8 4.8-.5-3.3-1.5-4.3-4.8-4.8 3.3-.5 4.3-1.5 4.8-4.8Z"
        fill="var(--claude)"
        transform="translate(0 1.5)"
      />
    </svg>
  );
}

export function Switch({ checked, onChange, label, id, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`switch ${checked ? "on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-knob" />
    </button>
  );
}

export function Tex({ tex, display = false, className = "" }) {
  const html = useMemo(
    () => katex.renderToString(tex, { throwOnError: false, displayMode: display, output: "html" }),
    [tex, display]
  );
  return <span className={`math ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Modal({ title, onClose, children, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function Toasts({ toasts, onDismiss }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast tone-${t.tone}`}>
          {t.tone === "claude" && <ClaudeMark size={16} />}
          <div className="toast-text">
            <strong>{t.title}</strong>
            {t.body && <span>{t.body}</span>}
          </div>
          <button className="icon-btn small" onClick={() => onDismiss(t.id)} aria-label="Dismiss">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function Meter({ value, tone = "ink", label }) {
  const v = Math.max(0, Math.min(1, value || 0));
  return (
    <span className={`meter tone-${tone}`} role="img" aria-label={`${label || "Value"} ${Math.round(v * 100)} of 100`}>
      <span className="meter-fill" style={{ width: `${v * 100}%` }} />
    </span>
  );
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
