"""
Synthetic demo data. Every patient, reading and alert here is invented.

The shift is generated relative to the moment you run it, so the demo always
looks live: about eight hours of alerts, mostly noise, with a handful of real
problems buried inside it. There are also 30 days of closed incidents with
outcomes, which is what the credibility model learns from.
"""
from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from .catalog import seed_alert_types
from .ingest import ingest_alert
from .models import (
    Alert, AlertType, Incident, IncidentEvent, ModelConfig, Patient, VitalReading,
)

SHIFT_HOURS = 8

# Each patient: profile, devices, routine baselines {param: (mean, sd)} and
# drifts {param: (change per day, started N days ago)}.
PATIENTS = [
    dict(
        key="margaret", name="Margaret O'Neill", age=84, sex="F", acuity=4, lives_alone=True,
        discharge_days_ago=5,
        conditions=["Heart failure (reduced ejection fraction)", "Atrial fibrillation", "Type 2 diabetes", "Chronic kidney disease, stage 3"],
        medications=["Furosemide 40 mg daily", "Apixaban 5 mg twice daily", "Metoprolol 25 mg twice daily", "Metformin 500 mg twice daily"],
        neighbourhood="Etobicoke", care_team="RN case manager, Team West. PSW visits morning and evening.",
        care_notes="Discharged 5 days ago after a heart failure admission. Daily weight at 8 a.m. Call the RN if weight rises more than 1 kg in a day or 2 kg in a week, or with new breathlessness.",
        devices=["Bluetooth scale", "BP cuff", "Pulse oximeter"],
        baselines={"weight": (68.0, 0.25), "spo2": (95, 0.9), "hr": (76, 5), "sbp": (126, 7)},
        drifts={"weight": (0.75, 3), "spo2": (-1.3, 2), "hr": (7, 2)},
    ),
    dict(
        key="arjun", name="Arjun Mehta", age=71, sex="M", acuity=4, hypercapnic_risk=True, on_oxygen=True,
        conditions=["COPD, on home oxygen 2 L/min", "Hypertension", "Osteoarthritis"],
        medications=["Tiotropium inhaler daily", "Salbutamol as needed", "Amlodipine 5 mg daily"],
        neighbourhood="Scarborough", care_team="RN case manager, Team East. Respiratory therapist monthly.",
        care_notes="Respirologist target SpO2 is 88-92%. The finger probe slips during sleep; family switches to the ear probe when they notice.",
        devices=["Pulse oximeter", "Oxygen concentrator"],
        baselines={"spo2": (90, 1.3), "hr": (84, 6)},
        drifts={},
    ),
    dict(
        key="lucille", name="Lucille Tremblay", age=79, sex="F", acuity=3, fall_history=True, cognitive_impairment=True,
        conditions=["Alzheimer's disease, moderate", "Hypertension", "Osteoporosis"],
        medications=["Donepezil 10 mg nightly", "Ramipril 5 mg daily"],
        neighbourhood="North York", care_team="Lives with her daughter, who works night shifts Mon, Wed, Fri.",
        care_notes="Exit-seeking in the evening. Front door sensor and hallway motion sensor installed. Daughter's mobile is the first call.",
        devices=["Door sensor", "Motion sensors"],
        baselines={"sbp": (134, 8)},
        drifts={},
    ),
    dict(
        key="wei", name="Wei Chen", age=68, sex="M", acuity=3, fall_history=True, discharge_days_ago=7,
        conditions=["Right total hip replacement, post-op day 9", "Type 2 diabetes", "Hypertension"],
        medications=["Rivaroxaban 10 mg daily", "Hydromorphone 1 mg as needed", "Metformin 1000 mg twice daily"],
        neighbourhood="Markham", care_team="Home care nursing for wound care every second day. Physio twice weekly.",
        care_notes="Surgical incision check every second day. Report fever, increasing redness, swelling or drainage to the surgeon's office.",
        devices=["Thermometer", "Wearable heart rate"],
        baselines={"temp": (36.8, 0.18), "hr": (78, 5)},
        drifts={"temp": (0.55, 2), "hr": (9, 2)},
    ),
    dict(
        key="fatima", name="Fatima Hassan", age=58, sex="F", acuity=3, lives_alone=True,
        conditions=["Type 1 diabetes, insulin pump", "Hypoglycemia unawareness", "Celiac disease"],
        medications=["Insulin aspart via pump", "Glucagon nasal powder (rescue)"],
        neighbourhood="Mississauga", care_team="Diabetes nurse educator, monthly. Sister has a key.",
        care_notes="Does not feel lows coming on. CGM alerts go to the desk overnight. Sister lives 10 minutes away.",
        devices=["Continuous glucose monitor"],
        baselines={"glucose": (7.6, 1.6)},
        drifts={},
    ),
    dict(
        key="robert", name="Robert Kowalski", age=91, sex="M", acuity=4, lives_alone=True, fall_history=True,
        conditions=["Frailty (Clinical Frailty Scale 6)", "Parkinsonism", "Hypertension", "Macular degeneration"],
        medications=["Carbidopa/levodopa 25/100 three times daily", "Perindopril 4 mg daily"],
        neighbourhood="Etobicoke", care_team="PSW visits three times daily. Son in Ottawa is the contact.",
        care_notes="Two falls in the last six months. Hard of hearing; pendant voice check may be missed if he is not wearing hearing aids.",
        devices=["Fall pendant", "Motion sensors"],
        baselines={"sbp": (142, 9)},
        drifts={},
    ),
    dict(
        key="grace", name="Grace Adeyemi", age=66, sex="F", acuity=2,
        conditions=["Hypertension", "Chronic kidney disease, stage 3"],
        medications=["Amlodipine 10 mg daily", "Hydrochlorothiazide 25 mg daily"],
        neighbourhood="Brampton", care_team="Remote monitoring only. Lives with her husband.",
        care_notes="On a blood pressure titration plan with her nurse practitioner.",
        devices=["BP cuff"],
        baselines={"sbp": (138, 6)},
        drifts={"sbp": (6, 3)},
    ),
    dict(
        key="helen", name="Helen Park", age=76, sex="F", acuity=5, goals_of_care="comfort",
        conditions=["Metastatic non-small cell lung cancer (palliative)", "COPD", "Cancer pain"],
        medications=["Hydromorphone 2 mg every 4 h", "Hydromorphone 1 mg breakthrough", "Dexamethasone 4 mg daily"],
        neighbourhood="Willowdale", care_team="Palliative care team. 24/7 palliative nurse line.",
        care_notes="Comfort-focused plan of care. Do-not-resuscitate order on file. Son is the substitute decision-maker. Symptom crises go to the palliative nurse line.",
        devices=["Pulse oximeter"],
        baselines={"spo2": (92, 1.2), "hr": (92, 6)},
        drifts={"spo2": (-1.4, 3)},
    ),
    dict(
        key="samuel", name="Samuel Brooks", age=73, sex="M", acuity=3, fall_history=True,
        conditions=["Parkinson's disease", "Orthostatic hypotension", "Depression"],
        medications=["Carbidopa/levodopa 25/100 at 7, 11, 15 and 19 h", "Sertraline 50 mg daily"],
        neighbourhood="East York", care_team="Lives with his wife. PSW morning visit.",
        care_notes="Levodopa timing is critical. A late dose brings stiffness and freezing within an hour, and his falls happen when he is off.",
        devices=["Smart pill dispenser", "Bed sensor"],
        baselines={"sbp": (128, 9)},
        drifts={},
    ),
    dict(
        key="priya", name="Priya Raman", age=62, sex="F", acuity=2,
        conditions=["Ischemic stroke, 3 months ago", "Atrial fibrillation", "Hypertension"],
        medications=["Apixaban 5 mg twice daily", "Atorvastatin 40 mg daily", "Ramipril 10 mg daily"],
        neighbourhood="Scarborough", care_team="Stroke rehab at home. PSW personal care daily. Lives with her husband.",
        care_notes="Mild right-side weakness, improving. Husband knows the FAST signs.",
        devices=["BP cuff", "Wearable heart rate"],
        baselines={"sbp": (132, 7), "hr": (72, 5)},
        drifts={},
    ),
    dict(
        key="thomas", name="Thomas Leblanc", age=87, sex="M", acuity=3,
        conditions=["Heart failure", "COPD", "Atrial fibrillation"],
        medications=["Furosemide 20 mg daily", "Warfarin per INR", "Bisoprolol 2.5 mg daily"],
        neighbourhood="York", care_team="Lives with his wife. Nursing visit weekly.",
        care_notes="Chronic mild hypoxemia. Oximeter readings dip when he walks to the bathroom.",
        devices=["Pulse oximeter", "Bluetooth scale", "Wearable heart rate", "Bed sensor"],
        baselines={"spo2": (94, 1.0), "hr": (80, 6), "weight": (74.5, 0.3), "rr": (17, 1.5), "temp": (36.7, 0.2)},
        drifts={},
    ),
    dict(
        key="rosa", name="Rosa Alvarez", age=81, sex="F", acuity=3, lives_alone=True, cognitive_impairment=True,
        conditions=["Type 2 diabetes", "Mild cognitive impairment", "Hypertension", "Hypothyroidism"],
        medications=["Metformin 500 mg twice daily", "Levothyroxine 75 mcg daily"],
        neighbourhood="Davenport", care_team="PSW visit at breakfast. Niece checks in by phone.",
        care_notes="Forgets midday meal and medication without a prompt. Kitchen motion sensor is the best sign she is up and about.",
        devices=["Smart pill dispenser", "Motion sensors", "Glucose meter"],
        baselines={"glucose": (8.4, 1.4), "sbp": (136, 8)},
        drifts={},
    ),
]

