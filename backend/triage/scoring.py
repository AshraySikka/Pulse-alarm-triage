"""
Pulse scoring engine: the Triage Priority Index (TPI)
====================================================

Pure functions with no Django imports, so the math is easy to read, test and tune.

Every active incident gets four factors in [0, 1] plus a waiting-time term.
A logistic scorecard turns them into a 0-100 priority:

    z   = b0 + bS*S + bC*ln(C) + bV*V + bU*(U*S) + bW*W
    TPI = 100 * sigmoid(z)

Credibility enters as a log, so in odds terms the score is proportional to
C^bC: an alert half as likely to be real loses the same ground however
severe it looks. That is the expected-harm idea, P(real) x harm, written as
log-odds so every factor's contribution can be shown as a bar.

  S  Severity       How abnormal is it? NEWS2-style bands, deviation from the
                    patient's own robust baseline, and the 48-hour trend,
                    combined with a noisy-OR. Events use a catalog severity,
                    payload modifiers and red-flag phrases.
  C  Credibility    How likely is it real and actionable? A hierarchical
                    Beta-Bernoulli posterior learned from past outcomes
                    (alert type, then this patient), times signal persistence
                    and quality, then a noisy-OR boost from corroborating alerts.
  V  Vulnerability  How fragile is this patient? A saturating hazard sum:
                    V = 1 - exp(-H).
  U  Urgency        How fast does a real event of this type cause harm?
                    U = T_ref / (T_ref + T_harm). It enters as U*S, the
                    ISA-18.2 idea that priority = consequence x time to respond.
  W  Waiting        Unacknowledged time: W = 1 - exp(-t / tau). Stops alarms
                    from rotting at the bottom of the queue.

A safety floor keeps any top-band vital sign (with a plausible signal) or any
emergency event at or above a set score, whatever the other factors say.
Claude's second opinion can then be blended in, see final_score().
"""
from __future__ import annotations

import math
from statistics import median

# ---------------------------------------------------------------------------
# Tunable parameters. Every knob the admin can move lives here, with the
# metadata the UI needs to draw a slider for it.
# ---------------------------------------------------------------------------

PARAM_GROUPS = [
    ("weights", "Score weights", "How much each factor moves the final score. These are the main dials."),
    ("severity", "Severity", "How the model reads an abnormal value."),
    ("credibility", "Credibility", "How the model separates real alarms from noise, and how fast it learns."),
    ("urgency", "Urgency and waiting", "How time changes priority."),
    ("vulnerability", "Patient vulnerability", "Which patient factors make the same alert more worrying."),
    ("safety", "Tiers and safety", "Where the tier lines sit, and what can never be buried."),
    ("claude", "Claude second opinion", "How much Claude's judgement can move the ranking."),
]

