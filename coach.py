"""AI coaching for HomeFit — routed through PeakAI (OpenAI-compatible)."""

import json
import os
import requests

import database

PEAKAI_URL = os.environ.get("PEAKAI_URL", "http://192.168.68.33:4000").rstrip("/")
PEAKAI_API_KEY = os.environ.get("PEAKAI_API_KEY", "peak-homefit-key")
PEAKAI_MODEL = os.environ.get("PEAKAI_MODEL", "claude-sonnet")
# Only the STRUCTURED/interactive work (workout generation, coach chat/plan-edits) needs
# Sonnet. The high-volume prose (post-workout insights, brief, digest, nudges) goes to a
# cheap model — "only fire Sonnet when it's needed."
MODEL_CHEAP = os.environ.get("PEAKAI_CHEAP_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """You are APEX, a personal AI fitness coach embedded in HomeFit.
You have access to the user's complete fitness profile and workout history.
Be concise, encouraging, and specific — always reference their actual data.
Give practical advice they can act on immediately.
The data block below is LIVE and authoritative — it is rebuilt fresh on every message. If a workout dated today appears in "Recent workouts," it IS logged; treat it as fact and discuss it. Never tell the user there is a "sync delay," that something isn't logged yet, or that you can't see a workout that is present in the data — and never let an earlier reply of yours override the current data block. Re-check the data on every message.
Never suggest exercises outside their available equipment or that conflict with their limitations.
When discussing weights, always use lbs.
You keep a persistent MEMORY about this user (shown in the data block when present) that carries across all conversations — use it to be personal, warm, and consistent, like a coach who actually knows them. If the user shares something durable worth remembering (a goal, an injury, a preference, life context, a milestone), weave it in; they can also say "remember that ..." to make you save it.
IMPORTANT: You cannot save plans yourself. When you propose a plan change, always end with "Say 'save the change' to commit it." Never claim a plan has been saved unless the user has explicitly asked you to save/commit/update it."""


def _peakai_call(messages, max_tokens=1024, timeout=90, model=None):
    resp = requests.post(
        f"{PEAKAI_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {PEAKAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or PEAKAI_MODEL,
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


def _generate(prompt, json_mode=False, system=None, max_tokens=1024, timeout=90, model=None):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _peakai_call(messages, max_tokens=max_tokens, timeout=timeout, model=model)


def is_available():
    try:
        resp = requests.get(f"{PEAKAI_URL}/v1/models", timeout=3)
        return resp.ok
    except Exception:
        return False


def _uid(coaching_data):
    return (coaching_data.get("profile") or {}).get("user_id")


def _user_now(coaching_data):
    """Current naive datetime in the user's timezone (profile.timezone)."""
    uid = _uid(coaching_data)
    if uid:
        return database.user_now(uid)
    from datetime import datetime as _dt
    return _dt.now()


def _today_iso(coaching_data):
    """User's local 'today' as ISO, preferring the client-supplied date."""
    from datetime import date as _date
    ds = coaching_data.get("local_date")
    if ds:
        try:
            return _date.fromisoformat(ds).isoformat()
        except Exception:
            pass
    return _user_now(coaching_data).date().isoformat()


def _build_context(coaching_data):
    from datetime import date as _date
    profile = coaching_data.get("profile") or {}
    workouts = coaching_data.get("recent_workouts") or []
    weight_history = coaching_data.get("weight_history") or []
    apex_plan = coaching_data.get("apex_plan") or None
    nutrition_log = coaching_data.get("nutrition_log") or []
    hydration_log = coaching_data.get("hydration_log") or []
    nutrition_goals = coaching_data.get("nutrition_goals") or {}

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    # Prefer client-supplied local date (avoids UTC offset issues)
    local_date_str = coaching_data.get("local_date")
    local_day_str = coaching_data.get("local_day")
    if local_date_str and local_day_str:
        today_label = f"{local_day_str}, {local_date_str}"
        try:
            today = _date.fromisoformat(local_date_str)
        except Exception:
            today = _user_now(coaching_data).date()
    else:
        today = _user_now(coaching_data).date()
        today_label = f"{day_names[today.weekday()]}, {today.isoformat()}"

    # Time of day so APEX greets/advises correctly (it was telling users to
    # "get some sleep" in the morning because it only knew the date, not the
    # hour). Uses profile.timezone; prefers a client-supplied time if present.
    now_local = _user_now(coaching_data)
    clock_str = coaching_data.get("local_time")  # e.g. "07:42"
    try:
        if clock_str:
            hh, mm = (int(x) for x in clock_str.split(":")[:2])
            now_local = now_local.replace(hour=hh, minute=mm)
    except Exception:
        pass
    hour = now_local.hour
    part = ("the middle of the night" if hour < 5 else "early morning" if hour < 8
            else "morning" if hour < 12 else "afternoon" if hour < 17
            else "evening" if hour < 21 else "night")

    lines = [
        f"Today is {today_label} (user's local time).",
        f"It is currently {now_local.strftime('%-I:%M %p')} — {part}. Greet and "
        f"advise for THIS time of day; only suggest sleep/rest/winding down in the "
        f"evening or night, never in the morning or daytime.",
        "",
    ]

    memory = (coaching_data.get("apex_memory") or "").strip()
    if memory:
        lines += [
            "What you remember about this user (your persistent memory across all "
            "conversations — use it to be personal and consistent):",
            memory,
            "",
        ]

    lines += [
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

    from datetime import timedelta as _td
    dow_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def _dow(iso):
        try:
            return dow_short[_date.fromisoformat((iso or "")[:10]).weekday()]
        except Exception:
            return "?"

    # Pre-compute THIS WEEK's sessions so APEX never does its own (error-prone)
    # date math. Week runs Monday→Sunday. HomeFit workouts always count; an
    # external/Apple-Health session counts toward the weekly goal ONLY if it's a
    # real workout (>=45 min) — golf and long cardio count, short daily walks do not.
    monday = today - _td(days=today.weekday())
    monday_iso = monday.isoformat()
    externals = coaching_data.get("external_workouts") or []
    goal_days = profile.get("days_per_week") or 4
    hf_week = [w for w in workouts if (w.get("completed_at") or "")[:10] >= monday_iso]
    # started_at is naive UTC — bucket by the USER's local day (external_local_date)
    # or evening cardio gets credited to the next day.
    uid = _uid(coaching_data)
    ext_week = [c for c in externals
                if database.external_local_date(c.get("started_at"), uid) >= monday_iso
                and database.counts_as_workout_session(c)]
    workout_days = {(w.get("completed_at") or "")[:10] for w in hf_week}
    workout_days |= {database.external_local_date(c.get("started_at"), uid) for c in ext_week}
    workout_days.discard("")
    sessions = len(workout_days)
    hf_list = ", ".join(f"{_dow(w.get('completed_at',''))} {w.get('day_name') or 'workout'}"
                        for w in hf_week) or "none yet"
    ext_list = ", ".join(f"{_dow(database.external_local_date(c.get('started_at'), uid))} "
                         f"{c.get('workout_type','activity')} "
                         f"({c.get('duration_minutes')}min)" for c in ext_week) or "none"
    # Separate cardio-days goal: distinct days this week with ANY logged cardio/walk.
    cardio_goal = profile.get("cardio_days_per_week") or 5
    cardio_days = sorted({d for d in (database.external_local_date(c.get("started_at"), uid)
                                      for c in externals) if d >= monday_iso})
    cardio_dow = ", ".join(_dow(d) for d in cardio_days) or "none yet"
    # Away mode (travel/vacation/sick) relaxes both weekly goals: each paused
    # day this week reduces the targets by one (floor 0 = week fully excused).
    week_pause = coaching_data.get("week_pause") or {}
    paused_days = week_pause.get("paused_days") or 0
    goal_days_wk = max(0, goal_days - paused_days)
    cardio_goal_wk = max(0, cardio_goal - paused_days)
    pause_note = (f" (reduced from {goal_days} and {cardio_goal} — {paused_days} "
                  f"away-mode day(s) this week)" if paused_days else "")
    lines += [
        f"THIS WEEK so far (Monday {monday_iso} through today) — USE THESE NUMBERS, do not recount by hand:",
        f"- Workout days toward the {goal_days_wk}x/week goal: {sessions} of {goal_days_wk}",
        f"    · HomeFit workouts: {hf_list}",
        f"    · External sessions counted (Apple-recorded workouts like golf, or any session ≥45 min): {ext_list}",
        f"- Cardio/walk days toward the {cardio_goal_wk}x/week cardio goal: {len(cardio_days)} of {cardio_goal_wk} ({cardio_dow})",
        f"Two separate weekly goals{pause_note}: (1) {goal_days_wk} WORKOUT days (HomeFit workouts + Apple-recorded "
        f"workouts/long ≥45-min sessions; plain walks don't count); (2) {cardio_goal_wk} CARDIO days (any day "
        f"with a logged walk/cardio counts, one per day). Use these exact counts; never count anything "
        f"dated before {monday_iso} toward this week.",
        "",
    ]

    away = coaching_data.get("away_mode")
    if away:
        reason = away.get("reason") or "away"
        until = (f"through {away['end_date']}" if away.get("end_date")
                 else "until they say they're back")
        if reason == "sick":
            guidance = ("They're SICK: prioritize rest, fluids and recovery. No training "
                        "pressure at all; when they feel better, suggest easing back in light.")
        else:
            guidance = ("They're traveling: keep it totally low-pressure. Only offer "
                        "hotel/bodyweight or walking ideas if THEY ask.")
        lines += [
            f"AWAY MODE IS ON ({reason}, {until}). Streaks and weekly goals are paused — "
            f"never guilt them about missed workouts or goals during this window. {guidance}",
            "",
        ]

    if workouts:
        lines.append("Recent HomeFit workouts (newest first):")
        for w in workouts[:6]:
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
            w_date = w.get('completed_at', '')[:10]
            today_tag = " [TODAY]" if w_date == today.isoformat() else ""
            name = w.get('day_name', '')
            name_part = f"{name} — " if name else ""
            lines.append(f"- {_dow(w_date)} {w_date}{today_tag} ({duration}): {name_part}{', '.join(ex_names)}")
        lines.append("")

    if externals:
        lines.append("Recent Apple Health activity (NOT HomeFit gym sessions). Each is tagged: "
                     "[CARDIO] = counts toward the cardio-days goal only; [WORKOUT] = an Apple-recorded "
                     "workout (e.g. golf) or long session that ALSO counts as a workout day. Walks are "
                     "CARDIO, not workouts — never describe a walk as a workout:")
        for c in externals[:6]:
            d = database.external_local_date(c.get("started_at"), uid)
            kcal = f", {c.get('kcal')} kcal" if c.get("kcal") else ""
            tag = "[WORKOUT]" if database.counts_as_workout_session(c) else "[CARDIO]"
            lines.append(f"- {_dow(d)} {d}: {c.get('workout_type','activity')} {c.get('duration_minutes')}min{kcal} {tag}")
        lines.append("")

    if nutrition_goals:
        lines.append("Nutrition targets (the user's daily goals):")
        lines.append(
            f"- Calories: {nutrition_goals.get('calories')} kcal | "
            f"Protein: {nutrition_goals.get('protein_g')}g | "
            f"Carbs: {nutrition_goals.get('carbs_g')}g | "
            f"Fat: {nutrition_goals.get('fat_g')}g"
            + (f" | Water: {round(nutrition_goals['water_ml']/1000, 1)}L" if nutrition_goals.get('water_ml') else "")
        )
        lines.append("")
        lines.append("Note: You can propose updated nutrition goals. When you do, state them clearly as:")
        lines.append("  Calories: X kcal, Protein: Xg, Carbs: Xg, Fat: Xg")
        lines.append("Then tell the user to say 'update my goals' to apply them.")
        lines.append("")

    if nutrition_log:
        lines.append("Recent nutrition (logged in HomeFit):")
        for day in nutrition_log[:5]:
            meals = ", ".join(f"{m}: {', '.join(foods)}" for m, foods in day.get("meals", {}).items())
            lines.append(
                f"- {day['date']}: {day['calories']} kcal | "
                f"{day['protein_g']}g protein | {day['carbs_g']}g carbs | {day['fat_g']}g fat"
                + (f" | {meals}" if meals else "")
            )
        lines.append("")

    if hydration_log:
        lines.append("Recent hydration:")
        for day in hydration_log[:5]:
            liters = day["water_ml"] / 1000
            flag = " (low)" if liters < 1.5 else ""
            lines.append(f"- {day['date']}: {liters:.1f}L{flag}")
        lines.append("")

    readiness = coaching_data.get("readiness")
    if readiness:
        lines.append(f"Readiness today: {readiness['score']}/100 ({readiness['label']}) — {readiness['reason']}.")
        lines.append("")

    tl = coaching_data.get("training_load")
    if tl and tl.get("sessions_7d"):
        lines.append(
            f"Training load: {tl['sessions_7d']} sessions / {tl['minutes_7d']} min in the last 7 days "
            f"({tl['sessions_3d']} in the last 3)."
        )
        lines.append("If training load is high AND recent sleep is short or declining, warn about "
                     "under-recovery and recommend a lighter/deload day or rest.")
        lines.append("")

    sleep_log = coaching_data.get("sleep_log") or []
    if sleep_log:
        lines.append("Recent sleep (Apple Health / Oura):")
        for night in sleep_log[:7]:
            h, m = divmod((night.get("duration_seconds") or 0) // 60, 60)
            extra = []
            if night.get("deep_seconds"):
                dh, dm = divmod(night["deep_seconds"] // 60, 60)
                extra.append(f"deep {dh}h{dm:02d}m")
            if night.get("rem_seconds"):
                rh, rm = divmod(night["rem_seconds"] // 60, 60)
                extra.append(f"REM {rh}h{rm:02d}m")
            tail = f" ({', '.join(extra)})" if extra else ""
            flag = " — short" if (h + m / 60) < 6.5 else ""
            lines.append(f"- {night['entry_date']}: {h}h{m:02d}m asleep{tail}{flag}")
        lines.append("Use sleep to inform recovery — flag under-recovery, suggest lighter days or rest when sleep is short or deep/REM is low.")
        lines.append("")

    energy_log = coaching_data.get("energy_log") or []
    if energy_log:
        lines.append("Recent Apple Health energy (active 'move' + resting kcal):")
        for e in energy_log[:5]:
            parts = []
            if e.get("active") is not None:
                parts.append(f"{e['active']} active")
            if e.get("resting") is not None:
                parts.append(f"{e['resting']} resting")
            if parts:
                lines.append(f"- {e['date']}: {', '.join(parts)} kcal (total ~{e.get('total', 0)})")
        lines.append("Active (move) calories reflect today's effort/NEAT — a good gauge of how active they've "
                     "actually been beyond logged workouts. Total burn (active+resting) vs logged intake gives "
                     "their rough energy balance; use it for fueling/deficit guidance, not as a hard number.")
        lines.append("")

    steps_log = coaching_data.get("steps_log") or []
    if steps_log:
        goal = coaching_data.get("step_goal")
        recent = ", ".join(f"{s['date']}: {s['steps']:,}" for s in steps_log[:5])
        lines.append(f"Recent daily steps (personal goal ~{goal:,}): {recent}.")
        lines.append("Steps are a NEAT / general-activity signal beyond logged training. The goal is their own "
                     "rolling average, so low-step days (especially rest days) are worth a gentle 'get a walk in' "
                     "nudge; consistently high steps means an active lifestyle to factor into recovery/fueling.")
        lines.append("")

    other_activity = coaching_data.get("other_activity") or []
    if other_activity:
        lines.append("Other activity (Apple Health / Oura / manual — NOT HomeFit workouts):")
        for day in other_activity[:5]:
            parts = []
            for a in day.get("activities", []):
                desc = a["name"]
                if a.get("minutes"):
                    desc += f" {a['minutes']}min"
                if a.get("kcal"):
                    desc += f" {a['kcal']}kcal"
                if a.get("avg_hr"):
                    desc += f" avgHR {a['avg_hr']}"
                parts.append(desc)
            lines.append(f"- {day['date']}: {'; '.join(parts)}")
        lines.append("")

    if apex_plan:
        today_idx = today.weekday()  # 0=Monday
        lines.append("Weekly plan — this is the user's CURRENT SAVED plan and is "
                     "AUTHORITATIVE. When the user asks about their plan or a specific "
                     "day, read it straight from here and state exactly what is listed "
                     "(full exercise list below). Never describe a different plan, deny "
                     "an exercise that is listed, or substitute what you remember "
                     "designing — the saved plan wins:")
        for i, day in enumerate(apex_plan[:7]):
            marker = " ← TODAY" if i == today_idx else ""
            if day.get("rest"):
                lines.append(f"- {day_names[i]}: Rest Day{marker}")
            else:
                ex_list = ", ".join(e.get("name", e.get("id", "?")) for e in day.get("exercises", []))
                lines.append(f"- {day_names[i]}: {day.get('name', '')} — {ex_list}{marker}")
        lines.append("")

    return "\n".join(lines)


def chat(message, coaching_data, history=None):
    context = _build_context(coaching_data)
    system = f"{SYSTEM_PROMPT}\n\n{context}"
    messages = [{"role": "system", "content": system}]
    for turn in (history or []):
        messages.append({"role": turn["role"], "content": turn["content"]})

    # Re-assert TODAY's date AFTER the history. A chat thread can span multiple
    # days; APEX otherwise anchors on the date it stated earlier in the thread
    # and thinks it's still yesterday. Placing this last gives it the most weight.
    from datetime import datetime as _dt
    _now = _dt.now()
    ld, lday = coaching_data.get("local_date"), coaching_data.get("local_day")
    _today = f"{lday}, {ld}" if (ld and lday) else _now.strftime("%A, %Y-%m-%d")
    messages.append({
        "role": "system",
        "content": (
            f"CURRENT DATE & TIME (authoritative): it is now {_today}, "
            f"{_now.strftime('%-I:%M %p')}. The conversation above may have started on a "
            f"PREVIOUS day — ignore any 'today is …' you stated earlier; anchor 'today' to "
            f"THIS date for everything (today's workout, recommendations, greetings)."
        ),
    })

    # Re-assert the live data AFTER the history so it outranks any stale
    # "I can't see it / sync delay" replies earlier in the thread.
    workouts = coaching_data.get("recent_workouts") or []
    if workouts:
        latest = workouts[0]
        if (latest.get("completed_at", "")[:10] == _today_iso(coaching_data)):
            mins = latest.get("duration_seconds", 0) // 60
            name = latest.get("day_name", "a workout")
            messages.append({
                "role": "system",
                "content": (
                    f"REMINDER: The data above is current. The user's most recent "
                    f"logged workout is TODAY: \"{name}\" ({mins} min). It IS logged and "
                    f"visible to you. Disregard any earlier message of yours claiming you "
                    f"couldn't see it or that there was a sync delay."
                ),
            })
    messages.append({"role": "user", "content": message})
    return _peakai_call(messages, max_tokens=1024, timeout=60)


def generate_workout(coaching_data, exercise_library, focus=None):
    """Use the LLM to generate a personalised workout plan."""
    profile = coaching_data.get("profile") or {}
    context = _build_context(coaching_data)

    # Build a compact exercise reference the LLM can pick from
    ignored = set(profile.get("ignored_exercises") or [])
    limitations = profile.get("limitations") or []

    eligible = [
        e for e in exercise_library
        if e["id"] not in ignored
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


def generate_post_workout_insight(coaching_data, suggestions, completed_exercises=None):
    """Generate a post-workout insight based on the completed session."""
    context = _build_context(coaching_data)
    workouts = coaching_data.get("recent_workouts") or []
    last = workouts[0] if workouts else {}

    # Build a readable summary of what was just done
    ex_lines = []
    if completed_exercises:
        for ex in completed_exercises[:8]:
            sets = ex.get("sets_logged") or []
            if sets:
                set_str = ", ".join(
                    f"{s.get('weight', 0)}lbs×{s.get('reps', 0)}" if s.get("weight") else f"{s.get('reps', 0)} reps"
                    for s in sets if s.get("reps")
                )
                if set_str:
                    ex_lines.append(f"  - {ex.get('name', ex.get('id', ''))}: {set_str}")
    ex_summary = "\nExercises completed:\n" + "\n".join(ex_lines) if ex_lines else ""

    overload_text = ""
    if suggestions:
        items = [f"{s['exercise_name']} ({s['current_weight']}→{s['suggested_weight']}lbs)" for s in suggestions[:3]]
        overload_text = f"\nReady to increase weight next session: {', '.join(items)}"

    prompt = f"""{context}
The user just finished: {last.get('day_name', 'Workout')} ({last.get('duration_seconds', 0) // 60} min).{ex_summary}{overload_text}

Write a 2-3 sentence post-workout insight. Reference something specific from their sets/weights if available. One thing done well, one concrete tip for next time. Be direct and brief — no greeting, no sign-off."""

    return _generate(prompt, system=SYSTEM_PROMPT, timeout=60, model=MODEL_CHEAP)


_NUDGE_GUIDE = {
    "untrained_plan_day":
        "It's a planned training day ({day_name}: {exercises}) and they haven't logged "
        "a workout yet. Nudge them to get it in — warm, motivating, not nagging.",
    "dinner_reminder":
        "It's evening and they've only eaten {eaten} of {goal} cal ({remaining} left), "
        "likely haven't logged dinner. Remind them to fuel up / log their meal.",
    "protein_low":
        "It's evening; calories are fine but protein is low at {protein}g of {goal}g "
        "({remaining}g short). Suggest a protein-rich choice to close the gap.",
}


def generate_nudge(coaching_data, nudge_type, facts):
    """A single short, personable push notification. Returns (title, body).

    Uses the live context (so APEX's memory + the user's name come through). Falls
    back to None if the AI is offline so the caller can use a deterministic line."""
    guide = _NUDGE_GUIDE.get(nudge_type)
    if not guide:
        return None
    try:
        situation = guide.format(
            day_name=facts.get("day_name", ""),
            exercises=", ".join(facts.get("exercises", [])) or "your session",
            eaten=facts.get("eaten", 0), goal=facts.get("goal", 0),
            remaining=facts.get("remaining", 0), protein=facts.get("protein", 0))
    except Exception:
        situation = guide
    context = _build_context(coaching_data)
    prompt = f"""{context}
Situation: {situation}

Write ONE short push notification from APEX to this user. Personable and specific
to them (use their name/memory naturally if it fits), under 120 characters, plain
text, no emoji-spam (one tasteful emoji max), no hashtags. Return ONLY JSON:
{{"title": "2-4 word title", "body": "the one-line message"}}"""
    obj = _parse_json_safe(_generate(prompt, json_mode=True, system=SYSTEM_PROMPT,
                                     max_tokens=200, timeout=45, model=MODEL_CHEAP))
    if isinstance(obj, dict) and obj.get("body"):
        title = str(obj.get("title") or "HomeFit").strip()[:40]
        body = str(obj["body"]).strip().strip('"')[:160]
        return (title, body)
    return None


def generate_weekly_digest(coaching_data):
    """Generate a weekly summary digest for the dashboard card."""
    from datetime import date, timedelta
    context = _build_context(coaching_data)
    workouts = coaching_data.get("recent_workouts") or []
    profile = coaching_data.get("profile") or {}
    uid = _uid(coaching_data)
    today = _user_now(coaching_data).date()
    days_since_monday = today.weekday()
    week_start_date = today - timedelta(days=days_since_monday)
    week_start_str = week_start_date.isoformat()

    this_week = [w for w in workouts if (w.get("completed_at") or "") >= week_start_str]
    # Long external sessions (golf / cardio >=45 min) also count toward the weekly
    # goal; short daily walks do not.
    ext_week = [c for c in (coaching_data.get("external_workouts") or [])
                if database.external_local_date(c.get("started_at"), uid) >= week_start_str
                and database.counts_as_workout_session(c)]
    week_days = {(w.get("completed_at") or "")[:10] for w in this_week}
    week_days |= {database.external_local_date(c.get("started_at"), uid) for c in ext_week}
    week_days.discard("")
    week_sessions = len(week_days)
    goal_days = int(profile.get("days_per_week") or 4)

    nutrition_log = coaching_data.get("nutrition_log") or []
    nutrition_note = ""
    if nutrition_log:
        week_nutrition = [d for d in nutrition_log if d.get("date", "") >= week_start_str]
        if week_nutrition:
            avg_cal = sum(d.get("calories", 0) for d in week_nutrition) // len(week_nutrition)
            avg_protein = sum(d.get("protein_g", 0) for d in week_nutrition) // len(week_nutrition)
            nutrition_note = f"\nNutrition avg this week: {avg_cal} kcal/day, {avg_protein}g protein/day."

    sleep_note = ""
    sleep_week = [s for s in (coaching_data.get("sleep_log") or [])
                  if (s.get("entry_date") or "") >= week_start_str]
    if sleep_week:
        avg_h = sum((s.get("duration_seconds") or 0) for s in sleep_week) / len(sleep_week) / 3600
        sleep_note = f"\nSleep this week: {len(sleep_week)} nights, avg {avg_h:.1f}h."

    tl = coaching_data.get("training_load") or {}
    cardio_goal = int(profile.get("cardio_days_per_week") or 5)
    cardio_days = {d for d in (database.external_local_date(c.get("started_at"), uid)
                               for c in (coaching_data.get("external_workouts") or []))
                   if d >= week_start_str}
    cardio_note = f"\nCardio: {len(cardio_days)} of {cardio_goal} walk/cardio days hit this week."

    activity_note = ""
    steps_week = [s for s in (coaching_data.get("steps_log") or []) if (s.get("date") or "") >= week_start_str]
    if steps_week:
        avg_steps = sum(s["steps"] for s in steps_week) // len(steps_week)
        goal = coaching_data.get("step_goal")
        activity_note += f"\nSteps avg this week: {avg_steps:,}/day" + (f" (personal goal ~{goal:,})." if goal else ".")
    energy_week = [e for e in (coaching_data.get("energy_log") or [])
                   if (e.get("date") or "") >= week_start_str and e.get("active") is not None]
    if energy_week:
        avg_active = sum(e["active"] for e in energy_week) // len(energy_week)
        activity_note += f"\nActive (move) calories avg: {avg_active}/day."

    prompt = f"""{context}

WEEKLY REVIEW for {week_start_str} to today:
- Training: {week_sessions} of {goal_days} workout days done (HomeFit workouts + Apple-recorded workouts like golf or long ≥45-min sessions); {tl.get('minutes_7d', 0)} active min over 7 days.{nutrition_note}{sleep_note}{cardio_note}{activity_note}

Write a weekly review as 4-6 short plain-text bullets (each starting with "- ", one sentence each,
no headers, no greeting, no sign-off). Cover ONLY the areas that have data:
- Training: consistency vs goal + total volume
- A standout lift/improvement from the data
- Sleep: average + consistency, and how it tracked with training
- Daily movement: steps / active calories as a NEAT signal (active lifestyle vs sedentary week)
- Nutrition: protein/calorie adherence if logged
- One specific focus for next week that ties it together (recovery-aware)."""

    return _generate(prompt, system=SYSTEM_PROMPT, timeout=60, model=MODEL_CHEAP)


def generate_daily_brief(coaching_data):
    """Short morning push: yesterday's training + nutrition, today's focus."""
    from datetime import timedelta
    context = _build_context(coaching_data)
    user_today = _user_now(coaching_data).date()
    yesterday = (user_today - timedelta(days=1)).isoformat()

    workouts = coaching_data.get("recent_workouts") or []
    trained = [w for w in workouts if (w.get("completed_at") or "").startswith(yesterday)]
    trained_note = f"trained ({trained[0].get('day_name', 'workout')})" if trained else "did not train"

    nutrition_note = ""
    for d in coaching_data.get("nutrition_log") or []:
        if d.get("date") == yesterday:
            nutrition_note = f" Ate {d.get('calories', 0)} kcal, {d.get('protein_g', 0)}g protein."
            break

    sleep_note = ""
    sleep_log = coaching_data.get("sleep_log") or []
    if sleep_log:
        n = sleep_log[0]
        secs = n.get("duration_seconds") or 0
        h, m = divmod(secs // 60, 60)
        deep = (n.get("deep_seconds") or 0) // 60
        sleep_note = f" Last night: {h}h{m:02d}m asleep, {deep}m deep sleep."

    # Yesterday's Apple Health activity: steps + active (move) calories
    activity_note = ""
    yday_steps = next((s["steps"] for s in coaching_data.get("steps_log") or []
                       if s.get("date") == yesterday), None)
    yday_energy = next((e for e in coaching_data.get("energy_log") or []
                        if e.get("date") == yesterday), None)
    if yday_steps is not None:
        goal = coaching_data.get("step_goal")
        activity_note += f" {yday_steps:,} steps" + (f" (goal ~{goal:,})" if goal else "") + "."
    if yday_energy and yday_energy.get("active") is not None:
        activity_note += f" {yday_energy['active']} active kcal."

    # Today's outlook: planned session + readiness
    today_note = ""
    plan = coaching_data.get("apex_plan")
    if plan:
        wd = user_today.weekday()
        if wd < len(plan):
            day = plan[wd]
            today_note = " Today's plan: " + ("a rest day." if day.get("rest")
                                              else f"{day.get('name', 'a workout')}.")
    readiness = coaching_data.get("readiness")
    if readiness:
        today_note += f" Readiness {readiness.get('score')} ({readiness.get('label')})."

    prompt = f"""{context}

Yesterday ({yesterday}): the user {trained_note}.{nutrition_note}{sleep_note}{activity_note}
Today:{today_note or ' (no plan set).'}

Write the user's DAILY DIGEST — a real recap of yesterday and what today looks like.
3-4 short sentences, plain text, under ~420 characters, conversational and specific.
- Recap YESTERDAY using the most notable real numbers across training, nutrition,
  sleep, steps, and active calories — call out what went well and what slipped.
- Then TODAY: state what's planned, and give a recovery-aware recommendation from
  readiness + last night's sleep (short sleep / low readiness -> dial back or
  recover; well-rested -> push the planned session). If steps were low yesterday
  and today is light, a "get a walk in" nudge fits.
Reference only data that exists. Output ONLY the digest sentences — no title, date,
header, separators, bullets, markdown, greeting, sign-off, or character count."""

    return _clean_brief(_generate(prompt, system=SYSTEM_PROMPT, max_tokens=400, timeout=60, model=MODEL_CHEAP))


def _clean_brief(text):
    """Strip any title/header/separator/character-count cruft the model adds."""
    if not text:
        return text
    import re
    kept = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("---") or s.startswith("==="):
            continue
        if re.match(r"(?i)\*{0,2}_{0,2}\s*(daily brief|character count|here'?s|today'?s brief)", s):
            continue
        kept.append(s)
    return " ".join(kept).replace("**", "").replace("__", "").strip()


def generate_weekly_plan(coaching_data, exercise_library):
    """Generate a structured 7-day workout plan."""
    profile = coaching_data.get("profile") or {}
    context = _build_context(coaching_data)

    days_per_week = int(profile.get("days_per_week") or 4)
    duration_target = int(profile.get("workout_duration_target") or 45)
    fitness_goal = profile.get("fitness_goal", "general")
    fitness_level = profile.get("fitness_level", "beginner")

    ignored = set(profile.get("ignored_exercises") or [])
    eligible = [
        e for e in exercise_library
        if e["id"] not in ignored
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


GOAL_UPDATE_PHRASES = [
    "update my goals", "update my macros", "update my calorie goal",
    "update my calories", "set my goals", "set my macros",
    "save my goals", "apply those goals", "apply the goals",
    "use those numbers", "use those macros", "use those goals",
    "change my goals", "change my macros",
]


def wants_to_update_goals(message):
    m = message.lower()
    return any(phrase in m for phrase in GOAL_UPDATE_PHRASES)


def extract_goals_from_chat(history):
    """Parse proposed calorie/macro targets from recent APEX messages. Returns dict."""
    recent = history[-6:]
    apex_msgs = "\n\n".join(
        m["content"][:800] for m in recent if m["role"] == "assistant"
    )[-1500:]

    prompt = f"""Extract the most recently proposed nutrition goals from these coach messages.
Return ONLY valid JSON. If no specific numbers were proposed, return {{}}.

MESSAGES:
{apex_msgs}

Return: {{"calories": 2800, "protein_g": 190, "carbs_g": 250, "fat_g": 80}}
Only include keys where a specific number was proposed. Omit keys with no proposed value."""

    raw = _generate(prompt, json_mode=True, max_tokens=256, timeout=30)
    result = _parse_json_safe(raw)
    return {k: v for k, v in result.items() if isinstance(v, (int, float)) and v > 0}


def extract_plan_from_chat(history, exercise_library, current_plan=None):
    """Produce the user's final 7-day plan from the design conversation.

    Reads BOTH the user's instructions and the coach's replies (the user's
    messages carry the actual changes — swaps, rest days, splits), keeps a
    generous amount of the recent transcript, and asks for the COMPLETE final
    plan rather than a fragile diff. The current plan is provided only as
    reference so untouched days are preserved.
    """
    recent = history[-12:]
    transcript = "\n\n".join(
        f"{'USER' if m['role'] == 'user' else 'COACH'}: {m['content'][:2200]}"
        for m in recent
    )[-9000:]

    # Full exercise library for name->id mapping (was capped at 50, which
    # dropped exercises the user asked for, e.g. dumbbell chest press).
    lookup = "\n".join(f'  "{e["name"]}" -> "{e["id"]}"' for e in exercise_library)

    ref = ""
    if current_plan:
        compact = []
        for d in current_plan:
            exs = [{"id": e.get("id"), "sets": e.get("sets", 3), "reps": e.get("reps", 10)}
                   for e in d.get("exercises", []) if e.get("id")]
            compact.append({"day": d.get("day"), "name": d.get("name"), "focus": d.get("focus", ""),
                            "rest": d.get("rest", False), "exercises": exs})
        ref = ("\nThe user's CURRENT saved plan (reference only — keep days that were "
               f"NOT discussed, apply every change that WAS):\n{json.dumps(compact)}\n")

    prompt = f"""Produce the user's FINAL 7-day workout plan as JSON, reflecting the whole \
conversation below between the user and their coach. Apply EVERY change the user asked for \
(exercise swaps, additions/removals, rest days, split changes).
{ref}
CONVERSATION:
{transcript}

Exercise name -> id map (use ONLY these ids):
{lookup}

Rules:
- Output the COMPLETE final plan: exactly 7 day objects.
- Apply every change the user requested across ALL relevant days (e.g. "swap X for Y on all days").
- NEVER list the same exercise id twice within a single day.
- Keep each day's exercises consistent with its focus: a legs/lower day must contain leg exercises (squats, lunges, deadlifts, glute/calf work) — do NOT pad it with upper-body pulls/pushes (lat pulldown, chest fly, rows, presses) or vice versa. If short on exercises for a day, repeat the movement pattern with a different variation, not a different body part.
- Rest days: "rest":true with an empty exercises list.

Return ONLY: {{"plan":[{{"day":1,"name":"...","focus":"...","rest":false,"exercises":[{{"id":"...","sets":3,"reps":10}}]}}, ...]}}"""

    raw = _generate(prompt, json_mode=True, max_tokens=3000, timeout=120)
    result = _parse_json_safe(raw)
    plan = result.get("plan", [])
    if len(plan) < 5:
        raise ValueError(f"Only got {len(plan)} days")
    # Dedupe exercises within each day (belt-and-suspenders; also enforced in app).
    for d in plan:
        seen, deduped = set(), []
        for ex in d.get("exercises", []):
            eid = ex.get("id")
            if eid and eid not in seen:
                seen.add(eid)
                deduped.append(ex)
        d["exercises"] = deduped
    while len(plan) < 7:
        plan.append({"day": len(plan)+1, "name": "Rest Day", "focus": "recovery", "rest": True, "exercises": []})
    return {"plan": plan[:7]}


# ── APEX persistent memory ────────────────────────────────────────────────────

REMEMBER_PHRASES = [
    "remember that", "remember this", "remember i", "remember my", "remember to",
    "don't forget", "dont forget", "keep in mind", "note that", "make a note",
    "for future reference", "from now on", "going forward, ",
]

MEMORY_CHAR_LIMIT = 1800


def wants_to_remember(message):
    m = message.lower()
    return any(phrase in m for phrase in REMEMBER_PHRASES)


def update_memory(existing_memory, recent_messages, profile=None):
    """Distill durable facts about the user into an updated memory document.

    Merges new durable facts from the recent conversation into the existing
    memory, prunes stale/outdated lines, and keeps it bounded. Returns the new
    markdown (or the existing memory unchanged on any failure)."""
    convo = "\n".join(
        f"{'USER' if m['role'] == 'user' else 'APEX'}: {m['content'][:600]}"
        for m in (recent_messages or [])[-12:]
    )[-3500:]
    name = (profile or {}).get("name", "the user")

    prompt = f"""You maintain APEX's long-term MEMORY about {name} — durable facts that make \
coaching personal across conversations. Update the memory below using the recent conversation.

RULES:
- Keep ONLY durable, useful facts: goals & the "why", injuries/constraints, coaching-style \
preferences, equipment quirks, life context, milestones/PRs, dislikes.
- Do NOT store one-off data already tracked elsewhere (today's weight, a single meal, one \
workout) — that lives in the live data.
- MERGE new facts in; UPDATE facts that changed; REMOVE anything now outdated or contradicted.
- Be concise. Group under short markdown headers. Hard limit ~{MEMORY_CHAR_LIMIT} characters.
- If nothing durable is worth changing, return the existing memory unchanged.

CURRENT MEMORY:
{existing_memory or "(empty)"}

RECENT CONVERSATION:
{convo}

Return ONLY the updated memory markdown — no preamble, no code fences."""

    try:
        result = _generate(prompt, system=None, max_tokens=700, timeout=45).strip()
        result = _strip_code_fences(result)
        if not result or result.lower() in ("(empty)", "none", "no changes"):
            return existing_memory
        return result[:MEMORY_CHAR_LIMIT]
    except Exception:
        return existing_memory
