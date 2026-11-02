"""
Tests for the scoring math and the main API flows.

Run with:  python manage.py test triage
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import claude_judge
from . import scoring as sc
from .engine import active_incidents, score_incidents
from .ingest import ingest_alert
from .models import AlertType, Incident, ModelConfig, Patient
from .seed import seed_demo

CFG = dict(sc.DEFAULT_PARAMS)


class BandTests(TestCase):
    def test_spo2_scale_1(self):
        self.assertEqual(sc.band_spo2(97)[0], 0)
        self.assertEqual(sc.band_spo2(95)[0], 1)
        self.assertEqual(sc.band_spo2(92)[0], 2)
        self.assertEqual(sc.band_spo2(91)[0], 3)

    def test_spo2_scale_2_respects_prescribed_target(self):
        # 89% is a band-3 emergency on scale 1 but on target for a hypercapnic patient.
        self.assertEqual(sc.band_spo2(89)[0], 3)
        self.assertEqual(sc.band_spo2(89, scale2=True)[0], 0)
        self.assertEqual(sc.band_spo2(85, scale2=True)[0], 2)
        self.assertEqual(sc.band_spo2(97, scale2=True, on_oxygen=True)[0], 3)

    def test_news2_pulse_and_temperature(self):
        self.assertEqual([sc.band_hr(v)[0] for v in (38, 45, 70, 100, 120, 140)], [3, 1, 0, 1, 2, 3])
        self.assertEqual([sc.band_temp(v)[0] for v in (34.9, 35.5, 37.0, 38.5, 39.5)], [3, 1, 0, 1, 2])

    def test_glucose_and_weight_rules(self):
        self.assertEqual(sc.band_glucose(2.8)[0], 3)
        self.assertEqual(sc.band_glucose(3.5)[0], 2)
        self.assertEqual(sc.band_glucose(7.0)[0], 0)
        self.assertEqual(sc.band_weight_gain(2.1)[0], 3)
        self.assertEqual(sc.band_weight_gain(0.4)[0], 0)


class FactorTests(TestCase):
    patient = {"age": 80, "acuity": 3, "conditions": ["a", "b"], "lives_alone": False}

    def test_severity_rises_as_spo2_falls(self):
        base = (95.0, 1.0)
        values = [94, 92, 90, 87]
        sev = [sc.severity_vital("spo2", v, self.patient, base, None, CFG)[0] for v in values]
        self.assertEqual(sev, sorted(sev))
        self.assertTrue(all(0 <= s <= 1 for s in sev))

    def test_personal_baseline_discounts_a_usual_reading(self):
        typical = sc.severity_vital("hr", 96, self.patient, (95.0, 4.0), None, CFG)[0]
        unusual = sc.severity_vital("hr", 96, self.patient, (70.0, 4.0), None, CFG)[0]
        self.assertLess(typical, unusual)

    def test_worsening_trend_adds_severity(self):
        flat = sc.severity_vital("temp", 38.4, self.patient, (36.8, 0.2), 0.0, CFG)[0]
        rising = sc.severity_vital("temp", 38.4, self.patient, (36.8, 0.2), 0.6, CFG)[0]
        self.assertGreater(rising, flat)

    def test_beta_posterior_learns_from_outcomes(self):
        prior = sc.beta_posterior(0.3, 10, 0, 0)
        self.assertAlmostEqual(prior, 0.3)
        after_false = sc.beta_posterior(0.3, 10, 0, 10)
        after_real = sc.beta_posterior(0.3, 10, 10, 0)
        self.assertLess(after_false, prior)
        self.assertGreater(after_real, prior)

    def test_short_blips_are_less_credible(self):
        short, _ = sc.credibility_base(0.3, (0, 0), (0, 0), 5, 1, 1.0, CFG)
        long, _ = sc.credibility_base(0.3, (0, 0), (0, 0), 300, 1, 1.0, CFG)
        self.assertLess(short, long)

    def test_patient_history_shrinks_toward_type_rate(self):
        noisy, _ = sc.credibility_base(0.3, (5, 15), (0, 9), None, 1, 1.0, CFG)
        clean, _ = sc.credibility_base(0.3, (5, 15), (0, 0), None, 1, 1.0, CFG)
        self.assertLess(noisy, clean)

    def test_corroboration_noisy_or(self):
        c, used = sc.corroborate(0.2, [{"rel": 1.0, "c_base": 0.5}, {"rel": 0.5, "c_base": 0.6}], CFG)
        self.assertGreater(c, 0.2)
        self.assertLess(c, 1.0)
        self.assertEqual(len(used), 2)

    def test_devices_never_corroborate(self):
        self.assertEqual(sc.relatedness("device", "cardioresp"), 0.0)
        self.assertEqual(sc.relatedness("cardioresp", "cardioresp"), 1.0)

    def test_vulnerability_is_bounded_and_monotonic(self):
        low, _ = sc.vulnerability({"age": 60, "acuity": 1, "conditions": []}, CFG, None)
        high, _ = sc.vulnerability({"age": 92, "acuity": 5, "conditions": list("abcdef"), "lives_alone": True,
                                    "fall_history": True, "cognitive_impairment": True}, CFG, 2)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.9)
        self.assertLess(high, 1.0)

    def test_red_flags(self):
        self.assertEqual(sc.red_flags("Her speech is slurred")[0][0], "slurred")
        s, d = sc.severity_event("SYMPTOM", 0.6, {}, ["chest pain since lunch"])
        self.assertEqual(s, 1.0)
        self.assertTrue(d["critical_event"])


class CombineTests(TestCase):
    def test_score_is_monotonic_in_each_factor(self):
        base = sc.combine(0.5, 0.5, 0.5, 0.5, 0.2, CFG)[1]
        for i in range(4):
            args = [0.5, 0.5, 0.5, 0.5]
            args[i] = 0.9
            self.assertGreater(sc.combine(*args, 0.2, CFG)[1], base)

    def test_contributions_sum_to_z(self):
        z, score, parts = sc.combine(0.7, 0.4, 0.6, 0.5, 0.3, CFG)
        self.assertAlmostEqual(z, sum(p["value"] for p in parts))
        self.assertAlmostEqual(score, 100 * sc.sigmoid(z))

    def test_safety_floor(self):
        score, applied = sc.apply_floor(20.0, True, CFG)
        self.assertEqual(score, CFG["safety_floor"])
        self.assertTrue(applied)
        off = dict(CFG, safety_floor_enabled=0.0)
        self.assertEqual(sc.apply_floor(20.0, True, off)[0], 20.0)

    def test_claude_cannot_bury_a_floored_alert(self):
        claude = {"score": 5, "priority": "low", "agreement": "de-escalate", "confidence": "high"}
        final, notes = sc.final_score(CFG["safety_floor"], True, dict(claude), dict(CFG, claude_blend=1.0))
        self.assertEqual(final, CFG["safety_floor"])

    def test_claude_can_escalate_to_critical(self):
        claude = {"score": 92, "priority": "critical", "agreement": "escalate", "confidence": "high"}
        final, notes = sc.final_score(50.0, False, claude, CFG)
        self.assertEqual(final, CFG["tier_critical"])
        self.assertIn("Claude escalated to critical", notes)

    def test_sanitize_clamps_and_orders_tiers(self):
        p = sc.sanitize_params({"beta_severity": 99, "tier_critical": 60, "tier_high": 70, "nope": 1})
        self.assertEqual(p["beta_severity"], 6.0)
        self.assertLess(p["tier_high"], p["tier_critical"])
        self.assertNotIn("nope", p)


class DemoFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_demo(seed=7)

    def setUp(self):
        self.api = APIClient()

    def _queue(self):
        r = self.api.get("/api/queue/")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_grouping_cuts_the_noise(self):
        q = self._queue()
        s = q["summary"]
        self.assertGreater(s["raw_alerts"], 100)
        self.assertLess(s["incidents"], s["raw_alerts"] / 2)

    def test_real_problems_rank_above_noise(self):
        rows = self._queue()["incidents"]
        top_names = [r["patient"]["name"] for r in rows[:6]]
        self.assertIn("Robert Kowalski", top_names)  # fall, no response
        arjun = next(r for r in rows if r["patient"]["name"] == "Arjun Mehta" and r["alert_type"]["code"] == "SPO2")
        self.assertIn(arjun["tier"], ("low", "medium"))
        self.assertGreater(arjun["alert_count"], 10)

    def test_dismissing_teaches_the_model(self):
        inc = Incident.objects.filter(status=Incident.OPEN, alert_type__code="SPO2",
                                      patient__name="Thomas Leblanc").first()
        r = self.api.post(f"/api/incidents/{inc.id}/action/", {"action": "dismiss"}, format="json")
        self.assertEqual(r.status_code, 200)
        learning = r.json()["learning"]
        self.assertLess(learning["type_after"], learning["type_before"])
        self.assertLess(learning["patient_after"], learning["patient_before"])

    def test_repeats_join_the_open_incident(self):
        p = Patient.objects.get(name="Grace Adeyemi")
        t = AlertType.objects.get(code="SBP_HIGH")
        _, inc1, g1 = ingest_alert(p, t, value=171)
        _, inc2, g2 = ingest_alert(p, t, value=174)
        self.assertEqual(inc1.id, inc2.id)
        self.assertTrue(g2)

    def test_preview_does_not_save(self):
        before = ModelConfig.current()["beta_severity"]
        r = self.api.post("/api/config/preview/", {"params": {"beta_severity": 6}}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["rows"]), active_incidents().count())
        self.assertEqual(ModelConfig.current()["beta_severity"], before)

    def test_config_round_trip(self):
        r = self.api.put("/api/config/", {"params": {"beta_vulnerability": 2.2}}, format="json")
        self.assertEqual(r.json()["params"]["beta_vulnerability"], 2.2)
        self.api.post("/api/config/reset/")
        self.assertEqual(ModelConfig.current()["beta_vulnerability"], sc.DEFAULT_PARAMS["beta_vulnerability"])

    def test_scenario_corroboration_lifts_thomas(self):
        incs = list(active_incidents())
        before = score_incidents(incs)
        self.api.post("/api/simulate/scenario/", {"scenario": "deterioration"}, format="json")
        incs = list(active_incidents())
        after = score_incidents(incs)
        hr = [i for i in incs if i.patient.name == "Thomas Leblanc" and i.alert_type.code == "HR"
              and i.alert_count and i.last_seen >= timezone.now() - timedelta(minutes=2)][0]
        self.assertTrue(after[hr.id]["corroborators"])
        self.assertGreater(after[hr.id]["score"], before.get(hr.id, {"score": 0})["score"])

    def test_ingest_webhook(self):
        p = Patient.objects.get(name="Rosa Alvarez")
        r = self.api.post("/api/ingest/", {"patient_id": p.id, "alert_type": "PANIC", "source": "Pendant"},
                          format="json")
        self.assertEqual(r.status_code, 201)
        bad = self.api.post("/api/ingest/", {"patient_id": p.id, "alert_type": "NOPE"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_every_endpoint_answers(self):
        inc = active_incidents().first()
        for url in ["/api/queue/", f"/api/incidents/{inc.id}/", f"/api/incidents/{inc.id}/prompt/",
                    "/api/config/", "/api/alert-types/", "/api/patients/", "/api/stats/",
                    "/api/simulate/scenarios/", "/api/claude/status/"]:
            self.assertEqual(self.api.get(url).status_code, 200, url)
        self.assertEqual(self.api.post("/api/simulate/tick/", {}, format="json").status_code, 200)


class AutoRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_demo(seed=3)

    @override_settings(AUTO_RESEED_HOURS=3)
    def test_stale_demo_data_is_replaced_on_visit(self):
        from .models import Alert

        Alert.objects.update(raised_at=timezone.now() - timedelta(hours=10))
        body = APIClient().get("/api/queue/").json()
        self.assertGreater(body["summary"]["raw_alerts"], 100)

    @override_settings(AUTO_RESEED_HOURS=0)
    def test_off_by_default(self):
        from .models import Alert

        Alert.objects.update(raised_at=timezone.now() - timedelta(hours=10))
        body = APIClient().get("/api/queue/").json()
        self.assertEqual(body["summary"]["raw_alerts"], 0)


class ClaudeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_demo(seed=3)

    def setUp(self):
        self.api = APIClient()

    def test_normalize_cleans_bad_values(self):
        out = claude_judge.normalize({"priority": "URGENT", "score": 140, "agreement": "de_escalate",
                                      "confidence": "very", "missing_info": ["a", "b", "c", "d"]})
        self.assertEqual(out["priority"], "medium")
        self.assertEqual(out["score"], 100)
        self.assertEqual(out["agreement"], "de-escalate")
        self.assertEqual(out["confidence"], "medium")
        self.assertEqual(len(out["missing_info"]), 3)

    def test_parse_json_inside_text(self):
        self.assertEqual(claude_judge._parse_json('Here you go: {"a": 1}')["a"], 1)

    @override_settings(ANTHROPIC_API_KEY="")
    def test_judge_without_key_explains_setup(self):
        claude_judge._client = None
        inc = active_incidents().first()
        r = self.api.post(f"/api/incidents/{inc.id}/judge/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "not_configured")

    def test_judgement_is_saved_and_blended(self):
        inc = Incident.objects.filter(status=Incident.OPEN, alert_type__code="MISSED_MED",
                                      patient__name="Samuel Brooks").first()
        fake = claude_judge.normalize({
            "priority": "critical", "score": 90, "agreement": "escalate", "headline": "Time-critical dose",
            "rationale": "x", "recommended_action": "Call now.", "notify": "RN", "respond_within_minutes": 15,
            "confidence": "high", "likely_false_alarm": False, "missing_info": [],
        })
        with mock.patch.object(claude_judge, "call_claude", return_value=fake):
            r = self.api.post(f"/api/incidents/{inc.id}/judge/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["claude"]["agreement"], "escalate")
        self.assertGreaterEqual(body["score"], ModelConfig.current()["tier_critical"])
        self.assertTrue(any(e["kind"] == "claude" for e in body["events"]))

    @override_settings(ANTHROPIC_API_KEY="test-key", CLAUDE_HOURLY_LIMIT=1)
    def test_hourly_cap_stops_runaway_claude_use(self):
        from django.core.cache import cache

        cache.clear()
        inc = active_incidents().first()
        fake = claude_judge.normalize({"priority": "medium", "score": 50, "headline": "ok"})
        with mock.patch.object(claude_judge, "call_claude", return_value=fake) as call:
            first = self.api.post(f"/api/incidents/{inc.id}/judge/")
            second = self.api.post(f"/api/incidents/{inc.id}/judge/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "rate_limited")
        self.assertEqual(call.call_count, 1)
        cache.clear()

    def test_prompt_contains_the_model_evidence(self):
        inc = active_incidents().first()
        r = self.api.get(f"/api/incidents/{inc.id}/prompt/")
        text = r.json()["user"]
        self.assertIn("MODEL ASSESSMENT", text)
        self.assertIn("Log-odds contributions", text)
        self.assertIn("synthetic", text)


class AdminTests(TestCase):
    def test_model_settings_form_renders_and_saves(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("admin", "admin@example.com", "pass-for-tests-123")
        self.client.login(username="admin", password="pass-for-tests-123")
        obj = ModelConfig.load()
        url = f"/admin/triage/modelconfig/{obj.pk}/change/"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="beta_severity"')
        data = {k: v for k, v in sc.DEFAULT_PARAMS.items() if k not in ("safety_floor_enabled", "claude_enabled", "claude_can_escalate")}
        data.update({"safety_floor_enabled": "on", "claude_enabled": "on", "claude_can_escalate": "on", "beta_severity": "3.6", "updated_by": "test"})
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ModelConfig.current()["beta_severity"], 3.6)