# (group, key, label, help, default, min, max, step, kind)
PARAM_SCHEMA = [
    ("weights", "beta_0", "Starting point", "Where every alert starts before evidence. Lower makes the whole queue quieter.", -2.5, -6.0, 1.0, 0.1, "float"),
    ("weights", "beta_severity", "Severity", "How much an abnormal reading pushes the score up.", 3.0, 0.0, 6.0, 0.1, "float"),
    ("weights", "beta_credibility", "Credibility", "How hard unlikely alarms are pushed down. Enters as a log: at 1, an alert half as likely to be real has half the odds.", 1.2, 0.0, 3.0, 0.05, "float"),
    ("weights", "beta_vulnerability", "Vulnerability", "How much patient frailty and risk matter.", 1.5, 0.0, 6.0, 0.1, "float"),
    ("weights", "beta_urgency", "Urgency x severity", "How much a short time-to-harm matters, scaled by how severe the alert is.", 1.5, 0.0, 6.0, 0.1, "float"),
    ("weights", "beta_waiting", "Waiting time", "How much an unacknowledged alert climbs while it waits.", 0.8, 0.0, 4.0, 0.1, "float"),

    ("severity", "personal_baseline_trust", "Personal baseline trust", "0 uses population thresholds only. 1 fully discounts a reading that is normal for this patient.", 0.3, 0.0, 1.0, 0.05, "float"),
    ("severity", "deviation_weight", "Baseline deviation", "How strongly a reading far from the patient's own baseline adds severity.", 0.4, 0.0, 1.0, 0.05, "float"),
    ("severity", "deviation_midpoint", "Deviation midpoint (SD)", "Robust standard deviations from baseline that count as half-severe.", 2.5, 1.0, 5.0, 0.1, "float"),
    ("severity", "deviation_steepness", "Deviation steepness", "How sharply severity rises around the midpoint.", 1.5, 0.5, 4.0, 0.1, "float"),
    ("severity", "trend_weight", "Trend", "How strongly a worsening 48-hour trend adds severity.", 0.5, 0.0, 1.0, 0.05, "float"),
    ("severity", "trend_scale", "Trend scale (SD per day)", "Worsening rate at which trend severity reaches 63%.", 1.5, 0.25, 5.0, 0.05, "float"),

    ("credibility", "prior_strength", "Prior strength, alert type", "Pseudo-outcomes behind each alert type's starting rate. Higher learns more slowly from feedback.", 10.0, 1.0, 50.0, 1.0, "float"),
    ("credibility", "patient_prior_strength", "Prior strength, patient", "How quickly one patient's own false alarms override the alert-type rate.", 4.0, 0.5, 20.0, 0.5, "float"),
    ("credibility", "artifact_time_constant", "Artifact time constant (s)", "Alerts much shorter than this look like motion artifact.", 45.0, 5.0, 300.0, 5.0, "float"),
    ("credibility", "repeat_boost", "Repeat boost", "How much each repeat of the same alert raises credibility.", 0.15, 0.0, 0.5, 0.01, "float"),
    ("credibility", "corroboration_strength", "Corroboration strength", "How much other alerts on the same patient back each other up.", 0.5, 0.0, 1.0, 0.05, "float"),
    ("credibility", "corroboration_window_min", "Corroboration window (min)", "How close in time other alerts must be to count.", 90.0, 10.0, 360.0, 5.0, "float"),

    ("urgency", "reference_time_min", "Reference time-to-harm (min)", "A time-to-harm equal to this gives urgency 0.5.", 30.0, 5.0, 240.0, 5.0, "float"),
    ("urgency", "escalation_time_constant_min", "Escalation time constant (min)", "How fast waiting time escalates an unacknowledged alert.", 120.0, 15.0, 720.0, 15.0, "float"),

    ("vulnerability", "theta_acuity", "Acuity", "Weight of care acuity (1 to 5).", 1.2, 0.0, 3.0, 0.05, "float"),
    ("vulnerability", "theta_age", "Age over 65", "Weight of age beyond 65, saturating at 95.", 0.5, 0.0, 2.0, 0.05, "float"),
    ("vulnerability", "theta_comorbidity", "Chronic conditions", "Weight of the number of chronic conditions, saturating at 6.", 0.5, 0.0, 2.0, 0.05, "float"),
    ("vulnerability", "theta_lives_alone", "Lives alone", "Added risk when nobody else is home.", 0.35, 0.0, 2.0, 0.05, "float"),
    ("vulnerability", "theta_discharge", "Recent discharge", "Added risk right after a hospital discharge, fading over time.", 0.6, 0.0, 2.0, 0.05, "float"),
    ("vulnerability", "discharge_decay_days", "Discharge risk fades over (days)", "Time constant for post-discharge risk.", 14.0, 1.0, 60.0, 1.0, "float"),
    ("vulnerability", "theta_fall_history", "Fall history", "Added risk for patients who have fallen before.", 0.3, 0.0, 2.0, 0.05, "float"),
    ("vulnerability", "theta_cognition", "Cognitive impairment", "Added risk when the patient may not be able to call for help.", 0.3, 0.0, 2.0, 0.05, "float"),

    ("safety", "tier_critical", "Critical from", "Scores at or above this are critical.", 80.0, 50.0, 95.0, 1.0, "float"),
    ("safety", "tier_high", "High from", "Scores at or above this are high.", 60.0, 30.0, 90.0, 1.0, "float"),
    ("safety", "tier_medium", "Medium from", "Scores at or above this are medium. Below is low.", 35.0, 10.0, 70.0, 1.0, "float"),
    ("safety", "safety_floor_enabled", "Safety floor", "Never rank a top-band vital sign or an emergency event below the floor score.", 1.0, 0.0, 1.0, 1.0, "bool"),
    ("safety", "safety_floor", "Floor score", "The lowest score a floored alert can have.", 75.0, 40.0, 95.0, 1.0, "float"),
    ("safety", "floor_min_quality", "Floor needs signal quality of", "Keeps probe-off artifacts from triggering the floor.", 0.4, 0.0, 1.0, 0.05, "float"),
    ("safety", "dedup_window_min", "Group repeats within (min)", "A repeat of the same alert for the same patient joins the open incident if it arrives within this gap. Applies to new alerts.", 120.0, 5.0, 480.0, 5.0, "float"),

    ("claude", "claude_enabled", "Use Claude", "Let Claude review incidents after the model scores them.", 1.0, 0.0, 1.0, 1.0, "bool"),
    ("claude", "claude_blend", "Claude blend weight", "0 ranks by the model alone. 1 ranks by Claude's score alone.", 0.3, 0.0, 1.0, 0.05, "float"),
    ("claude", "claude_can_escalate", "Claude can raise to critical", "If Claude calls something critical with medium or high confidence, it jumps to the critical tier. Claude can never push a floored alert below the floor.", 1.0, 0.0, 1.0, 1.0, "bool"),
]

