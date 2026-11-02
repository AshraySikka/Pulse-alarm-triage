"""
Engine: pulls what the math needs out of the database and scores incidents.

The scoring itself lives in scoring.py as pure functions. This module builds
the context (baselines, trends, learned outcome counts, other alerts on the
same patient) in a handful of queries, then runs two passes:

  1. severity, vulnerability, urgency, waiting and base credibility per incident
  2. corroboration across a patient's active incidents, then the final score
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from . import scoring as sc
from .models import Alert, Incident, ModelConfig, Patient, VitalReading

BASELINE_DAYS = 14
BASELINE_EXCLUDE_HOURS = 24
TREND_HOURS = 48
FEEDBACK_DAYS = 30
CHATTER_WINDOW_MIN = 10


def patient_dict(p: Patient) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "initials": p.initials,
        "age": p.age,
        "sex": p.sex,
        "acuity": p.acuity,
        "conditions": p.conditions or [],
        "medications": p.medications or [],
        "lives_alone": p.lives_alone,
        "discharge_date": p.discharge_date.isoformat() if p.discharge_date else None,
        "fall_history": p.fall_history,
        "cognitive_impairment": p.cognitive_impairment,
        "hypercapnic_risk": p.hypercapnic_risk,
        "on_oxygen": p.on_oxygen,
        "goals_of_care": p.goals_of_care,
        "care_notes": p.care_notes,
        "neighbourhood": p.neighbourhood,
        "care_team": p.care_team,
        "devices": p.devices or [],
    }


def days_since(d, now):
    if not d:
        return None
    return (timezone.localtime(now).date() - d).days


def fmt_value(param, value, decimals=None):
    if value is None:
        return ""
    d = sc.PARAM_DISPLAY.get(param, {}).get("decimals", 1) if decimals is None else decimals
    return f"{value:.{d}f}"


def describe(alert_type, alert, patient) -> tuple[str, str]:
    """Human title and short value text for an incident's index alert."""
    code = alert_type.code
    v = alert.value
    p = alert.payload or {}
    if code == "SPO2":
        return "Low oxygen saturation", f"SpO2 {fmt_value('spo2', v)}%"
    if code == "HR":
        return ("High heart rate" if (v or 0) > 90 else "Low heart rate"), f"{fmt_value('hr', v)} bpm"
    if code in ("SBP_HIGH", "SBP_LOW"):
        return ("High blood pressure" if (v or 0) > 140 else "Low blood pressure"), f"{fmt_value('sbp', v)} mmHg systolic"
    if code == "TEMP":
        return ("Fever" if (v or 0) >= 38.1 else "Low temperature"), f"{fmt_value('temp', v)} °C"
    if code == "RR":
        return ("Fast breathing" if (v or 0) > 20 else "Slow breathing"), f"{fmt_value('rr', v)} breaths/min"
    if code in ("GLUCOSE_LOW", "GLUCOSE_HIGH"):
        return ("Low blood glucose" if (v or 0) < 4 else "High blood glucose"), f"{fmt_value('glucose', v)} mmol/L"
    if code == "WEIGHT":
        delta = float(p.get("delta_kg", 0))
        return "Rapid weight gain", f"+{delta:.1f} kg in 24 h"
    if code == "FALL":
        resp = p.get("response")
        extra = {"no_response": "No response to voice check", "help_needed": "Asked for help",
                 "ok": "Said they were OK"}.get(resp, "Voice check pending")
        return "Fall detected", extra
    if code == "PANIC":
        return "Help button pressed", "Call-back unanswered" if p.get("response") == "no_answer" else "Call-back pending"
    if code == "MISSED_MED":
        return "Missed medication dose", p.get("medication", "Scheduled dose not taken")
    if code == "MISSED_VISIT":
        return "Visit not checked in", p.get("visit", "Scheduled visit")
    if code in ("DEVICE_OFFLINE", "LOW_BATTERY", "O2_EQUIPMENT"):
        return alert_type.name, alert.message or alert.source
    # SYMPTOM, WANDERING, INACTIVITY and anything else: the message carries it
    return alert_type.name, alert.message or alert_type.name


