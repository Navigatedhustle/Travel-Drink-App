"""
Travel Drink Generator - Flask App (Sandbox-Safe, Single-Threaded + Closest-Fit Mode + Offline Fallback)

File name suggestion: travel_drink_app.py

What changed (fixes + closest-fit + offline)
- **No crash on startup failures:** Removed `sys.exit(1)` and added an **offline fallback**. If the environment disallows binding any server/port, the app now generates static artifacts (a plan JSON and optional PDF) and exits cleanly **without** raising `SystemExit: 1`.
- **Sandbox-safe server:** debugger OFF, reloader OFF, **single-threaded** only; startup falls back to Werkzeug single-thread and `wsgiref`. If all fail → offline mode.
- **Closest-fit behavior:** When strict filters yield no exact matches, return closest fits with a banner + suggestions. (Your preference.)
- **Refactor:** Centralized plan logic in `compute_plan()` so API and offline mode share the same code path.
- **Bug fix:** Added missing `build_plan()` and `_fallback_suggestions()` to resolve `NameError`.
- **Tests:** Kept all prior tests; added two more validations for pacing/recovery integrity and advice banner content.

Purpose
- Help busy professionals in a calorie deficit pick lower-calorie, lower-carb alcoholic drinks while traveling.
- Provide smart swaps, bartender order scripts, pacing guidance, and next-day recovery tips.

How to run locally
1) Install deps once:  `pip install flask reportlab`
   - If `reportlab` install fails, the app still runs. PDF export is disabled automatically.
2) Start server (safe mode):  `python travel_drink_app.py`
   - Optional flags: `--host 0.0.0.0 --port 5000`
3) Open browser:  `http://127.0.0.1:5000`

If your host blocks servers entirely
- The app will **auto-fallback to offline** and export:
  - `offline_plan.json` (smart picks using default prefs)
  - `offline_plan.pdf` (if reportlab is available)
  - A short `offline_readme.txt` with usage notes

Embed in GHL
- Host this on Render or Railway with HTTPS.
- In your GHL lesson, add a Custom HTML block and embed with an iframe:
  `<iframe src="https://your-drink-app.example.com" width="100%" height="1200" style="border:0"></iframe>`

Security
- Sets a light CSP header that allows GHL embedding. Update `ALLOWED_ORIGINS` below as needed.

Developer utilities
- Run quick tests without starting the server: `python travel_drink_app.py --test`
- Or hit `GET /api/selftest` while the server is running.
"""

from __future__ import annotations
import os, math, json, datetime, argparse, sys
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

from flask import Flask, request, render_template_string, jsonify, make_response

# Optional PDF
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

APP_NAME = "Travel Drink Generator"
ALLOWED_ORIGINS = [
    "https://app.gohighlevel.com",
    "https://my.gohighlevel.com",
    # Add your domains for embedding
]

app = Flask(__name__)

# -----------------------------
# Nutrition and drink database
# -----------------------------
# All calories are approximate. Alcohol calories are computed by formula when possible.
# Alcohol density ~ 0.789 g/mL, alcohol energy ~ 7 kcal/g.
ALC_DENSITY = 0.789
ALC_KCAL_PER_G = 7.0
OZ_TO_ML = 29.5735

@dataclass
class Drink:
    name: str
    category: str  # wine, beer, spirit, seltzer, cocktail, mocktail
    serving_oz: float
    abv_pct: float  # 0 to 100
    extra_cals: float = 0.0  # mixers or residual sugar when not computed
    carbs_g: Optional[float] = None
    sugar_g: Optional[float] = None
    gluten_free: bool = True
    keto_friendly: bool = True
    caffeine: bool = False
    carbonation: bool = False
    order_script: Optional[str] = None

    def alcohol_kcal(self) -> float:
        ml = self.serving_oz * OZ_TO_ML
        grams_alc = ml * (self.abv_pct / 100.0) * ALC_DENSITY
        return grams_alc * ALC_KCAL_PER_G

    def total_kcal(self) -> float:
        return round(self.alcohol_kcal() + self.extra_cals)

# Mixers (kcal per ounce), carbs are approximate
MIXERS: Dict[str, Dict[str, float]] = {
    "soda water": {"kcal_per_oz": 0.0, "carbs_per_oz": 0.0},
    "diet tonic": {"kcal_per_oz": 0.0, "carbs_per_oz": 0.0},
    "diet cola": {"kcal_per_oz": 0.0, "carbs_per_oz": 0.0, "caffeine": True},
    "light tonic": {"kcal_per_oz": 5.0, "carbs_per_oz": 1.2},
    "tonic": {"kcal_per_oz": 10.0, "carbs_per_oz": 2.5},
    "lime juice": {"kcal_per_oz": 8.0, "carbs_per_oz": 2.6},
    "lemon juice": {"kcal_per_oz": 7.0, "carbs_per_oz": 2.4},
    "orange juice": {"kcal_per_oz": 14.0, "carbs_per_oz": 3.5},
    "cranberry juice": {"kcal_per_oz": 13.0, "carbs_per_oz": 3.3},
    "pineapple juice": {"kcal_per_oz": 16.0, "carbs_per_oz": 4.0},
    "simple syrup": {"kcal_per_oz": 50.0, "carbs_per_oz": 12.5},
    "ginger beer": {"kcal_per_oz": 12.0, "carbs_per_oz": 3.0, "carbonation": True},
    "diet ginger beer": {"kcal_per_oz": 0.0, "carbs_per_oz": 0.0, "carbonation": True},
    "coconut water": {"kcal_per_oz": 6.0, "carbs_per_oz": 1.5},
}