DEFAULT_PARAMS = {row[1]: row[4] for row in PARAM_SCHEMA}
PARAM_LIMITS = {row[1]: (row[5], row[6]) for row in PARAM_SCHEMA}

PRESETS = [
    {
        "key": "balanced",
        "name": "Balanced",
        "help": "The default. Severity leads, noise sinks, nothing serious gets buried.",
        "params": {},
    },
    {
        "key": "safety_first",
        "name": "Safety first",
        "help": "Trust thresholds over history. More alerts reach the top and fewer are discounted.",
        "params": {
            "beta_0": -2.0, "beta_severity": 4.0, "beta_credibility": 0.6, "personal_baseline_trust": 0.0,
            "safety_floor": 80.0, "floor_min_quality": 0.2, "prior_strength": 20.0,
        },
    },
    {
        "key": "fatigue_buster",
        "name": "Fatigue buster",
        "help": "Lean hard on learned credibility. Chattering, short and low-quality alerts sink fast.",
        "params": {
            "beta_0": -2.8, "beta_credibility": 2.0, "prior_strength": 5.0, "patient_prior_strength": 2.0,
            "artifact_time_constant": 90.0, "personal_baseline_trust": 0.6,
        },
    },
    {
        "key": "frail_first",
        "name": "Frail first",
        "help": "Weight the patient more than the signal. Useful for a high-acuity caseload.",
        "params": {
            "beta_vulnerability": 3.0, "theta_lives_alone": 0.7, "theta_discharge": 1.0,
            "theta_cognition": 0.6, "theta_fall_history": 0.5,
        },
    },
]


def sanitize_params(raw: dict) -> dict:
    """Merge user-supplied params over defaults and clamp to their slider range."""
    out = dict(DEFAULT_PARAMS)
    for key, value in (raw or {}).items():
        if key not in DEFAULT_PARAMS:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        lo, hi = PARAM_LIMITS[key]
        out[key] = min(hi, max(lo, v))
    # Keep tiers ordered: critical > high > medium
    out["tier_high"] = min(out["tier_high"], out["tier_critical"] - 1)
    out["tier_medium"] = min(out["tier_medium"], out["tier_high"] - 1)
    return out


# ---------------------------------------------------------------------------
# Small math helpers
# ---------------------------------------------------------------------------

def sigmoid(u: float) -> float:
    if u >= 0:
        return 1.0 / (1.0 + math.exp(-u))
    e = math.exp(u)
    return e / (1.0 + e)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def robust_baseline(values: list[float], sigma_min: float):
    """Median and MAD-based standard deviation (1.4826 x MAD), floored at sigma_min.

    Robust statistics stop one bad reading from dragging the baseline around.
    """
    if len(values) < 5:
        return None
    med = median(values)
    mad = median(abs(v - med) for v in values)
    return med, max(1.4826 * mad, sigma_min)