def chattering(times) -> bool:
    """ISA-18.2 style chattering: 3 or more alerts inside a short window."""
    ts = sorted(times)
    j = 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > timedelta(minutes=CHATTER_WINDOW_MIN):
            j += 1
        if i - j + 1 >= 3:
            return True
    return False


class Context:
    """Everything the scorer needs for a set of incidents, loaded once."""

    def __init__(self, targets, now=None):
        self.now = now or timezone.now()
        targets = list(targets)
        patient_ids = {i.patient_id for i in targets}

        # The corroboration pool: every active incident for the same patients.
        pool = {i.id: i for i in targets}
        for inc in Incident.objects.filter(patient_id__in=patient_ids, status__in=Incident.ACTIVE).select_related("alert_type"):
            pool.setdefault(inc.id, inc)
        self.pool = list(pool.values())
        self.targets = targets

        self.patients = {p.id: p for p in Patient.objects.filter(id__in=patient_ids)}
        self.patient_dicts = {pid: patient_dict(p) for pid, p in self.patients.items()}

        self.alerts = defaultdict(list)
        for a in Alert.objects.filter(incident_id__in=list(pool.keys())).order_by("raised_at"):
            self.alerts[a.incident_id].append(a)

        params = {i.alert_type.param for i in self.pool if i.alert_type.param}
        self.baselines = {}
        self.trend_points = defaultdict(list)
        if params:
            since = self.now - timedelta(days=BASELINE_DAYS)
            base_cut = self.now - timedelta(hours=BASELINE_EXCLUDE_HOURS)
            trend_cut = self.now - timedelta(hours=TREND_HOURS)
            series = defaultdict(list)
            for pid, param, value, taken in VitalReading.objects.filter(
                patient_id__in=patient_ids, param__in=params, taken_at__gte=since
            ).values_list("patient_id", "param", "value", "taken_at"):
                series[(pid, param)].append((taken, value))
            for key, rows in series.items():
                base_vals = [v for t, v in rows if t < base_cut]
                self.baselines[key] = sc.robust_baseline(base_vals, sc.SIGMA_MIN.get(key[1], 1.0))
                self.trend_points[key] = [
                    ((t - trend_cut).total_seconds() / 3600.0, v) for t, v in rows if t >= trend_cut
                ]

        cutoff = self.now - timedelta(days=FEEDBACK_DAYS)
        self.type_counts = defaultdict(lambda: [0, 0])
        self.patient_counts = defaultdict(lambda: [0, 0])
        rows = (
            Incident.objects.filter(outcome_actionable__isnull=False, closed_at__gte=cutoff)
            .values("alert_type_id", "patient_id", "outcome_actionable")
            .annotate(n=Count("id"))
        )
        for r in rows:
            idx = 0 if r["outcome_actionable"] else 1
            self.type_counts[r["alert_type_id"]][idx] += r["n"]
            self.patient_counts[(r["patient_id"], r["alert_type_id"])][idx] += r["n"]


def _index_alert_vital(inc, alerts, patient):
    """The reading that speaks for the incident: the worst band, but a poor-signal
    reading counts one band lower, so a probe-off artifact can't speak for a run
    of plausible readings. Ties go to the most recent."""
    best, best_key = None, None
    for a in alerts:
        if a.value is None:
            continue
        band, _ = sc.vital_band(inc.alert_type.param, a.value, patient, a.payload)
        effective = band if (a.signal_quality or 0) >= 0.5 else max(band - 1, 0)
        key = (effective, band)
        if best_key is None or key >= best_key:
            best, best_key = a, key
    return best or (alerts[-1] if alerts else None)


def _plausible(alert, cfg) -> bool:
    """Good enough to trigger the safety floor: decent signal and not a blip."""
    if (alert.signal_quality or 0) < cfg["floor_min_quality"]:
        return False
    if alert.duration_s is not None and alert.duration_s < cfg["artifact_time_constant"] * 0.693:
        return False
    return True


