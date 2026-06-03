"""Ollama-backed coaching for HomeFit."""

import json
import requests

OLLAMA_URL = "http://192.168.68.56:11434"
MODEL = "llama3.1:8b"

SYSTEM_PROMPT = """You are a personal fitness coach embedded in HomeFit, a home workout app.
You have access to the user's complete fitness profile and workout history.
Be concise, encouraging, and specific — always reference their actual data.
Give practical advice they can act on immediately.
Never suggest exercises outside their available equipment or that conflict with their limitations.
When discussing weights, always use lbs."""


def _build_context(coaching_data):
    profile = coaching_data.get("profile") or {}
    workouts = coaching_data.get("recent_workouts") or []
    weight_history = coaching_data.get("weight_history") or []

    lines = [
        f"User profile:",
        f"- Fitness level: {profile.get('fitness_level', 'unknown')}",
        f"- Current weight: {profile.get('current_weight')} lbs, Goal: {profile.get('goal_weight')} lbs",
        f"- Days per week: {profile.get('days_per_week')}",
        f"- Equipment: {', '.join(profile.get('equipment') or [])}",
        f"- Limitations: {', '.join(profile.get('limitations') or []) or 'none'}",
        f"- Ignored exercises: {', '.join(profile.get('ignored_exercises') or []) or 'none'}",
        "",
    ]

    if weight_history:
        start = weight_history[0]["weight"]
        current = weight_history[-1]["weight"]
        lines.append(f"Weight trend: {start} lbs → {current} lbs over {len(weight_history)} entries")
        lines.append("")

    if workouts:
        lines.append(f"Recent workouts ({len(workouts)} total):")
        for w in workouts[:5]:
            exercises = w.get("exercises", [])
            completed = [e for e in exercises if e.get("completed")]
            ex_names = []
            for ex in completed[:4]:
                sets = ex.get("sets") or []
                weighted = [s for s in sets if s.get("weight")]
                if weighted:
                    avg_w = sum(s["weight"] for s in weighted) / len(weighted)
                    ex_names.append(f"{ex.get('id', '?')} @{avg_w:.0f}lbs")
                else:
                    ex_names.append(ex.get("id", "?"))
            duration = f"{w.get('duration_seconds', 0) // 60}min"
            lines.append(f"- {w.get('completed_at', '')[:10]} ({duration}): {', '.join(ex_names)}")
        lines.append("")

    return "\n".join(lines)


def is_available():
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return resp.ok
    except Exception:
        return False


def chat(message, coaching_data, history=None):
    """Send a message to the coach and return the response."""
    context = _build_context(coaching_data)
    system = f"{SYSTEM_PROMPT}\n\n{context}"

    messages = [{"role": "system", "content": system}]
    for turn in (history or []):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": MODEL, "messages": messages, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def generate_workout(coaching_data, exercise_library):
    """Use the LLM to generate a personalised workout plan."""
    profile = coaching_data.get("profile") or {}
    context = _build_context(coaching_data)

    # Build a compact exercise reference the LLM can pick from
    available_equipment = set(profile.get("equipment") or [])
    ignored = set(profile.get("ignored_exercises") or [])
    limitations = profile.get("limitations") or []

    eligible = [
        e for e in exercise_library
        if e["id"] not in ignored
        and (e["equipment"] in available_equipment or e["equipment"] == "bodyweight")
    ]

    library_lines = "\n".join(
        f'  {{"id":"{e["id"]}","name":"{e["name"]}","muscle":"{e["muscle_group"]}","sets":{e["default_sets"]},"reps":{e["default_reps"]}}}'
        for e in eligible
    )

    # Summarise recent muscle groups hit so the LLM can balance
    recent_workouts = coaching_data.get("recent_workouts") or []
    recently_worked = []
    for w in recent_workouts[:3]:
        ids = [e["id"] for e in w.get("exercises", []) if e.get("completed")]
        recently_worked.append(f"{w.get('completed_at','')[:10]}: {', '.join(ids[:5])}")

    recent_text = "\n".join(recently_worked) if recently_worked else "No recent workouts."

    prompt = f"""{context}
Recent workout history (avoid overworking these muscle groups today):
{recent_text}

Available exercises (choose ONLY from this list, use the exact id values):
[
{library_lines}
]

Generate a single workout session for today. Rules:
- Choose 5-7 exercises
- Avoid muscle groups worked in the last 1-2 days
- Respect limitations: {', '.join(limitations) or 'none'}
- Vary from the most recent workout — don't repeat the same exercises
- Adjust sets/reps for {profile.get('fitness_level','beginner')} fitness level
- Give the workout a descriptive name (e.g. "Upper Pull Focus", "Leg Power Day")

Return ONLY valid JSON in this exact format, no other text:
{{
  "name": "workout name",
  "focus": "brief focus description",
  "exercises": [
    {{"id": "exercise_id", "sets": 3, "reps": 10}},
    {{"id": "exercise_id", "sets": 3, "reps": 12}}
  ]
}}"""

    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": MODEL, "prompt": prompt, "stream": False},
        timeout=90,
    )
    resp.raise_for_status()
    raw = resp.json()["response"].strip()

    # Extract JSON — LLM sometimes adds surrounding text despite instructions
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON found in LLM response")
    return json.loads(raw[start:end])


def generate_post_workout_insight(coaching_data, suggestions):
    """Generate a brief post-workout insight based on the completed session."""
    context = _build_context(coaching_data)
    workouts = coaching_data.get("recent_workouts") or []
    last = workouts[0] if workouts else {}

    overload_text = ""
    if suggestions:
        items = [f"{s['exercise_name']} ({s['current_weight']}→{s['suggested_weight']}lbs)" for s in suggestions[:3]]
        overload_text = f"\nProgressive overload opportunities: {', '.join(items)}"

    prompt = f"""{context}
The user just completed a workout: {last.get('day_name', 'Workout')} ({last.get('duration_seconds', 0) // 60} min).{overload_text}

Give a 2-3 sentence post-workout insight. Mention one specific thing they did well and one actionable tip for next time. Be encouraging but direct."""

    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": MODEL, "prompt": prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["response"]
