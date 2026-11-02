"""
REST API for the React app. Function views keep it easy to read.

Everything under /api/ is open on localhost for the demo. See the README for
what to add before this touches real data.
"""
from __future__ import annotations

import threading
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import claude_judge
from . import scoring as sc
from .engine import (
    FEEDBACK_DAYS, Context, active_incidents, learned_rates, patient_dict, rank, score_incidents,
)
from .ingest import ingest_alert
from .models import Alert, AlertType, Incident, IncidentEvent, ModelConfig, Patient, VitalReading
from .seed import seed_demo
from .simulator import SCENARIOS, run_scenario, tick

WINDOW_HOURS = 8
BUCKET_MIN = 10
FLOOD_PER_BUCKET = 10  # ISA-18.2 alarm flood: more than 10 alarms in 10 minutes per operator
STALE_HOURS = 4

_reseed_lock = threading.Lock()


def _refresh_if_stale():
    """Hosted demo only (PULSE_AUTO_RESEED_HOURS): load a fresh shift when the data has aged out."""
    hours = settings.AUTO_RESEED_HOURS
    if not hours:
        return

    def stale():
        newest = Alert.objects.order_by("-raised_at").values_list("raised_at", flat=True).first()
        return newest is None or newest < timezone.now() - timedelta(hours=hours)

    if stale():
        with _reseed_lock:
            if stale():  # another request may have just done it
                seed_demo(keep_config=True)


# ---------------------------------------------------------------------------
# Serializers (plain functions)
# ---------------------------------------------------------------------------

def _claude_summary(inc: Incident):
    j = inc.claude_judgement
    if not j:
        return None
    return {
        "priority": j.get("priority"),
        "score": j.get("score"),
        "agreement": j.get("agreement"),
        "headline": j.get("headline"),
        "confidence": j.get("confidence"),
        "notify": j.get("notify"),
        "respond_within_minutes": j.get("respond_within_minutes"),
        "likely_false_alarm": j.get("likely_false_alarm"),
        "judged_at": inc.claude_judged_at,
        "model": inc.claude_model,
        "stale": bool(inc.claude_judged_at and inc.last_seen > inc.claude_judged_at),
    }


def _row(inc: Incident, res: dict, pd: dict) -> dict:
    return {
        "id": inc.id,
        "status": inc.status,
        "patient": {
            "id": pd["id"], "name": pd["name"], "initials": pd["initials"], "age": pd["age"],
            "acuity": pd["acuity"], "lives_alone": pd["lives_alone"], "goals_of_care": pd["goals_of_care"],
            "neighbourhood": pd["neighbourhood"],
        },
        "alert_type": {"code": inc.alert_type.code, "name": inc.alert_type.name,
                       "category": inc.alert_type.category, "system": inc.alert_type.system},
        "title": res["title"],
        "value_text": res["value_text"],
        "range": res["range"],
        "first_seen": inc.first_seen,
        "last_seen": inc.last_seen,
        "alert_count": inc.alert_count,
        "chattering": res["chattering"],
        "score": res["score"],
        "model_score": res["model_score"],
        "tier": res["tier"],
        "model_tier": res["model_tier"],
        "floor_applied": res["floor_applied"],
        "floor_triggered": res["floor_triggered"],
        "final_notes": res["final_notes"],
        "factors": res["factors"],
        # Only flag corroboration when it moved credibility by a meaningful amount.
        "corroborated": res["corroboration_lift"] >= 0.10,
        "claude": _claude_summary(inc),
        "acknowledged_at": inc.acknowledged_at,
        "assigned_to": inc.assigned_to,
    }


