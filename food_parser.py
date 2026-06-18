"""Single-call meal parser — the cheap alternative to Sparky's agentic logger.

One LLM call turns a free-text meal OR a photo into structured foods + macros.
No tool loop, no 15k-token manual re-sent six times — just parse-once. Text uses
a cheap model; photos use a vision model (gpt-4o) for accuracy. Either way it's
a single call (~a fraction of a cent) instead of Sparky's ~6-call agentic flow.
"""
import json
import re

import requests

PEAKAI_URL = "http://192.168.68.33:4000/v1/chat/completions"
PEAKAI_KEY = "peak-homelab-key"
TEXT_MODEL = "gpt-4o-mini"      # text parsing is easy + cheap
IMAGE_MODEL = "gpt-4o"          # vision accuracy matters for plated meals

# price per 1M tokens (in / out)
_PRICE = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}

SYSTEM = (
    "You estimate nutrition for a meal. Break it into individual food items with "
    "realistic quantities and per-item nutrition using common nutrition data. If a "
    "quantity is vague, assume a standard serving. Also give a short, friendly "
    "one-line coaching note (max ~12 words) reacting to the meal — praise good "
    "choices, or gently flag if it's heavy on calories or light on protein. If "
    "daily goal/progress context is provided, make the note aware of it (e.g. "
    "running low on protein for the day, or near the calorie goal). Return ONLY "
    "JSON:\n"
    '{"items":[{"name":"string","quantity":"string","calories":int,'
    '"protein_g":number,"carbs_g":number,"fat_g":number}],"note":"string"}\n'
    "No prose, no code fences. If you cannot identify any food, return "
    '{"items":[],"note":""}.'
)


def _context_line(goal=None, day_total=None):
    """Build a one-line goal/progress hint to inject into the user message."""
    if not goal:
        return ""
    parts = [f"User's daily goal: {int(goal.get('calories') or 0)} cal, "
             f"{int(goal.get('protein_g') or 0)}g protein, "
             f"{int(goal.get('carbs_g') or 0)}g carbs, "
             f"{int(goal.get('fat_g') or 0)}g fat."]
    if day_total:
        parts.append(
            f"So far today (before this meal): {int(day_total.get('calories') or 0)} cal, "
            f"{int(day_total.get('protein_g') or 0)}g protein, "
            f"{int(day_total.get('carbs_g') or 0)}g carbs, "
            f"{int(day_total.get('fat_g') or 0)}g fat.")
    return " ".join(parts)


def _num(v):
    try:
        return round(float(v or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def _call(messages, model):
    resp = requests.post(
        PEAKAI_URL,
        headers={"Authorization": f"Bearer {PEAKAI_KEY}", "Content-Type": "application/json"},
        json={"model": model, "stream": False, "max_tokens": 900, "messages": messages},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _extract(data, model, in_est, out_est_text):
    content = data["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content)
    try:
        obj = json.loads(content[content.find("{"): content.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        obj = {"items": []}  # model couldn't identify food — return empty, not error
    items = []
    for it in obj.get("items", []):
        items.append({
            "name": str(it.get("name", "?"))[:80],
            "quantity": str(it.get("quantity", ""))[:40],
            "calories": int(_num(it.get("calories"))),
            "protein_g": _num(it.get("protein_g")),
            "carbs_g": _num(it.get("carbs_g")),
            "fat_g": _num(it.get("fat_g")),
        })
    totals = {
        "calories": sum(i["calories"] for i in items),
        "protein_g": round(sum(i["protein_g"] for i in items), 1),
        "carbs_g": round(sum(i["carbs_g"] for i in items), 1),
        "fat_g": round(sum(i["fat_g"] for i in items), 1),
    }
    note = str(obj.get("note", "") or "").strip()[:120]
    usage = data.get("usage", {}) or {}
    pin, pout = _PRICE.get(model, (0.15, 0.60))
    in_tok = usage.get("prompt_tokens") or in_est
    out_tok = usage.get("completion_tokens") or (len(content) // 4)
    cost = (in_tok * pin + out_tok * pout) / 1_000_000
    return {"items": items, "totals": totals, "note": note,
            "cost_usd": round(cost, 6), "model": model, "tokens": in_tok + out_tok}


def parse_meal(text, goal=None, day_total=None):
    text = text.strip()[:500]
    ctx = _context_line(goal, day_total)
    user_msg = f"{ctx}\n\nMeal: {text}" if ctx else text
    data = _call([{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": user_msg}], TEXT_MODEL)
    return _extract(data, TEXT_MODEL, (len(SYSTEM) + len(user_msg)) // 4, None)


def parse_meal_image(data_url, note="", goal=None, day_total=None):
    """data_url = 'data:image/jpeg;base64,...'. Optional text note adds context."""
    user_content = [{"type": "image_url", "image_url": {"url": data_url}}]
    prompt = "Identify the foods in this meal photo and estimate the nutrition."
    if note.strip():
        prompt += f" Note from the user: {note.strip()[:200]}"
    ctx = _context_line(goal, day_total)
    if ctx:
        prompt += f" {ctx}"
    user_content.insert(0, {"type": "text", "text": prompt})
    data = _call([{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": user_content}], IMAGE_MODEL)
    # image ≈ 765 tokens + system + prompt
    return _extract(data, IMAGE_MODEL, (len(SYSTEM) + len(prompt)) // 4 + 800, None)
