"""Evidence-aware, abstaining quality checks for BuiltHere's exercise catalog.

This module never copies reference-library text or media.  It sends BuiltHere's
own canonical movement record plus a candidate demo URL to the configured
OpenAI-compatible vision model and accepts a demo only on a high-confidence,
exact-variation match.
"""

import json
import time
from urllib.parse import quote_plus

import requests

import ai_provider
import database


ACE_LIBRARY_URL = "https://www.acefitness.org/resources/everyone/exercise-library/"
APPROVAL_CONFIDENCE = 0.90


def canonical_record(exercise):
    """Return BuiltHere's own factual specification and a public reference link."""
    equipment = exercise.get("equipment") or exercise.get("equipment_needed") or []
    if isinstance(equipment, str):
        equipment = [equipment]
    name = exercise.get("name", "")
    return {
        "id": exercise.get("id"),
        "name": name,
        "category": exercise.get("category", ""),
        "equipment": equipment,
        "instructions": exercise.get("instructions", ""),
        # Kept as an outbound reference rather than scraped or reproduced.
        "reference_name": "ACE Exercise Library",
        "reference_url": f"{ACE_LIBRARY_URL}?search={quote_plus(name)}",
    }


def _json_object(raw):
    raw = (raw or "").strip().replace("```json", "").replace("```", "").strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start < 0 or not end:
        raise ValueError("The verifier did not return a JSON result.")
    return json.loads(raw[start:end])


def verify_demo(exercise, demo_frames):
    """Ask vision to verify an exact movement match, or explicitly abstain.

    A model response can only approve a demo when it names the correct movement,
    setup, and required equipment. Any uncertainty returns needs_review.
    """
    if not demo_frames or len(demo_frames) < 2:
        return {"status": "needs_review", "confidence": 0, "reason": "No candidate demo is available."}

    record = canonical_record(exercise)
    settings = database.get_ai_provider_settings()
    base_url = ai_provider.validate_settings(settings)
    prompt = f"""You are a conservative exercise-content verifier. Compare the two candidate demo frames with BuiltHere's canonical movement record below.

Canonical record (BuiltHere-owned facts):
{json.dumps(record, ensure_ascii=False)}

Return JSON only with this shape:
{{"exact_match": true|false, "confidence": 0.0-1.0, "reason": "short explanation", "instructions": "original concise BuiltHere instructions"}}

Rules:
- Approve only an exact movement variation and equipment/setup match.
- A wall push-up is not an incline push-up; a band curl is not a dumbbell curl; a dead hang is not a one-arm hang.
- If frames are unclear, incomplete, or you cannot inspect them, exact_match must be false.
- Instructions must be original wording, no more than three sentences, based only on the canonical record. Do not give medical advice or invent contraindications.
"""
    content = [{"type": "text", "text": prompt}]
    content.extend({"type": "image_url", "image_url": {"url": frame}} for frame in demo_frames[:2])
    response = None
    # PeakAI and many hosted OpenAI-compatible gateways can return a temporary
    # 5xx while a vision worker is warming up. Retry only those transient
    # server-side failures; bad credentials or malformed requests still fail.
    for attempt in range(3):
        response = requests.post(
            ai_provider.chat_completions_url(base_url),
            headers=ai_provider.headers(settings["api_key"]),
            json={"model": settings["vision_model"], "messages": [{"role": "user", "content": content}], "stream": False},
            timeout=45,
        )
        if response.status_code < 500:
            break
        if attempt < 2:
            time.sleep(2 ** attempt)
    response.raise_for_status()
    result = _json_object(response.json()["choices"][0]["message"]["content"])
    confidence = float(result.get("confidence") or 0)
    exact = result.get("exact_match") is True
    status = "approved" if exact and confidence >= APPROVAL_CONFIDENCE else "needs_review"
    return {
        "status": status,
        "confidence": max(0, min(1, confidence)),
        "reason": str(result.get("reason") or "The verifier could not confirm an exact match.")[:500],
        "instructions": str(result.get("instructions") or "").strip()[:1200],
        "reference": {"name": record["reference_name"], "url": record["reference_url"]},
    }