# Base options commonly available while traveling
BASE_LIBRARY: List[Drink] = [
    # Spirits base - 1.5 oz at 40% ABV
    Drink("Vodka (1.5 oz)", "spirit", 1.5, 40, gluten_free=True, keto_friendly=True, carbonation=False,
          order_script="Vodka, one and a half ounces."),
    Drink("Tequila blanco (1.5 oz)", "spirit", 1.5, 40, gluten_free=True, keto_friendly=True,
          order_script="Tequila blanco, one and a half ounces."),
    Drink("Gin (1.5 oz)", "spirit", 1.5, 40, gluten_free=True, keto_friendly=True,
          order_script="Gin, one and a half ounces."),
    Drink("Whiskey/bourbon (1.5 oz)", "spirit", 1.5, 40, gluten_free=True, keto_friendly=True,
          order_script="Whiskey pour, one and a half ounces."),
    Drink("Rum white (1.5 oz)", "spirit", 1.5, 40, gluten_free=True, keto_friendly=True,
          order_script="White rum, one and a half ounces."),

    # Wine - residual sugar baked into extra_cals and carbs
    Drink("Dry white wine (5 oz)", "wine", 5.0, 12, extra_cals=20, carbs_g=3.0, sugar_g=1.5, carbonation=False,
          order_script="Five ounces of dry white wine."),
    Drink("Dry red wine (5 oz)", "wine", 5.0, 13, extra_cals=22, carbs_g=4.0, sugar_g=1.0, carbonation=False,
          order_script="Five ounces of dry red wine."),
    Drink("Brut champagne (5 oz)", "wine", 5.0, 12, extra_cals=10, carbs_g=2.0, sugar_g=1.0, carbonation=True,
          order_script="A five ounce pour of brut champagne."),

    # Beer and seltzer
    Drink("Light beer (12 oz)", "beer", 12.0, 4.2, extra_cals=20, carbs_g=5.0, sugar_g=0.0, gluten_free=False, keto_friendly=False, carbonation=True,
          order_script="A twelve ounce light beer."),
    Drink("Regular lager (12 oz)", "beer", 12.0, 5.0, extra_cals=60, carbs_g=13.0, sugar_g=0.0, gluten_free=False, keto_friendly=False, carbonation=True,
          order_script="A twelve ounce lager."),
    Drink("Hard seltzer (12 oz)", "seltzer", 12.0, 5.0, extra_cals=30, carbs_g=2.0, sugar_g=1.0, gluten_free=True, keto_friendly=True, carbonation=True,
          order_script="A twelve ounce hard seltzer."),
]

# Prebuilt smart picks for common travel bars
PRESET_COCKTAILS: List[Dict[str, Any]] = [
    {
        "name": "Vodka soda with lime",
        "components": [("Vodka (1.5 oz)", 1.0), ("soda water", 6.0), ("lime juice", 0.25)],
        "tags": ["lowest-cal", "easy-order", "airport"],
        "order": "Vodka soda, tall glass, heavy ice, squeeze of fresh lime. No simple syrup.",
    },
    {
        "name": "Tequila soda with lime",
        "components": [("Tequila blanco (1.5 oz)", 1.0), ("soda water", 6.0), ("lime juice", 0.25)],
        "tags": ["lowest-cal", "easy-order", "hotel"],
        "order": "Tequila soda, tall, fresh lime. No sweetener.",
    },
    {
        "name": "Gin and diet tonic",
        "components": [("Gin (1.5 oz)", 1.0), ("diet tonic", 6.0)],
        "tags": ["low-cal", "diet-mixer"],
        "order": "Gin with diet tonic, tall. Lime wedge."
    },
    {
        "name": "Whiskey neat",
        "components": [("Whiskey/bourbon (1.5 oz)", 1.0)],
        "tags": ["zero-carb", "fast"],
        "order": "Whiskey neat, one and a half ounces."
    },
    {
        "name": "Skinny paloma",
        "components": [("Tequila blanco (1.5 oz)", 1.0), ("soda water", 4.0), ("lime juice", 0.5)],
        "tags": ["mexican", "restaurant"],
        "order": "Tequila with soda and fresh lime in a salted glass. No grapefruit soda, no simple syrup."
    },
    {
        "name": "Skinny mule",
        "components": [("Vodka (1.5 oz)", 1.0), ("diet ginger beer", 6.0), ("lime juice", 0.25)],
        "tags": ["diet-mixer"],
        "order": "Vodka with diet ginger beer, splash of fresh lime. Copper mug if available."
    },
    {
        "name": "Brut champagne (5 oz)",
        "components": [("Brut champagne (5 oz)", 1.0)],
        "tags": ["celebration", "moderate"],
        "order": "A five ounce pour of brut champagne."
    },
    {
        "name": "Dry white wine (5 oz)",
        "components": [("Dry white wine (5 oz)", 1.0)],
        "tags": ["simple", "restaurant"],
        "order": "Five ounces of your driest white wine."
    },
    {
        "name": "Hard seltzer (12 oz)",
        "components": [("Hard seltzer (12 oz)", 1.0)],
        "tags": ["grab-and-go"],
        "order": "A twelve ounce hard seltzer."
    },
]