def ols_slope_per_day(points: list[tuple[float, float]], min_span_hours: float = 6.0):
    """Least-squares slope of (hours, value) points, returned per day.

    Needs at least 3 points spread over min_span_hours, otherwise None.
    """
    if len(points) < 3:
        return None
    ts = [p[0] for p in points]
    if max(ts) - min(ts) < min_span_hours:
        return None
    mt = sum(ts) / len(ts)
    mv = sum(p[1] for p in points) / len(points)
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx == 0:
        return None
    sxy = sum((t - mt) * (v - mv) for t, v in points)
    return (sxy / sxx) * 24.0


def beta_posterior(prior_rate: float, prior_strength: float, actionable: int, false_alarms: int) -> float:
    """Posterior mean of a Beta(a0, b0) prior after observing outcomes.

    a0 = rate * strength, b0 = (1 - rate) * strength
    E[p] = (a0 + actionable) / (a0 + b0 + actionable + false_alarms)
    """
    a0 = clamp(prior_rate, 0.001, 0.999) * prior_strength
    b0 = (1 - clamp(prior_rate, 0.001, 0.999)) * prior_strength
    return (a0 + actionable) / (a0 + b0 + actionable + false_alarms)


# ---------------------------------------------------------------------------
# Vital-sign bands. NEWS2 (Royal College of Physicians, 2017) where it exists,
# common home-care remote-monitoring rules where it doesn't. Each returns
# (band 0-3, plain-language rule).
# ---------------------------------------------------------------------------

def band_spo2(v: float, scale2: bool = False, on_oxygen: bool = False):
    v = round(v)
    if not scale2:
        if v <= 91:
            return 3, "NEWS2 SpO2 scale 1: 91% or lower"
        if v <= 93:
            return 2, "NEWS2 SpO2 scale 1: 92-93%"
        if v <= 95:
            return 1, "NEWS2 SpO2 scale 1: 94-95%"
        return 0, "NEWS2 SpO2 scale 1: 96% or higher"
    if v <= 83:
        return 3, "NEWS2 SpO2 scale 2: 83% or lower"
    if v <= 85:
        return 2, "NEWS2 SpO2 scale 2: 84-85%"
    if v <= 87:
        return 1, "NEWS2 SpO2 scale 2: 86-87%"
    if v <= 92:
        return 0, "NEWS2 SpO2 scale 2: 88-92%, inside this patient's prescribed target"
    if not on_oxygen:
        return 0, "NEWS2 SpO2 scale 2: 93% or higher on air"
    if v <= 94:
        return 1, "NEWS2 SpO2 scale 2: 93-94% on oxygen"
    if v <= 96:
        return 2, "NEWS2 SpO2 scale 2: 95-96% on oxygen"
    return 3, "NEWS2 SpO2 scale 2: 97% or higher on oxygen"


def band_hr(v: float):
    v = round(v)
    if v <= 40:
        return 3, "NEWS2 pulse: 40 or lower"
    if v <= 50:
        return 1, "NEWS2 pulse: 41-50"
    if v <= 90:
        return 0, "NEWS2 pulse: 51-90"
    if v <= 110:
        return 1, "NEWS2 pulse: 91-110"
    if v <= 130:
        return 2, "NEWS2 pulse: 111-130"
    return 3, "NEWS2 pulse: 131 or higher"


def band_sbp(v: float):
    v = round(v)
    if v <= 90:
        return 3, "NEWS2 systolic BP: 90 or lower"
    if v <= 100:
        return 2, "NEWS2 systolic BP: 91-100"
    if v <= 110:
        return 1, "NEWS2 systolic BP: 101-110"
    if v <= 179:
        return 0, "NEWS2 systolic BP: 111-219 scores 0"
    if v <= 219:
        return 2, "Home-care rule: systolic 180 or higher"
    return 3, "NEWS2 systolic BP: 220 or higher"


def band_temp(v: float):
    v = round(v, 1)
    if v <= 35.0:
        return 3, "NEWS2 temperature: 35.0 C or lower"
    if v <= 36.0:
        return 1, "NEWS2 temperature: 35.1-36.0 C"
    if v <= 38.0:
        return 0, "NEWS2 temperature: 36.1-38.0 C"
    if v <= 39.0:
        return 1, "NEWS2 temperature: 38.1-39.0 C"
    return 2, "NEWS2 temperature: 39.1 C or higher"


