import { useEffect, useState } from "react";
import { Empty, Meter, PriorityMark } from "../components/bits.jsx";
import { api } from "../lib/api.js";
import { fix, plural } from "../lib/format.js";

export default function PatientsPage({ onOpen, version }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.patients().then(setRows).catch((e) => setError(e.message));
  }, [version]);

  if (error) return <Empty title="Patients didn't load">{error}</Empty>;
  if (!rows) return <div className="loading">Loading patients</div>;

  return (
    <div className="page patients-page">
      <header className="page-head">
        <div>
          <h1>Patients</h1>
          <p className="lede">
            {plural(rows.length, "person", "people")} on the caseload, sorted by their most urgent open alert. Vulnerability is
            the same factor the model uses: the more fragile someone is, the harder an alert about them counts.
          </p>
        </div>
      </header>
      <div className="roster">
        {rows.map((p) => (
          <article key={p.id} className={`pt ${p.top_tier ? `tier-${p.top_tier}` : ""}`}>
            <span className="avatar" aria-hidden="true">
              {p.initials}
            </span>
            <div className="pt-name">
              <strong>{p.name}</strong>
              <span>
                {p.age}, {p.neighbourhood}. Acuity {p.acuity}
                {p.lives_alone ? ", lives alone" : ""}
                {p.goals_of_care === "comfort" ? ", palliative" : ""}
              </span>
            </div>
            <div className="pt-cond chips">
              {p.conditions.map((c) => (
                <span className="chip" key={c}>
                  {c}
                </span>
              ))}
            </div>
            <div className="pt-vul" title={p.vulnerability_terms.map((t) => `${t.label}: +${fix(t.value)}`).join("\n")}>
              <div className="pt-vul-top">
                <span>Vulnerability</span>
                <span className="num">{fix(p.vulnerability)}</span>
              </div>
              <Meter value={p.vulnerability} label="Vulnerability" />
            </div>
            <div className="pt-inc">
              {p.incidents.length === 0 && <span className="muted small">No open alerts</span>}
              {p.incidents.slice(0, 4).map((i) => (
                <button key={i.id} onClick={() => onOpen(i.id)}>
                  <PriorityMark tier={i.tier} size={12} />
                  <span className="num">{Math.round(i.score)}</span>
                  <span>{i.title}</span>
                </button>
              ))}
              {p.incidents.length > 4 && <span className="muted small">and {p.incidents.length - 4} more</span>}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
