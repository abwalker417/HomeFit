"""Single-call meal parser — the cheap alternative to Sparky's agentic logger.

One LLM call turns a free-text meal into structured foods + macros. No tool
loop, no 15k-token manual re-sent six times — just parse-once. Runs on a cheap
model (gpt-4o-mini) since single-shot extraction is easy (it's the multi-step
agent that the small models choked on, not the parsing).
"""
import json
import re

import requests

PEAKAI_URL = "http://192.168.68.33:4000/v1/chat/completions"
PEAKAI_KEY = "peak-homelab-key"
MODEL = "gpt-4o-mini"
# gpt-4o-mini price per 1M tokens
_PRICE_IN, _PRICE_OUT = 0.15, 0.60

SYSTEM = (
    "You estimate nutrition for a meal. Break the description into individual food "
    "items with realistic quantities and per-item nutrition using common nutrition "
    "data. If a quantity is vague, assume a standard serving. Return ONLY JSON:\n"
    '{"items":[{"name":"string","quantity":"string","calories":int,'
    '"protein_g":number,"carbs_g":number,"fat_g":number}]}\n'
    "No prose, no code fences."
)


def _num(v):
    try:
        return round(float(v or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def parse_meal(text):
    """Return {items, totals, cost_usd} for a free-text meal."""
    resp = requests.post(
        PEAKAI_URL,
        headers={"Authorization": f"Bearer {PEAKAI_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "stream": False, "max_tokens": 900,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": text.strip()[:500]}]},
        timeout=40,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content)
    obj = json.loads(content[content.find("{"): content.rfind("}") + 1])

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
    usage = data.get("usage", {}) or {}
    # PeakAI doesn't return usage on non-streaming calls — estimate from text.
    in_tok = usage.get("prompt_tokens") or (len(SYSTEM) + len(text)) // 4
    out_tok = usage.get("completion_tokens") or len(content) // 4
    cost = (in_tok * _PRICE_IN + out_tok * _PRICE_OUT) / 1_000_000
    return {"items": items, "totals": totals,
            "cost_usd": round(cost, 6), "model": MODEL,
            "tokens": in_tok + out_tok}