def band_rr(v: float):
    v = round(v)
    if v <= 8:
        return 3, "NEWS2 respiration: 8 or lower"
    if v <= 11:
        return 1, "NEWS2 respiration: 9-11"
    if v <= 20:
        return 0, "NEWS2 respiration: 12-20"
    if v <= 24:
        return 2, "NEWS2 respiration: 21-24"
    return 3, "NEWS2 respiration: 25 or higher"


def band_glucose(v: float):
    if v < 3.0:
        return 3, "Level 2 hypoglycemia: under 3.0 mmol/L"
    if v < 4.0:
        return 2, "Hypoglycemia: under 4.0 mmol/L"
    if v < 14.0:
        return 0, "4.0-13.9 mmol/L"
    if v < 20.0:
        return 1, "Hyperglycemia: 14.0-19.9 mmol/L"
    if v < 25.0:
        return 2, "Hyperglycemia: 20.0-24.9 mmol/L"
    return 3, "Severe hyperglycemia: 25 mmol/L or higher"


def band_weight_gain(delta_kg: float):
    if delta_kg < 1.0:
        return 0, "Weight change under 1 kg in 24 h"
    if delta_kg < 1.5:
        return 1, "Heart-failure rule: gain of 1.0-1.4 kg in 24 h"
    if delta_kg < 2.0:
        return 2, "Heart-failure rule: gain of 1.5-1.9 kg in 24 h"
    return 3, "Heart-failure rule: gain of 2 kg or more in 24 h"


SIGMA_MIN = {"spo2": 1.0, "hr": 4.0, "sbp": 6.0, "temp": 0.2, "rr": 1.5, "glucose": 0.8, "weight": 0.4}

# Display scale for the analog range indicator in the UI, and the band-0 range.
PARAM_DISPLAY = {
    "spo2": {"label": "SpO2", "unit": "%", "min": 78, "max": 100, "normal": [96, 100], "decimals": 0},
    "hr": {"label": "Heart rate", "unit": "bpm", "min": 30, "max": 160, "normal": [51, 90], "decimals": 0},
    "sbp": {"label": "Systolic BP", "unit": "mmHg", "min": 70, "max": 230, "normal": [111, 179], "decimals": 0},
    "temp": {"label": "Temperature", "unit": "°C", "min": 34.0, "max": 41.0, "normal": [36.1, 38.0], "decimals": 1},
    "rr": {"label": "Respiratory rate", "unit": "/min", "min": 4, "max": 36, "normal": [12, 20], "decimals": 0},
    "glucose": {"label": "Glucose", "unit": "mmol/L", "min": 1.5, "max": 28, "normal": [4.0, 13.9], "decimals": 1},
    "weight": {"label": "Weight, 24 h change", "unit": "kg", "min": -1.0, "max": 3.0, "normal": [-1.0, 0.9], "decimals": 1},
}


def vital_band(param: str, value: float, patient: dict, payload: dict | None = None):
    payload = payload or {}
    if param == "spo2":
        return band_spo2(value, patient.get("hypercapnic_risk", False), patient.get("on_oxygen", False))
    if param == "hr":
        return band_hr(value)
    if param == "sbp":
        return band_sbp(value)
    if param == "temp":
        return band_temp(value)
    if param == "rr":
        return band_rr(value)
    if param == "glucose":
        return band_glucose(value)
    if param == "weight":
        return band_weight_gain(float(payload.get("delta_kg", 0.0)))
    return 0, "No band table"


def display_range(param: str, patient: dict):
    d = dict(PARAM_DISPLAY.get(param, {}))
    if param == "spo2" and patient.get("hypercapnic_risk"):
        d["normal"] = [88, 92]
        d["note"] = "Prescribed target 88-92% (NEWS2 scale 2)"
    return d


# ---------------------------------------------------------------------------
# Red-flag phrases for free-text reports. A transparent keyword screen, the
# same idea as a triage line's red-flag questions. Claude reads the full text.
# ---------------------------------------------------------------------------