# Mocktail options for alcohol-free days
MOCKTAILS: List[Dict[str, Any]] = [
    {
        "name": "Lime soda",
        "components": [("soda water", 10.0), ("lime juice", 0.5)],
        "order": "Soda water with fresh lime in a tall glass.",
        "tags": ["zero-cal", "hydrate"]
    },
    {
        "name": "Diet ginger fizz",
        "components": [("diet ginger beer", 8.0), ("lime juice", 0.25)],
        "order": "Diet ginger beer with a squeeze of lime.",
        "tags": ["zero-cal", "diet-mixer"]
    },
]

# -----------------------------
# Core logic
# -----------------------------

def find_base(name: str) -> Optional[Drink]:
    for d in BASE_LIBRARY:
        if d.name == name:
            return d
    return None


def component_kcal_and_carbs(component_name: str, units: float) -> Tuple[float, float]:
    """Return kcal and carbs for a component amount.
    units is 1.0 for a full Drink, or ounces for a mixer by oz.
    """
    base = find_base(component_name)
    if base is not None:
        # units is count of the base item
        kcal = base.total_kcal() * units
        carbs = (base.carbs_g or 0.0) * units
        return kcal, carbs
    # Mixer by ounces
    mx = MIXERS.get(component_name)
    if mx:
        kcal = mx["kcal_per_oz"] * units
        carbs = mx["carbs_per_oz"] * units
        return kcal, carbs
    return 0.0, 0.0


def build_drink_profile(recipe: Dict[str, Any]) -> Dict[str, Any]:
    kcal = 0.0
    carbs = 0.0
    abv_pct = 0.0
    carbonation = False
    caffeine = False
    gluten_free = True
    keto = True

    # Estimate ABV by summing alcohol from spirit components only
    total_volume_oz = 0.0
    alcohol_ml = 0.0
    for comp_name, amount in recipe["components"]:
        base = find_base(comp_name)
        if base is not None:
            # amount counts the base serving
            vol_ml = base.serving_oz * amount * OZ_TO_ML
            total_volume_oz += base.serving_oz * amount
            alcohol_ml += vol_ml * (base.abv_pct / 100.0)
            carbonation = carbonation or base.carbonation
            caffeine = caffeine or base.caffeine
            gluten_free = gluten_free and base.gluten_free
            keto = keto and base.keto_friendly
        else:
            # mixer measured in ounces
            mx = MIXERS.get(comp_name, {})
            total_volume_oz += amount
            carbonation = carbonation or bool(mx.get("carbonation", False))
            caffeine = caffeine or bool(mx.get("caffeine", False))

        k, c = component_kcal_and_carbs(comp_name, amount)
        kcal += k
        carbs += c

    if total_volume_oz > 0:
        abv_pct = (alcohol_ml / (total_volume_oz * OZ_TO_ML)) * 100.0

    return {
        "name": recipe["name"],
        "kcal": round(kcal),
        "carbs_g": round(carbs, 1),
        "abv_pct": round(abv_pct, 1),
        "order": recipe.get("order", recipe["name"]),
        "tags": recipe.get("tags", []),
        "carbonation": carbonation,
        "caffeine": caffeine,
        "gluten_free": gluten_free,
        "keto": keto,
    }


