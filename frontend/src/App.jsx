import { ChartColumn, ListOrdered, Moon, RotateCcw, SlidersHorizontal, Sun, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ClaudeMark, Logo, Modal, Toasts } from "./components/bits.jsx";
import IncidentDrawer from "./components/IncidentDrawer.jsx";
import { api } from "./lib/api.js";
import { useHashRoute, useTheme, useToasts } from "./lib/hooks.js";
import InsightsPage from "./pages/InsightsPage.jsx";
import ModelPage from "./pages/ModelPage.jsx";
import PatientsPage from "./pages/PatientsPage.jsx";
import QueuePage from "./pages/QueuePage.jsx";

const NAV = [
  { key: "queue", label: "Queue", icon: ListOrdered },
  { key: "patients", label: "Patients", icon: Users },
  { key: "model", label: "Model", icon: SlidersHorizontal },
  { key: "insights", label: "Insights", icon: ChartColumn },
];

function ClaudeSetup({ status, onClose }) {
  return (
    <Modal title="Connect Claude" onClose={onClose}>
      <p>
        Pulse ranks everything with its own model first, so the queue works without Claude. Claude adds a second opinion on top: it
        reads the incident, the care plan and the model's numbers, then agrees, escalates or de-escalates and names the next step.
      </p>
      <ol className="steps">
        <li>
          Create a key at <strong>platform.claude.com</strong> under API keys.
        </li>
        <li>
          Open <code>backend/.env</code> (copy it from <code>backend/.env.example</code> if it isn't there) and set{" "}
          <code>ANTHROPIC_API_KEY=your-key</code>. On a hosted copy, add it as an environment variable on the server instead.
        </li>
        <li>
          Restart the server. Locally: <kbd>Ctrl</kbd> + <kbd>C</kbd>, then <code>python manage.py runserver</code>.
        </li>
      </ol>
      <p className="muted">
        Current model: <code>{status?.model || "claude-sonnet-5-5"}</code>. Change it with <code>PULSE_CLAUDE_MODEL</code> in the same file.
      </p>
    </Modal>
  );
}

export default function App() {
  const [route, go] = useHashRoute();
  const [theme, toggleTheme] = useTheme();
  const [toasts, toast, dismiss] = useToasts();
  const [openId, setOpenId] = useState(null);
  const [version, setVersion] = useState(0);
  const [claude, setClaude] = useState(null);
  const [setup, setSetup] = useState(false);
  const [resetting, setResetting] = useState(false);

  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const loadClaude = useCallback(() => {
    api.claudeStatus().then(setClaude).catch(() => setClaude(null));
  }, []);

  useEffect(() => {
    loadClaude();
  }, [loadClaude, version]);

  async function resetDemo() {
    if (!window.confirm("Start a fresh synthetic shift? This replaces every alert and incident. Model settings are kept.")) return;
    setResetting(true);
    try {
      const r = await api.resetDemo();
      setOpenId(null);
      toast({ title: "Fresh shift loaded", body: `${r.alerts} alerts across ${r.patients} patients, ending now.` });
      bump();
    } catch (e) {
      toast({ title: "Reset failed", body: e.message, tone: "error" });
    } finally {
      setResetting(false);
    }
  }

  const common = { onOpen: setOpenId, version, bump, toast, claude, onClaudeSetup: () => setSetup(true) };

  return (
    <div className="shell">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <nav className="sidebar" aria-label="Main">
        <div className="brand">
          <Logo size={30} />
          <div>
            <span className="brand-name">Pulse</span>
            <span className="brand-sub">Alarm triage for care teams</span>
          </div>
        </div>
        <ul className="nav">
          {NAV.map(({ key, label, icon: Icon }) => (
            <li key={key}>
              <a href={`#/${key}`} className={route === key ? "active" : ""} aria-current={route === key ? "page" : undefined}>
                <Icon size={18} aria-hidden="true" />
                {label}
              </a>
            </li>
          ))}
        </ul>
        <div className="side-foot">
          <button
            className={`claude-status ${claude?.configured ? "ok" : ""}`}
            onClick={() => !claude?.configured && setSetup(true)}
            aria-label={claude?.configured ? "Claude connected" : "Claude not connected. Show how to connect."}
          >
            <ClaudeMark size={15} />
            <span className="cs-text">
              {claude?.configured ? (claude.enabled ? "Claude connected" : "Claude turned off") : "Claude not connected"}
              <small>{claude?.configured ? claude.model : "Add an API key to enable"}</small>
            </span>
          </button>
          <div className="side-actions">
            <button className="icon-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} title="Theme">
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button className="btn ghost small" onClick={resetDemo} disabled={resetting} title="Generate a fresh shift ending now">
              <RotateCcw size={14} /> <span className="hide-sm">{resetting ? "Loading" : "New shift"}</span>
            </button>
          </div>
          <p className="synthetic">Synthetic data. No real patients.</p>
        </div>
      </nav>

      <main id="main" className="main">
        {route === "queue" && <QueuePage {...common} selectedId={openId} />}
        {route === "patients" && <PatientsPage {...common} />}
        {route === "model" && <ModelPage {...common} />}
        {route === "insights" && <InsightsPage {...common} />}
      </main>

      {openId && (
        <IncidentDrawer
          id={openId}
          version={version}
          onClose={() => setOpenId(null)}
          onChanged={bump}
          onOpen={setOpenId}
          toast={toast}
          claudeStatus={claude}
          onClaudeSetup={() => setSetup(true)}
        />
      )}
      {setup && <ClaudeSetup status={claude} onClose={() => setSetup(false)} />}
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}