# Outcomes over the last 30 days: (actionable, false alarm) per alert type.
HISTORY = {
    "SPO2": (9, 41), "HR": (6, 14), "SBP_HIGH": (4, 14), "SBP_LOW": (2, 3), "TEMP": (6, 6), "RR": (2, 4),
    "GLUCOSE_LOW": (8, 4), "GLUCOSE_HIGH": (3, 9),
    "WEIGHT": (7, 5), "FALL": (8, 9), "PANIC": (4, 5), "WANDERING": (3, 4), "INACTIVITY": (3, 10),
    "MISSED_VISIT": (6, 12), "MISSED_MED": (7, 13), "SYMPTOM": (9, 4), "O2_EQUIPMENT": (2, 1),
    "DEVICE_OFFLINE": (1, 22), "LOW_BATTERY": (0, 30),
}
# Patient-specific history inside the totals above.
PATIENT_HISTORY = {
    ("arjun", "SPO2"): (0, 9),
    ("thomas", "SPO2"): (1, 6),
    ("robert", "FALL"): (3, 1),
    ("fatima", "GLUCOSE_LOW"): (4, 1),
    ("margaret", "WEIGHT"): (2, 0),
}

ROUND = {"spo2": 0, "hr": 0, "sbp": 0, "rr": 0, "temp": 1, "glucose": 1, "weight": 1}


