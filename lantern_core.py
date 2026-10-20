"""Lantern Guide helpers that don't need a browser: weather advice, event search and storage.
Everything here is plain Python (standard library only) so it can be tested on its own."""
import datetime as dt
import hashlib
import json
import os
import re
import threading
import urllib.parse
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STORE = os.path.join(DATA_DIR, "lantern_data.json")
_lock = threading.Lock()

DEFAULT = {"profile": None, "done": {}, "added_events": [], "settings": {"text_size": "M", "gemini_key": "",
           "gemini_model": "gemini-2.5-flash"}, "events_cache": {}, "board_cache": {}}


# ------------------------------------------------------------------ storage (one JSON file on this computer)
def load():
    with _lock:
        if not os.path.exists(STORE):
            return json.loads(json.dumps(DEFAULT))
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    for k, v in DEFAULT.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    for k, v in DEFAULT["settings"].items():
        data["settings"].setdefault(k, v)
    return data


def save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, STORE)


def http_json(url, body=None, headers=None, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "LanternGuide/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ------------------------------------------------------------------ places and weather (Open-Meteo: free, no key)
def geocode(query):
    """Town name (e.g. 'Cary, NC') -> list of {name, region, country, lat, lon}."""
    name = query.split(",")[0].strip()
    region = query.split(",")[1].strip().lower() if "," in query else ""
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": name, "count": 10, "language": "en"})
    results = http_json(url).get("results", [])
    out = [{"name": r["name"], "region": r.get("admin1", ""), "country": r.get("country_code", ""), "county": r.get("admin2", ""),
            "lat": r["latitude"], "lon": r["longitude"]} for r in results]
    if region:
        abbrev = US_STATES.get(region.upper(), region).lower()
        out.sort(key=lambda r: (r["region"].lower() not in (region, abbrev), r["country"] != "US"))
    return out[:5]


WEATHER_WORDS = {0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
                 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
                 66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow",
                 80: "rain showers", 81: "rain showers", 82: "heavy showers", 85: "snow showers", 86: "snow showers",
                 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def get_weather(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,uv_index_max,wind_speed_10m_max",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "auto", "forecast_days": 2})
    w = http_json(url)
    c, d = w["current"], w["daily"]
    today = {"high": round(d["temperature_2m_max"][0]), "low": round(d["temperature_2m_min"][0]),
             "rain_chance": d["precipitation_probability_max"][0] or 0, "code": d["weather_code"][0],
             "uv": d["uv_index_max"][0] or 0, "wind": round(d["wind_speed_10m_max"][0] or 0)}
    now = {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]), "code": c["weather_code"]}
    return {"now": now, "today": today, "summary": WEATHER_WORDS.get(today["code"], "mixed weather"),
            "advice": clothing_advice(today, now)}


def clothing_advice(today, now):
    """Plain-language 'what to wear' tips from today's forecast."""
    lo, hi, rain, code = today["low"], today["high"], today["rain_chance"], today["code"]
    tips = []
    if hi <= 32: tips.append("It's freezing. Wear a heavy coat, hat, gloves and a scarf.")
    elif hi <= 50: tips.append("It's cold. Wear a warm coat and layers.")
    elif hi <= 65: tips.append("It's cool. A sweater or light jacket is a good idea.")
    elif hi <= 80: tips.append("It's mild. Light layers will keep you comfortable.")
    else: tips.append("It's hot. Wear light, loose clothes and drink plenty of water.")
    if hi - lo >= 20 and hi <= 85: tips.append(f"It warms up from {lo}° to {hi}°, so dress in layers you can take off.")
    if code in (71, 73, 75, 77, 85, 86, 66, 67): tips.append("Snow or ice is possible. Wear boots with good grip and take your time.")
    elif rain >= 50 or code in (61, 63, 65, 80, 81, 82, 95, 96, 99): tips.append(f"{rain}% chance of rain. Bring an umbrella and wear shoes that won't slip.")
    elif rain >= 25: tips.append(f"{rain}% chance of rain. Keep an umbrella handy.")
    if code in (95, 96, 99): tips.append("Thunderstorms are possible. Plan outdoor errands for calmer hours.")
    if today["uv"] >= 6: tips.append("The sun is strong today. Wear a hat and sunscreen.")
    if today["wind"] >= 20: tips.append("It's windy. A jacket with a hood will help.")
    if hi >= 90: tips.append("Avoid long walks in the afternoon heat.")
    return tips


def parse_time(s):
    """'6:00 PM' -> '18:00', '14:30' -> '14:30', 'All day' -> ''."""
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s?m?", str(s or ""), re.I)
    if not m:
        return ""
    h, mm = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) and m.group(3).lower() == "p" and h < 12: h += 12
    if m.group(3) and m.group(3).lower() == "a" and h == 12: h = 0
    return f"{h:02d}:{mm:02d}" if h < 24 and mm < 60 else ""