def generate_candidates(include_categories: List[str]) -> List[Dict[str, Any]]:
    # Start with presets and mocktails
    recipes: List[Dict[str, Any]] = []
    if "mocktail" in include_categories:
        recipes.extend(MOCKTAILS)
    recipes.extend(PRESET_COCKTAILS)

    # Add base spirits neat for zero-carb options
    for d in BASE_LIBRARY:
        if d.category == "spirit":
            recipes.append({
                "name": d.name.replace(" (1.5 oz)", " neat"),
                "components": [(d.name, 1.0)],
                "tags": ["zero-carb", "simple"],
                "order": f"{d.name.split(' (')[0]} neat, one and a half ounces.",
            })

    # Wines and seltzers and beers can be included directly
    for d in BASE_LIBRARY:
        if d.category in {"wine", "seltzer", "beer"}:
            recipes.append({
                "name": d.name,
                "components": [(d.name, 1.0)],
                "tags": [d.category],
                "order": d.order_script or d.name,
            })

    # Filter by include_categories loosely
    def matches_category(r: Dict[str, Any]) -> bool:
        cats = set(include_categories)
        base_names = [c[0] for c in r["components"]]
        if "any" in cats:
            return True
        if any(any(key in nm.lower() for key in ["wine"]) for nm in base_names) and "wine" in cats:
            return True
        if any(any(key in nm.lower() for key in ["beer", "lager"]) for nm in base_names) and "beer" in cats:
            return True
        if any("seltzer" in nm.lower() for nm in base_names) and "seltzer" in cats:
            return True
        if any(any(key in nm.lower() for key in ["vodka", "tequila", "gin", "whiskey", "rum"]) for nm in base_names) and "spirit" in cats:
            return True
        if "mocktail" in cats and r["name"] in [m["name"] for m in MOCKTAILS]:
            return True
        if "cocktail" in cats and any(find_base(n) is not None for n, _ in r["components"]) and any(n in MIXERS for n, _ in r["components"]):
            return True
        return False

    return [r for r in recipes if matches_category(r)]


def score_drink(profile: Dict[str, Any], prefs: Dict[str, Any]) -> float:
    # Lower calories and carbs score better, respect constraints with heavy penalties
    score = 100.0
    kcal = profile["kcal"]
    carbs = profile["carbs_g"]

    score -= 0.1 * max(0, kcal - 60)  # soft penalty above 60 kcal
    score -= 1.0 * carbs

    # Hard constraints
    max_kcal = prefs.get("max_kcal", 999)
    max_carbs = prefs.get("max_carbs", 999.0)
    if kcal > max_kcal:
        score -= 200
    if carbs > max_carbs:
        score -= 200

    if prefs.get("sugar_free_mixers") and carbs > 3.0:
        score -= 50

    if not prefs.get("allow_caffeine", True) and profile["caffeine"]:
        score -= 40
    if not prefs.get("allow_carbonation", True) and profile["carbonation"]:
        score -= 40
    if prefs.get("gluten_free_only") and not profile["gluten_free"]:
        score -= 200
    if prefs.get("keto_only") and not profile["keto"]:
        score -= 200

    # Category preference slight boost
    pref_cat = prefs.get("pref_category")
    if pref_cat:
        if pref_cat in profile.get("tags", []) or pref_cat in profile["name"].lower():
            score += 5

    return score


