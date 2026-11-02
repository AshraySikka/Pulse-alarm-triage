"""
Ingest: turn one raw alert into either a new incident or a repeat on an
existing one. This is the first and cheapest cut of alarm fatigue: the same
alert for the same patient inside the grouping window is one thing to look at,
not twelve.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Alert, AlertType, Incident, IncidentEvent, ModelConfig, Patient, VitalReading

MIN_QUALITY_FOR_TREND = 0.5


@transaction.atomic
def ingest_alert(
    patient: Patient,
    alert_type: AlertType,
    *,
    value: float | None = None,
    message: str = "",
    source: str = "",
    signal_quality: float = 1.0,
    duration_s: float | None = None,
    payload: dict | None = None,
    raised_at=None,
    cfg: dict | None = None,
):
    cfg = cfg or ModelConfig.current()
    raised_at = raised_at or timezone.now()
    window = timedelta(minutes=cfg["dedup_window_min"])

    incident = (
        Incident.objects.select_for_update()
        .filter(
            patient=patient,
            alert_type=alert_type,
            status__in=Incident.ACTIVE,
            last_seen__gte=raised_at - window,
        )
        .order_by("-last_seen")
        .first()
    )
    grouped = incident is not None
    if grouped:
        incident.alert_count += 1
        incident.last_seen = max(incident.last_seen, raised_at)
        incident.first_seen = min(incident.first_seen, raised_at)
        incident.save(update_fields=["alert_count", "last_seen", "first_seen"])
    else:
        incident = Incident.objects.create(
            patient=patient,
            alert_type=alert_type,
            first_seen=raised_at,
            last_seen=raised_at,
            alert_count=1,
            created_at=raised_at,
        )
        IncidentEvent.objects.create(
            incident=incident, kind="created", message=f"First alert from {source or 'device'}",
            created_at=raised_at,
        )

    alert = Alert.objects.create(
        incident=incident,
        patient=patient,
        alert_type=alert_type,
        source=source,
        value=value,
        message=message,
        signal_quality=signal_quality,
        duration_s=duration_s,
        payload=payload or {},
        raised_at=raised_at,
    )

    # Good-quality vital readings also feed the patient's trend line.
    if alert_type.param and value is not None and signal_quality >= MIN_QUALITY_FOR_TREND:
        VitalReading.objects.create(
            patient=patient, param=alert_type.param, value=value, taken_at=raised_at, source="alert"
        )

    return alert, incident, grouped
