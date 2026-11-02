import { ChevronDown, Radio, RefreshCw, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ClaudeMark, Empty, PriorityMark } from "../components/bits.jsx";
import FloodStrip from "../components/FloodStrip.jsx";
import QueueRow, { ClaudeChip } from "../components/QueueRow.jsx";
import { api } from "../lib/api.js";
import { TIERS, TIER_LABEL, plural } from "../lib/format.js";
import { useFlip, useInterval, useNow } from "../lib/hooks.js";

const REVIEW_N = 6;

export default function QueuePage({ onOpen, selectedId, version, bump, toast, claude, onClaudeSetup }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [tier, setTier] = useState("all");
  const [mode, setMode] = useState("alert");
  const [query, setQuery] = useState("");
  const [hideAcked, setHideAcked] = useState(false);
  const [live, setLive] = useState(false);
  const [ticker, setTicker] = useState("");
  const [busy, setBusy] = useState(null);
  const [scenarios, setScenarios] = useState([]);
  const [menu, setMenu] = useState(false);
  const [newIds, setNewIds] = useState(() => new Set());
  const seen = useRef(null);
  const listRef = useRef(null);
  const now = useNow(20000);

  async function load() {
    try {
      const d = await api.queue();
      const ids = d.incidents.map((r) => r.id);
      if (seen.current) {
        const fresh = ids.filter((id) => !seen.current.has(id));
        if (fresh.length) {
          setNewIds(new Set(fresh));
          setTimeout(() => setNewIds(new Set()), 2600);
        }
      }
      seen.current = new Set(ids);
      setData(d);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, [version]);

  useEffect(() => {
    api.scenarios().then(setScenarios).catch(() => {});
  }, []);

  useInterval(load, 15000, !live);
  useInterval(
    async () => {
      try {
        const t = await api.tick();
        const parts = [];
        if (t.grouped) parts.push(`${t.grouped} joined open incident${t.grouped === 1 ? "" : "s"}`);
        if (t.new_incidents) parts.push(`${t.new_incidents} new`);
        setTicker(`${plural(t.alerts, "alert")} just arrived: ${parts.join(", ")}.`);
        await load();
      } catch (e) {
        setTicker(e.message);
      }
    },
    6000,
    live
  );

  const rows = data?.incidents || [];
  const counts = useMemo(() => {
    const c = { all: rows.length };
    TIERS.forEach((t) => (c[t] = rows.filter((r) => r.tier === t).length));
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(
      (r) =>
        (tier === "all" || r.tier === tier) &&
        (!hideAcked || r.status === "open") &&
        (!q || r.patient.name.toLowerCase().includes(q) || r.title.toLowerCase().includes(q) || (r.value_text || "").toLowerCase().includes(q))
    );
  }, [rows, tier, query, hideAcked]);

  const groups = useMemo(() => {
    if (mode !== "patient") return [];
    const m = new Map();
    visible.forEach((r) => {
      if (!m.has(r.patient.id)) m.set(r.patient.id, { patient: r.patient, rows: [] });
      m.get(r.patient.id).rows.push(r);
    });
    return [...m.values()];
  }, [visible, mode]);

  useFlip(listRef, [data, tier, mode, query, hideAcked]);

  const needPeople = useMemo(
    () => new Set(rows.filter((r) => (r.tier === "critical" || r.tier === "high") && r.status === "open").map((r) => r.patient.id)).size,
    [rows]
  );

  async function acknowledge(row) {
    try {
      await api.action(row.id, "acknowledge", { actor: "You" });
      toast({ title: "Acknowledged", body: `${row.patient.name}: ${row.title}. It stays in the queue, marked as yours.` });
      bump();
    } catch (e) {
      toast({ title: "Couldn't acknowledge", body: e.message, tone: "error" });
    }
  }

  async function review() {
    if (!claude?.configured) return onClaudeSetup();
    setBusy("review");
    try {
      const r = await api.review(REVIEW_N);
      const tally = r.items.reduce((a, i) => ((a[i.agreement] = (a[i.agreement] || 0) + 1), a), {});
      const parts = [];
      if (tally.agree) parts.push(`agreed on ${tally.agree}`);
      if (tally.escalate) parts.push(`escalated ${tally.escalate}`);
      if (tally["de-escalate"]) parts.push(`de-escalated ${tally["de-escalate"]}`);
      if (r.judged === 0 && r.skipped) {
        toast({ tone: "claude", title: "Claude is up to date", body: "The top incidents already have a current review." });
      } else {
        toast({
          tone: r.errors.length ? "error" : "claude",
          title: `Claude reviewed ${plural(r.judged, "incident")}`,
          body: [parts.length ? `It ${parts.join(", ")}.` : "", r.errors[0] || ""].filter(Boolean).join(" "),
          ttl: 8000,
        });
      }
      bump();
    } catch (e) {
      if (e.code === "not_configured") onClaudeSetup();
      else toast({ title: "Claude review failed", body: e.message, tone: "error", ttl: 9000 });
    } finally {
      setBusy(null);
    }
  }

  async function inject(s) {
    setMenu(false);
    setBusy("scenario");
    try {
      const r = await api.scenario(s.key);
      toast({ title: `Injected: ${s.name}`, body: `Watch ${s.patient}. ${s.help}`, ttl: 9000 });
      await load();
      const first = r.items[0];
      if (first) onOpen(first.incident_id);
    } catch (e) {
      toast({ title: "Couldn't inject the story", body: e.message, tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  if (error && !data)
    return (
      <Empty title="The queue didn't load" action={<button className="btn" onClick={load}>Try again</button>}>
        {error}
      </Empty>
    );
  if (!data) return <div className="loading">Loading the queue</div>;

  const s = data.summary;

  return (
    <div className="page queue-page">
      <section className="hero" aria-label="Shift summary">
        <p className="hero-noise">
          {s.raw_alerts} alerts in the last {s.window_hours} hours became {plural(s.incidents, "incident")}.
        </p>
        <h1 className="hero-signal">
          {needPeople === 0
            ? "Nobody needs you right now."
            : `${needPeople} ${needPeople === 1 ? "person needs" : "people need"} you now.`}
        </h1>
        <FloodStrip timeline={data.timeline} />
      </section>

      <section className="controls" aria-label="Live controls">
        <div className="live">
          <Radio size={16} aria-hidden="true" className={live ? "live-on" : ""} />
          <label htmlFor="live-switch">Live feed</label>
          <button
            type="button"
            role="switch"
            id="live-switch"
            aria-checked={live}
            className={`switch ${live ? "on" : ""}`}
            onClick={() => {
              setLive(!live);
              setTicker(live ? "" : "New alerts arrive every 6 seconds.");
            }}
          >
            <span className="switch-knob" />
          </button>
          <span className="ticker" aria-live="polite">
            {ticker}
          </span>
        </div>
        <div className="control-actions">
          <div className="menu-wrap">
            <button className="btn" onClick={() => setMenu(!menu)} aria-expanded={menu} disabled={busy === "scenario"}>
              Inject a story <ChevronDown size={15} />
            </button>
            {menu && (
              <div className="menu" role="menu" onMouseLeave={() => setMenu(false)}>
                {scenarios.map((sc) => (
                  <button key={sc.key} role="menuitem" className="menu-item" onClick={() => inject(sc)}>
                    <strong>{sc.name}</strong>
                    <span>{sc.patient}</span>
                    <small>{sc.help}</small>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="btn claude" onClick={review} disabled={busy === "review"}>
            <ClaudeMark size={15} />
            {busy === "review" ? `Claude is reviewing the top ${REVIEW_N}` : `Ask Claude to review the top ${REVIEW_N}`}
          </button>
          <button className="icon-btn" onClick={load} aria-label="Refresh queue" title="Refresh">
            <RefreshCw size={17} />
          </button>
        </div>
      </section>

      <section className="queue" aria-label="Ranked queue">
        <div className="queue-head">
          <div className="filters" role="tablist" aria-label="Filter by tier">
            {["all", ...TIERS].map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tier === t}
                className={`filter ${tier === t ? "on" : ""}`}
                onClick={() => setTier(t)}
              >
                {t !== "all" && <PriorityMark tier={t} size={11} />}
                {t === "all" ? "All" : TIER_LABEL[t]}
                <span className="filter-n">{counts[t]}</span>
              </button>
            ))}
          </div>
          <div className="queue-tools">
            <div className="seg" role="group" aria-label="Group">
              <button className={mode === "alert" ? "on" : ""} onClick={() => setMode("alert")}>
                By incident
              </button>
              <button className={mode === "patient" ? "on" : ""} onClick={() => setMode("patient")}>
                By patient
              </button>
            </div>
            <label className="check">
              <input type="checkbox" checked={hideAcked} onChange={(e) => setHideAcked(e.target.checked)} />
              Hide acknowledged
            </label>
            <label className="search">
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                placeholder="Patient or alert"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search the queue"
              />
            </label>
          </div>
        </div>

        <div className="qcols" aria-hidden="true">
          <span>Priority</span>
          <span>Patient and alert</span>
          <span>Reading</span>
          <span>Waiting</span>
          <span>Claude</span>
          <span />
        </div>

        <div className="qlist" ref={listRef}>
          {visible.length === 0 && (
            <Empty title="Nothing matches">Clear the search or pick another tier.</Empty>
          )}
          {mode === "alert" &&
            visible.map((r) => (
              <QueueRow
                key={r.id}
                row={r}
                now={now}
                selected={selectedId === r.id}
                isNew={newIds.has(r.id)}
                onOpen={onOpen}
                onAck={acknowledge}
              />
            ))}
          {mode === "patient" &&
            groups.map((g) => (
              <div className={`pgroup tier-${g.rows[0].tier}`} key={g.patient.id} data-flip-key={`p${g.patient.id}`}>
                <div className="pgroup-head">
                  <PriorityMark tier={g.rows[0].tier} size={15} />
                  <span className="num">{Math.round(g.rows[0].score)}</span>
                  <strong>{g.patient.name}</strong>
                  <span className="q-meta">
                    {g.patient.age}, acuity {g.patient.acuity}
                    {g.patient.lives_alone ? ", lives alone" : ""}
                    {g.patient.goals_of_care === "comfort" ? ", palliative" : ""}
                  </span>
                  <span className="pgroup-count">{plural(g.rows.length, "open incident")}</span>
                  {g.rows[0].claude && <ClaudeChip claude={g.rows[0].claude} compact />}
                </div>
                {g.rows.map((r) => (
                  <QueueRow
                    key={r.id}
                    row={r}
                    now={now}
                    compact
                    selected={selectedId === r.id}
                    isNew={newIds.has(r.id)}
                    onOpen={onOpen}
                    onAck={acknowledge}
                  />
                ))}
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}
