"""
Alert catalog for a home and community care coordination desk.

Each type carries the starting beliefs the model uses before it has learned
anything from outcomes. All of these are editable from the Model page and the
Django admin.

time_to_harm_min      typical minutes before a real event of this kind causes harm
prior_actionable_rate share of these alerts that usually need a human to act
base_severity         used for events; vital signs are scored from their value
"""

ALERT_TYPES = [
    # code, name, category, system, param, unit, base_severity, time_to_harm_min, prior_actionable_rate, description
    ("SPO2", "Low oxygen saturation", "vital", "cardioresp", "spo2", "%", 0.6, 10, 0.25,
     "Pulse oximeter reading under the alert threshold. The noisiest alert in remote monitoring: probes slip, hands are cold, people move."),
    ("HR", "Heart rate out of range", "vital", "cardioresp", "hr", "bpm", 0.5, 20, 0.30,
     "Wearable or oximeter heart rate outside the alert range."),
    ("SBP_HIGH", "High blood pressure", "vital", "cardioresp", "sbp", "mmHg", 0.5, 120, 0.25,
     "Home blood pressure cuff reading above the alert range. Usually a call to the nurse, rarely an emergency."),
    ("SBP_LOW", "Low blood pressure", "vital", "cardioresp", "sbp", "mmHg", 0.5, 30, 0.40,
     "Home blood pressure cuff reading below the alert range. Dizziness and falls follow quickly."),
    ("TEMP", "Temperature out of range", "vital", "thermo", "temp", "°C", 0.5, 60, 0.40,
     "Fever or low temperature from a connected thermometer."),
    ("RR", "Breathing rate out of range", "vital", "cardioresp", "rr", "/min", 0.5, 15, 0.35,
     "Respiratory rate from an under-mattress sensor."),
    ("GLUCOSE_LOW", "Low blood glucose", "vital", "metabolic", "glucose", "mmol/L", 0.5, 15, 0.55,
     "Glucose monitor or meter reading under 4.0 mmol/L. Confusion and collapse can follow within minutes."),
    ("GLUCOSE_HIGH", "High blood glucose", "vital", "metabolic", "glucose", "mmol/L", 0.5, 240, 0.30,
     "Glucose monitor or meter reading above the alert range. Harm builds over hours, not minutes."),
    ("WEIGHT", "Rapid weight gain", "vital", "cardioresp", "weight", "kg", 0.5, 720, 0.45,
     "Daily weight up sharply. In heart failure this is often fluid, days before breathlessness."),
    ("FALL", "Fall detected", "safety", "safety", "", "", 0.8, 10, 0.45,
     "Fall pendant or wearable detected a fall and ran a voice check."),
    ("PANIC", "Help button pressed", "safety", "safety", "", "", 0.75, 5, 0.55,
     "Personal emergency response button pressed."),
    ("WANDERING", "Exit alert", "safety", "safety", "", "", 0.7, 15, 0.35,
     "Door sensor or geofence shows the patient may have left home unexpectedly."),
    ("INACTIVITY", "No movement detected", "safety", "safety", "", "", 0.6, 60, 0.25,
     "Motion sensors saw no activity for longer than usual."),
    ("MISSED_VISIT", "Visit not checked in", "care", "care", "", "", 0.55, 60, 0.30,
     "Scheduled home visit has no electronic visit verification check-in."),
    ("MISSED_MED", "Missed medication dose", "care", "care", "", "", 0.4, 180, 0.35,
     "Smart dispenser shows a dose was not taken."),
    ("SYMPTOM", "Symptom reported", "report", "general", "", "", 0.6, 30, 0.60,
     "Patient, family or worker reported a new symptom by app or phone."),
    ("O2_EQUIPMENT", "Oxygen equipment fault", "device", "cardioresp", "", "", 0.7, 20, 0.50,
     "Home oxygen concentrator reported a fault."),
    ("DEVICE_OFFLINE", "Device not reporting", "device", "device", "", "", 0.2, 720, 0.08,
     "A monitoring device has not sent data in its expected window."),
    ("LOW_BATTERY", "Device battery low", "device", "device", "", "", 0.08, 1440, 0.05,
     "A monitoring device needs charging."),
]


def seed_alert_types():
    from .models import AlertType

    codes = [row[0] for row in ALERT_TYPES]
    # Remove retired types (only possible once their incidents are gone, which seed_demo ensures).
    AlertType.objects.exclude(code__in=codes).filter(incidents__isnull=True, alerts__isnull=True).delete()
    for row in ALERT_TYPES:
        code, name, category, system, param, unit, base, tth, prior, desc = row
        AlertType.objects.update_or_create(
            code=code,
            defaults=dict(
                name=name, category=category, system=system, param=param, unit=unit,
                base_severity=base, time_to_harm_min=tth, prior_actionable_rate=prior, description=desc,
            ),
        )
