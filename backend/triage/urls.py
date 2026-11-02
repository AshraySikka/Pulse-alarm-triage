from django.urls import path

from . import views

urlpatterns = [
    path("queue/", views.queue),
    path("incidents/<int:pk>/", views.incident_detail),
    path("incidents/<int:pk>/action/", views.incident_action),
    path("incidents/<int:pk>/judge/", views.judge_incident),
    path("incidents/<int:pk>/prompt/", views.claude_prompt_preview),
    path("claude/status/", views.claude_status),
    path("claude/review/", views.claude_review),
    path("config/", views.config_view),
    path("config/preview/", views.config_preview),
    path("config/reset/", views.config_reset),
    path("alert-types/", views.alert_types),
    path("alert-types/<str:code>/", views.alert_type_update),
    path("patients/", views.patients),
    path("stats/", views.stats),
    path("simulate/tick/", views.simulate_tick),
    path("simulate/scenarios/", views.scenarios),
    path("simulate/scenario/", views.simulate_scenario),
    path("ingest/", views.ingest),
    path("demo/reset/", views.demo_reset),
]