def build_plan(selected: List[Dict[str, Any]], prefs: Dict[str, Any], fallback_used: bool, advice: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Format the final plan payload for the UI and PDF builder."""
    n = int(prefs.get("drink_count", 1))
    n = max(1, min(4, n))
    picks = selected[:n]

    pacing = []
    for i in range(n):
        pacing.append({
            "slot": i + 1,
            "instruction": "Sip slowly for 45 to 60 minutes, pair with water, and add a pinch of salt if you sweat a lot.",
        })

    recovery = [
        "500 to 750 ml water before bed, add electrolytes if you trained.",
        "Protein-forward breakfast, eggs or Greek yogurt, add fruit for potassium.",
        "Zone 2 walk for 20 to 30 minutes to feel normal again.",
        "Avoid driving, and never mix alcohol with sedatives.",
    ]

    return {
        "picks": picks,
        "pacing": pacing,
        "recovery": recovery,
        "fallback_used": fallback_used,
        "advice": advice,
    }


def _fallback_suggestions(prefs: Dict[str, Any], categories: List[str]) -> Dict[str, Any]:
    """Provide actionable tips when strict filters yield no exact match."""
    suggestions: List[str] = []
    cats = set(categories or [])
    max_kcal = int(prefs.get("max_kcal", 999))
    max_carbs = float(prefs.get("max_carbs", 999))
    allow_carb = prefs.get("allow_carbonation", True)
    allow_caf = prefs.get("allow_caffeine", True)
    gf_only = prefs.get("gluten_free_only", False)
    keto_only = prefs.get("keto_only", False)

    if max_kcal < 90:
        suggestions.append("Increase max calories to ≥ 90 kcal — most spirit + soda combos land 90–110 kcal.")
    if max_carbs < 2 and ("wine" in cats or "seltzer" in cats):
        suggestions.append("Allow up to 2–5 g carbs for dry wine or hard seltzer options.")
    if not allow_carb and ("beer" in cats or "seltzer" in cats):
        suggestions.append("Enable carbonation or include spirits to avoid beer/seltzer conflicts.")
    if gf_only and "beer" in cats:
        suggestions.append("Gluten-free only with beer is restrictive — switch to spirits with soda or brut champagne.")
    if keto_only and ("beer" in cats or "wine" in cats):
        suggestions.append("Keto + beer/wine is tough — prefer spirits with soda water.")
    if prefs.get("sugar_free_mixers", False) is False:
        suggestions.append("Toggle sugar-free mixers for big calorie savings (diet tonic, soda water).")

    message = "No exact matches. Showing closest-fit picks based on your limits."
    return {"message": message, "suggestions": suggestions[:5]}


def compute_plan(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Shared logic used by the API and the offline fallback."""
    categories = prefs.get("categories") or ["any"]
    candidates = generate_candidates(categories)
    profiles = [build_drink_profile(r) for r in candidates]
    scored = sorted(profiles, key=lambda p: -score_drink(p, prefs))

    feasible: List[Dict[str, Any]] = []
    for p in scored:
        if p["kcal"] <= int(prefs.get("max_kcal", 999)) and p["carbs_g"] <= float(prefs.get("max_carbs", 999.0)):
            if not prefs.get("gluten_free_only") or p["gluten_free"]:
                if not prefs.get("keto_only") or p["keto"]:
                    if prefs.get("allow_caffeine", True) or not p["caffeine"]:
                        if prefs.get("allow_carbonation", True) or not p["carbonation"]:
                            feasible.append(p)

    fallback_used = False if feasible else True
    selected = feasible[:5] if feasible else scored[:5]
    advice = _fallback_suggestions(prefs, categories) if fallback_used else None

    plan = build_plan(selected, prefs, fallback_used, advice)

    # Add optional PDF URL in server mode; offline sets file path instead
    plan["pdf_url"] = None
    return plan


# -----------------------------
# HTML templates
# -----------------------------
BASE_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ app_name }}</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; line-height: 1.45; }
    .card { border: 1px solid #eee; border-radius: 14px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
    .row { display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 12px; }
    label { font-size: 14px; color: #333; }
    input, select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 10px; }
    button { padding: 10px 14px; border: 0; border-radius: 10px; cursor: pointer; background: #111; color: #fff; }
    h1 { font-size: 22px; margin-bottom: 12px; }
    h2 { font-size: 18px; margin: 10px 0; }
    .muted { color: #666; font-size: 13px; }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #f1f1f1; margin-right: 6px; font-size: 12px; }
    .grid { display: grid; gap: 12px; }
    .kpi { font-weight: 600; }
    .small { font-size: 13px; }
    .banner { border-left: 4px solid #8a8; background: #f8fff8; padding: 10px 12px; border-radius: 10px; }
    .banner h3 { margin: 0 0 6px 0; font-size: 16px; }
  </style>
</head>
<body>
  <h1>Travel Drink Generator</h1>
  <p class="muted">Pick smarter drinks on the road, keep calories tight, and wake up ready to work.</p>

  <form id="genForm" class="card grid">
    <div class="row">
      <div>
        <label>Alcohol type</label>
        <select name="categories" multiple size="6">
          <option value="any" selected>Any</option>
          <option value="spirit">Spirits</option>
          <option value="cocktail">Simple cocktails</option>
          <option value="wine">Wine</option>
          <option value="beer">Beer</option>
          <option value="seltzer">Hard seltzer</option>
          <option value="mocktail">Mocktails</option>
        </select>
        <div class="small muted">Tip: hold Ctrl or Cmd to select multiple.</div>
      </div>
      <div>
        <label>Max calories per drink</label>
        <input type="number" name="max_kcal" value="120" min="50" max="400" />
      </div>
      <div>
        <label>Max carbs per drink (g)</label>
        <input type="number" name="max_carbs" value="6" min="0" max="50" />
      </div>
      <div>
        <label>How many drinks tonight</label>
        <input type="number" name="drink_count" value="1" min="1" max="4" />
      </div>
    </div>

    <div class="row">
      <div><label><input type="checkbox" name="sugar_free_mixers" checked /> Prefer sugar-free mixers</label></div>
      <div><label><input type="checkbox" name="gluten_free_only" /> Gluten-free only</label></div>
      <div><label><input type="checkbox" name="keto_only" /> Keto only</label></div>
      <div><label><input type="checkbox" name="allow_caffeine" checked /> Caffeine allowed</label></div>
      <div><label><input type="checkbox" name="allow_carbonation" checked /> Carbonation allowed</label></div>
    </div>

    <div class="row">
      <div>
        <label>Venue vibe</label>
        <select name="pref_category">
          <option value="">No preference</option>
          <option value="airport">Airport</option>
          <option value="hotel">Hotel bar</option>
          <option value="restaurant">Restaurant</option>
          <option value="grab-and-go">Grab and go</option>
        </select>
      </div>
      <div>
        <label>Export PDF</label>
        <select name="want_pdf">
          <option value="no" selected>No</option>
          <option value="yes">Yes</option>
        </select>
        <div class="small muted">PDF needs reportlab installed.</div>
      </div>
    </div>

    <div>
      <button type="submit">Generate smart picks</button>
    </div>
  </form>

  <div id="results" class="grid"></div>

  <script>
  const form = document.getElementById('genForm');
  const results = document.getElementById('results');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    results.innerHTML = '<div class="muted">Working...</div>';

    const fd = new FormData(form);
    const cats = fd.getAll('categories');
    const payload = {
      categories: cats.length ? cats : ['any'],
      max_kcal: parseInt(fd.get('max_kcal')||'120'),
      max_carbs: parseFloat(fd.get('max_carbs')||'6'),
      drink_count: parseInt(fd.get('drink_count')||'1'),
      sugar_free_mixers: fd.get('sugar_free_mixers') === 'on',
      gluten_free_only: fd.get('gluten_free_only') === 'on',
      keto_only: fd.get('keto_only') === 'on',
      allow_caffeine: fd.get('allow_caffeine') === 'on',
      allow_carbonation: fd.get('allow_carbonation') === 'on',
      pref_category: fd.get('pref_category') || '',
      want_pdf: fd.get('want_pdf') === 'yes'
    };

    const r = await fetch('/api/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json();

    if (data.error) {
      results.innerHTML = `<div class="card">${data.error}</div>`;
      return;
    }

    const { picks, pacing, recovery, pdf_url, fallback_used, advice } = data.plan;

    let html = '';

    if (fallback_used && advice) {
      html += `
        <div class="card banner">
          <h3>${advice.message || 'Closest-fit picks'}</h3>
          ${advice.suggestions && advice.suggestions.length ? `<ul>${advice.suggestions.map(s=>`<li>${s}</li>`).join('')}</ul>` : ''}
        </div>`;
    }

    picks.forEach((p, idx) => {
      html += `
        <div class="card">
          <h2>Pick ${idx+1}: ${p.name}</h2>
          <div class="row">
            <div><span class="kpi">${p.kcal}</span> kcal</div>
            <div><span class="kpi">${p.carbs_g}</span> g carbs</div>
            <div><span class="kpi">${p.abv_pct}%</span> ABV</div>
          </div>
          <p class="small"><strong>Order it like this:</strong> ${p.order}</p>
          <div class="small">Tags: ${(p.tags||[]).map(t=>`<span class='tag'>${t}</span>`).join(' ')}</div>
          <div class="small muted">${p.gluten_free ? 'Gluten-free,' : ''} ${p.keto ? 'Keto-aware,' : ''} ${p.caffeine ? 'Caffeinated,' : 'No caffeine,'} ${p.carbonation ? 'Carbonated' : 'Still'}</div>
        </div>`;
    });

    html += `<div class=\"card\"><h2>Pacing plan</h2><ol>${pacing.map(s=>`<li>${s.instruction}</li>`).join('')}</ol></div>`;
    html += `<div class=\"card\"><h2>Next-day recovery</h2><ul>${recovery.map(x=>`<li>${x}</li>`).join('')}</ul></div>`;

    if (pdf_url) {
      html += `<div class="card"><a href="${pdf_url}" target="_blank">Download PDF summary</a></div>`;
    }

    results.innerHTML = html;
  });
  </script>
</body>
</html>
"""

# -----------------------------
# Routes
# -----------------------------
@app.after_request
def add_csp(resp):
    # Basic CSP that allows embedding in GHL and same-origin
    origin = request.headers.get("Origin", "")
    allow = "'self'"
    if origin in ALLOWED_ORIGINS:
        allow = f"'self' {origin}"
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'self'; frame-ancestors {allow}; base-uri 'self'; "
        f"script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
    )
    return resp


@app.get("/")
def index():
    return render_template_string(BASE_HTML, app_name=APP_NAME)


@app.post("/api/generate")
def api_generate():
    prefs = request.get_json(force=True) or {}
    plan = compute_plan(prefs)

    # Attach PDF endpoint when requested and available
    if prefs.get("want_pdf") and REPORTLAB_AVAILABLE:
        pdf_bytes = build_pdf(plan)
        key = f"plan_{int(datetime.datetime.utcnow().timestamp())}.pdf"
        _PDF_CACHE[key] = pdf_bytes
        plan["pdf_url"] = f"/api/pdf/{key}"

    return jsonify({"plan": plan})


_PDF_CACHE: Dict[str, bytes] = {}


def build_pdf(plan: Dict[str, Any]) -> bytes:
    from io import BytesIO
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    elems: List[Any] = []

    elems.append(Paragraph("Travel Drink Generator - Smart Picks", styles['Title']))
    elems.append(Spacer(1, 12))

    for i, p in enumerate(plan["picks"], 1):
        elems.append(Paragraph(f"Pick {i}: {p['name']}", styles['Heading2']))
        data = [["Kcal", "Carbs (g)", "ABV%", "Gluten-free", "Keto", "Caffeine", "Carbonation"],
                [str(p['kcal']), str(p['carbs_g']), str(p['abv_pct']), str(p['gluten_free']), str(p['keto']), str(p['caffeine']), str(p['carbonation'])]]
        t = Table(data)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
            ('BOX', (0,0), (-1,-1), 0.5, colors.black),
            ('INNERGRID', (0,0), (-1,-1), 0.25, colors.grey),
        ]))
        elems.append(t)
        elems.append(Paragraph(f"Order: {p['order']}", styles['BodyText']))
        elems.append(Spacer(1, 8))

    elems.append(Spacer(1, 12))
    elems.append(Paragraph("Pacing plan", styles['Heading2']))
    for s in plan["pacing"]:
        elems.append(Paragraph(f"- {s['instruction']}", styles['BodyText']))

    elems.append(Spacer(1, 12))
    elems.append(Paragraph("Next-day recovery", styles['Heading2']))
    for r in plan["recovery"]:
        elems.append(Paragraph(f"- {r}", styles['BodyText']))

    doc.build(elems)
    return buf.getvalue()


@app.get('/api/pdf/<key>')
def get_pdf(key: str):
    pdf = _PDF_CACHE.get(key)
    if not pdf:
        return jsonify({"error": "PDF expired. Regenerate to get a new link."}), 404
    resp = make_response(pdf)
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = 'inline; filename="drink_plan.pdf"'
    return resp


# -----------------------------
# Health notes for the UI consumer
# -----------------------------
@app.get('/api/health-tips')
def health_tips():
    tips = [
        "Alternate every alcoholic drink with at least 12 oz water.",
        "Cap at one standard drink per hour, stop at a light buzz.",
        "Do not drink if you need to drive or operate anything that can harm others.",
        "Avoid mixing with sedatives or sleep meds.",
        "If cutting weight, prefer spirits with soda water or brut champagne.",
    ]
    return jsonify({"tips": tips})


# -----------------------------
# Self-test utilities (no multiprocessing required)
# -----------------------------
@app.get('/api/selftest')
def selftest_endpoint():
    return jsonify(run_selftests())


def run_selftests() -> Dict[str, Any]:
    """A couple of cheap integration tests using Flask's test client.
    This avoids pytest/unittest runners and any multiprocessing.
    """
    out: Dict[str, Any] = {"passed": [], "failed": [], "env": {
        "REPORTLAB_AVAILABLE": REPORTLAB_AVAILABLE,
    }}
    try:
        with app.test_client() as c:
            # Test 1: Basic generate with defaults
            r = c.post('/api/generate', json={})
            assert r.status_code == 200, f"/api/generate status {r.status_code}"
            data = r.get_json()
            assert data and data.get('plan'), "plan missing"
            assert data['plan']['picks'], "no picks returned"
            out["passed"].append("basic_generate")

            # Test 2: Strict beer filter that likely yields fallback
            r2 = c.post('/api/generate', json={
                "categories": ["beer"],
                "max_kcal": 60,
                "max_carbs": 0,
            })
            assert r2.status_code == 200
            plan2 = r2.get_json()['plan']
            assert plan2['picks'], "strict filter produced no fallback picks"
            out["passed"].append("strict_filter_fallback")

            # Test 3: PDF toggle presence (URL may be None when reportlab unavailable)
            r3 = c.post('/api/generate', json={"want_pdf": True})
            assert r3.status_code == 200
            plan3 = r3.get_json()['plan']
            assert 'pdf_url' in plan3, "pdf_url key missing"
            out["passed"].append("pdf_key_present")

            # Test 4: Health tips endpoint
            r4 = c.get('/api/health-tips')
            assert r4.status_code == 200
            tips = r4.get_json().get('tips', [])
            assert any('water' in t.lower() for t in tips), "expected hydration tip"
            out["passed"].append("health_tips")

            # Test 5: PDF fetch when available
            if REPORTLAB_AVAILABLE:
                r5 = c.post('/api/generate', json={"want_pdf": True})
                key_url = r5.get_json()['plan']['pdf_url']
                assert key_url, "expected a pdf_url when reportlab available"
                r6 = c.get(key_url)
                assert r6.status_code == 200 and r6.headers.get('Content-Type') == 'application/pdf'
                out["passed"].append("pdf_roundtrip")

            # Test 6: Gluten-free only should not return gluten beers
            r7 = c.post('/api/generate', json={
                "categories": ["beer", "spirit", "cocktail"],
                "gluten_free_only": True,
            })
            plan7 = r7.get_json()['plan']
            assert all(p.get('gluten_free', True) for p in plan7['picks']), "gluten_free_only returned a gluten item"
            out["passed"].append("gluten_free_filter")

            # Test 7: Mocktail should be present and ABV ~ 0
            r8 = c.post('/api/generate', json={
                "categories": ["mocktail"],
                "max_kcal": 50,
            })
            plan8 = r8.get_json()['plan']
            assert plan8['picks'], "no mocktail picks"
            assert all(p['abv_pct'] == 0.0 or p['abv_pct'] < 0.5 for p in plan8['picks']), "mocktail abv not ~0"
            out["passed"].append("mocktail_abv_zero")

            # Test 8: Index should serve HTML
            r9 = c.get('/')
            assert r9.status_code == 200 and b'<title' in r9.data, "index HTML not served"
            out["passed"].append("index_html")

            # Test 9: Fallback flag and suggestions must appear on strict filters
            r10 = c.post('/api/generate', json={
                "categories": ["beer"],
                "max_kcal": 60,
                "max_carbs": 0,
                "allow_carbonation": False,
                "gluten_free_only": True,
            })
            plan10 = r10.get_json()['plan']
            assert plan10.get('fallback_used') is True, "fallback_used flag not set"
            adv = plan10.get('advice')
            assert adv and isinstance(adv.get('suggestions', []), list), "advice suggestions missing"
            out["passed"].append("fallback_banner_and_suggestions")

            # Test 10: compute_plan should mirror API behavior
            p = compute_plan({"categories": ["spirit"], "max_kcal": 200, "max_carbs": 10})
            assert p['picks'], "compute_plan returned empty picks"
            out["passed"].append("compute_plan_basic")

            # Test 11: pacing length matches requested drink_count (capped 4)
            p2 = compute_plan({"drink_count": 3})
            assert len(p2['pacing']) == 3, "pacing length should equal drink_count"
            out["passed"].append("pacing_matches_count")

            # Test 12: advice message string present on fallback
            p3 = compute_plan({"categories": ["beer"], "max_kcal": 10, "max_carbs": 0})
            if p3.get('fallback_used'):
                assert isinstance(p3.get('advice', {}).get('message', ''), str) and p3['advice']['message'], "advice.message missing"
                out["passed"].append("advice_message_present")

    except AssertionError as e:
        out["failed"].append(str(e))
    except Exception as e:
        out["failed"].append(f"unexpected: {e}")
    return out


# -----------------------------
# Entry point (debugger & reloader OFF, single-threaded + fallbacks + offline)
# -----------------------------

def _serve_with_flask(host: str, port: int) -> None:
    # Single-threaded Flask dev server
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=False)


def _serve_with_werkzeug(host: str, port: int) -> None:
    # Single-threaded run_simple
    from werkzeug.serving import run_simple
    run_simple(host, port, app, use_reloader=False, threaded=False)


def _serve_with_wsgiref(host: str, port: int) -> None:
    from wsgiref.simple_server import make_server
    with make_server(host, port, app) as httpd:
        httpd.serve_forever()


def _write_offline_artifacts(default_prefs: Optional[Dict[str, Any]] = None) -> None:
    """When server bind is not possible, compute a plan and write local files."""
    prefs = default_prefs or {}
    plan = compute_plan(prefs)

    # Write JSON artifact
    with open("offline_plan.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.datetime.utcnow().isoformat() + "Z", "plan": plan}, f, indent=2)

    # Optionally write PDF
    if REPORTLAB_AVAILABLE:
        pdf_bytes = build_pdf(plan)
        with open("offline_plan.pdf", "wb") as fpdf:
            fpdf.write(pdf_bytes)

    with open("offline_readme.txt", "w", encoding="utf-8") as fr:
        fr.write(
            "Offline mode was activated because a local web server could not be started.\n"
            "Artifacts created: offline_plan.json" + (", offline_plan.pdf" if REPORTLAB_AVAILABLE else "") + "\n"
            "Run `python travel_drink_app.py --host 0.0.0.0 --port 5000` on a host that allows servers to view the UI.\n"
        )

    print("[offline] Wrote offline_plan.json" + (" and offline_plan.pdf" if REPORTLAB_AVAILABLE else "") + ".")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--no-serve", action="store_true", help="Generate offline artifacts and exit.")
    args = parser.parse_args()

    if args.test:
        # Run tests and print JSON result, then return (no SystemExit)
        print(json.dumps(run_selftests(), indent=2))
        return

    if args.no_serve or os.environ.get("NO_SERVE") == "1":
        _write_offline_artifacts()
        return

    host, port = args.host, args.port
    try:
        _serve_with_flask(host, port)
    except (SystemExit, OSError, RuntimeError):
        try:
            _serve_with_werkzeug(host, port)
        except (SystemExit, OSError, RuntimeError):
            try:
                # Fallback to a safer bind
                safe_host = os.environ.get("SAFE_HOST", "0.0.0.0")
                safe_port = int(os.environ.get("SAFE_PORT", "8000"))
                _serve_with_wsgiref(safe_host, safe_port)
            except Exception as e:
                # Final fallback: offline artifacts (do not raise SystemExit)
                print(f"[offline] Unable to start a server in this environment. Last error: {e}")
                _write_offline_artifacts()
                return


if __name__ == "__main__":
    main()
