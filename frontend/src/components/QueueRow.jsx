import { BatteryLow, ClipboardList, Footprints, MessageSquareText, ShieldCheck, Waypoints } from "lucide-react";
import { AGREEMENT, CATEGORY_LABEL, TIER_LABEL, duration, minutesSince } from "../lib/format.js";
import { ClaudeMark, PriorityMark } from "./bits.jsx";
import RangeBar from "./RangeBar.jsx";

const CATEGORY_ICON = {
  safety: Footprints,
  care: ClipboardList,
  report: MessageSquareText,
  device: BatteryLow,
};

export function ClaudeChip({ claude, compact = false }) {
  if (!claude) return compact ? null : <span className="claude-none">Not reviewed</span>;
  const moved = claude.agreement !== "agree";
  return (
    <span className={`claude-chip ${moved ? "moved" : ""}`} title={claude.headline || ""}>
      <ClaudeMark size={13} />
      <span>
        {AGREEMENT[claude.agreement]}
        {moved ? ` to ${TIER_LABEL[claude.priority].toLowerCase()}` : ""}
      </span>
      {claude.stale && <span className="claude-stale" title="New alerts arrived after Claude's review">new alerts</span>}
    </span>
  );
}

function Signal({ row }) {
  if (row.range) return <RangeBar range={row.range} tier={row.tier} />;
  const Icon = CATEGORY_ICON[row.alert_type.category] || MessageSquareText;
  return (
    <span className="q-cat">
      <Icon size={15} aria-hidden="true" />
      {CATEGORY_LABEL[row.alert_type.category]}
    </span>
  );
}

export default function QueueRow({ row, selected, onOpen, onAck, now, isNew, compact = false }) {
  const acked = row.status === "acknowledged";
  const p = row.patient;
  const meta = [
    `${p.age}`,
    `acuity ${p.acuity}`,
    p.lives_alone ? "lives alone" : null,
    p.goals_of_care === "comfort" ? "palliative" : null,
  ]
    .filter(Boolean)
    .join(", ");
  const open = () => onOpen(row.id);
  return (
    <div
      className={`qrow tier-${row.tier} ${acked ? "acked" : ""} ${selected ? "selected" : ""} ${isNew ? "is-new" : ""} ${compact ? "compact" : ""}`}
      data-flip-key={row.id}
      role="button"
      tabIndex={0}
      aria-label={`${TIER_LABEL[row.tier]}, score ${Math.round(row.score)}. ${p.name}. ${row.title}, ${row.value_text}`}
      onClick={open}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open())}
    >
      <div className="q-score">
        <PriorityMark tier={row.tier} acknowledged={acked} size={15} />
        <span className="num">{Math.round(row.score)}</span>
      </div>
      <div className="q-main">
        {!compact && (
          <div className="q-patient">
            <strong>{p.name}</strong>
            <span className="q-meta">{meta}</span>
          </div>
        )}
        <div className="q-alert">
          <span className="q-title">{row.title}</span>
          <span className="q-value">{row.value_text}</span>
        </div>
        <div className="q-flags">
          {row.floor_triggered && (
            <span className="flag" title="Top-band reading or emergency event. The safety floor keeps it high.">
              <ShieldCheck size={12} aria-hidden="true" /> Safety floor
            </span>
          )}
          {row.corroborated && (
            <span className="flag" title="Other alerts on this patient back this one up">
              <Waypoints size={12} aria-hidden="true" /> Corroborated
            </span>
          )}
          {row.chattering && (
            <span className="flag quiet" title="3 or more alerts inside 10 minutes">
              Chattering
            </span>
          )}
          {acked && <span className="flag quiet">With {row.assigned_to || "a coordinator"}</span>}
        </div>
      </div>
      <div className="q-signal">
        <Signal row={row} />
      </div>
      <div className="q-time">
        <span className="q-age">{duration(minutesSince(row.first_seen, now))}</span>
        <span className="q-count">{row.alert_count > 1 ? `${row.alert_count} alerts grouped` : "1 alert"}</span>
      </div>
      <div className="q-claude">
        <ClaudeChip claude={row.claude} />
      </div>
      <div className="q-actions">
        {row.status === "open" ? (
          <button
            className="btn ghost small"
            onClick={(e) => {
              e.stopPropagation();
              onAck(row);
            }}
          >
            Acknowledge
          </button>
        ) : (
          <span className="q-acked">Acknowledged</span>
        )}
      </div>
    </div>
  );
}
