"""AI coaching for HomeFit — routed through PeakAI (OpenAI-compatible)."""

import json
import requests

PEAKAI_URL = "http://192.168.68.33:4000"
PEAKAI_API_KEY = "peak-homelab-key"
PEAKAI_MODEL = "claude-haiku"

SYSTEM_PROMPT = """You are APEX, a personal AI fitness coach embedded in HomeFit.
You have access to the user's complete fitness profile and workout history.
Be concise, encouraging, and specific — always reference their actual data.
Give practical advice they can act on immediately.
Never suggest exercises outside their available equipment or that conflict with their limitations.
When discussing weights, always use lbs.
IMPORTANT: You cannot save plans yourself. When you propose a plan change, always end with "Say 'save the change' to commit it." Never claim a plan has been saved unless the user has explicitly asked you to save/commit/update it."""


def _peakai_call(messages, max_tokens=1024, timeout=90):
    resp = requests.post(
        f"{PEAKAI_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {PEAKAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": PEAKAI_MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _strip_code_fences(text):
    """Remove markdown code fences the model sometimes wraps JSON in."""
    import re
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _parse_json_safe(raw):
    """Extract and parse JSON from raw text, repairing common issues."""
    import re
    raw = _strip_code_fences(raw)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    candidate = raw[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
        return json.loads(fixed)


def _generate(prompt, json_mode=False, system=None, max_tokens=1024, timeout=90):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _peakai_call(messages, max_tokens=max_tokens, timeout=timeout)


def is_available():
    try:
        resp = requests.get(f"{PEAKAI_URL}/v1/models", timeout=3)
        return resp.ok
    except Exception:
        return False


def _build_context(coaching_data):
    from datetime import date as _date
    profile = coaching_data.get("profile") or {}
    workouts = coaching_data.get("recent_workouts") or []
    weight_history = coaching_data.get("weight_history") or []
    apex_plan = coaching_data.get("apex_plan") or None
    nutrition_log = coaching_data.get("nutrition_log") or []

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    # Prefer client-supplied local date (avoids UTC offset issues)
    local_date_str = coaching_data.get("local_date")
    local_day_str = coaching_data.get("local_day")
    if local_date_str and local_day_str:
        today_label = f"{local_day_str}, {local_date_str}"
        try:
            today = _date.fromisoformat(local_date_str)
        except Exception:
            today = _date.today()
    else:
        today = _date.today()
        today_label = f"{day_names[today.weekday()]}, {today.isoformat()}"

    lines = [
        f"Today is {today_label} (user's local time).",
        "",
        f"User profile:",
        f"- Fitness level: {profile.get('fitness_level', 'unknown')}",
        f"- Current weight: {profile.get('current_weight')} lbs, Goal: {profile.get('goal_weight')} lbs",
        f"- Days per week: {profile.get('days_per_week')}",
        f"- Fitness goal: {profile.get('fitness_goal', 'general')}",
        f"- Target workout length: {profile.get('workout_duration_target', 45)} minutes",
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

    if nutrition_log:
        lines.append("Recent nutrition (from Sparky food diary):")
        for day in nutrition_log[:5]:
            meals = ", ".join(f"{m}: {', '.join(foods)}" for m, foods in day.get("meals", {}).items())
            lines.append(
                f"- {day['date']}: {day['calories']} kcal | "
                f"{day['protein_g']}g protein | {day['carbs_g']}g carbs | {day['fat_g']}g fat"
                + (f" | {meals}" if meals else "")
            )
        lines.append("")

    if apex_plan:
        today_idx = today.weekday()  # 0=Monday
        lines.append("Weekly plan:")
        for i, day in enumerate(apex_plan[:7]):
            marker = " ← TODAY" if i == today_idx else ""
            if day.get("rest"):
                lines.append(f"- {day_names[i]}: Rest Day{marker}")
            else:
                ex_list = ", ".join(e.get("name", e.get("id", "?")) for e in day.get("exercises", [])[:4])
                lines.append(f"- {day_names[i]}: {day.get('name', '')} — {ex_list}{marker}")
        lines.append("")

    return "\n".join(lines)


def chat(message, coaching_data, history=None):
    context = _build_context(coaching_data)
    system = f"{SYSTEM_PROMPT}\n\n{context}"
    messages = [{"role": "system", "content": system}]
    for turn in (history or []):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})
    return _peakai_call(messages, max_tokens=1024, timeout=60)


def generate_workout(coaching_data, exercise_library, focus=None):
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

    fitness_goal = profile.get("fitness_goal", "general")
    duration_target = int(profile.get("workout_duration_target") or 45)
    # Estimate exercise count from target duration (avg ~6-8 min per exercise including rest)
    ex_count = max(3, min(10, duration_target // 7))

    goal_guidance = {
        "weight_loss": "Higher reps (12-20), shorter rest, circuit-style. Prioritise compound movements and keep intensity high.",
        "muscle_building": "Lower reps (6-10), heavier sets, longer rest. Focus on progressive overload with compound lifts.",
        "toning": "Moderate reps (10-15), moderate rest. Mix compound and isolation exercises. Keep volume consistent.",
        "general": "Balanced reps (8-12), standard rest. Mix of compound and isolation movements.",
    }.get(fitness_goal, "Balanced approach.")

    focus_line = f"- The user has requested a focus on: {focus}. Prioritise exercises that match this focus." if focus else "- Choose a focus based on what muscle groups need the most rest/attention given their history."

    prompt = f"""{context}
Recent workout history (avoid overworking these muscle groups today):
{recent_text}

Available exercises (choose ONLY from this list, use the exact id values):
[
{library_lines}
]

You MUST select EXACTLY {ex_count} exercises — no more, no fewer.
{focus_line}
- Avoid muscle groups worked in the last 1-2 days unless the user explicitly requested that focus
- Respect limitations: {', '.join(limitations) or 'none'}
- Vary from the most recent workout — don't repeat exercises
- Fitness goal is {fitness_goal}: {goal_guidance}
- Adjust sets/reps for {profile.get('fitness_level','beginner')} fitness level
- Give the workout a descriptive name (e.g. "Upper Pull Focus", "Leg Power Day")

Respond with ONLY a raw JSON object. No markdown fences, no explanation, nothing before or after the JSON.
The "exercises" array must contain exactly {ex_count} objects.

{{
  "name": "workout name",
  "focus": "brief focus description",
  "exercises": [
{chr(10).join(f'    {{"id": "exercise_id_{i+1}", "sets": 3, "reps": 10}}{"," if i < ex_count-1 else ""}' for i in range(ex_count))}
  ]
}}"""

    raw = _generate(prompt, json_mode=True, max_tokens=2048, timeout=90)
    result = _parse_json_safe(raw)
    if len(result.get("exercises", [])) < 3:
        raise ValueError(f"Too few exercises returned: {len(result.get('exercises', []))}")
    return result


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

    return _generate(prompt, system=SYSTEM_PROMPT, timeout=60)


def generate_weekly_plan(coaching_data, exercise_library):
    """Generate a structured 7-day workout plan."""
    profile = coaching_data.get("profile") or {}
    context = _build_context(coaching_data)

    days_per_week = int(profile.get("days_per_week") or 4)
    duration_target = int(profile.get("workout_duration_target") or 45)
    fitness_goal = profile.get("fitness_goal", "general")
    fitness_level = profile.get("fitness_level", "beginner")

    available_equipment = set(profile.get("equipment") or [])
    ignored = set(profile.get("ignored_exercises") or [])
    eligible = [
        e for e in exercise_library
        if e["id"] not in ignored
        and (e["equipment"] in available_equipment or e["equipment"] == "bodyweight")
    ]
    library_lines = "\n".join(
        f'  {{"id":"{e["id"]}","name":"{e["name"]}","muscle":"{e["muscle_group"]}"}}'
        for e in eligible
    )

    ex_count = max(3, min(10, duration_target // 7))
    rest_days = 7 - days_per_week

    prompt = f"""{context}

Available exercises (use exact id values):
[
{library_lines}
]

Create a 7-day workout plan for a {fitness_level} with goal: {fitness_goal}.
- {days_per_week} training days, {rest_days} rest days
- Each workout: EXACTLY {ex_count} exercises, ~{duration_target} minutes
- Balance muscle groups across the week — no two consecutive days hitting the same primary muscles
- Rest days should be labelled "Rest Day" with no exercises

Respond with ONLY raw JSON — no markdown, no explanation:
{{
  "plan": [
    {{
      "day": 1,
      "name": "Upper Body Power",
      "focus": "chest, shoulders, triceps",
      "rest": false,
      "exercises": [
        {{"id": "exercise_id_1", "sets": 3, "reps": 10}},
        {{"id": "exercise_id_2", "sets": 3, "reps": 12}},
        {{"id": "exercise_id_3", "sets": 4, "reps": 8}},
        {{"id": "exercise_id_4", "sets": 3, "reps": 10}},
        {{"id": "exercise_id_5", "sets": 3, "reps": 12}},
        {{"id": "exercise_id_6", "sets": 3, "reps": 15}}
      ]
    }},
    {{"day": 2, "name": "Rest Day", "focus": "recovery", "rest": true, "exercises": []}}
  ]
}}

The plan array must have exactly 7 items (one per day). Training days need exactly {ex_count} exercises each."""

    raw = _generate(prompt, json_mode=True, max_tokens=4096, timeout=120)
    result = _parse_json_safe(raw)
    if len(result.get("plan", [])) != 7:
        raise ValueError(f"Expected 7 days, got {len(result.get('plan', []))}")
    return result


PLAN_SAVE_PHRASES = [
    "set this as my plan", "save this plan", "save this as my plan",
    "use this plan", "make this my plan", "let's get after it",
    "lets get after it", "save it", "set it", "commit to this",
    "lock it in", "go with this", "implement this plan",
    "commit the change", "commit this change", "commit these changes",
    "update my plan", "update the plan", "save the change", "save the update",
    "save these changes", "apply this", "apply the change", "apply changes",
    "sounds good", "save that", "save this schedule",
]


def wants_to_save_plan(message):
    m = message.lower()
    return any(phrase in m for phrase in PLAN_SAVE_PHRASES)


def extract_plan_from_chat(history, exercise_library, current_plan=None):
    """Extract or update a 7-day plan from conversation history."""
    # Only look at recent APEX messages where the plan was described
    recent = history[-8:]
    apex_msgs = "\n\n".join(
        m["content"][:1000] for m in recent if m["role"] == "assistant"
    )[-2000:]

    lookup = "\n".join(f'  "{e["name"]}" -> "{e["id"]}"' for e in exercise_library[:50])

    if current_plan:
        # Strip to compact form — only id/sets/reps to keep output small
        compact = []
        for d in current_plan:
            exs = [{"id": e.get("id"), "sets": e.get("sets", 3), "reps": e.get("reps", 10)}
                   for e in d.get("exercises", []) if e.get("id")]
            compact.append({"day": d.get("day"), "name": d.get("name"), "focus": d.get("focus", ""),
                            "rest": d.get("rest", False), "exercises": exs})
        current_json = json.dumps(compact)
        prompt = f"""Update this 7-day plan based on the changes described. Return compact JSON only.

CURRENT PLAN (compact): {current_json}

CHANGES TO APPLY:
{apex_msgs}

Rules: Apply ONLY the described changes. Keep other days identical. Map exercise names to IDs:
{lookup}

Return ONLY: {{"plan":[{{"day":1,"name":"...","focus":"...","rest":false,"exercises":[{{"id":"...","sets":3,"reps":10}}]}},...]}}
Must have exactly 7 items."""
    else:
        prompt = f"""Extract a 7-day workout plan from these coach messages. Return compact JSON only.

MESSAGES:
{apex_msgs}

Map names to IDs: {lookup}

Return ONLY: {{"plan":[{{"day":1,"name":"...","focus":"...","rest":false,"exercises":[{{"id":"...","sets":3,"reps":10}}]}},...]}}
Must have exactly 7 items. Use rest:true for rest days."""

    raw = _generate(prompt, json_mode=True, max_tokens=2048, timeout=90)
    result = _parse_json_safe(raw)
    plan = result.get("plan", [])
    if len(plan) < 5:
        raise ValueError(f"Only got {len(plan)} days")
    while len(plan) < 7:
        plan.append({"day": len(plan)+1, "name": "Rest Day", "focus": "recovery", "rest": True, "exercises": []})
    return {"plan": plan[:7]}