def _local_at(day, hour, minute=0):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(day, time(hour, minute)), tz)


def _readings(patient, spec, now, rng):
    rows = []
    today = timezone.localtime(now).date()
    for param, (mean, sd) in spec["baselines"].items():
        per_day, start = spec["drifts"].get(param, (0.0, 0))
        hours = (8, 20) if param != "glucose" else (7, 12, 17, 22)
        for d in range(14, -1, -1):
            day = today - timedelta(days=d)
            for h in hours:
                t = _local_at(day, h, rng.randint(0, 25))
                if t > now - timedelta(minutes=20):
                    continue
                days_ago = (now - t).total_seconds() / 86400
                drift = per_day * max(0.0, start - days_ago)
                v = mean + rng.gauss(0, sd) + drift
                rows.append(VitalReading(patient=patient, param=param, value=round(v, ROUND[param]), taken_at=t))
    VitalReading.objects.bulk_create(rows)


def _plan(now, rng, P, night):
    """Every alert of the shift as (minutes_ago, patient_key, code, fields)."""
    plan = []

    def add(mins, key, code, **kw):
        plan.append((mins, key, code, kw))

    # Margaret: heart failure decompensation building over the shift.
    add(246, "margaret", "WEIGHT", value=70.3, source="Bluetooth scale", payload={"delta_kg": 1.6})
    for m, v, q, d in [(232, 91, 0.82, 70), (190, 90, 0.86, 120), (131, 91, 0.80, 95), (66, 90, 0.88, 150), (21, 89, 0.90, 210)]:
        add(m, "margaret", "SPO2", value=v, signal_quality=q, duration_s=d, source="Pulse oximeter")
    add(61, "margaret", "HR", value=104, signal_quality=0.9, duration_s=300, source="Pulse oximeter")
    add(19, "margaret", "HR", value=108, signal_quality=0.92, duration_s=420, source="Pulse oximeter")
    add(36, "margaret", "SYMPTOM", source="Family call",
        message="Daughter called: Mom has been sleeping in her recliner because she gets short of breath lying flat. Her ankles are more swollen than yesterday.")

    # Arjun: chattering oximeter, readings inside his prescribed target.
    m = 470
    while m > 3:
        v = rng.choice([86, 87, 88, 88, 89, 89, 90])
        add(m, "arjun", "SPO2", value=v, signal_quality=round(rng.uniform(0.28, 0.62), 2),
            duration_s=rng.randint(5, 26), source="Pulse oximeter (finger probe)")
        m -= rng.choice([2, 3, 4, 7, 9, 11, 13, 14])
    for mm in (152, 91, 33):
        add(mm, "arjun", "LOW_BATTERY", source="Pulse oximeter", message="Pulse oximeter battery at 9%")

    # Lucille: exit alert while her daughter is at work.
    add(27, "lucille", "WANDERING", source="Door sensor",
        message="Front door opened and stayed open 2 min. No motion inside since.",
        payload={"night": night, "alone": True})
    add(305, "lucille", "DEVICE_OFFLINE", source="Hallway motion sensor", message="Hallway motion sensor not reporting for 3 h")

    # Wei: post-op fever, rising heart rate, wound changes.
    for mm, v in ((152, 38.2), (96, 38.4), (31, 38.7)):
        add(mm, "wei", "TEMP", value=v, source="Thermometer")
    add(33, "wei", "HR", value=112, signal_quality=0.9, duration_s=260, source="Wearable heart rate")
    add(17, "wei", "HR", value=114, signal_quality=0.88, duration_s=380, source="Wearable heart rate")
    add(52, "wei", "SYMPTOM", source="PSW visit note",
        message="Incision on the right hip looks redder and warmer than Monday. He says there is more pain when he moves and he felt shivery last night.")

    # Fatima: persistent low glucose, lives alone with hypoglycemia unawareness.
    add(25, "fatima", "GLUCOSE_LOW", value=3.4, signal_quality=0.95, duration_s=900, source="Continuous glucose monitor")
    add(13, "fatima", "GLUCOSE_LOW", value=3.1, signal_quality=0.95, duration_s=1500, source="Continuous glucose monitor")

    # Robert: fall with no answer.
    add(7, "robert", "FALL", source="Fall pendant", payload={"response": "no_response"},
        message="Pendant detected a hard fall in the hallway. Voice check got no answer after 60 s.")
    add(4, "robert", "INACTIVITY", source="Motion sensors", message="No movement in the kitchen or bedroom since the fall alert.")
    add(402, "robert", "LOW_BATTERY", source="Fall pendant", message="Fall pendant battery at 15%")

    # Grace: blood pressure creeping up.
    for mm, v in ((191, 172), (126, 168), (71, 176)):
        add(mm, "grace", "SBP_HIGH", value=v, source="BP cuff")
    add(298, "grace", "LOW_BATTERY", source="BP cuff", message="BP cuff battery low")

    # Helen: palliative, breathless.
    add(43, "helen", "SPO2", value=86, signal_quality=0.9, duration_s=300, source="Pulse oximeter")
    add(16, "helen", "SPO2", value=85, signal_quality=0.92, duration_s=420, source="Pulse oximeter")
    add(31, "helen", "SYMPTOM", source="Family call",
        message="Son called: she is more breathless and anxious tonight and using her breakthrough dose more often. He does not want her to go to hospital.")

    # Samuel: time-critical Parkinson's dose missed.
    dose_time = timezone.localtime(now - timedelta(minutes=76)).strftime("%H:%M")
    add(76, "samuel", "MISSED_MED", source="Smart pill dispenser",
        message="Dispenser drawer not opened 45 min after the dose time.",
        payload={"medication": f"Carbidopa/levodopa 25/100, {dose_time} dose", "med_risk": "time-critical"})
    add(182, "samuel", "DEVICE_OFFLINE", source="Bed sensor", message="Bed sensor not reporting for 9 h")

    # Priya: visit with no check-in, plus device noise.
    add(53, "priya", "MISSED_VISIT", source="Visit verification",
        message="No electronic check-in from the assigned PSW 45 min after the visit start.",
        payload={"visit": "PSW personal care visit"})
    add(241, "priya", "DEVICE_OFFLINE", source="BP cuff", message="BP cuff: no readings for 26 h")
    add(121, "priya", "DEVICE_OFFLINE", source="BP cuff", message="BP cuff: no readings for 28 h")
    add(301, "priya", "HR", value=108, signal_quality=0.7, duration_s=22, source="Wearable heart rate")
    add(288, "priya", "HR", value=104, signal_quality=0.66, duration_s=18, source="Wearable heart rate")

    # Thomas: oximeter and wearable blips when he walks.
    for mm in sorted(rng.sample(range(10, 470), 18), reverse=True):
        add(mm, "thomas", "SPO2", value=rng.choice([92, 92, 93, 93, 93]), signal_quality=round(rng.uniform(0.45, 0.8), 2),
            duration_s=rng.randint(8, 32), source="Pulse oximeter")
    for mm in sorted(rng.sample(range(10, 470), 6), reverse=True):
        add(mm, "thomas", "HR", value=rng.randint(96, 104), signal_quality=round(rng.uniform(0.5, 0.85), 2),
            duration_s=rng.randint(15, 45), source="Wearable heart rate")
    add(362, "thomas", "DEVICE_OFFLINE", source="Bluetooth scale", message="Scale: no weight received this morning")

    # Rosa: quiet house, missed midday dose, high sugar earlier.
    add(161, "rosa", "GLUCOSE_HIGH", value=15.8, source="Glucose meter")
    add(112, "rosa", "INACTIVITY", source="Motion sensors", message="No kitchen motion since breakfast. Her usual lunch activity is missing.")
    add(96, "rosa", "MISSED_MED", source="Smart pill dispenser",
        message="Midday dose not taken.", payload={"medication": "Metformin 500 mg, midday dose", "med_risk": "routine"})

    # Background device noise across the caseload.
    noisy = [("thomas", "Wearable heart rate"), ("priya", "Wearable heart rate"), ("margaret", "BP cuff"),
             ("samuel", "Smart pill dispenser"), ("rosa", "Motion sensors"), ("thomas", "Bed sensor")]
    for key, dev in noisy:
        for mm in sorted(rng.sample(range(15, 470), rng.randint(2, 4)), reverse=True):
            add(mm, key, "LOW_BATTERY", source=dev, message=f"{dev} battery at {rng.randint(6, 18)}%")
    return plan