RED_FLAGS = [
    ("chest pain", 1.0), ("slurred", 1.0), ("face drooping", 1.0), ("facial droop", 1.0),
    ("unresponsive", 1.0), ("not breathing", 1.0), ("arm weak", 0.95), ("can't breathe", 0.95),
    ("cannot breathe", 0.95), ("blue lips", 0.95), ("seizure", 0.95), ("passed out", 0.9),
    ("fainted", 0.9), ("confused", 0.75), ("confusion", 0.75), ("short of breath", 0.7),
    ("bleeding", 0.7), ("fell", 0.7), ("breathless", 0.65), ("vomiting", 0.5), ("fever", 0.5),
    ("redder", 0.5), ("dizzy", 0.5), ("swollen", 0.45), ("more pain", 0.45),
]


def red_flags(text: str):
    t = (text or "").lower()
    return [(phrase, w) for phrase, w in RED_FLAGS if phrase in t]


# ---------------------------------------------------------------------------
# Factor 1: Severity
# ---------------------------------------------------------------------------

def severity_vital(param, value, patient, baseline, slope_per_day, cfg, payload=None):
    """Severity of a vital-sign reading.

    S_b  = band / 3                                   population threshold
    z    = |x - median| / robust_sd                   personal deviation
    S_z  = sigmoid(k * (z - z0))
    S_b' = S_b * (1 - trust * (1 - S_z))              discount readings normal for this patient
    S_t  = 1 - exp(-max(0, worsening slope / sd) / t_scale)
    S    = 1 - (1 - S_b')(1 - w_z * S_z)(1 - w_t * S_t)     noisy-OR
    """
    band, rule = vital_band(param, value, patient, payload)
    s_b = band / 3.0
    detail = {"band": band, "band_rule": rule, "s_band": s_b, "baseline": None,
              "z": None, "s_dev": 0.0, "slope_per_day": slope_per_day, "s_trend": 0.0}

    s_dev = 0.0
    s_trend = 0.0
    if baseline:
        mu, sd = baseline
        z = abs(value - mu) / sd
        s_dev = sigmoid(cfg["deviation_steepness"] * (z - cfg["deviation_midpoint"]))
        detail.update({"baseline": {"median": mu, "sd": sd}, "z": z, "s_dev": s_dev})
        if slope_per_day is not None:
            direction = 1.0 if value >= mu else -1.0
            worsening = max(0.0, slope_per_day * direction / sd)
            s_trend = 1.0 - math.exp(-worsening / cfg["trend_scale"])
            detail["s_trend"] = s_trend
            detail["trend_sd_per_day"] = worsening

    trust = cfg["personal_baseline_trust"] if baseline else 0.0
    s_b_personal = s_b * (1.0 - trust * (1.0 - s_dev))
    s = 1.0 - (1.0 - s_b_personal) * (1.0 - cfg["deviation_weight"] * s_dev) * (1.0 - cfg["trend_weight"] * s_trend)
    detail["s_band_personal"] = s_b_personal
    detail["severity"] = clamp(s)
    detail["critical_band"] = band == 3
    return clamp(s), detail


def severity_event(code: str, base: float, payload: dict, messages: list[str]):
    """Severity of a non-vital event: catalog base, payload modifiers, red-flag phrases."""
    s = base
    critical = False
    notes = []
    payload = payload or {}

    if code == "FALL":
        resp = payload.get("response")
        if resp == "no_response":
            s, critical = max(s, 1.0), True
            notes.append("No response to the pendant's voice check")
        elif resp == "help_needed":
            s, critical = max(s, 0.95), True
            notes.append("Patient asked for help on the voice check")
        elif resp == "ok":
            s *= 0.55
            notes.append("Patient said they were OK on the voice check")
    elif code == "PANIC":
        if payload.get("response") == "no_answer":
            s, critical = max(s, 0.95), True
            notes.append("Call-back went unanswered")
    elif code == "WANDERING":
        if payload.get("night"):
            s = min(1.0, s + 0.1)
            notes.append("Overnight exit")
        if payload.get("alone"):
            s = min(1.0, s + 0.1)
            notes.append("No caregiver at home")
    elif code == "MISSED_MED":
        risk = payload.get("med_risk")
        if risk in ("high-alert", "time-critical"):
            s = min(1.0, s + 0.25)
            notes.append(f"{risk.capitalize()} medication")
    elif code == "MISSED_VISIT":
        if payload.get("no_contact"):
            s = min(1.0, s + 0.1)
            notes.append("Patient not reachable by phone")
    elif code == "O2_EQUIPMENT":
        if payload.get("oxygen_dependent"):
            s = min(1.0, s + 0.15)
            notes.append("Patient depends on continuous oxygen")

    flags = []
    for m in messages:
        flags.extend(red_flags(m))
    if flags:
        top = max(w for _, w in flags)
        if top > s:
            s = top
        if top >= 0.95:
            critical = True
        notes.append("Red-flag phrase: " + ", ".join(sorted({p for p, _ in flags})))

    s = clamp(s)
    return s, {"base": base, "notes": notes, "red_flags": sorted({p for p, _ in flags}),
               "severity": s, "critical_event": critical}


