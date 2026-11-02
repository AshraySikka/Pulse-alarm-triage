"""
Claude second opinion.

Order of operations is deliberate: the math model scores and ranks every
alert first, so the queue works with no network and no API key. Claude then
reads one incident at a time, with the model's numbers as evidence, and
returns a structured judgement: priority, score, agree or disagree, the next
action and who should take it. The final rank blends the two (see
scoring.final_score), and the human makes the call.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.utils import timezone

log = logging.getLogger("triage")

SYSTEM_PROMPT = """You are the second-opinion layer in Pulse, an alarm triage console used by the coordination desk of a home and community care agency in Ontario, Canada. A transparent scoring model has already scored and ranked this alert. You review one incident at a time, the way an experienced RN case manager would.

Your job:
1. Check whether the model's priority makes clinical sense for this specific patient.
2. Notice what a formula cannot: combinations of alerts, what free-text reports actually say, the care plan, goals of care, and signs of device artifact.
3. Recommend the single most useful next action for the coordinator, and who should do it.

Rules:
- You support a human decision. You do not diagnose or prescribe. Write in plain language for a care coordinator.
- If anything suggests an emergency (no response after a fall, stroke signs, chest pain, severe breathing trouble with a good signal, unresponsiveness), the action is to call 911 or follow the agency emergency protocol, unless the goals of care say otherwise.
- For comfort-focused (palliative) patients, route symptoms to the palliative care team or palliative nurse line, and respect the substitute decision-maker's wishes about hospital transfer.
- Treat the model's factors as evidence. Agree when the ranking is reasonable. Escalate or de-escalate only for a reason you can state in one sentence.
- If the model applied a safety floor, do not set the priority below high.
- Calibrate the score on the model's scale: critical 80-100, high 60-79, medium 35-59, low 0-34. Keep score and priority consistent.
- Be brief. Headline under 90 characters. Rationale at most 3 sentences. The recommended action is one or two sentences and starts with a verb.
- All data is synthetic demo data."""

JUDGEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "score": {"type": "integer", "description": "0-100 on the same scale as the model"},
        "agreement": {"type": "string", "enum": ["agree", "escalate", "de-escalate"]},
        "headline": {"type": "string", "description": "One line, under 90 characters"},
        "rationale": {"type": "string", "description": "At most 3 sentences"},
        "recommended_action": {"type": "string", "description": "One or two sentences, starting with a verb"},
        "notify": {"type": "string", "description": "Who should act, for example RN case manager, 911, palliative nurse line, PSW on shift, family contact"},
        "respond_within_minutes": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "likely_false_alarm": {"type": "boolean"},
        "missing_info": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 facts that would change this judgement"},
    },
    "required": [
        "priority", "score", "agreement", "headline", "rationale", "recommended_action", "notify",
        "respond_within_minutes", "confidence", "likely_false_alarm", "missing_info",
    ],
    "additionalProperties": False,
}

PRIORITIES = ["critical", "high", "medium", "low"]


class ClaudeNotConfigured(Exception):
    pass


class ClaudeError(Exception):
    pass


def status() -> dict:
    return {
        "configured": bool(settings.ANTHROPIC_API_KEY),
        "model": settings.CLAUDE_MODEL,
        "effort": settings.CLAUDE_EFFORT,
    }


_client = None


def get_client():
    global _client
    if not settings.ANTHROPIC_API_KEY:
        raise ClaudeNotConfigured(
            "Claude is not set up. Add ANTHROPIC_API_KEY to backend/.env and restart the server."
        )
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY, base_url=settings.CLAUDE_BASE_URL, timeout=60.0, max_retries=2
        )
    return _client


def _hhmm(dt):
    return timezone.localtime(dt).strftime("%H:%M") if dt else "-"


def build_prompt(inc, res: dict, patient: dict, alerts: list, related: list[dict]) -> str:
    at = inc.alert_type
    sev = res["severity_detail"]
    cred = res["credibility_detail"]
    f = res["factors"]
    lines = ["INCIDENT"]
    lines.append(f"- Alert: {res['title']} (type {at.name}, category {at.get_category_display()})")
    if res.get("value_text"):
        lines.append(f"- Key value: {res['value_text']}")
    if sev.get("kind") == "vital":
        lines.append(f"- Threshold band {sev['band']} of 3: {sev['band_rule']}")
        b = sev.get("baseline")
        if b:
            lines.append(
                f"- Personal 14-day baseline: median {b['median']:.1f}, robust SD {b['sd']:.2f}. "
                f"This reading is {sev['z']:.1f} SD from it."
            )
        else:
            lines.append("- Not enough history for a personal baseline.")
        if sev.get("slope_per_day") is not None:
            lines.append(f"- 48-hour trend: {sev['slope_per_day']:+.2f} per day")
    else:
        for n in sev.get("notes", []):
            lines.append(f"- {n}")
    status_text = inc.get_status_display()
    if inc.assigned_to:
        status_text += f", assigned to {inc.assigned_to}"
    lines.append(
        f"- {inc.alert_count} alert(s) grouped. First at {_hhmm(inc.first_seen)}, last at {_hhmm(inc.last_seen)}. "
        f"Status: {status_text}. Unacknowledged for {res['waiting_detail']['minutes']:.0f} min."
    )
    if res.get("chattering"):
        lines.append("- Chattering: 3 or more alerts within 10 minutes.")
    lines.append("- Most recent raw alerts:")
    for a in alerts[-8:][::-1]:
        bits = [_hhmm(a.raised_at)]
        if a.value is not None:
            bits.append(f"value {a.value:g}")
        if a.message:
            bits.append(f'"{a.message}"')
        if a.duration_s is not None:
            bits.append(f"lasted {a.duration_s:.0f} s")
        if a.signal_quality is not None and a.signal_quality < 1:
            bits.append(f"signal quality {a.signal_quality:.2f}")
        if a.source:
            bits.append(f"source {a.source}")
        lines.append("    " + ", ".join(bits))

    lines.append("")
    lines.append("PATIENT (synthetic)")
    lines.append(f"- {patient['name']}, {patient['age']}, {patient['sex']}. Care acuity {patient['acuity']} of 5.")
    lines.append(f"- Conditions: {'; '.join(patient['conditions']) or 'none listed'}")
    lines.append(f"- Medications: {'; '.join(patient['medications']) or 'none listed'}")
    living = "Lives alone" if patient["lives_alone"] else "Does not live alone"
    lines.append(
        f"- {living}. Fall history: {'yes' if patient['fall_history'] else 'no'}. "
        f"Cognitive impairment: {'yes' if patient['cognitive_impairment'] else 'no'}."
    )
    if patient.get("discharge_date"):
        lines.append(f"- Last hospital discharge: {patient['discharge_date']}")
    if patient.get("hypercapnic_risk"):
        lines.append("- Prescribed SpO2 target 88-92% (scored on NEWS2 scale 2).")
    goals = "Comfort-focused (palliative)" if patient["goals_of_care"] == "comfort" else "Full treatment"
    lines.append(f"- Goals of care: {goals}")
    lines.append(f"- Care team: {patient['care_team']}")
    lines.append(f"- Care plan notes: {patient['care_notes']}")

    lines.append("")
    lines.append("OTHER ACTIVE ALERTS FOR THIS PATIENT")
    if related:
        for r in related:
            lines.append(f"- {r['title']}: {r['value_text']} (model score {r['score']:.0f}, {r['tier']})")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("MODEL ASSESSMENT")
    lines.append(
        f"- Model score {res['model_score']:.0f}/100 ({res['model_tier']}). Safety floor: "
        f"{'triggered and raised the score' if res['floor_applied'] else 'triggered, score already above it' if res['floor_triggered'] else 'not triggered'}."
    )
    lines.append(
        f"- Factors (0-1): severity {f['severity']:.2f}, credibility {f['credibility']:.2f}, "
        f"vulnerability {f['vulnerability']:.2f}, urgency {f['urgency']:.2f}, waiting {f['waiting']:.2f}"
    )
    contrib = ", ".join(f"{c['label']} {c['value']:+.2f}" for c in res["contributions"])
    lines.append(f"- Log-odds contributions: {contrib}")
    lines.append(
        f"- Learned actionable rate: {cred['r_type'] * 100:.0f}% for this alert type "
        f"({cred['type_actionable']} actionable, {cred['type_false']} false in 30 days); "
        f"{cred['r_patient'] * 100:.0f}% for this patient "
        f"({cred['patient_actionable']} actionable, {cred['patient_false']} false)."
    )
    if res.get("corroborators"):
        names = "; ".join(c["title"] for c in res["corroborators"])
        lines.append(f"- Corroborated by: {names}")

    lines.append("")
    lines.append(
        "Return your judgement as a JSON object with exactly these keys: priority, score, agreement, "
        "headline, rationale, recommended_action, notify, respond_within_minutes, confidence, "
        "likely_false_alarm, missing_info."
    )
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
    raise ClaudeError("Claude's reply was not valid JSON.")


def normalize(data: dict) -> dict:
    out = {}
    pr = str(data.get("priority", "medium")).lower()
    out["priority"] = pr if pr in PRIORITIES else "medium"
    try:
        out["score"] = max(0, min(100, int(round(float(data.get("score", 50))))))
    except (TypeError, ValueError):
        out["score"] = 50
    ag = str(data.get("agreement", "agree")).lower().replace("_", "-")
    out["agreement"] = ag if ag in ("agree", "escalate", "de-escalate") else "agree"
    out["headline"] = str(data.get("headline", "")).strip()[:140]
    out["rationale"] = str(data.get("rationale", "")).strip()
    out["recommended_action"] = str(data.get("recommended_action", "")).strip()
    out["notify"] = str(data.get("notify", "")).strip()
    try:
        out["respond_within_minutes"] = max(0, int(data.get("respond_within_minutes", 30)))
    except (TypeError, ValueError):
        out["respond_within_minutes"] = 30
    conf = str(data.get("confidence", "medium")).lower()
    out["confidence"] = conf if conf in ("low", "medium", "high") else "medium"
    out["likely_false_alarm"] = bool(data.get("likely_false_alarm", False))
    mi = data.get("missing_info") or []
    out["missing_info"] = [str(x) for x in mi][:3] if isinstance(mi, list) else []
    return out


def call_claude(prompt: str) -> dict:
    """One Messages API call with structured output. Falls back to plainer requests if a model rejects an option."""
    import anthropic

    client = get_client()
    fmt = {"type": "json_schema", "schema": JUDGEMENT_SCHEMA}
    configs = []
    if settings.CLAUDE_EFFORT in ("low", "medium", "high", "xhigh", "max"):
        configs.append({"format": fmt, "effort": settings.CLAUDE_EFFORT})
    configs += [{"format": fmt}, None]

    last = None
    for oc in configs:
        kwargs = dict(
            model=settings.CLAUDE_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        if oc:
            kwargs["output_config"] = oc
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            last = e
            log.warning("Claude rejected request options %s: %s", oc, e)
            continue
        except anthropic.AuthenticationError:
            raise ClaudeError("Claude rejected the API key. Check ANTHROPIC_API_KEY in backend/.env.")
        except anthropic.PermissionDeniedError:
            raise ClaudeError("This API key cannot use that model. Try PULSE_CLAUDE_MODEL=claude-haiku-5-5 in backend/.env.")
        except anthropic.NotFoundError:
            raise ClaudeError(f"Model '{settings.CLAUDE_MODEL}' was not found. Check PULSE_CLAUDE_MODEL in backend/.env.")
        except anthropic.RateLimitError:
            raise ClaudeError("Claude rate limit reached. Wait a minute and try again.")
        except anthropic.APIConnectionError:
            raise ClaudeError("Could not reach the Claude API. Check your internet connection.")
        except anthropic.APIStatusError as e:
            raise ClaudeError(f"Claude API error {e.status_code}. Try again in a moment.")

        if resp.stop_reason == "refusal":
            raise ClaudeError("Claude declined to judge this incident.")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        data = normalize(_parse_json(text))
        data["usage"] = {
            "input_tokens": getattr(resp.usage, "input_tokens", None),
            "output_tokens": getattr(resp.usage, "output_tokens", None),
        }
        return data
    raise ClaudeError(f"Claude could not process the request: {last}")


def judge_prompts(prompts: dict[int, str], max_workers: int = 4) -> tuple[dict[int, dict], dict[int, str]]:
    """Run several judgements in parallel. Threads only make network calls; the caller writes to the DB."""
    results, errors = {}, {}
    if not prompts:
        return results, errors
    get_client()  # fail fast if not configured
    with ThreadPoolExecutor(max_workers=min(max_workers, len(prompts))) as pool:
        futures = {pool.submit(call_claude, p): iid for iid, p in prompts.items()}
        for fut in as_completed(futures):
            iid = futures[fut]
            try:
                results[iid] = fut.result()
            except (ClaudeError, ClaudeNotConfigured) as e:
                errors[iid] = str(e)
            except Exception as e:  # noqa: BLE001 - surface anything unexpected to the UI
                log.exception("Claude judgement failed")
                errors[iid] = f"Unexpected error: {e}"
    return results, errors