def _history(now, rng, P, types, featured=frozenset()):
    """Closed incidents over the last 30 days with outcomes, for the learning model.

    Random history never lands on a (patient, alert type) pair that appears in
    today's shift, so the featured cases learn only from PATIENT_HISTORY.
    """
    items = []
    remaining = {code: list(v) for code, v in HISTORY.items()}
    for (pkey, code), (a, f) in PATIENT_HISTORY.items():
        items += [(pkey, code, True)] * a + [(pkey, code, False)] * f
        remaining[code][0] -= a
        remaining[code][1] -= f
    keys = list(P.keys())
    for code, (a, f) in remaining.items():
        pool = [k for k in keys if (k, code) not in featured] or keys
        items += [(rng.choice(pool), code, True) for _ in range(max(a, 0))]
        items += [(rng.choice(pool), code, False) for _ in range(max(f, 0))]

    rows = []
    for pkey, code, actionable in items:
        first = now - timedelta(days=rng.uniform(1.2, 29.5))
        ack_min = rng.uniform(2, 25) if actionable else rng.uniform(6, 90)
        ack = first + timedelta(minutes=ack_min)
        closed = ack + timedelta(minutes=rng.uniform(10, 180))
        score = rng.uniform(55, 97) if actionable else rng.uniform(4, 62)
        tier = "critical" if score >= 80 else "high" if score >= 60 else "medium" if score >= 35 else "low"
        rows.append(Incident(
            patient=P[pkey], alert_type=types[code],
            status=Incident.RESOLVED if actionable else Incident.DISMISSED,
            first_seen=first, last_seen=first + timedelta(minutes=rng.uniform(0, 40)),
            alert_count=rng.randint(1, 6), acknowledged_at=ack, closed_at=closed,
            outcome_actionable=actionable, score_snapshot=round(score, 1), tier_snapshot=tier,
            created_at=first,
        ))
    Incident.objects.bulk_create(rows)


