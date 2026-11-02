"""
Data model.

Raw device and workflow events arrive as Alerts. Repeats of the same alert for
the same patient inside the grouping window collapse into one Incident, which
is what a coordinator actually works. Scores are computed on read (see
engine.py) so they always reflect the current model settings and the clock.
"""
from django.db import models
from django.utils import timezone

from .scoring import DEFAULT_PARAMS


class Patient(models.Model):
    GOALS = [("full", "Full treatment"), ("comfort", "Comfort-focused (palliative)")]

    name = models.CharField(max_length=120)
    age = models.PositiveIntegerField()
    sex = models.CharField(max_length=10, blank=True)
    acuity = models.PositiveSmallIntegerField(default=2, help_text="Care acuity, 1 (low) to 5 (very high)")
    conditions = models.JSONField(default=list, blank=True)
    medications = models.JSONField(default=list, blank=True)
    lives_alone = models.BooleanField(default=False)
    discharge_date = models.DateField(null=True, blank=True, help_text="Most recent hospital discharge")
    fall_history = models.BooleanField(default=False)
    cognitive_impairment = models.BooleanField(default=False)
    hypercapnic_risk = models.BooleanField(
        default=False, help_text="Use NEWS2 SpO2 scale 2 (prescribed target 88-92%)"
    )
    on_oxygen = models.BooleanField(default=False)
    goals_of_care = models.CharField(max_length=20, choices=GOALS, default="full")
    care_notes = models.TextField(blank=True)
    neighbourhood = models.CharField(max_length=80, blank=True)
    care_team = models.CharField(max_length=160, blank=True)
    devices = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = [p for p in self.name.replace("'", "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper()


class VitalReading(models.Model):
    PARAMS = [
        ("spo2", "SpO2"),
        ("hr", "Heart rate"),
        ("sbp", "Systolic BP"),
        ("temp", "Temperature"),
        ("rr", "Respiratory rate"),
        ("glucose", "Glucose"),
        ("weight", "Weight"),
    ]
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="readings")
    param = models.CharField(max_length=12, choices=PARAMS)
    value = models.FloatField()
    taken_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=20, default="routine")

    class Meta:
        indexes = [models.Index(fields=["patient", "param", "taken_at"])]
        ordering = ["taken_at"]

    def __str__(self):
        return f"{self.patient} {self.param}={self.value}"


class AlertType(models.Model):
    CATEGORIES = [
        ("vital", "Vital sign"),
        ("safety", "Safety"),
        ("care", "Care delivery"),
        ("report", "Reported symptom"),
        ("device", "Device"),
    ]
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)
    category = models.CharField(max_length=12, choices=CATEGORIES)
    system = models.CharField(
        max_length=16,
        help_text="Body system or domain, used to decide which alerts corroborate each other",
    )
    param = models.CharField(max_length=12, blank=True, help_text="Vital parameter, if this is a vital-sign alert")
    unit = models.CharField(max_length=16, blank=True)
    base_severity = models.FloatField(default=0.5, help_text="0-1. Used for non-vital alerts.")
    time_to_harm_min = models.FloatField(default=60, help_text="Typical minutes before a real event causes harm")
    prior_actionable_rate = models.FloatField(
        default=0.3, help_text="Starting belief: share of these alerts that need action"
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class Incident(models.Model):
    OPEN, ACK, RESOLVED, DISMISSED = "open", "acknowledged", "resolved", "dismissed"
    STATUSES = [(OPEN, "Open"), (ACK, "Acknowledged"), (RESOLVED, "Resolved"), (DISMISSED, "Dismissed")]
    ACTIVE = (OPEN, ACK)

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="incidents")
    alert_type = models.ForeignKey(AlertType, on_delete=models.PROTECT, related_name="incidents")
    status = models.CharField(max_length=14, choices=STATUSES, default=OPEN, db_index=True)
    first_seen = models.DateTimeField(db_index=True)
    last_seen = models.DateTimeField(db_index=True)
    alert_count = models.PositiveIntegerField(default=1)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    outcome_actionable = models.BooleanField(
        null=True, blank=True, help_text="Feedback: did this need action? Feeds the credibility model."
    )
    assigned_to = models.CharField(max_length=80, blank=True)
    score_snapshot = models.FloatField(null=True, blank=True)
    tier_snapshot = models.CharField(max_length=10, blank=True)

    claude_judgement = models.JSONField(null=True, blank=True)
    claude_model = models.CharField(max_length=60, blank=True)
    claude_judged_at = models.DateTimeField(null=True, blank=True)
    claude_error = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"#{self.pk} {self.alert_type} - {self.patient} ({self.status})"


class Alert(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="alerts")
    alert_type = models.ForeignKey(AlertType, on_delete=models.PROTECT, related_name="alerts")
    source = models.CharField(max_length=80, blank=True)
    value = models.FloatField(null=True, blank=True)
    message = models.TextField(blank=True)
    signal_quality = models.FloatField(default=1.0)
    duration_s = models.FloatField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    raised_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-raised_at"]

    def __str__(self):
        return f"{self.alert_type.code} {self.patient} {self.raised_at:%H:%M}"


class IncidentEvent(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20)
    message = models.TextField(blank=True)
    actor = models.CharField(max_length=80, default="System")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]


class ModelConfig(models.Model):
    """Singleton row holding every tunable factor of the scoring model."""

    params = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=80, blank=True)

    class Meta:
        verbose_name = "Model settings"
        verbose_name_plural = "Model settings"

    def __str__(self):
        return "Scoring model settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"params": dict(DEFAULT_PARAMS)})
        return obj

    @classmethod
    def current(cls):
        """Merged parameters: saved values over defaults, so new knobs always exist."""
        saved = cls.load().params or {}
        merged = dict(DEFAULT_PARAMS)
        merged.update({k: v for k, v in saved.items() if k in DEFAULT_PARAMS})
        return merged
