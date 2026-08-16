import requests

import usda_food


def _food():
    return {"fdcId": 123, "description": "Bananas, raw", "foodNutrients": [
        {"nutrientName": "Energy", "value": 89},
        {"nutrientName": "Protein", "value": 1.1},
        {"nutrientName": "Carbohydrate, by difference", "value": 22.8},
        {"nutrientName": "Total lipid (fat)", "value": 0.3},
    ]}


def test_enrich_items_scales_usda_per_100g(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"foods": [_food()]}

    monkeypatch.setattr(usda_food.requests, "post", lambda *args, **kwargs: Response())
    items = [{"name": "banana", "grams": 120, "calories": 200, "protein_g": 1, "carbs_g": 50, "fat_g": 1}]

    assert usda_food.enrich_items(items, "test-key") == 1
    assert items[0]["calories"] == 107
    assert items[0]["carbs_g"] == 27.4
    assert items[0]["usda_verified"] is True


def test_enrichment_keeps_ai_estimate_when_usda_unavailable(monkeypatch):
    monkeypatch.setattr(usda_food.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout()))
    items = [{"name": "banana", "grams": 120, "calories": 200, "protein_g": 1, "carbs_g": 50, "fat_g": 1}]

    assert usda_food.enrich_items(items, "test-key") == 0
    assert items[0]["calories"] == 200


def test_lookup_barcode_uses_exact_gtin_and_label_serving(monkeypatch):
    food = {
        "fdcId": 456, "description": "Protein bar", "brandName": "BuiltHere",
        "gtinUpc": "012345678905", "servingSize": 50, "servingSizeUnit": "g",
        "labelNutrients": {
            "calories": {"value": 210}, "protein": {"value": 20},
            "carbohydrates": {"value": 22}, "fat": {"value": 7},
        },
    }
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"foods": [food]}
    monkeypatch.setattr(usda_food.requests, "post", lambda *args, **kwargs: Response())

    result = usda_food.lookup_barcode("012345678905", "test-key")
    assert result["name"] == "BuiltHere — Protein bar"
    assert result["quantity"] == "1 serving (50 g)"
    assert result["calories"] == 210