def _first_pass(inc, ctx: Context, cfg: dict) -> dict:
    at = inc.alert_type
    patient = ctx.patient_dicts[inc.patient_id]
    alerts = ctx.alerts.get(inc.id, [])
    now = ctx.now

    if at.param:
        idx = _index_alert_vital(inc, alerts, patient)
    else:
        idx = alerts[-1] if alerts else None

    payload = (idx.payload if idx else {}) or {}
    floor_triggered = False

    if at.param and idx is not None and idx.value is not None:
        key = (inc.patient_id, at.param)
        baseline = ctx.baselines.get(key)
        slope = sc.ols_slope_per_day(ctx.trend_points.get(key, []))
        S, sev = sc.severity_vital(at.param, idx.value, patient, baseline, slope, cfg, payload)
        sev["kind"] = "vital"
        sev["param"] = at.param
        sev["value"] = idx.value
        floor_triggered = sev["critical_band"] and _plausible(idx, cfg)
        sev["plausible"] = _plausible(idx, cfg)
    else:
        messages = [a.message for a in alerts if a.message]
        S, sev = sc.severity_event(at.code, at.base_severity, payload, messages)
        sev["kind"] = "event"
        floor_triggered = sev["critical_event"]

    days = days_since(ctx.patients[inc.patient_id].discharge_date, now)
    V, vul = sc.vulnerability(patient, cfg, days)

    durations = [a.duration_s for a in alerts if a.duration_s is not None]
    duration = max(durations) if durations else None
    quality = idx.signal_quality if idx else 1.0
    c_base, cred = sc.credibility_base(
        at.prior_actionable_rate,
        tuple(ctx.type_counts[at.id]),
        tuple(ctx.patient_counts[(inc.patient_id, at.id)]),
        duration,
        max(inc.alert_count, len(alerts), 1),
        quality,
        cfg,
    )

    U = sc.urgency(at.time_to_harm_min, cfg)
    stop = inc.acknowledged_at or inc.closed_at or now
    wait_min = max(0.0, (stop - inc.first_seen).total_seconds() / 60.0)
    W = sc.waiting(wait_min, cfg)

    title, value_text = describe(at, idx, patient) if idx else (at.name, "")
    return {
        "inc": inc, "idx": idx, "S": S, "sev": sev, "V": V, "vul": vul, "c_base": c_base, "cred": cred,
        "U": U, "W": W, "wait_min": wait_min, "floor_triggered": bool(floor_triggered),
        "title": title, "value_text": value_text,
        "chattering": chattering([a.raised_at for a in alerts]),
    }


def _overlaps(a: Incident, b: Incident, window_min: float) -> bool:
    w = timedelta(minutes=window_min)
    return b.last_seen >= a.first_seen - w and b.first_seen <= a.last_seen + w