@transaction.atomic
def seed_demo(seed: int | None = None, keep_config: bool = True):
    rng = random.Random(seed if seed is not None else 20261026)
    now = timezone.now()

    Alert.objects.all().delete()
    IncidentEvent.objects.all().delete()
    Incident.objects.all().delete()
    VitalReading.objects.all().delete()
    Patient.objects.all().delete()
    seed_alert_types()
    if not keep_config:
        ModelConfig.objects.all().delete()
    ModelConfig.load()

    types = {t.code: t for t in AlertType.objects.all()}
    P = {}
    for spec in PATIENTS:
        dd = spec.get("discharge_days_ago")
        p = Patient.objects.create(
            name=spec["name"], age=spec["age"], sex=spec["sex"], acuity=spec["acuity"],
            conditions=spec["conditions"], medications=spec["medications"],
            lives_alone=spec.get("lives_alone", False),
            discharge_date=(timezone.localtime(now).date() - timedelta(days=dd)) if dd is not None else None,
            fall_history=spec.get("fall_history", False),
            cognitive_impairment=spec.get("cognitive_impairment", False),
            hypercapnic_risk=spec.get("hypercapnic_risk", False), on_oxygen=spec.get("on_oxygen", False),
            goals_of_care=spec.get("goals_of_care", "full"), care_notes=spec["care_notes"],
            neighbourhood=spec["neighbourhood"], care_team=spec["care_team"], devices=spec["devices"],
        )
        P[spec["key"]] = p
        _readings(p, spec, now, rng)

    local_hour = timezone.localtime(now).hour
    night = local_hour >= 21 or local_hour < 6
    plan = sorted(_plan(now, rng, P, night), key=lambda r: -r[0])
    featured = frozenset((key, code) for _, key, code, _ in plan) | frozenset(PATIENT_HISTORY)
    _history(now, rng, P, types, featured)
    cfg = ModelConfig.current()
    for mins, key, code, kw in plan:
        ingest_alert(P[key], types[code], raised_at=now - timedelta(minutes=mins), cfg=cfg, **kw)

    _shift_activity(now, rng)
    return {"patients": len(P), "alerts": Alert.objects.count(), "incidents": Incident.objects.count()}