# ------------------------------------------------------------------ local events (Gemini + Google Search)
CATEGORIES = ["festival", "volunteering", "class", "music", "faith", "health", "outdoors", "social", "library", "arts"]


def events_prompt(town, interests, today):
    return f"""Today is {today:%A, %B %d, %Y}. Search the web for real, upcoming events and opportunities in or near {town}
(within about 25 miles) between today and {today + dt.timedelta(days=30):%B %d, %Y} that would suit an older adult.
Their interests: {", ".join(interests) or "community events"}.
Include a mix of: cultural and seasonal festivals, volunteering opportunities, senior center or library programs and
classes, free health screenings, music, faith community events and gentle outdoor activities. Prefer free or low-cost.

Rules:
- Only include events you actually found on a web page in your search results. Never invent an event, date or place.
- Every event must have the URL of the page where you found it.
- Skip anything that has already happened or whose date you are unsure of.

Reply with ONLY a JSON array (no other words), up to 10 items, each like:
{{"title": "...", "date": "YYYY-MM-DD", "time": "e.g. 6:00 PM or All day", "place": "venue, town",
 "category": one of {CATEGORIES}, "why": "one friendly sentence on why they might enjoy it", "url": "https://..."}}"""


def parse_events(text, today):
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out, seen = [], set()
    for e in items if isinstance(items, list) else []:
        try:
            day = dt.date.fromisoformat(str(e.get("date", ""))[:10])
        except ValueError:
            continue
        url = str(e.get("url", ""))
        title = str(e.get("title", "")).strip()
        if day < today or day > today + dt.timedelta(days=45) or not url.startswith("http") or not title:
            continue                                         # past, too far away, or no source: drop it
        key = (title.lower(), day)
        if key in seen:
            continue
        seen.add(key)
        cat = str(e.get("category", "social")).lower()
        out.append({"id": hashlib.md5(f"{title}{day}".encode()).hexdigest()[:10], "title": title[:120],
                    "date": day.isoformat(), "time": str(e.get("time", ""))[:40], "place": str(e.get("place", ""))[:120],
                    "category": cat if cat in CATEGORIES else "social", "why": str(e.get("why", ""))[:200], "url": url})
    return sorted(out, key=lambda e: e["date"])


def fetch_events(key, model, town, interests, today=None):
    today = today or dt.date.today()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": events_prompt(town, interests, today)}]}], "tools": [{"google_search": {}}]}
    r = http_json(url, body, headers={"x-goog-api-key": key}, timeout=90)
    parts = r.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return parse_events("".join(p.get("text", "") for p in parts), today)


def events_for(data, refresh=False, today=None):
    """Cached once per day per town+interests. Returns {'events', 'status', 'message'}."""
    today = today or dt.date.today()
    prof, st = data.get("profile") or {}, data["settings"]
    town, interests = prof.get("town", ""), prof.get("interests", [])
    if not town:
        return {"events": [], "status": "setup", "message": "Add your town in Settings to see what's happening nearby."}
    if not st.get("gemini_key"):
        return {"events": [], "status": "nokey", "message": "To see local events, a family member can add a free Google Gemini key in Settings."}
    ck = hashlib.md5(json.dumps([town, sorted(interests), today.isoformat()]).encode()).hexdigest()
    cache = data.get("events_cache", {})
    if not refresh and cache.get("key") == ck:
        events = cache["events"]
    else:
        try:
            events = fetch_events(st["gemini_key"], st.get("gemini_model") or "gemini-2.5-flash", town, interests, today)
        except Exception as e:
            old = [ev for ev in cache.get("events", []) if ev["date"] >= today.isoformat()]
            return {"events": old, "status": "error", "message": f"Couldn't get new events right now ({e.__class__.__name__}). Showing what we found before."}
        data["events_cache"] = {"key": ck, "events": events, "fetched": dt.datetime.now().isoformat(timespec="minutes")}
        save(data)
    events = [ev for ev in events if ev["date"] >= today.isoformat()]
    return {"events": events, "status": "ok", "message": "" if events else "No upcoming events found this time. Try again tomorrow."}


US_STATES = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
             "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
             "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
             "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
             "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
             "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
             "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
             "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
             "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
             "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia"}