def score_incidents(incidents, cfg: dict | None = None, now=None, ctx: Context | None = None):
    """Score incidents. Returns a dict {incident_id: result}."""
    cfg = cfg or ModelConfig.current()
    incidents = list(incidents)
    if not incidents:
        return {}
    ctx = ctx or Context(incidents, now)

    first = {inc.id: _first_pass(inc, ctx, cfg) for inc in ctx.pool}
    by_patient = defaultdict(list)
    for inc in ctx.pool:
        if inc.status in Incident.ACTIVE:
            by_patient[inc.patient_id].append(inc)

    results = {}
    for inc in incidents:
        f = first[inc.id]
        others = []
        for other in by_patient[inc.patient_id]:
            if other.id == inc.id or not _overlaps(inc, other, cfg["corroboration_window_min"]):
                continue
            rel = sc.relatedness(inc.alert_type.system, other.alert_type.system)
            if rel <= 0:
                continue
            fo = first[other.id]
            others.append({
                "incident_id": other.id, "title": fo["title"], "value_text": fo["value_text"],
                "system": other.alert_type.system, "rel": rel, "c_base": fo["c_base"],
            })
        C, used = sc.corroborate(f["c_base"], others, cfg)
        S, V, U, W = f["S"], f["V"], f["U"], f["W"]
        z, model_raw, contributions = sc.combine(S, C, V, U, W, cfg)
        model_score, floor_applied = sc.apply_floor(model_raw, f["floor_triggered"], cfg)
        claude = inc.claude_judgement if inc.claude_judgement else None
        final, notes = sc.final_score(model_score, f["floor_triggered"], claude, cfg)

        idx = f["idx"]
        rng = None
        param = inc.alert_type.param
        if param and idx is not None and idx.value is not None:
            rng = sc.display_range(param, ctx.patient_dicts[inc.patient_id])
            shown = float((idx.payload or {}).get("delta_kg", 0.0)) if param == "weight" else idx.value
            rng["value"] = shown
            base = ctx.baselines.get((inc.patient_id, param))
            if base and param != "weight":
                rng["baseline"] = base[0]
                rng["baseline_sd"] = base[1]
            rng["param"] = param

        f["cred"]["credibility"] = C
        results[inc.id] = {
            "id": inc.id,
            "score": round(final, 1),
            "model_score": round(model_score, 1),
            "model_raw": round(model_raw, 1),
            "tier": sc.tier_for(final, cfg),
            "model_tier": sc.tier_for(model_score, cfg),
            "z": round(z, 3),
            "floor_triggered": f["floor_triggered"],
            "floor_applied": floor_applied,
            "final_notes": notes,
            "factors": {"severity": round(S, 4), "credibility": round(C, 4), "vulnerability": round(V, 4),
                        "urgency": round(U, 4), "waiting": round(W, 4)},
            "contributions": [{**c, "value": round(c["value"], 4)} for c in contributions],
            "severity_detail": f["sev"],
            "credibility_detail": f["cred"],
            "vulnerability_detail": f["vul"],
            "urgency_detail": {"time_to_harm_min": inc.alert_type.time_to_harm_min,
                               "reference_time_min": cfg["reference_time_min"], "urgency": U},
            "waiting_detail": {"minutes": round(f["wait_min"], 1),
                               "tau": cfg["escalation_time_constant_min"], "waiting": W},
            "corroborators": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in u.items()} for u in used],
            "corroboration_lift": round(C - f["c_base"], 4),
            "title": f["title"],
            "value_text": f["value_text"],
            "range": rng,
            "chattering": f["chattering"],
            "index_alert_id": idx.id if idx else None,
        }
    return results


def active_incidents():
    return Incident.objects.filter(status__in=Incident.ACTIVE).select_related("alert_type", "patient")


def rank(results: dict) -> list[int]:
    """Incident ids ordered by final score, then model score, then most recent."""
    return [r["id"] for r in sorted(results.values(), key=lambda r: (-r["score"], -r["model_score"], -r["id"]))]


def learned_rates(now=None, cfg=None):
    """Prior vs learned actionable rate per alert type, for the Insights page."""
    from .models import AlertType

    cfg = cfg or ModelConfig.current()
    now = now or timezone.now()
    cutoff = now - timedelta(days=FEEDBACK_DAYS)
    counts = defaultdict(lambda: [0, 0])
    for r in (
        Incident.objects.filter(outcome_actionable__isnull=False, closed_at__gte=cutoff)
        .values("alert_type_id", "outcome_actionable")
        .annotate(n=Count("id"))
    ):
        counts[r["alert_type_id"]][0 if r["outcome_actionable"] else 1] += r["n"]
    out = []
    for at in AlertType.objects.all():
        a, f = counts[at.id]
        out.append({
            "code": at.code, "name": at.name, "category": at.category,
            "prior": at.prior_actionable_rate, "actionable": a, "false_alarms": f,
            "learned": sc.beta_posterior(at.prior_actionable_rate, cfg["prior_strength"], a, f),
        })
    return out
