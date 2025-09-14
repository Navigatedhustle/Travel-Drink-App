"""
Travel Drink Generator - Flask App (Client-Ready UI + Spirit Chooser + Closest-Fit + Offline Fallback)

File name: travel_drink_app.py

What changed (this edit)
- **Fixed IndentationError**: added the missing body inside a final `except Exception as e:` block in `main()`.
- **Removed duplicate trailing code** that redefined entry points and reintroduced `utcnow()`.
- **Kept all existing tests** and **added 2 new tests** (spirit filtering + defaults enforcement).
- **UI** remains refreshed with spirit chooser and simplified inputs (only drink count required).
- **UTC** timestamps remain timezone‑aware.

Purpose
- Help busy professionals in a calorie deficit pick lower-calorie, lower-carb alcoholic drinks while traveling.

How to run locally
1) `pip install flask reportlab` (reportlab optional; enables PDF)
2) `python travel_drink_app.py` then open `http://127.0.0.1:5000`
3) If the host blocks servers, offline artifacts are written instead.
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
]

app = Flask(__name__)

# -----------------------------
# Nutrition and drink database
# -----------------------------
ALC_DENSITY = 0.789
ALC_KCAL_PER_G = 7.0
OZ_TO_ML = 29.5735

@dataclass
class Drink:
    name: str
    category: str  # wine, beer, spirit, seltzer, cocktail, mocktail
    serving_oz: float
    abv_pct: float  # 0 to 100
    extra_cals: float = 0.0
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

# Mixers (kcal per ounce)
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

    # Wine
    Drink("Dry white wine (5 oz)", "wine", 5.0, 12, extra_cals=20, carbs_g=3.0, sugar_g=1.5, carbonation=False,
          order_script="Five ounces of dry white wine."),
    Drink("Dry red wine (5 oz)", "wine", 5.0, 13, extra_cals=22, carbs_g=4.0, sugar_g=1.0, carbonation=False,
          order_script="Five ounces of dry red wine."),
    Drink("Brut champagne (5 oz)", "wine", 5.0, 12, extra_cals=10, carbs_g=2.0, sugar_g=1.0, carbonation=True,
          order_script="A five ounce pour of brut champagne."),

    # Beer & seltzer
    Drink("Light beer (12 oz)", "beer", 12.0, 4.2, extra_cals=20, carbs_g=5.0, sugar_g=0.0, gluten_free=False, keto_friendly=False, carbonation=True,
          order_script="A twelve ounce light beer."),
    Drink("Regular lager (12 oz)", "beer", 12.0, 5.0, extra_cals=60, carbs_g=13.0, sugar_g=0.0, gluten_free=False, keto_friendly=False, carbonation=True,
          order_script="A twelve ounce lager."),
    Drink("Hard seltzer (12 oz)", "seltzer", 12.0, 5.0, extra_cals=30, carbs_g=2.0, sugar_g=1.0, gluten_free=True, keto_friendly=True, carbonation=True,
          order_script="A twelve ounce hard seltzer."),
]

# Preset cocktails
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
    base = find_base(component_name)
    if base is not None:
        kcal = base.total_kcal() * units
        carbs = (base.carbs_g or 0.0) * units
        return kcal, carbs
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

    total_volume_oz = 0.0
    alcohol_ml = 0.0
    for comp_name, amount in recipe["components"]:
        base = find_base(comp_name)
        if base is not None:
            vol_ml = base.serving_oz * amount * OZ_TO_ML
            total_volume_oz += base.serving_oz * amount
            alcohol_ml += vol_ml * (base.abv_pct / 100.0)
            carbonation = carbonation or base.carbonation
            caffeine = caffeine or base.caffeine
            gluten_free = gluten_free and base.gluten_free
            keto = keto and base.keto_friendly
        else:
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


def _matches_any(haystack: List[str], needles: List[str]) -> bool:
    h = ",".join(haystack).lower()
    return any(n in h for n in needles)


def generate_candidates(include_categories: List[str], include_spirits: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    include_spirits = [s.lower() for s in (include_spirits or []) if s]
    recipes: List[Dict[str, Any]] = []

    if "mocktail" in include_categories:
        recipes.extend(MOCKTAILS)
    recipes.extend(PRESET_COCKTAILS)

    # Spirits neat
    for d in BASE_LIBRARY:
        if d.category == "spirit":
            recipes.append({
                "name": d.name.replace(" (1.5 oz)", " neat"),
                "components": [(d.name, 1.0)],
                "tags": ["zero-carb", "simple"],
                "order": f"{d.name.split(' (')[0]} neat, one and a half ounces.",
            })

    # Wines/Seltzers/Beers direct
    for d in BASE_LIBRARY:
        if d.category in {"wine", "seltzer", "beer"}:
            recipes.append({
                "name": d.name,
                "components": [(d.name, 1.0)],
                "tags": [d.category],
                "order": d.order_script or d.name,
            })

    def matches_category(r: Dict[str, Any]) -> bool:
        cats = set(include_categories or ["any"])
        base_names = [c[0].lower() for c in r["components"]]
        if "any" in cats:
            return True
        if ("wine" in cats) and _matches_any(base_names, ["wine", "champagne"]):
            return True
        if ("beer" in cats) and _matches_any(base_names, ["beer", "lager"]):
            return True
        if ("seltzer" in cats) and _matches_any(base_names, ["seltzer"]):
            return True
        if ("cocktail" in cats) and any(find_base(n) is not None for n, _ in r["components"]) and any(n in MIXERS for n, _ in r["components"]):
            return True
        if ("spirit" in cats) and _matches_any(base_names, ["vodka", "tequila", "gin", "whiskey", "rum"]):
            return True
        if ("mocktail" in cats) and r["name"] in [m["name"] for m in MOCKTAILS]:
            return True
        return False

    def matches_spirit(r: Dict[str, Any]) -> bool:
        if not include_spirits:
            return True
        base_names = [c[0].lower() for c in r["components"]]
        return _matches_any(base_names, include_spirits)

    return [r for r in recipes if matches_category(r) and matches_spirit(r)]


def score_drink(profile: Dict[str, Any], prefs: Dict[str, Any]) -> float:
    score = 100.0
    kcal = profile["kcal"]
    carbs = profile["carbs_g"]

    score -= 0.1 * max(0, kcal - 60)
    score -= 1.0 * carbs

    max_kcal = prefs.get("max_kcal", 130)  # healthy defaults
    max_carbs = prefs.get("max_carbs", 8.0)
    if kcal > max_kcal:
        score -= 200
    if carbs > max_carbs:
        score -= 200

    if prefs.get("sugar_free_mixers", True) and carbs > 3.0:
        score -= 50
    if not prefs.get("allow_caffeine", True) and profile["caffeine"]:
        score -= 40
    if not prefs.get("allow_carbonation", True) and profile["carbonation"]:
        score -= 40
    if prefs.get("gluten_free_only") and not profile["gluten_free"]:
        score -= 200
    if prefs.get("keto_only") and not profile["keto"]:
        score -= 200

    pref_cat = prefs.get("pref_category")
    if pref_cat and (pref_cat in profile.get("tags", []) or pref_cat in profile["name"].lower()):
        score += 5

    return score


def build_plan(selected: List[Dict[str, Any]], prefs: Dict[str, Any], fallback_used: bool, advice: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    n = int(prefs.get("drink_count", 1))
    n = max(1, min(4, n))
    picks = selected[:n]

    pacing = []
    for i in range(n):
        pacing.append({
            "slot": i + 1,
            "instruction": "Sip slowly for 45–60 minutes, alternate with water (12 oz), add a pinch of salt if you sweat a lot.",
        })

    recovery = [
        "500–750 ml water before bed; electrolytes if you trained.",
        "Protein-forward breakfast (eggs/Greek yogurt); add fruit for potassium.",
        "Zone 2 walk 20–30 min.",
        "Avoid driving; never mix alcohol with sedatives.",
    ]

    return {
        "picks": picks,
        "pacing": pacing,
        "recovery": recovery,
        "fallback_used": fallback_used,
        "advice": advice,
    }


def _fallback_suggestions(prefs: Dict[str, Any], categories: List[str]) -> Dict[str, Any]:
    suggestions: List[str] = []
    cats = set(categories or [])
    max_kcal = int(prefs.get("max_kcal", 130))
    max_carbs = float(prefs.get("max_carbs", 8))

    if max_kcal < 90:
        suggestions.append("Increase max calories to ≥ 90 kcal — most spirit + soda combos land 90–110 kcal.")
    if max_carbs < 2 and ("wine" in cats or "seltzer" in cats):
        suggestions.append("Allow up to 2–5 g carbs for dry wine or hard seltzer options.")
    if not prefs.get("allow_carbonation", True) and ("beer" in cats or "seltzer" in cats):
        suggestions.append("Enable carbonation or include spirits to avoid beer/seltzer conflicts.")
    if prefs.get("gluten_free_only") and "beer" in cats:
        suggestions.append("Gluten-free only with beer is restrictive — switch to spirits with soda or brut champagne.")
    if prefs.get("keto_only") and ("beer" in cats or "wine" in cats):
        suggestions.append("Keto + beer/wine is tough — prefer spirits with soda water.")

    message = "No exact matches. Showing closest-fit picks based on your limits."
    return {"message": message, "suggestions": suggestions[:5]}


def compute_plan(prefs: Dict[str, Any]) -> Dict[str, Any]:
    categories = prefs.get("categories") or ["any"]
    include_spirits = prefs.get("spirits") or []  # e.g., ["vodka","tequila"]

    # Inject healthy defaults if not supplied
    prefs = {**{"max_kcal": 130, "max_carbs": 8.0, "sugar_free_mixers": True, "allow_caffeine": True, "allow_carbonation": True}, **(prefs or {})}

    candidates = generate_candidates(categories, include_spirits)
    profiles = [build_drink_profile(r) for r in candidates]
    scored = sorted(profiles, key=lambda p: -score_drink(p, prefs))

    feasible: List[Dict[str, Any]] = []
    for p in scored:
        if p["kcal"] <= int(prefs.get("max_kcal", 130)) and p["carbs_g"] <= float(prefs.get("max_carbs", 8.0)):
            if not prefs.get("gluten_free_only") or p["gluten_free"]:
                if not prefs.get("keto_only") or p["keto"]:
                    if prefs.get("allow_caffeine", True) or not p["caffeine"]:
                        if prefs.get("allow_carbonation", True) or not p["carbonation"]:
                            feasible.append(p)

    fallback_used = False if feasible else True
    selected = feasible[:5] if feasible else scored[:5]
    advice = _fallback_suggestions(prefs, categories) if fallback_used else None

    plan = build_plan(selected, prefs, fallback_used, advice)
    plan["pdf_url"] = None
    return plan


# -----------------------------
# HTML templates (client-facing UI)
# -----------------------------
BASE_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ app_name }}</title>
  <style>
    :root { --ink:#0f172a; --muted:#64748b; --card:#ffffff; --bg:#f8fafc; --accent:#14b8a6; --ring: rgba(20,184,166,0.25); }
    * { box-sizing: border-box; }
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin:0; background: var(--bg); color: var(--ink); }
    header { padding: 36px 20px; background: linear-gradient(135deg, #0ea5e9 0%, #14b8a6 100%); color:#fff; }
    header .wrap { max-width: 980px; margin: 0 auto; }
    h1 { margin:0; font-size: 28px; letter-spacing: .3px; }
    .sub { opacity:.95; margin-top:8px; }
    main { max-width: 980px; margin: -20px auto 48px; padding: 0 20px; }
    .card { background: var(--card); border:1px solid #e2e8f0; border-radius: 16px; box-shadow: 0 10px 30px rgba(2,8,23,.06); padding:20px; margin-top: 20px; }
    .row { display:grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
    label { font-size: 13px; color: var(--muted); display:block; margin-bottom:6px; }
    input[type="number"], select { width:100%; padding:10px 12px; border-radius:12px; border:1px solid #cbd5e1; outline:none; box-shadow: 0 0 0 0 var(--ring); transition: box-shadow .15s, border-color .15s; }
    input[type="number"]:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 4px var(--ring); }
    .checks { display:grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap:8px; }
    .pill { display:flex; align-items:center; gap:8px; border:1px solid #cbd5e1; padding:10px 12px; border-radius: 999px; background:#fff; }
    .cta { display:flex; gap:12px; align-items:center; }
    .btn { background:#111827; color:#fff; padding:12px 16px; border:0; border-radius: 12px; cursor:pointer; }
    .btn:focus { outline: none; box-shadow: 0 0 0 4px var(--ring); }
    .muted { color: var(--muted); font-size: 13px; }
    .kpi { font-weight:700; }
    .tag { display:inline-block; background:#f1f5f9; border:1px solid #e2e8f0; padding:4px 10px; border-radius:999px; margin-right:6px; font-size:12px; }
    .banner { border-left: 4px solid #10b981; background: #ecfdf5; padding: 12px 14px; border-radius: 12px; }
    h2 { margin: 0 0 12px 0; font-size: 18px; }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>Travel Drink Generator</h1>
      <div class="sub">Smarter orders, lower calories, next-day you will thank you.</div>
    </div>
  </header>
  <main>
    <div class="card">
      <form id="genForm" class="grid" autocomplete="off">
        <div class="row">
          <div>
            <label>How many drinks tonight</label>
            <input type="number" name="drink_count" value="1" min="1" max="4" />
            <div class="muted">We’ll pace you. Max 4.</div>
          </div>
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
        </div>

        <div style="margin-top:8px;">
          <label>Choose alcohol types</label>
          <div class="checks">
            <label class="pill"><input type="checkbox" name="spirits" value="vodka" checked> Vodka</label>
            <label class="pill"><input type="checkbox" name="spirits" value="tequila"> Tequila</label>
            <label class="pill"><input type="checkbox" name="spirits" value="gin"> Gin</label>
            <label class="pill"><input type="checkbox" name="spirits" value="whiskey"> Whiskey/Bourbon</label>
            <label class="pill"><input type="checkbox" name="spirits" value="rum"> Rum</label>
            <label class="pill"><input type="checkbox" name="cats" value="wine"> Wine</label>
            <label class="pill"><input type="checkbox" name="cats" value="seltzer"> Seltzer</label>
            <label class="pill"><input type="checkbox" name="cats" value="beer"> Beer</label>
            <label class="pill"><input type="checkbox" name="cats" value="mocktail"> Mocktail</label>
          </div>
          <div class="muted">No calorie or carb inputs needed — we use healthy defaults.</div>
        </div>

        <div style="margin-top:8px;" class="checks">
          <label class="pill"><input type="checkbox" name="sugar_free_mixers" checked> Prefer sugar‑free mixers</label>
          <label class="pill"><input type="checkbox" name="gluten_free_only"> Gluten‑free only</label>
          <label class="pill"><input type="checkbox" name="keto_only"> Keto only</label>
          <label class="pill"><input type="checkbox" name="allow_caffeine" checked> Allow caffeine</label>
          <label class="pill"><input type="checkbox" name="allow_carbonation" checked> Allow carbonation</label>
        </div>

        <div class="cta" style="margin-top:12px;">
          <button class="btn" type="submit">Generate Smart Picks</button>
          <span class="muted">Optimized around ~≤130 kcal / ≤8g carbs by default.</span>
        </div>
      </form>
    </div>

    <div id="results"></div>
  </main>

  <script>
  const form = document.getElementById('genForm');
  const results = document.getElementById('results');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    results.innerHTML = '<div class="card muted">Working…</div>';

    const fd = new FormData(form);
    const drink_count = parseInt(fd.get('drink_count')||'1');
    const pref_category = fd.get('pref_category') || '';

    // Collect spirit preferences and category add-ons
    const spirits = fd.getAll('spirits');
    const cats = fd.getAll('cats');
    const categories = ['spirit', ...cats]; // always include spirits bucket if any spirit is chosen

    const payload = {
      drink_count,
      pref_category,
      spirits,
      categories,
      // healthy defaults applied server-side; we still pass filters
      sugar_free_mixers: fd.get('sugar_free_mixers') === 'on',
      gluten_free_only: fd.get('gluten_free_only') === 'on',
      keto_only: fd.get('keto_only') === 'on',
      allow_caffeine: fd.get('allow_caffeine') === 'on',
      allow_carbonation: fd.get('allow_carbonation') === 'on'
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
      html += `<div class="card banner"><strong>${advice.message || 'Closest‑fit picks'}</strong>${advice.suggestions && advice.suggestions.length ? `<ul>${advice.suggestions.map(s=>`<li>${s}</li>`).join('')}</ul>`: ''}</div>`;
    }

    picks.forEach((p, idx) => {
      html += `
        <div class="card">
          <h2>Pick ${idx+1}: ${p.name}</h2>
          <div class="row" style="margin-top:8px;">
            <div><span class="kpi">${p.kcal}</span> kcal</div>
            <div><span class="kpi">${p.carbs_g}</span> g carbs</div>
            <div><span class="kpi">${p.abv_pct}%</span> ABV</div>
          </div>
          <p class="muted" style="margin-top:6px;"><strong>Order:</strong> ${p.order}</p>
          <div class="muted">${(p.tags||[]).map(t=>`<span class='tag'>${t}</span>`).join(' ')}</div>
          <div class="muted" style="margin-top:4px;">${p.gluten_free ? 'Gluten‑free,' : ''} ${p.keto ? 'Keto‑aware,' : ''} ${p.caffeine ? 'Caffeinated,' : 'No caffeine,'} ${p.carbonation ? 'Carbonated' : 'Still'}</div>
        </div>`;
    });

    html += `<div class=\"card\"><h2>Pacing plan</h2><ol>${pacing.map(s=>`<li>${s.instruction}</li>`).join('')}</ol></div>`;
    html += `<div class=\"card\"><h2>Next‑day recovery</h2><ul>${recovery.map(x=>`<li>${x}</li>`).join('')}</ul></div>`;

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

    if prefs.get("want_pdf") and REPORTLAB_AVAILABLE:
        pdf_bytes = build_pdf(plan)
        key = f"plan_{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}.pdf"
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
# Self-test utilities
# -----------------------------
@app.get('/api/selftest')
def selftest_endpoint():
    return jsonify(run_selftests())


def run_selftests() -> Dict[str, Any]:
    out: Dict[str, Any] = {"passed": [], "failed": [], "env": {"REPORTLAB_AVAILABLE": REPORTLAB_AVAILABLE}}
    try:
        with app.test_client() as c:
            r = c.post('/api/generate', json={})
            assert r.status_code == 200
            data = r.get_json(); assert data and data.get('plan') and data['plan']['picks']
            out["passed"].append("basic_generate")

            r2 = c.post('/api/generate', json={"categories": ["beer"], "max_kcal": 60, "max_carbs": 0})
            assert r2.status_code == 200 and r2.get_json()['plan']['picks']
            out["passed"].append("strict_filter_fallback")

            r3 = c.post('/api/generate', json={"want_pdf": True})
            assert r3.status_code == 200 and 'pdf_url' in r3.get_json()['plan']
            out["passed"].append("pdf_key_present")

            r4 = c.get('/api/health-tips'); assert r4.status_code == 200 and r4.get_json().get('tips')
            out["passed"].append("health_tips")

            if REPORTLAB_AVAILABLE:
                r5 = c.post('/api/generate', json={"want_pdf": True}); key_url = r5.get_json()['plan']['pdf_url']
                assert key_url; r6 = c.get(key_url); assert r6.status_code == 200 and r6.headers.get('Content-Type') == 'application/pdf'
                out["passed"].append("pdf_roundtrip")

            r7 = c.post('/api/generate', json={"categories": ["beer", "spirit", "cocktail"], "gluten_free_only": True})
            plan7 = r7.get_json()['plan']; assert all(p.get('gluten_free', True) for p in plan7['picks'])
            out["passed"].append("gluten_free_filter")

            r8 = c.post('/api/generate', json={"categories": ["mocktail"], "max_kcal": 50})
            plan8 = r8.get_json()['plan']; assert plan8['picks'] and all(p['abv_pct'] == 0.0 or p['abv_pct'] < 0.5 for p in plan8['picks'])
            out["passed"].append("mocktail_abv_zero")

            r9 = c.get('/'); assert r9.status_code == 200 and b'<title' in r9.data
            out["passed"].append("index_html")

            r10 = c.post('/api/generate', json={"categories": ["beer"], "max_kcal": 60, "max_carbs": 0, "allow_carbonation": False, "gluten_free_only": True})
            plan10 = r10.get_json()['plan']; assert plan10.get('fallback_used') is True and plan10.get('advice')
            out["passed"].append("fallback_banner_and_suggestions")

            # Added tests
            r11 = c.post('/api/generate', json={"categories": ["spirit","cocktail"], "spirits": ["vodka"]})
            plan11 = r11.get_json()['plan']
            assert plan11['picks'] and all(('vodka' in p['name'].lower()) or ('vodka' in p.get('order','').lower()) for p in plan11['picks']), "spirit filter should favor vodka recipes"
            out["passed"].append("spirit_filtering_vodka")

            r12 = c.post('/api/generate', json={})
            plan12 = r12.get_json()['plan']
            assert all(p['kcal'] <= 130 and p['carbs_g'] <= 8.0 for p in plan12['picks']), "defaults (kcal≤130, carbs≤8) not enforced"
            out["passed"].append("defaults_enforced")

    except AssertionError as e:
        out["failed"].append(str(e))
    except Exception as e:
        out["failed"].append(f"unexpected: {e}")
    return out


# -----------------------------
# Entry point (single-threaded + fallbacks + offline)
# -----------------------------

def _serve_with_flask(host: str, port: int) -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=False)


def _serve_with_werkzeug(host: str, port: int) -> None:
    from werkzeug.serving import run_simple
    run_simple(host, port, app, use_reloader=False, threaded=False)


def _serve_with_wsgiref(host: str, port: int) -> None:
    from wsgiref.simple_server import make_server
    with make_server(host, port, app) as httpd:
        httpd.serve_forever()


def _write_offline_artifacts(default_prefs: Optional[Dict[str, Any]] = None) -> None:
    prefs = default_prefs or {}
    plan = compute_plan(prefs)

    with open("offline_plan.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "plan": plan}, f, indent=2)

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