# ---------------------------------------------------------------------------
# Factor 2: Vulnerability
# ---------------------------------------------------------------------------

def vulnerability(patient: dict, cfg: dict, days_since_discharge: float | None):
    """V = 1 - exp(-H), where H adds up patient risk factors."""
    a = patient.get("acuity", 2)
    age = patient.get("age", 65)
    m = len(patient.get("conditions") or [])
    terms = [
        (f"Acuity {a}/5", cfg["theta_acuity"] * (a - 1) / 4.0),
        (f"Age {age}", cfg["theta_age"] * clamp((age - 65) / 30.0)),
        (f"{m} chronic condition{'s' if m != 1 else ''}", cfg["theta_comorbidity"] * min(m, 6) / 6.0),
    ]
    if patient.get("lives_alone"):
        terms.append(("Lives alone", cfg["theta_lives_alone"]))
    if days_since_discharge is not None and days_since_discharge >= 0:
        decay = math.exp(-days_since_discharge / cfg["discharge_decay_days"])
        terms.append((f"Discharged {int(round(days_since_discharge))} days ago", cfg["theta_discharge"] * decay))
    if patient.get("fall_history"):
        terms.append(("Fall history", cfg["theta_fall_history"]))
    if patient.get("cognitive_impairment"):
        terms.append(("Cognitive impairment", cfg["theta_cognition"]))
    h = sum(t[1] for t in terms)
    v = 1.0 - math.exp(-h)
    return v, {"hazard": h, "terms": [{"label": l, "value": round(x, 4)} for l, x in terms if x > 0], "vulnerability": v}


# ---------------------------------------------------------------------------
# Factor 3: Credibility
# ---------------------------------------------------------------------------

def credibility_base(prior_rate, type_counts, patient_counts, duration_s, repeats, quality, cfg):
    """Credibility before corroboration.

    r_type = Beta posterior for the alert type across all patients
    r      = Beta posterior for this patient, shrunk toward r_type (partial pooling)
    q_dur  = 1 - exp(-duration / tau)            short blips look like artifact
    q_rep  = 1 - (1 - q_dur)(1 - rho)^(n - 1)     repeats add persistence
    C_base = r * q_rep * quality
    """
    a_t, f_t = type_counts
    a_p, f_p = patient_counts
    r_type = beta_posterior(prior_rate, cfg["prior_strength"], a_t, f_t)
    r = beta_posterior(r_type, cfg["patient_prior_strength"], a_p, f_p)
    if duration_s is None:
        q_dur = 1.0
    else:
        q_dur = 1.0 - math.exp(-max(duration_s, 0.0) / cfg["artifact_time_constant"])
    q_rep = 1.0 - (1.0 - q_dur) * (1.0 - cfg["repeat_boost"]) ** max(repeats - 1, 0)
    q = clamp(quality if quality is not None else 1.0)
    c = r * q_rep * q
    return clamp(c), {
        "prior_rate": prior_rate,
        "type_actionable": a_t, "type_false": f_t, "r_type": r_type,
        "patient_actionable": a_p, "patient_false": f_p, "r_patient": r,
        "duration_s": duration_s, "q_duration": q_dur, "repeats": repeats, "q_repeat": q_rep,
        "signal_quality": q, "credibility_base": c,
    }


SYSTEM_RELATEDNESS_CROSS = 0.5


