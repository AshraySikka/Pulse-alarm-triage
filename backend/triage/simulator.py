"""
Live feed for demos. Each tick produces a few realistic alerts, mostly the
noise a desk sees all day, sometimes something real. Scenarios inject a
story on demand so you can show corroboration, red flags and Claude routing.
"""
from __future__ import annotations

import random

from django.utils import timezone

from .ingest import ingest_alert
from .models import AlertType, ModelConfig, Patient

_rng = random.Random()


def _p(name_prefix):
    return Patient.objects.filter(name__startswith=name_prefix).first()


def _t(code):
    return AlertType.objects.get(code=code)


def _noise_spo2():
    patient = _p(_rng.choice(["Arjun", "Arjun", "Thomas", "Margaret", "Helen"]))
    if not patient:
        return None
    v =_rng.choice([88, 89, 90]) if patient.hypercapnic_risk else _rng.choice([91, 92, 92, 93, 93])
    return patient, "SPO2", dict(value=v, signal_quality=round(_rng.uniform(0.25, 0.7), 2),
                                 duration_s=_rng.randint(4, 30), source="Pulse oximeter")


def _noise_hr():
    patient = _p(_rng.choice(["Thomas", "Priya", "Wei", "Margaret"]))
    return patient, "HR", dict(value=_rng.randint(95, 112), signal_quality=round(_rng.uniform(0.45, 0.85), 2),
                               duration_s=_rng.randint(10, 50), source="Wearable heart rate")


def _noise_battery():
    patient = _rng.choice(list(Patient.objects.all()))
    dev = _rng.choice(patient.devices or ["Sensor"])
    return patient, "LOW_BATTERY", dict(source=dev, message=f"{dev} battery at {_rng.randint(5, 18)}%")


def _noise_offline():
    patient = _rng.choice(list(Patient.objects.all()))
    dev = _rng.choice(patient.devices or ["Sensor"])
    return patient, "DEVICE_OFFLINE", dict(source=dev, message=f"{dev} not reporting for {_rng.randint(2, 30)} h")


def _noise_bp():
    patient = _p(_rng.choice(["Grace", "Priya", "Margaret"]))
    return patient, "SBP_HIGH", dict(value=_rng.randint(158, 184), source="BP cuff")


def _noise_glucose():
    patient = _p(_rng.choice(["Rosa", "Fatima"]))
    return patient, "GLUCOSE_HIGH", dict(value=round(_rng.uniform(14.2, 17.5), 1), source="Glucose meter")


def _noise_visit():
    patient = _rng.choice(list(Patient.objects.all()))
    return patient, "MISSED_VISIT", dict(source="Visit verification",
                                         message="PSW check-in 20 min late. Traffic reported on the 401.",
                                         payload={"visit": "PSW visit, running late"})


GENERATORS = [
    (_noise_spo2, 36), (_noise_hr, 16), (_noise_battery, 14), (_noise_offline, 8),
    (_noise_bp, 10), (_noise_glucose, 8), (_noise_visit, 4),
]


def tick(count: int | None = None):
    """Generate 1-3 noisy alerts now. Returns a summary for the UI toast."""
    cfg = ModelConfig.current()
    n = count or _rng.choice([1, 1, 2, 2, 3])
    gens, weights = zip(*GENERATORS)
    created, grouped = [], 0
    for _ in range(n):
        out = _rng.choices(gens, weights=weights)[0]()
        if not out or not out[0]:
            continue
        patient, code, kw = out
        alert, incident, was_grouped = ingest_alert(patient, _t(code), cfg=cfg, **kw)
        grouped += int(was_grouped)
        created.append({"alert_id": alert.id, "incident_id": incident.id, "patient": patient.name,
                        "type": incident.alert_type.name, "grouped": was_grouped})
    return {"alerts": len(created), "grouped": grouped, "new_incidents": len(created) - grouped, "items": created}


SCENARIOS = [
    {
        "key": "deterioration",
        "name": "Quiet deterioration",
        "patient": "Thomas Leblanc",
        "help": "Four moderate readings on one patient. None is alarming alone; together they corroborate and he rises.",
    },
    {
        "key": "stroke",
        "name": "Stroke signs in a call",
        "patient": "Priya Raman",
        "help": "A family call with red-flag words. The keyword screen floors it; Claude should name the protocol.",
    },
    {
        "key": "oxygen",
        "name": "Oxygen concentrator fault",
        "patient": "Arjun Mehta",
        "help": "An equipment alert that matters because of who it is attached to.",
    },
]


def run_scenario(key: str):
    cfg = ModelConfig.current()
    now = timezone.now()
    made = []

    def emit(name, code, **kw):
        patient = Patient.objects.get(name=name)
        alert, incident, grouped = ingest_alert(patient, _t(code), raised_at=now, cfg=cfg, **kw)
        made.append({"incident_id": incident.id, "patient": patient.name, "type": incident.alert_type.name,
                     "grouped": grouped})

    if key == "deterioration":
        emit("Thomas Leblanc", "HR", value=118, signal_quality=0.9, duration_s=240, source="Wearable heart rate")
        emit("Thomas Leblanc", "RR", value=25, signal_quality=0.88, duration_s=600, source="Bed sensor")
        emit("Thomas Leblanc", "TEMP", value=38.3, source="Thermometer")
        emit("Thomas Leblanc", "SPO2", value=90, signal_quality=0.86, duration_s=180, source="Pulse oximeter")
    elif key == "stroke":
        emit("Priya Raman", "SYMPTOM", source="Family call",
             message="Husband called: since about 20 minutes ago her speech is slurred and her right arm is weaker than this morning.")
        emit("Priya Raman", "SBP_HIGH", value=194, source="BP cuff")
    elif key == "oxygen":
        emit("Arjun Mehta", "O2_EQUIPMENT", source="Oxygen concentrator",
             message="Concentrator flow fault. Unit alarming, output below 1 L/min.",
             payload={"oxygen_dependent": True})
        emit("Arjun Mehta", "SPO2", value=85, signal_quality=0.84, duration_s=200, source="Pulse oximeter (ear probe)")
    else:
        raise ValueError(f"Unknown scenario {key}")
    return {"scenario": key, "items": made}
