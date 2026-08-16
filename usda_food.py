"""Small, optional USDA FoodData Central enrichment for parsed meals.

The AI identifies foods and estimates portions in grams.  When an owner has
configured an FDC key, this module replaces an item's estimated macros with
the closest USDA search result.  Any lookup failure intentionally falls back
to the original estimate so food logging is never dependent on USDA uptime.
"""
import re

import requests


FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
MAX_ITEMS_PER_MEAL = 5


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tokens(text):
    return {word.rstrip("s") for word in re.findall(r"[a-z]+", (text or "").lower()) if len(word) > 1}


def _nutrients(food):
    """Return kcal/protein/carbs/fat per 100 g from an FDC search result."""
    values = {}
    for nutrient in food.get("foodNutrients") or []:
        name = (nutrient.get("nutrientName") or nutrient.get("name") or "").lower()
        unit = (nutrient.get("unitName") or nutrient.get("unit") or "").lower()
        value = _number(nutrient.get("value") if "value" in nutrient else nutrient.get("amount"))
        if value is None:
            continue
        if name == "protein":
            values["protein_g"] = value
        elif "carbohydrate" in name:
            values["carbs_g"] = value
        elif "total lipid" in name or name == "fat":
            values["fat_g"] = value
        elif name.startswith("energy") and ("kcal" in name or "kcal" in unit or "calories" not in values):
            values["calories"] = value
    return values if {"calories", "protein_g", "carbs_g", "fat_g"}.issubset(values) else None


def lookup(name, api_key):
    """Find a conservative USDA match for an AI-identified food, or None."""
    query_words = _tokens(name)
    if not api_key or not query_words:
        return None
    try:
        response = requests.post(
            FDC_SEARCH_URL,
            params={"api_key": api_key},
            json={"query": name[:120], "pageSize": 5, "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"]},
            timeout=3,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    try:
        foods = response.json().get("foods") or []
    except (ValueError, AttributeError):
        return None
    for food in foods:
        description = food.get("description") or ""
        # FDC supplies results ranked by relevance, but avoid replacing an item
        # with something unrelated if a broad AI item name receives a weak hit.
        if not (_tokens(description) & query_words):
            continue
        nutrients = _nutrients(food)
        if nutrients:
            return {"fdc_id": food.get("fdcId"), "description": description, "nutrients": nutrients}
    return None


def lookup_barcode(barcode, api_key):
    """Return a single branded USDA serving for an exact UPC/EAN/GTIN match."""
    code = re.sub(r"[^0-9A-Za-z]", "", barcode or "")[:32]
    if not api_key or len(code) < 8:
        return None
    # EAN-13 and UPC-A may be represented with or without a leading zero.
    acceptable = {code, code.lstrip("0"), code.zfill(13)}
    try:
        response = requests.post(
            FDC_SEARCH_URL,
            params={"api_key": api_key},
            json={"query": code, "pageSize": 10, "dataType": ["Branded"]},
            timeout=4,
        )
        response.raise_for_status()
        foods = response.json().get("foods") or []
    except (requests.RequestException, ValueError, AttributeError):
        return None

    for food in foods:
        gtin = re.sub(r"[^0-9A-Za-z]", "", food.get("gtinUpc") or "")
        if gtin not in acceptable and gtin.lstrip("0") not in acceptable:
            continue
        label = food.get("labelNutrients") or {}
        def label_value(field):
            return _number((label.get(field) or {}).get("value"))
        calories = label_value("calories")
        protein = label_value("protein")
        carbs = label_value("carbohydrates")
        fat = label_value("fat")
        if None in (calories, protein, carbs, fat):
            continue
        serving_size = food.get("servingSize")
        serving_unit = food.get("servingSizeUnit") or "serving"
        quantity = f"1 serving ({serving_size:g} {serving_unit})" if isinstance(serving_size, (int, float)) else "1 serving"
        brand = food.get("brandName") or food.get("brandOwner") or ""
        description = food.get("description") or "Scanned food"
        name = f"{brand} — {description}" if brand else description
        return {
            "name": name[:120], "quantity": quantity,
            "calories": int(round(calories)), "protein_g": round(protein, 1),
            "carbs_g": round(carbs, 1), "fat_g": round(fat, 1),
            "usda_verified": True, "fdc_id": food.get("fdcId"), "barcode": code,
        }
    return None


def enrich_items(items, api_key):
    """Apply USDA macros where both an FDC match and an AI gram estimate exist."""
    matched = 0
    for item in (items or [])[:MAX_ITEMS_PER_MEAL]:
        grams = _number(item.get("grams"))
        if grams is None or not 1 <= grams <= 2500:
            continue
        match = lookup(item.get("name", ""), api_key)
        if not match:
            continue
        scale = grams / 100
        item.update({
            "calories": int(round(match["nutrients"]["calories"] * scale)),
            "protein_g": round(match["nutrients"]["protein_g"] * scale, 1),
            "carbs_g": round(match["nutrients"]["carbs_g"] * scale, 1),
            "fat_g": round(match["nutrients"]["fat_g"] * scale, 1),
            "usda_verified": True,
            "fdc_id": match["fdc_id"],
        })
        matched += 1
    return matched
