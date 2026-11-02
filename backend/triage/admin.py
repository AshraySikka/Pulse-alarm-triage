from django import forms
from django.contrib import admin

from . import scoring as sc
from .models import Alert, AlertType, Incident, IncidentEvent, ModelConfig, Patient, VitalReading


def _param_fields():
    """One form field per tunable factor, built from scoring.PARAM_SCHEMA."""
    fields = {}
    for group, key, label, help_text, default, lo, hi, step, kind in sc.PARAM_SCHEMA:
        if kind == "bool":
            fields[key] = forms.BooleanField(
                label=label, required=False, help_text=f"{help_text} Default: {'on' if default >= 0.5 else 'off'}."
            )
        else:
            fields[key] = forms.FloatField(
                label=label, min_value=lo, max_value=hi,
                help_text=f"{help_text} Range {lo:g} to {hi:g}, default {default:g}.",
                widget=forms.NumberInput(attrs={"step": step}),
            )
    return fields


class _ModelConfigFormBase(forms.ModelForm):
    """Edits the JSON params as normal fields instead of a raw JSON box."""

    class Meta:
        model = ModelConfig
        fields = ["updated_by"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        saved = (self.instance.params or {}) if self.instance and self.instance.pk else {}
        current = {**sc.DEFAULT_PARAMS, **saved}
        for _, key, *_rest in sc.PARAM_SCHEMA:
            kind = _rest[-1]
            self.fields[key].initial = (current[key] >= 0.5) if kind == "bool" else current[key]

    def save(self, commit=True):
        obj = super().save(commit=False)
        raw = {}
        for _, key, *_rest in sc.PARAM_SCHEMA:
            kind = _rest[-1]
            v = self.cleaned_data.get(key)
            raw[key] = (1.0 if v else 0.0) if kind == "bool" else v
        obj.params = sc.sanitize_params(raw)
        if commit:
            obj.save()
        return obj


# Declared on the class (not added in __init__) so the admin's form factory accepts them.
ModelConfigForm = type("ModelConfigForm", (_ModelConfigFormBase,), _param_fields())


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
    form = ModelConfigForm
    list_display = ("__str__", "updated_at", "updated_by")

    def get_fieldsets(self, request, obj=None):
        sets = [(None, {"fields": ["updated_by"]})]
        for gkey, gname, ghelp in sc.PARAM_GROUPS:
            keys = [row[1] for row in sc.PARAM_SCHEMA if row[0] == gkey]
            sets.append((gname, {"fields": keys, "description": ghelp}))
        return sets

    def has_add_permission(self, request):
        return not ModelConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertType)
class AlertTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "system", "base_severity", "time_to_harm_min", "prior_actionable_rate")
    list_editable = ("base_severity", "time_to_harm_min", "prior_actionable_rate")
    list_filter = ("category", "system")
    search_fields = ("code", "name")


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("name", "age", "acuity", "lives_alone", "goals_of_care", "neighbourhood")
    list_filter = ("acuity", "lives_alone", "goals_of_care")
    search_fields = ("name",)


class AlertInline(admin.TabularInline):
    model = Alert
    extra = 0
    fields = ("raised_at", "value", "message", "signal_quality", "duration_s", "source")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


class EventInline(admin.TabularInline):
    model = IncidentEvent
    extra = 0
    fields = ("created_at", "kind", "actor", "message")
    readonly_fields = fields
    can_delete = False


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "alert_type", "status", "alert_count", "first_seen", "last_seen", "outcome_actionable")
    list_filter = ("status", "alert_type__category", "outcome_actionable")
    search_fields = ("patient__name", "alert_type__name")
    inlines = [AlertInline, EventInline]
    date_hierarchy = "first_seen"


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("raised_at", "patient", "alert_type", "value", "signal_quality", "duration_s", "source", "incident")
    list_filter = ("alert_type",)
    search_fields = ("patient__name", "message")


@admin.register(VitalReading)
class VitalReadingAdmin(admin.ModelAdmin):
    list_display = ("taken_at", "patient", "param", "value", "source")
    list_filter = ("param", "source")
    search_fields = ("patient__name",)