def _shift_activity(now, rng):
    """A few things the desk already handled earlier in the shift."""
    closed = 0
    for inc in Incident.objects.filter(status=Incident.OPEN, alert_type__code__in=["LOW_BATTERY", "DEVICE_OFFLINE"],
                                       last_seen__lt=now - timedelta(minutes=150)).order_by("first_seen")[:5]:
        inc.status = Incident.DISMISSED
        inc.acknowledged_at = inc.last_seen + timedelta(minutes=rng.randint(5, 30))
        inc.closed_at = inc.acknowledged_at + timedelta(minutes=rng.randint(2, 15))
        inc.outcome_actionable = False
        inc.save()
        IncidentEvent.objects.create(incident=inc, kind="dismissed", actor="Coordinator (day shift)",
                                     message="Not actionable. Device team notified.", created_at=inc.closed_at)
        closed += 1

    grace = Incident.objects.filter(status=Incident.OPEN, alert_type__code="SBP_HIGH", patient__name="Grace Adeyemi").first()
    if grace:
        grace.status = Incident.ACK
        grace.acknowledged_at = now - timedelta(minutes=40)
        grace.assigned_to = "Coordinator, Team East"
        grace.save()
        IncidentEvent.objects.create(incident=grace, kind="acknowledged", actor="Coordinator, Team East",
                                     message="Left a message for her nurse practitioner about the titration plan.",
                                     created_at=grace.acknowledged_at)
    return closed