def _timeline(now, start, results):
    n = int(WINDOW_HOURS * 60 / BUCKET_MIN)
    buckets = [{"start": start + timedelta(minutes=i * BUCKET_MIN), "count": 0,
                "tiers": {"critical": 0, "high": 0, "medium": 0, "low": 0, "closed": 0}} for i in range(n)]
    for iid, raised in Alert.objects.filter(raised_at__gte=start).values_list("incident_id", "raised_at"):
        i = int((raised - start).total_seconds() // (BUCKET_MIN * 60))
        if 0 <= i < n:
            buckets[i]["count"] += 1
            tier = results[iid]["tier"] if iid in results else "closed"
            buckets[i]["tiers"][tier] += 1
    return {"bucket_minutes": BUCKET_MIN, "flood_threshold": FLOOD_PER_BUCKET, "buckets": buckets,
            "start": start, "end": now}


def _cfg_payload():
    obj = ModelConfig.load()
    return {
        "params": ModelConfig.current(),
        "defaults": sc.DEFAULT_PARAMS,
        "groups": [{"key": k, "name": n, "help": h} for k, n, h in sc.PARAM_GROUPS],
        "schema": [
            {"group": g, "key": k, "label": l, "help": h, "default": d, "min": lo, "max": hi, "step": st, "kind": kind}
            for g, k, l, h, d, lo, hi, st, kind in sc.PARAM_SCHEMA
        ],
        "presets": sc.PRESETS,
        "updated_at": obj.updated_at,
        "updated_by": obj.updated_by,
    }


def _learned_for(inc: Incident, cfg: dict, now):
    cutoff = now - timedelta(days=FEEDBACK_DAYS)
    base = Incident.objects.filter(alert_type=inc.alert_type, outcome_actionable__isnull=False, closed_at__gte=cutoff)
    ta = base.filter(outcome_actionable=True).count()
    tf = base.filter(outcome_actionable=False).count()
    mine = base.filter(patient=inc.patient)
    pa = mine.filter(outcome_actionable=True).count()
    pf = mine.filter(outcome_actionable=False).count()
    r_type = sc.beta_posterior(inc.alert_type.prior_actionable_rate, cfg["prior_strength"], ta, tf)
    r_patient = sc.beta_posterior(r_type, cfg["patient_prior_strength"], pa, pf)
    return {"type": r_type, "patient": r_patient}


def _results_for_patient(inc: Incident, cfg: dict, now):
    """Score an incident together with the patient's other active incidents."""
    group = list(Incident.objects.filter(patient=inc.patient, status__in=Incident.ACTIVE)
                 .select_related("alert_type", "patient"))
    if inc.id not in {i.id for i in group}:
        group.append(inc)
    ctx = Context(group, now)
    return score_incidents(group, cfg, now, ctx), ctx, group


def _prompt(inc: Incident, results: dict, ctx: Context, group) -> str:
    res = results[inc.id]
    related = [
        {"title": results[o.id]["title"], "value_text": results[o.id]["value_text"],
         "score": results[o.id]["score"], "tier": results[o.id]["tier"]}
        for o in group if o.id != inc.id and o.patient_id == inc.patient_id and o.status in Incident.ACTIVE
        and o.id in results
    ]
    return claude_judge.build_prompt(inc, res, ctx.patient_dicts[inc.patient_id], ctx.alerts.get(inc.id, []), related)


def _detail(inc: Incident, extra: dict | None = None):
    cfg = ModelConfig.current()
    now = timezone.now()
    inc.refresh_from_db()
    results, ctx, group = _results_for_patient(inc, cfg, now)
    res = results[inc.id]
    pd = ctx.patient_dicts[inc.patient_id]

    vitals = None
    param = inc.alert_type.param
    if param:
        since = now - timedelta(days=7)
        series = list(VitalReading.objects.filter(patient=inc.patient, param=param, taken_at__gte=since)
                      .values("taken_at", "value", "source"))
        base = ctx.baselines.get((inc.patient_id, param))
        alerts_pts = [{"taken_at": a.raised_at, "value": a.value, "quality": a.signal_quality}
                      for a in ctx.alerts.get(inc.id, []) if a.value is not None]
        vitals = {
            "param": param,
            "display": sc.display_range(param, pd),
            "series": series,
            "alert_points": alerts_pts,
            "baseline": {"median": base[0], "sd": base[1]} if base else None,
        }

    related = []
    for o in group:
        if o.id == inc.id or o.status not in Incident.ACTIVE:
            continue
        r = results[o.id]
        related.append({"id": o.id, "title": r["title"], "value_text": r["value_text"], "score": r["score"],
                        "tier": r["tier"], "alert_type": o.alert_type.name, "last_seen": o.last_seen})
    related.sort(key=lambda r: -r["score"])

    alerts = [
        {"id": a.id, "raised_at": a.raised_at, "value": a.value, "message": a.message, "source": a.source,
         "signal_quality": a.signal_quality, "duration_s": a.duration_s, "payload": a.payload}
        for a in reversed(ctx.alerts.get(inc.id, []))
    ]
    events = [{"kind": e.kind, "message": e.message, "actor": e.actor, "created_at": e.created_at}
              for e in inc.events.all()]

    out = _row(inc, res, pd)
    out.update({
        "explanation": res,
        "params": cfg,
        "patient_full": pd,
        "alerts": alerts,
        "events": events,
        "vitals": vitals,
        "related": related,
        "closed_at": inc.closed_at,
        "outcome_actionable": inc.outcome_actionable,
        "score_snapshot": inc.score_snapshot,
        "tier_snapshot": inc.tier_snapshot,
        "claude_full": inc.claude_judgement,
        "claude_judged_at": inc.claude_judged_at,
        "claude_model": inc.claude_model,
        "claude_error": inc.claude_error,
        "alert_type_full": {
            "code": inc.alert_type.code, "name": inc.alert_type.name, "description": inc.alert_type.description,
            "time_to_harm_min": inc.alert_type.time_to_harm_min,
            "prior_actionable_rate": inc.alert_type.prior_actionable_rate,
            "base_severity": inc.alert_type.base_severity,
        },
    })
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

@api_view(["GET"])
def queue(request):
    _refresh_if_stale()
    now = timezone.now()
    cfg = ModelConfig.current()
    incs = list(active_incidents())
    ctx = Context(incs, now) if incs else None
    results = score_incidents(incs, cfg, now, ctx) if incs else {}
    by_id = {i.id: i for i in incs}
    rows = [_row(by_id[iid], results[iid], ctx.patient_dicts[by_id[iid].patient_id]) for iid in rank(results)]

    start = now - timedelta(hours=WINDOW_HOURS)
    raw = Alert.objects.filter(raised_at__gte=start).count()
    touched = Incident.objects.filter(last_seen__gte=start).count()
    tiers = Counter(r["tier"] for r in rows)
    need_now = sum(1 for r in rows if r["tier"] in ("critical", "high") and r["status"] == Incident.OPEN)
    claude = claude_judge.status()
    claude["enabled"] = cfg["claude_enabled"] >= 0.5
    return Response({
        "generated_at": now,
        "summary": {
            "window_hours": WINDOW_HOURS, "window_start": start, "raw_alerts": raw, "incidents": touched,
            "active": len(rows), "need_now": need_now, "tiers": tiers,
            "noise_reduction": (1 - touched / raw) if raw else 0,
            "acknowledged": sum(1 for r in rows if r["status"] == Incident.ACK),
        },
        "timeline": _timeline(now, start, results),
        "incidents": rows,
        "claude": claude,
        "tiers": {"critical": cfg["tier_critical"], "high": cfg["tier_high"], "medium": cfg["tier_medium"]},
    })


@api_view(["GET"])
def incident_detail(request, pk):
    inc = get_object_or_404(Incident.objects.select_related("alert_type", "patient"), pk=pk)
    return Response(_detail(inc))


@api_view(["POST"])
def incident_action(request, pk):
    inc = get_object_or_404(Incident.objects.select_related("alert_type", "patient"), pk=pk)
    action = request.data.get("action")
    actor = (request.data.get("actor") or "Coordinator").strip()[:80]
    note = (request.data.get("note") or "").strip()
    cfg = ModelConfig.current()
    now = timezone.now()
    before = _learned_for(inc, cfg, now)

    # Snapshot the score at decision time, for the record.
    results, _, _ = _results_for_patient(inc, cfg, now)
    inc.score_snapshot = results[inc.id]["score"]
    inc.tier_snapshot = results[inc.id]["tier"]

    if action == "acknowledge":
        if inc.status != Incident.OPEN:
            return Response({"error": "Only open incidents can be acknowledged."}, status=http.HTTP_400_BAD_REQUEST)
        inc.status = Incident.ACK
        inc.acknowledged_at = now
        inc.assigned_to = actor
        msg = note or "On it."
    elif action in ("resolve", "dismiss"):
        if inc.status not in Incident.ACTIVE:
            return Response({"error": "This incident is already closed."}, status=http.HTTP_400_BAD_REQUEST)
        actionable = action == "resolve"
        inc.status = Incident.RESOLVED if actionable else Incident.DISMISSED
        inc.acknowledged_at = inc.acknowledged_at or now
        inc.closed_at = now
        inc.outcome_actionable = actionable
        msg = note or ("Action taken. Marked as a real alert." if actionable else "Marked as a false alarm.")
    elif action == "reopen":
        if inc.status in Incident.ACTIVE:
            return Response({"error": "This incident is already open."}, status=http.HTTP_400_BAD_REQUEST)
        inc.status = Incident.OPEN
        inc.closed_at = None
        inc.acknowledged_at = None
        inc.outcome_actionable = None
        msg = note or "Reopened."
    else:
        return Response({"error": "Unknown action."}, status=http.HTTP_400_BAD_REQUEST)

    inc.save()
    kind = {"acknowledge": "acknowledged", "resolve": "resolved", "dismiss": "dismissed", "reopen": "reopened"}[action]
    IncidentEvent.objects.create(incident=inc, kind=kind, message=msg, actor=actor, created_at=now)
    after = _learned_for(inc, cfg, now)
    learning = None
    if action in ("resolve", "dismiss", "reopen"):
        learning = {
            "alert_type": inc.alert_type.name, "patient": inc.patient.name,
            "type_before": before["type"], "type_after": after["type"],
            "patient_before": before["patient"], "patient_after": after["patient"],
        }
    return Response(_detail(inc, {"learning": learning}))


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _save_judgement(inc: Incident, data: dict, now):
    inc.claude_judgement = data
    inc.claude_model = settings.CLAUDE_MODEL
    inc.claude_judged_at = now
    inc.claude_error = ""
    inc.save(update_fields=["claude_judgement", "claude_model", "claude_judged_at", "claude_error"])
    verb = {"agree": "agrees", "escalate": "escalates", "de-escalate": "de-escalates"}.get(data["agreement"], "reviewed")
    IncidentEvent.objects.create(
        incident=inc, kind="claude", actor="Claude",
        message=f"Claude {verb}: {data['priority']}, {data['score']}/100. {data['headline']}", created_at=now,
    )


def _claude_budget_exceeded():
    """Optional hourly cap on Claude requests (PULSE_CLAUDE_HOURLY_LIMIT). Returns a 429 response or None."""
    limit = settings.CLAUDE_HOURLY_LIMIT
    if not limit:
        return None
    key = "pulse:claude-requests"
    cache.add(key, 0, 3600)
    if cache.incr(key) > limit:
        return Response(
            {"error": "This demo has reached its hourly limit for Claude. Try again later.", "code": "rate_limited"},
            status=http.HTTP_429_TOO_MANY_REQUESTS,
        )
    return None


@api_view(["GET"])
def claude_status(request):
    s = claude_judge.status()
    s["enabled"] = ModelConfig.current()["claude_enabled"] >= 0.5
    return Response(s)


@api_view(["POST"])
def judge_incident(request, pk):
    cfg = ModelConfig.current()
    if cfg["claude_enabled"] < 0.5:
        return Response({"error": "Claude is turned off. Turn it on from the Model page.", "code": "disabled"},
                        status=http.HTTP_400_BAD_REQUEST)
    inc = get_object_or_404(Incident.objects.select_related("alert_type", "patient"), pk=pk)
    now = timezone.now()
    results, ctx, group = _results_for_patient(inc, cfg, now)
    prompt = _prompt(inc, results, ctx, group)
    if settings.ANTHROPIC_API_KEY and (over := _claude_budget_exceeded()):
        return over
    try:
        data = claude_judge.call_claude(prompt)
    except claude_judge.ClaudeNotConfigured as e:
        return Response({"error": str(e), "code": "not_configured"}, status=http.HTTP_400_BAD_REQUEST)
    except claude_judge.ClaudeError as e:
        inc.claude_error = str(e)
        inc.save(update_fields=["claude_error"])
        return Response({"error": str(e), "code": "claude_error"}, status=http.HTTP_502_BAD_GATEWAY)
    _save_judgement(inc, data, timezone.now())
    return Response(_detail(inc))


@api_view(["GET"])
def claude_prompt_preview(request, pk):
    """The exact text Claude receives for an incident. Useful when a judge asks what the model sees."""
    cfg = ModelConfig.current()
    inc = get_object_or_404(Incident.objects.select_related("alert_type", "patient"), pk=pk)
    results, ctx, group = _results_for_patient(inc, cfg, timezone.now())
    return Response({"system": claude_judge.SYSTEM_PROMPT, "user": _prompt(inc, results, ctx, group),
                     "schema": claude_judge.JUDGEMENT_SCHEMA, "model": settings.CLAUDE_MODEL})


@api_view(["POST"])
def claude_review(request):
    cfg = ModelConfig.current()
    if cfg["claude_enabled"] < 0.5:
        return Response({"error": "Claude is turned off. Turn it on from the Model page.", "code": "disabled"},
                        status=http.HTTP_400_BAD_REQUEST)
    if not claude_judge.status()["configured"]:
        return Response({"error": "Claude is not set up. Add ANTHROPIC_API_KEY to backend/.env and restart the server.",
                         "code": "not_configured"}, status=http.HTTP_400_BAD_REQUEST)
    try:
        limit = max(1, min(12, int(request.data.get("limit", 6))))
    except (TypeError, ValueError):
        limit = 6
    force = bool(request.data.get("force", False))
    now = timezone.now()
    incs = list(active_incidents())
    ctx = Context(incs, now)
    results = score_incidents(incs, cfg, now, ctx)
    by_id = {i.id: i for i in incs}
    picked, skipped = [], 0
    for iid in rank(results):
        inc = by_id[iid]
        fresh = inc.claude_judged_at and inc.claude_judged_at >= inc.last_seen
        if fresh and not force:
            skipped += 1
            continue
        picked.append(inc)
        if len(picked) >= limit:
            break
    prompts = {inc.id: _prompt(inc, results, ctx, incs) for inc in picked}
    if picked and (over := _claude_budget_exceeded()):
        return over
    try:
        judged, errors = claude_judge.judge_prompts(prompts)
    except claude_judge.ClaudeNotConfigured as e:
        return Response({"error": str(e), "code": "not_configured"}, status=http.HTTP_400_BAD_REQUEST)
    done = timezone.now()
    for iid, data in judged.items():
        _save_judgement(by_id[iid], data, done)
    for iid, err in errors.items():
        by_id[iid].claude_error = err
        by_id[iid].save(update_fields=["claude_error"])
    changed = [
        {"id": iid, "title": results[iid]["title"], "patient": by_id[iid].patient.name,
         "agreement": d["agreement"], "priority": d["priority"]}
        for iid, d in judged.items()
    ]
    return Response({"judged": len(judged), "skipped": skipped, "errors": list(set(errors.values())), "items": changed})


# ---------------------------------------------------------------------------
# Model settings
# ---------------------------------------------------------------------------

@api_view(["GET", "PUT"])
def config_view(request):
    if request.method == "PUT":
        obj = ModelConfig.load()
        obj.params = sc.sanitize_params(request.data.get("params", {}))
        obj.updated_by = (request.data.get("actor") or "Admin")[:80]
        obj.save()
    return Response(_cfg_payload())


@api_view(["POST"])
def config_reset(request):
    obj = ModelConfig.load()
    obj.params = dict(sc.DEFAULT_PARAMS)
    obj.updated_by = "Reset to defaults"
    obj.save()
    return Response(_cfg_payload())


@api_view(["POST"])
def config_preview(request):
    """Re-rank the live queue under draft settings without saving them."""
    draft = sc.sanitize_params(request.data.get("params", {}))
    current = ModelConfig.current()
    now = timezone.now()
    incs = list(active_incidents())
    if not incs:
        return Response({"rows": [], "tiers_before": {}, "tiers_after": {}})
    ctx = Context(incs, now)
    before = score_incidents(incs, current, now, ctx)
    after = score_incidents(incs, draft, now, ctx)
    rb = {iid: i + 1 for i, iid in enumerate(rank(before))}
    ra = {iid: i + 1 for i, iid in enumerate(rank(after))}
    by_id = {i.id: i for i in incs}
    rows = []
    for iid in rank(after):
        inc = by_id[iid]
        rows.append({
            "id": iid, "title": after[iid]["title"], "value_text": after[iid]["value_text"],
            "patient": inc.patient.name, "status": inc.status,
            "before_rank": rb[iid], "after_rank": ra[iid],
            "before_score": before[iid]["score"], "after_score": after[iid]["score"],
            "before_tier": before[iid]["tier"], "after_tier": after[iid]["tier"],
            "factors": after[iid]["factors"],
        })
    return Response({
        "rows": rows,
        "tiers_before": Counter(r["before_tier"] for r in rows),
        "tiers_after": Counter(r["after_tier"] for r in rows),
        "moved": sum(1 for r in rows if r["before_rank"] != r["after_rank"]),
    })


@api_view(["GET"])
def alert_types(request):
    cfg = ModelConfig.current()
    learned = {r["code"]: r for r in learned_rates(cfg=cfg)}
    out = []
    for at in AlertType.objects.all():
        out.append({
            "code": at.code, "name": at.name, "category": at.category, "system": at.system, "param": at.param,
            "base_severity": at.base_severity, "time_to_harm_min": at.time_to_harm_min,
            "prior_actionable_rate": at.prior_actionable_rate, "description": at.description,
            "learned": learned.get(at.code),
        })
    return Response(out)


@api_view(["PATCH"])
def alert_type_update(request, code):
    at = get_object_or_404(AlertType, code=code)
    limits = {"base_severity": (0, 1), "time_to_harm_min": (1, 2880), "prior_actionable_rate": (0.01, 0.99)}
    for field, (lo, hi) in limits.items():
        if field in request.data:
            try:
                setattr(at, field, min(hi, max(lo, float(request.data[field]))))
            except (TypeError, ValueError):
                return Response({"error": f"{field} must be a number."}, status=http.HTTP_400_BAD_REQUEST)
    at.save()
    return Response({"code": at.code, "base_severity": at.base_severity, "time_to_harm_min": at.time_to_harm_min,
                     "prior_actionable_rate": at.prior_actionable_rate})


# ---------------------------------------------------------------------------
# Patients and insights
# ---------------------------------------------------------------------------

@api_view(["GET"])
def patients(request):
    _refresh_if_stale()
    cfg = ModelConfig.current()
    now = timezone.now()
    incs = list(active_incidents())
    results = score_incidents(incs, cfg, now) if incs else {}
    per = defaultdict(list)
    for inc in incs:
        per[inc.patient_id].append({"id": inc.id, "title": results[inc.id]["title"],
                                    "score": results[inc.id]["score"], "tier": results[inc.id]["tier"]})
    out = []
    for p in Patient.objects.all():
        days = (timezone.localtime(now).date() - p.discharge_date).days if p.discharge_date else None
        pd = patient_dict(p)
        v, vd = sc.vulnerability(pd, cfg, days)
        items = sorted(per[p.id], key=lambda r: -r["score"])
        pd.update({"vulnerability": v, "vulnerability_terms": vd["terms"], "incidents": items,
                   "top_tier": items[0]["tier"] if items else None,
                   "top_score": items[0]["score"] if items else None})
        out.append(pd)
    out.sort(key=lambda r: (-(r["top_score"] or 0), -r["vulnerability"]))
    return Response(out)


@api_view(["GET"])
def stats(request):
    _refresh_if_stale()
    cfg = ModelConfig.current()
    now = timezone.now()
    start = now - timedelta(hours=WINDOW_HOURS)
    incs = list(active_incidents())
    results = score_incidents(incs, cfg, now) if incs else {}

    window_alerts = list(Alert.objects.filter(raised_at__gte=start)
                         .values_list("raised_at", "alert_type__category", "alert_type__name", "patient__name"))
    raw = len(window_alerts)
    touched = Incident.objects.filter(last_seen__gte=start).count()
    need_now = sum(1 for i in incs if results[i.id]["tier"] in ("critical", "high") and i.status == Incident.OPEN)

    # Alerts per hour, by category
    hours = []
    for h in range(WINDOW_HOURS):
        hs = start + timedelta(hours=h)
        he = hs + timedelta(hours=1)
        c = Counter(cat for t, cat, _, _ in window_alerts if hs <= t < he)
        hours.append({"start": hs, "total": sum(c.values()), **{k: c.get(k, 0) for k in
                      ("vital", "safety", "care", "report", "device")}})

    buckets = Counter(int((t - start).total_seconds() // (BUCKET_MIN * 60)) for t, *_ in window_alerts)
    flood_buckets = sum(1 for n in buckets.values() if n > FLOOD_PER_BUCKET)
    peak = max(buckets.values()) if buckets else 0

    bad = Counter((name, pname) for _, _, name, pname in window_alerts).most_common(6)
    bad_actors = [{"alert_type": k[0], "patient": k[1], "count": n, "share": n / raw if raw else 0} for k, n in bad]

    chattering = sum(1 for i in incs if results[i.id]["chattering"])
    stale = sum(1 for i in incs if i.first_seen < now - timedelta(hours=STALE_HOURS))

    # Claude vs model
    judged = [i for i in Incident.objects.filter(claude_judgement__isnull=False)]
    agree = Counter((i.claude_judgement or {}).get("agreement", "agree") for i in judged)
    gaps = []
    for i in judged:
        if i.id in results and i.claude_judgement.get("score") is not None:
            gaps.append(abs(results[i.id]["model_score"] - i.claude_judgement["score"]))

    # Time to acknowledge by tier, last 30 days
    cutoff = now - timedelta(days=FEEDBACK_DAYS)
    ack = defaultdict(list)
    for tier, first, acked in Incident.objects.filter(acknowledged_at__isnull=False, first_seen__gte=cutoff) \
            .values_list("tier_snapshot", "first_seen", "acknowledged_at"):
        if tier:
            ack[tier].append((acked - first).total_seconds() / 60)
    ack_times = [{"tier": t, "median_min": round(median(ack[t]), 1), "n": len(ack[t])}
                 for t in ("critical", "high", "medium", "low") if ack[t]]

    outcomes = Incident.objects.filter(outcome_actionable__isnull=False, closed_at__gte=cutoff) \
        .values("outcome_actionable").annotate(n=Count("id"))
    oc = {("actionable" if r["outcome_actionable"] else "false_alarm"): r["n"] for r in outcomes}

    return Response({
        "window_hours": WINDOW_HOURS,
        "funnel": [
            {"key": "raw", "label": "Raw alerts", "value": raw},
            {"key": "incidents", "label": "Incidents after grouping", "value": touched},
            {"key": "active", "label": "Still open", "value": len(incs)},
            {"key": "need_now", "label": "Critical or high, not yet picked up", "value": need_now},
        ],
        "noise_reduction": (1 - touched / raw) if raw else 0,
        "hours": hours,
        "flood": {"threshold": FLOOD_PER_BUCKET, "bucket_minutes": BUCKET_MIN, "flood_buckets": flood_buckets,
                  "peak": peak},
        "chattering": chattering,
        "stale": stale,
        "stale_hours": STALE_HOURS,
        "bad_actors": bad_actors,
        "learned": learned_rates(now, cfg),
        "claude": {"judged": len(judged), "agreement": agree,
                   "mean_gap": round(sum(gaps) / len(gaps), 1) if gaps else None},
        "ack_times": ack_times,
        "outcomes": {"actionable": oc.get("actionable", 0), "false_alarm": oc.get("false_alarm", 0)},
        "tiers": Counter(results[i.id]["tier"] for i in incs),
    })


# ---------------------------------------------------------------------------
# Simulation, ingest, demo reset
# ---------------------------------------------------------------------------

@api_view(["POST"])
def simulate_tick(request):
    count = request.data.get("count")
    try:
        count = max(1, min(10, int(count))) if count else None
    except (TypeError, ValueError):
        count = None
    return Response(tick(count))


@api_view(["GET"])
def scenarios(request):
    return Response(SCENARIOS)


@api_view(["POST"])
def simulate_scenario(request):
    key = request.data.get("scenario")
    if key not in {s["key"] for s in SCENARIOS}:
        return Response({"error": "Unknown scenario."}, status=http.HTTP_400_BAD_REQUEST)
    return Response(run_scenario(key))


@api_view(["POST"])
def ingest(request):
    """Webhook-style ingest: POST one alert as JSON. See README for the payload."""
    d = request.data
    try:
        patient = Patient.objects.get(pk=d.get("patient_id"))
        at = AlertType.objects.get(code=d.get("alert_type"))
    except (Patient.DoesNotExist, AlertType.DoesNotExist, ValueError, TypeError):
        return Response({"error": "patient_id and alert_type (a catalog code) are required and must exist."},
                        status=http.HTTP_400_BAD_REQUEST)

    def num(key, default=None):
        v = d.get(key, default)
        return float(v) if v is not None and v != "" else default

    try:
        alert, inc, grouped = ingest_alert(
            patient, at, value=num("value"), message=str(d.get("message", ""))[:2000],
            source=str(d.get("source", "API"))[:80], signal_quality=num("signal_quality", 1.0),
            duration_s=num("duration_s"), payload=d.get("payload") if isinstance(d.get("payload"), dict) else {},
        )
    except (TypeError, ValueError):
        return Response({"error": "value, signal_quality and duration_s must be numbers."},
                        status=http.HTTP_400_BAD_REQUEST)
    return Response({"alert_id": alert.id, "incident_id": inc.id, "grouped": grouped}, status=http.HTTP_201_CREATED)


@api_view(["POST"])
def demo_reset(request):
    keep = request.data.get("keep_config", True)
    summary = seed_demo(keep_config=bool(keep))
    return Response(summary)


# ---------------------------------------------------------------------------
# The React app
# ---------------------------------------------------------------------------

NOT_BUILT = """<!doctype html><html><head><meta charset="utf-8"><title>Pulse</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:64px auto;padding:0 16px;color:#1f2429}
code{background:#eef0f2;padding:2px 6px;border-radius:4px}</style></head><body>
<h1>Pulse API is running</h1>
<p>The React app has not been built yet. Pick one:</p>
<ol><li>Demo mode: in <code>frontend/</code> run <code>npm install</code> then <code>npm run build</code>, and refresh this page.</li>
<li>Dev mode: in <code>frontend/</code> run <code>npm run dev</code> and open <a href="http://localhost:5173">localhost:5173</a>.</li></ol>
<p>The API is at <a href="/api/queue/">/api/queue/</a> and the admin at <a href="/admin/">/admin/</a>.</p>
</body></html>"""


def spa(request):
    index = settings.FRONTEND_DIST / "index.html"
    if index.exists():
        return HttpResponse(index.read_text(encoding="utf-8"), content_type="text/html")
    return HttpResponse(NOT_BUILT, content_type="text/html")