def relatedness(system_a: str, system_b: str) -> float:
    if "device" in (system_a, system_b):
        return 0.0
    if system_a == system_b:
        return 1.0
    return SYSTEM_RELATEDNESS_CROSS


def corroborate(c_base: float, others: list[dict], cfg: dict):
    """Noisy-OR: C = 1 - (1 - C_base) * prod(1 - kappa * rel_j * c_j)."""
    remaining = 1.0 - c_base
    used = []
    for o in others:
        lift = cfg["corroboration_strength"] * o["rel"] * o["c_base"]
        if lift <= 0:
            continue
        remaining *= 1.0 - lift
        used.append({**o, "lift": lift})
    return clamp(1.0 - remaining), used


# ---------------------------------------------------------------------------
# Factor 4 and 5: Urgency and waiting
# ---------------------------------------------------------------------------

def urgency(time_to_harm_min: float, cfg: dict) -> float:
    t_ref = cfg["reference_time_min"]
    return t_ref / (t_ref + max(time_to_harm_min, 0.0))


def waiting(minutes_unacknowledged: float, cfg: dict) -> float:
    return 1.0 - math.exp(-max(minutes_unacknowledged, 0.0) / cfg["escalation_time_constant_min"])


# ---------------------------------------------------------------------------
# Combine
# ---------------------------------------------------------------------------

CREDIBILITY_FLOOR = 0.005  # keeps ln(C) finite


def combine(S: float, C: float, V: float, U: float, W: float, cfg: dict):
    """Logistic scorecard. Returns (z, model_score, contributions).

    z = b0 + bS*S + bC*ln(C) + bV*V + bU*U*S + bW*W
    """
    contributions = [
        {"key": "baseline", "label": "Starting point", "value": cfg["beta_0"]},
        {"key": "severity", "label": "Severity", "value": cfg["beta_severity"] * S},
        {"key": "credibility", "label": "Credibility", "value": cfg["beta_credibility"] * math.log(max(C, CREDIBILITY_FLOOR))},
        {"key": "vulnerability", "label": "Vulnerability", "value": cfg["beta_vulnerability"] * V},
        {"key": "urgency", "label": "Urgency x severity", "value": cfg["beta_urgency"] * U * S},
        {"key": "waiting", "label": "Waiting time", "value": cfg["beta_waiting"] * W},
    ]
    z = sum(c["value"] for c in contributions)
    return z, 100.0 * sigmoid(z), contributions


def tier_for(score: float, cfg: dict) -> str:
    if score >= cfg["tier_critical"]:
        return "critical"
    if score >= cfg["tier_high"]:
        return "high"
    if score >= cfg["tier_medium"]:
        return "medium"
    return "low"


def apply_floor(model_score: float, floor_triggered: bool, cfg: dict):
    if cfg["safety_floor_enabled"] >= 0.5 and floor_triggered and model_score < cfg["safety_floor"]:
        return cfg["safety_floor"], True
    return model_score, False


def final_score(model_score: float, floor_triggered: bool, claude: dict | None, cfg: dict):
    """Blend Claude's score in and re-apply the safety rules.

    final = (1 - lambda) * model + lambda * claude
    Claude may lift an alert to critical (escalation is the safe direction).
    Claude may never take a floored alert below the floor.
    """
    notes = []
    score = model_score
    use_claude = claude and cfg["claude_enabled"] >= 0.5 and claude.get("score") is not None
    if use_claude:
        lam = cfg["claude_blend"]
        score = (1 - lam) * model_score + lam * float(claude["score"])
        if abs(score - model_score) >= 0.5:
            notes.append(f"Blended with Claude ({int(round(lam * 100))}% weight)")
        if (
            cfg["claude_can_escalate"] >= 0.5
            and claude.get("priority") == "critical"
            and claude.get("agreement") == "escalate"
            and claude.get("confidence") in ("medium", "high")
            and score < cfg["tier_critical"]
        ):
            score = cfg["tier_critical"]
            notes.append("Claude escalated to critical")
    if cfg["safety_floor_enabled"] >= 0.5 and floor_triggered and score < cfg["safety_floor"]:
        score = cfg["safety_floor"]
        notes.append("Safety floor held")
    return clamp(score, 0.0, 100.0), notes
