"""What's on nearby, for any town, with NO API key, kept fresh automatically.

Sections (each has its own short-lived cache, so the board keeps updating):
  events     - holidays/festivals calendar + upcoming events found in local news, matched to the person's interests
  volunteer  - volunteer calls and opportunities near them, matched to interests, only ones still ahead
  news       - recent local stories for each interest
  places     - senior centers, libraries, community centers, plus places that fit their interests (gardens, parks...)
Free sources: Google News RSS, Bing News RSS, Bing web RSS, OpenStreetMap (Overpass, then Nominatim as a backup).
Optional: a Google Gemini key adds an AI web search for dated events.
Only the town name and interests are used in searches; never messages, medicines or names.
"""
import datetime as dt
import email.utils
import hashlib
import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import os
import threading

import lantern_core as core
import lantern_sources as src

# ------------------------------------------------------------------ look-up results live in their own file
# (so saving your plans and a background look-up can never overwrite each other)
CACHE_FILE = os.path.join(core.DATA_DIR, "lookups.json")
_cache_lock = threading.Lock()
_cache = None


def _cache_path():
    return os.path.join(core.DATA_DIR, "lookups.json")


def bc_get(key, default=None):
    global _cache
    with _cache_lock:
        if _cache is None:
            try:
                with open(_cache_path(), encoding="utf-8") as f:
                    _cache = json.load(f)
            except (OSError, ValueError):
                _cache = {}
        v = _cache.get(key)
        return json.loads(json.dumps(v)) if v is not None else default


def bc_put(key, value):
    global _cache
    bc_get("_")                                                  # make sure it's loaded
    with _cache_lock:
        _cache[key] = value
        os.makedirs(core.DATA_DIR, exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f)
        os.replace(tmp, _cache_path())


def reset_cache(keep=("teams",)):
    global _cache
    bc_get("_")
    with _cache_lock:
        _cache = {k: v for k, v in _cache.items() if k in keep}

src._get = lambda *a, **k: _get(*a, **k)             # one fetcher for everything (easy to test)
src.STATE_ABBR = {v: k.lower() for k, v in core.US_STATES.items()}

OSM_UA = "LanternGuide/1.0 (student project for older adults; local app)"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
CACHE_MINUTES = {"events": 120, "volunteer": 180, "news": 60, "places": 60 * 24 * 7}
AREA_DAYS = 30            # nearby towns and county are looked up once a month
MAX_INTERESTS = 12          # every interest is searched (up to this many, to keep it quick)


HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8"}
try:                                                             # reuse connections: much faster than a new one each time
    import requests as _rq
    from requests.adapters import HTTPAdapter as _HA
    _session = _rq.Session()
    _session.mount("https://", _HA(pool_connections=30, pool_maxsize=30, max_retries=0))
    _session.mount("http://", _HA(pool_connections=10, pool_maxsize=10, max_retries=0))
except Exception:
    _session = None


def _get(url, data=None, timeout=12, headers=None, long=False):
    """Fetch a web page. Short waits by default; long=True only for the one big background map download."""
    timeout = min(timeout, 60 if long else 14)
    h = {**HEADERS, **(headers or {})}
    if "openstreetmap.org" in url or "overpass" in url:             # OpenStreetMap's rules: say who you are, don't pretend to be a browser
        h = {"User-Agent": OSM_UA, "Accept": "application/json", "Accept-Language": "en-US"}
    if _session is not None:
        if data is not None:
            r = _session.post(url, data=data, headers={**h, "Content-Type": "application/x-www-form-urlencoded"}, timeout=timeout)
        else:
            r = _session.get(url, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.content
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _id(*parts):
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:10]



def friendly_error(e):
    s = f"{e.__class__.__name__}: {e}"
    if "CERTIFICATE_VERIFY_FAILED" in s:
        return "Python's security certificates aren't installed. Run 'Install Certificates.command' in Applications > Python 3.12."
    if "timed out" in s.lower() or "Timeout" in s:
        return "The website took too long to answer."
    if "429" in s or "Too Many" in s:
        return "The website is busy. It will work again in a few minutes."
    if "getaddrinfo" in s or "Name or service" in s or "nodename" in s:
        return "No internet connection."
    return s[:160]


# ------------------------------------------------------------------ 1. calendar of holidays and festivals
def _nth(year, month, weekday, n):
    """n-th weekday (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        return d + dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    d = (dt.date(year, month + 1, 1) if month < 12 else dt.date(year + 1, 1, 1)) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(y):
    """Easter Sunday (Meeus/Jones/Butcher method)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return dt.date(y, month, (h + l - 7 * m + 114) % 31 + 1)


# Festivals that follow the moon: dates looked up ahead of time. approx=True means "may differ by a day".
MOON_FESTIVALS = [
    ("2026-11-08", "Diwali, the festival of lights", "festival", "A joyful Hindu, Sikh and Jain festival of lamps, sweets and family. Temples and community groups often hold celebrations open to everyone.", "Diwali celebration", False),
    ("2026-12-04", "Hanukkah begins at sundown", "faith", "The eight-night Jewish festival of lights. Synagogues and towns often hold public menorah lightings.", "Hanukkah menorah lighting", True),
    ("2027-02-06", "Lunar New Year", "festival", "Welcome the Year of the Goat with lion dances, food and family gatherings.", "Lunar New Year celebration", False),
    ("2027-02-08", "Ramadan begins", "faith", "The Muslim month of fasting and reflection. Many mosques host community iftar dinners that welcome neighbors.", "community iftar", True),
    ("2027-03-10", "Eid al-Fitr", "festival", "Celebrates the end of Ramadan with prayers, feasts and visits to family and friends.", "Eid al-Fitr celebration", True),
    ("2027-03-22", "Holi, the festival of colors", "festival", "A bright spring festival with colored powder, music and sweets. Many events are family friendly.", "Holi festival", True),
    ("2027-04-21", "Passover begins at sundown", "faith", "The Jewish festival remembering the Exodus, shared around the Seder table.", "community Passover seder", True),
    ("2027-10-29", "Diwali, the festival of lights", "festival", "A joyful Hindu, Sikh and Jain festival of lamps, sweets and family. Temples and community groups often hold celebrations open to everyone.", "Diwali celebration", True),
    ("2027-12-24", "Hanukkah begins at sundown", "faith", "The eight-night Jewish festival of lights. Synagogues and towns often hold public menorah lightings.", "Hanukkah menorah lighting", True),
    ("2028-01-26", "Lunar New Year", "festival", "Welcome the Year of the Monkey with lion dances, food and family gatherings.", "Lunar New Year celebration", False),
]


def _year_items(y):
    """(date, title, category, why, search-or-url, approx) for one year."""
    labor = _nth(y, 9, 0, 1)
    items = [
        (dt.date(y, 1, 1), "New Year's Day", "holiday", "A fresh start. A nice day to call old friends.", None),
        (_nth(y, 1, 0, 3), "Martin Luther King Jr. Day", "community", "Many towns hold service projects and celebrations.", "MLK Day service event"),
        (dt.date(y, 2, 14), "Valentine's Day", "holiday", "A good excuse to send a card to someone you love.", None),
        (_nth(y, 3, 6, 2), "Clocks move forward one hour tonight", "reminder", "Daylight saving time starts. Phones change by themselves, but kitchen and car clocks may not.", None),
        (dt.date(y, 3, 17), "St. Patrick's Day", "festival", "Parades and music in many towns.", "St. Patrick's Day parade"),
        (_easter(y), "Easter Sunday", "faith", "Many churches hold special services, and towns host egg hunts for grandchildren.", "Easter service and egg hunt"),
        (dt.date(y, 4, 22), "Earth Day", "outdoors", "Parks and gardens often hold clean-ups and planting days.", "Earth Day event"),
        (_nth(y, 5, 6, 2), "Mother's Day", "holiday", "A day for mothers and grandmothers.", None),
        (_nth(y, 5, 0, -1), "Memorial Day", "community", "Ceremonies honor those who died serving the country.", "Memorial Day ceremony"),
        (_nth(y, 6, 6, 3), "Father's Day", "holiday", "A day for fathers and grandfathers.", None),
        (dt.date(y, 6, 19), "Juneteenth", "community", "Celebrations of freedom with music, food and history.", "Juneteenth celebration"),
        (dt.date(y, 7, 4), "Independence Day", "festival", "Parades, picnics and fireworks. Fireworks are loud, so plan ahead if that bothers you.", "Fourth of July celebration"),
        (labor, "Labor Day", "holiday", "The unofficial end of summer.", None),
        (labor + dt.timedelta(days=6), "National Grandparents Day", "holiday", "A day to celebrate grandparents. Expect a phone call!", None),
        (dt.date(y, 10, 31), "Halloween", "festival", "Keep some treats by the door, and leave the porch light on if you'd like trick-or-treaters.", "Halloween event"),
        (dt.date(y, 11, 1), "Día de los Muertos", "festival", "A colorful Mexican tradition remembering loved ones with altars, flowers and food.", "Dia de los Muertos celebration"),
        (_nth(y, 11, 6, 1), "Clocks go back one hour tonight", "reminder", "Daylight saving time ends. You get an extra hour of sleep. Check kitchen and car clocks.", None),
        (dt.date(y, 11, 11), "Veterans Day", "community", "Ceremonies and free meals for veterans in many places.", "Veterans Day ceremony"),
        (_nth(y, 11, 3, 4), "Thanksgiving", "holiday", "Many churches and charities welcome volunteers to serve meals this week.", "Thanksgiving volunteer"),
        (dt.date(y, 12, 25), "Christmas Day", "holiday", "Many towns hold tree lightings and concerts in the weeks before.", "Christmas tree lighting and concert"),
        (dt.date(y, 12, 26), "Kwanzaa begins", "festival", "A week-long celebration of African American culture and family.", "Kwanzaa celebration"),
        (dt.date(y, 12, 31), "New Year's Eve", "holiday", "Some towns hold early, family-friendly countdowns.", "family New Year's Eve event"),
        (dt.date(y, 10, 15), "Medicare open enrollment starts", "health", "From October 15 to December 7 you can change your Medicare plan. A free SHIP counselor can help you compare.", "https://www.medicare.gov"),
        (dt.date(y, 12, 7), "Last day of Medicare open enrollment", "health", "Today is the last day to change your Medicare plan for next year.", "https://www.medicare.gov"),
    ]
    if y % 2 == 0:                                      # federal elections are in even years
        items.append((_nth(y, 11, 0, 1) + dt.timedelta(days=1), "Election Day", "community",
                      "Remember to vote. Your county board of elections can tell you where and when.", "https://vote.gov"))
    return [(d, t, c, w, s, False) for d, t, c, w, s in items]


US_ONLY = {"Martin Luther King Jr. Day", "Clocks move forward one hour tonight", "Memorial Day", "Juneteenth",
           "Independence Day", "Labor Day", "National Grandparents Day", "Clocks go back one hour tonight", "Veterans Day",
           "Thanksgiving", "Medicare open enrollment starts", "Last day of Medicare open enrollment", "Election Day"}


FAITH_OF = {"Diwali, the festival of lights": {"hindu", "sikh", "jain"}, "Holi, the festival of colors": {"hindu"},
            "Hanukkah begins at sundown": {"jewish"}, "Passover begins at sundown": {"jewish"}, "Ramadan begins": {"muslim"},
            "Eid al-Fitr": {"muslim"}, "Easter Sunday": {"christian"}, "Christmas Day": {"christian"}}


def faith_days(y, tradition):
    """Extra holy days, shown only to people of that faith. approx=True when the date follows the moon."""
    e, rows = _easter(y), []
    if tradition == "christian":
        xmas = dt.date(y, 12, 25)
        rows = [(e - dt.timedelta(days=46), "Ash Wednesday", "The start of Lent.", False),
                (e - dt.timedelta(days=7), "Palm Sunday", "The start of Holy Week.", False),
                (e - dt.timedelta(days=2), "Good Friday", "A day of prayer before Easter.", False),
                (dt.date(y, 11, 1), "All Saints' Day", "A day to remember the saints and loved ones.", False),
                (xmas - dt.timedelta(days=(xmas.weekday() + 1) % 7 + 21), "First Sunday of Advent", "The season of waiting for Christmas begins.", False),
                (dt.date(y, 12, 24), "Christmas Eve", "Many churches hold candlelight services tonight.", False)]
    if tradition == "sikh":
        rows = [(dt.date(y, 4, 14), "Vaisakhi", "Celebrates the founding of the Khalsa. Gurdwaras hold kirtan and langar.", True)]
        if y == 2026:
            rows.append((dt.date(2026, 11, 24), "Guru Nanak Gurpurab", "Celebrates the birth of Guru Nanak with prayers and langar.", True))
    if tradition == "muslim" and y == 2027:
        rows = [(dt.date(2027, 5, 16), "Eid al-Adha", "The festival of sacrifice, with prayers, meals and giving.", True)]
    if tradition == "buddhist" and y == 2027:
        rows = [(dt.date(2027, 5, 20), "Vesak", "Honors the birth, enlightenment and passing of the Buddha.", True)]
    return [(d, t, "your-faith", w, None, a) for d, t, w, a in rows]


def calendar_items(town, today, country="US", days=75, limit=12, faith=""):
    city = town.split(",")[0].strip() or town
    us = (country or "US").upper() == "US"
    rows = _year_items(today.year) + _year_items(today.year + 1)
    rows += [(dt.date.fromisoformat(d), t, c, w, s, a) for d, t, c, w, s, a in MOON_FESTIVALS]
    if faith:
        rows += faith_days(today.year, faith) + faith_days(today.year + 1, faith)
    out = []
    oe_start, oe_end = dt.date(today.year, 10, 15), dt.date(today.year, 12, 7)
    if us and oe_start < today <= oe_end:                     # a "happening now" note seniors care about
        out.append({"id": _id("medicare-now", today.year), "title": "Medicare open enrollment is happening now",
                    "date": today.isoformat(), "time": "", "place": "", "category": "health",
                    "why": "You can compare and change your Medicare plan until December 7. Call Medicare at 1-800-633-4227, or ask your local SHIP counselor for free help.",
                    "phone": "1-800-633-4227", "source": "calendar", "interest": ""})
    for d, title, cat, why, s, approx in rows:
        if not (today <= d <= today + dt.timedelta(days=days)) or (not us and title in US_ONLY):
            continue
        phone = "1-800-633-4227" if "Medicare" in title else ""
        out.append({"id": _id(title, d), "title": title, "date": d.isoformat(), "time": "", "place": "",
                    "category": "faith" if cat == "your-faith" else cat, "why": why + (" The exact date can differ by a day, so check locally." if approx else ""),
                    "phone": phone, "source": "calendar", "interest": "",
                    "your_faith": bool(faith) and (cat == "your-faith" or faith in FAITH_OF.get(title, set()))})
    out.sort(key=lambda e: e["date"])
    return out[:limit + (4 if faith else 0)]



# ------------------------------------------------------------------ 2. verified news search (Google News + Bing News RSS)
# Only stories from news publishers listed in Google News / Bing News are used. These kinds of sites are never used:
BLOCKED_DOMAINS = ("prnewswire", "globenewswire", "businesswire", "einpresswire", "openpr.com", "accessnewswire", "newswire.com",
                   "prweb", "issuewire", "facebook.", "instagram.", "twitter.", "x.com", "tiktok.", "reddit.", "youtube.",
                   "eventbrite.", "allevents.", "yelp.", "nextdoor.", "pinterest.", "quora.", "medium.com", "substack.com",
                   "blogspot.", "wordpress.com", "tumblr.", "linkedin.")
NEWS_SKIP = re.compile(r"\b(kill|shoot|shot|murder|crash|dies|died|death|dead|arrest|charged|police|stab|lawsuit|sued|"
                       r"obituar|assault|robber|missing|fatal|homicide|indict|sentenced|suspect|wreck|injur|scam|fraud)\w*", re.I)
# Specific words only: "exercise" alone matched a police "training exercise", "park" matched "parking", "plant" a power plant.
INTEREST_WORDS = {
    "Gardening": 'gardening OR garden OR gardens OR gardener OR "master gardener" OR horticulture OR "plant sale" OR "seed swap" OR arboretum',
    "Music": 'concert OR "live music" OR jazz OR symphony OR orchestra OR choir OR bluegrass OR opera OR musical OR "music festival" OR recital',
    "Faith and worship": 'church OR temple OR mosque OR synagogue OR gurdwara OR worship OR parish OR congregation OR kirtan OR langar OR puja OR shabbat OR interfaith',
    "Festivals and culture": 'festival OR "cultural celebration" OR heritage OR parade OR "street fair" OR "cultural center"',
    "Volunteering": "volunteer OR volunteers",
    "Cooking": 'cooking OR "cooking class" OR chef OR culinary OR recipe OR baking OR "farmers market"',
    "Reading and the library": 'library OR libraries OR "book club" OR "book sale" OR author OR authors OR poetry OR "story time" OR literacy',
    "Walking and nature": 'trail OR trails OR greenway OR hike OR hiking OR "nature walk" OR "nature center" OR "state park" OR "walking club" OR birding OR "bird walk"',
    "Arts and crafts": 'artist OR artists OR "art show" OR "art class" OR crafts OR craft OR gallery OR exhibit OR exhibition OR pottery OR quilt OR quilting OR painting OR watercolor OR drawing OR knitting OR crochet OR sewing OR ceramics',
    "Games and cards": 'bingo OR "game night" OR chess OR "card games" OR mahjong OR "board games" OR trivia OR pinochle OR canasta',
    "Exercise classes": '"exercise class" OR "fitness class" OR yoga OR "tai chi" OR "water aerobics" OR aerobics OR zumba OR pilates OR "chair yoga" OR SilverSneakers OR "line dancing"',
    "Learning new things": 'workshop OR lecture OR seminar OR "adult education" OR "lifelong learning" OR "free class" OR "computer class"',
    "Grandchildren and family": '"family friendly" OR "family-friendly" OR "for kids" OR "children\'s" OR storytime OR "story time" OR "family day"'}


# Related words for things people type in their own words, so "movies" also finds film, cinema and Hollywood stories.
RELATED = {
    "movie": 'movie OR movies OR film OR films OR cinema OR Hollywood OR screening OR matinee OR Oscars OR "film festival"',
    "film": 'movie OR movies OR film OR films OR cinema OR Hollywood OR screening OR matinee OR "film festival"',
    "theater": 'theater OR theatre OR play OR musical OR Broadway OR "community theater" OR playhouse',
    "history": 'history OR historical OR "historic site" OR heritage OR museum OR "history talk" OR genealogy OR reenactment',
    "genealogy": 'genealogy OR "family history" OR ancestry OR "family tree"',
    "football": 'football OR NFL OR "college football" OR kickoff',
    "baseball": 'baseball OR MLB OR "minor league" OR ballpark',
    "basketball": 'basketball OR NBA OR WNBA OR "college basketball"',
    "hockey": 'hockey OR NHL',
    "soccer": 'soccer OR MLS OR "football club"',
    "golf": 'golf OR golfing OR "golf course" OR PGA OR "golf tournament"',
    "tennis": 'tennis OR "tennis club"',
    "pickleball": 'pickleball',
    "sport": 'sports OR game OR tournament OR championship',
    "fishing": 'fishing OR angler OR "fishing pier" OR "fish fry"',
    "bird": 'birding OR "bird walk" OR "bird watching" OR birdwatching OR Audubon',
    "photography": 'photography OR photographer OR "photo exhibit" OR "camera club"',
    "dancing": 'dancing OR dance OR ballroom OR "line dancing" OR "square dance" OR "contra dance"',
    "dance": 'dancing OR dance OR ballroom OR "line dancing" OR "square dance"',
    "travel": 'travel OR trips OR tours OR "day trip" OR excursion',
    "car": '"car show" OR "classic cars" OR "cars and coffee" OR antique cars',
    "train": 'trains OR railroad OR railway OR "model train"',
    "jazz": 'jazz OR "jazz band" OR "big band" OR swing',
    "opera": 'opera OR "opera house" OR aria OR soprano',
    "choir": 'choir OR chorus OR chorale OR singing',
    "singing": 'choir OR chorus OR chorale OR singing OR "sing-along"',
    "gospel": 'gospel OR "gospel choir" OR hymns',
    "bridge": '"bridge club" OR "duplicate bridge" OR "bridge game"',
    "puzzle": 'puzzles OR jigsaw OR crossword OR sudoku',
    "knitting": 'knitting OR crochet OR yarn OR "knitting circle"',
    "quilting": 'quilting OR quilt OR quilters OR "quilt show"',
    "painting": 'painting OR painter OR watercolor OR "art class" OR "paint night"',
    "poetry": 'poetry OR poet OR poems OR "poetry reading"',
    "writing": 'writing OR writers OR "writing group" OR memoir OR author',
    "science": 'science OR "science talk" OR planetarium OR astronomy',
    "astronomy": 'astronomy OR stargazing OR planetarium OR telescope OR "star party"',
    "dog": 'dog OR dogs OR "dog park" OR puppy OR "pet adoption"',
    "cat": 'cat OR cats OR kitten OR "pet adoption"',
    "pet": 'pets OR dog OR cat OR "pet adoption" OR "animal shelter"',
    "animal": 'animals OR "animal shelter" OR zoo OR "pet adoption" OR wildlife',
    "computer": '"computer class" OR technology OR "tech help" OR smartphone OR iPad OR internet',
    "technology": '"computer class" OR technology OR "tech help" OR smartphone OR iPad',
    "wine": 'wine OR winery OR "wine tasting" OR vineyard',
    "coffee": 'coffee OR cafe OR "coffee shop"',
    "boating": 'boating OR boat OR sailing OR lake OR marina',
    "tea": 'tea OR "tea party" OR "afternoon tea"',
    "baking": 'baking OR bakery OR "bake sale" OR pastry',
    "comedy": 'comedy OR comedian OR "stand-up" OR improv',
    "trivia": 'trivia OR "quiz night"',
    "bingo": 'bingo',
    "chess": 'chess OR "chess club"',
}


def _bases(word):
    """'Movies' -> {'movies', 'movie'}, 'hobbies' -> {.., 'hobby'}, 'classes' -> {.., 'class'} (plain singular forms)."""
    w = word.lower().strip()
    out = {w}
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        out.add(w[:-1])
        if w.endswith("ies"): out.add(w[:-3] + "y")
        if re.search(r"(ss|sh|ch|x)es$", w): out.add(w[:-2])
    return out


def related_words(phrase):
    p = phrase.lower().strip()
    for w in [p] + re.findall(r"[a-z]+", p):               # 'old movies' -> movie, 'Carolina Panthers football' -> football
        for b in sorted(_bases(w), key=len):
            if b in RELATED:
                return (f'"{phrase}" OR ' if " " in phrase else "") + RELATED[b]
    return None


def interest_terms(interests):
    """[(label, search words)] for every interest, the person's own words included."""
    out, seen = [], set()
    for i in interests:
        i = str(i).strip()
        if not i or i.lower() in seen:
            continue
        seen.add(i.lower())
        words = INTEREST_WORDS.get(i) or related_words(i) or (f'"{i}"' if " " in i else i)
        out.append((i, words))
    return out[:MAX_INTERESTS]


def where_terms(town):
    city = town.split(",")[0].strip()
    rest = town.split(",")[1].strip() if "," in town else ""
    return city, f'"{city}" {rest}'.strip()


def _domain(url):
    return urllib.parse.urlparse(url or "").netloc.lower().removeprefix("www.")


def verified(item):
    """A story counts only if it names a publisher and comes from a news site (not a press release, social media or listing site)."""
    dom = item.get("domain", "")
    return bool(item.get("source")) and item["url"].startswith(("https://", "http://")) and dom and \
        not any(b in dom for b in BLOCKED_DOMAINS)


def _clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _xml(raw):
    try:
        return ET.fromstring(raw)
    except ET.ParseError:                                   # tolerate a stray "&" in a headline
        return ET.fromstring(re.sub(rb"&(?!#?\w+;)", b"&amp;", raw))


def parse_google_rss(raw):
    out = []
    for it in _xml(raw).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        source = (src.text or "").strip() if src is not None else ""
        site = src.get("url", "") if src is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        desc = _clean(it.findtext("description"))
        if desc.startswith(title):                                  # Google's description just repeats the title
            desc = ""
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "source": source, "domain": _domain(site),
                    "pub": it.findtext("pubDate") or "", "summary": desc, "via": "google"})
    return out


def parse_bing_rss(raw):
    out = []
    for it in _xml(raw).iter("item"):
        link = (it.findtext("link") or "").strip()
        real = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [link])[0]   # skip Bing's click-through page
        source = ""
        for child in it:
            if child.tag.lower().endswith("source"):
                source = (child.text or "").strip()
        out.append({"title": _clean(it.findtext("title")), "url": real, "source": source, "domain": _domain(real),
                    "pub": it.findtext("pubDate") or "", "summary": _clean(it.findtext("description")), "via": "bing"})
    return out


def _pubdate(s):
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except (TypeError, ValueError, IndexError):
        return None


def search_news(query, days):
    """Search both news indexes; returns (items, errors). Items are verified, de-duplicated and dated."""
    g = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    b = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": query, "format": "rss", "qft": "sortbydate=\"1\""})
    items, errors = [], []
    for name, url, parser in (("Google News", g, parse_google_rss), ("Bing News", b, parse_bing_rss)):
        try:
            items += parser(_get(url, timeout=15))
        except Exception as e:
            errors.append(f"{name}: {friendly_error(e)}")
    out = []
    for it in items:
        it["pub"] = _pubdate(it["pub"])
        if it["pub"] and it["title"] and verified(it):
            out.append(it)
    return out, errors


def merge(items):
    """Same story from both indexes -> one item; prefer the direct article link; count how many outlets reported it."""
    by = {}
    for it in items:
        k = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:70]
        if k not in by:
            by[k] = dict(it, outlets={it["source"]}, geo=bool(it.get("geo")))
        else:
            cur = by[k]
            cur["outlets"].add(it["source"])
            cur["geo"] = cur["geo"] or bool(it.get("geo"))
            if cur["via"] == "google" and it["via"] == "bing":           # direct link is better than a redirect link
                cur.update(url=it["url"], summary=it["summary"] or cur["summary"], domain=it["domain"], via="bing")
            cur["interests"] = cur.get("interests", [])
    return list(by.values())


# ------------------------------------------------------------------ dates: find WHEN an event happens in a story
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MD_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!:)(?:\s*(?:-|–|to|through)\s*\d{1,2})?(?:,?\s*(20\d\d))?", re.I)
NUM_RE = re.compile(r"\b(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])(?:/(20\d\d|\d\d))?\b")
REL_RE = re.compile(r"\b(this|next|on|coming)\s+(weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\b(tomorrow|tonight)\b", re.I)
TIME_RE = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s?m\b\.?", re.I)


def find_dates(text, pub):
    """All dates mentioned in a story, interpreted relative to when it was published."""
    found = []
    for m in MD_RE.finditer(text):
        mon, day = MONTHS[m.group(1)[:3].lower()], int(m.group(2))
        year = int(m.group(3)) if m.group(3) else pub.year
        try:
            d = dt.date(year, mon, day)
        except ValueError:
            continue
        if not m.group(3) and d < pub - dt.timedelta(days=60):   # "Jan. 5" in a December story means next year
            d = d.replace(year=d.year + 1)
        found.append(d)
    for m in NUM_RE.finditer(text):
        y = m.group(3)
        year = (2000 + int(y) if y and len(y) == 2 else int(y)) if y else pub.year
        try:
            found.append(dt.date(year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    for m in (REL_RE.finditer(text) if not found else []):        # "on Saturday, Nov. 7" -> trust the written date
        word = (m.group(2) or m.group(3)).lower()
        if word == "tomorrow":
            found.append(pub + dt.timedelta(days=1))
        elif word == "tonight":
            found.append(pub)
        else:
            target = 5 if word == "weekend" else WEEKDAYS.index(word)
            ahead = (target - pub.weekday()) % 7 or 7
            if m.group(1).lower() == "next" and ahead < 7:
                ahead += 7 if word == "weekend" else 0
            found.append(pub + dt.timedelta(days=ahead))
    return found


RANGE_RE = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap]\.?\s?m\.?)?\s*(?:-|–|to|until)\s*(1[0-2]|0?[1-9])(?::[0-5]\d)?\s*([ap])\.?\s?m\b", re.I)


def find_time(text):
    r = RANGE_RE.search(text)
    if r:                                                    # "4 to 10 p.m." starts at 4 p.m.; "10 a.m. to 2 p.m." at 10 a.m.
        h, end = int(r.group(1)), int(r.group(4))
        mer = (r.group(3) or "")[:1].lower() or r.group(5).lower()
        if not r.group(3) and r.group(5).lower() == "p" and h > end:
            mer = "a"
        h = h % 12 + (12 if mer == "p" else 0)
        return f"{h:02d}:{int(r.group(2) or 0):02d}"
    m = TIME_RE.search(text)
    if not m:
        return ""
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return f"{h:02d}:{int(m.group(2) or 0):02d}"


def future_date(text, pub, today, horizon=120):
    ds = sorted(d for d in find_dates(text, pub) if today <= d <= today + dt.timedelta(days=horizon))
    return ds[0] if ds else None


def has_past_date(text, pub, today):
    return any(d < today for d in find_dates(text, pub) if d >= pub - dt.timedelta(days=30))


# ------------------------------------------------------------------ the person's area: town + county + nearby towns
# Local news rarely says just "Cary": it says "Raleigh", "Apex" or "Wake County". So searches cover the whole area.
def town_parts(town):
    city = town.split(",")[0].strip()
    region = town.split(",")[1].strip() if "," in town else ""
    return city, core.US_STATES.get(region.upper(), region).title() if region else ""


# ------------------------------------------------------------------ one map download per town (OpenStreetMap)
# Everything map-based (nearby towns, places to go, places that welcome volunteers, libraries' websites, places of worship)
# comes from ONE request, saved for two weeks. The public map servers only allow a couple of requests at a time,
# so asking five separate times was what made things slow and fail.
MAP_DAYS = 14
_map_lock = threading.Lock()
KEEP_TAGS = ("name", "alt_name", "official_name", "amenity", "leisure", "shop", "tourism", "social_facility", "social_facility:for",
             "community_centre", "religion", "denomination", "website", "contact:website", "url", "phone", "contact:phone",
             "addr:housenumber", "addr:street", "addr:city", "opening_hours", "garden:type", "place")


def map_query(lat, lon):
    return (f'[out:json][timeout:50];('
            f'node["place"~"^(city|town)$"]["name"](around:25000,{lat},{lon});'
            f'nwr["amenity"~"^(library|community_centre|social_facility|place_of_worship|arts_centre|theatre|hospital|animal_shelter|food_bank)$"]["name"](around:20000,{lat},{lon});'
            f'nwr["social_facility"~"food_bank|soup_kitchen"](around:20000,{lat},{lon});'
            f'nwr["shop"="charity"]["name"](around:10000,{lat},{lon});'
            f'nwr["tourism"="museum"]["name"](around:20000,{lat},{lon});'
            f'nwr["leisure"~"^(garden|park|sports_centre|fitness_centre|nature_reserve)$"]["name"](around:8000,{lat},{lon});'
            f');out center tags;')


_map_failed = [0.0]


def map_snapshot(lat, lon, wait=True):
    """All the map places near home, as a list of {lat, lon, tags}. Downloaded once, then reused for two weeks."""
    if lat is None:
        return []
    key = f"{round(float(lat), 3)},{round(float(lon), 3)}"
    c = bc_get("map") or {}
    if c.get("key") == key and time.time() - c.get("t", 0) < MAP_DAYS * 86400 and c.get("els"):
        return c["els"]
    if time.time() - _map_failed[0] < 600:                       # the map servers were down a moment ago; don't keep asking
        return None if not wait else (c.get("els") or [])
    if not wait:                                                 # don't hold anything up: download it in the background
        if not _map_lock.locked():
            prefetch_map(lat, lon)
        return None
    with _map_lock:                                              # only one download at a time; others wait for it
        c = bc_get("map") or {}
        if c.get("key") == key and time.time() - c.get("t", 0) < MAP_DAYS * 86400 and c.get("els"):
            return c["els"]
        if time.time() - _map_failed[0] < 600:                   # it just failed while we waited: don't try it all again
            return c.get("els") or []
        body = urllib.parse.urlencode({"data": map_query(lat, lon)}).encode()
        last = None
        for url in OVERPASS:
            try:
                started = time.time()
                js = json.loads(_get(url, data=body, timeout=30, long=True))
                els = []
                for el in js.get("elements", []):
                    plat = el.get("lat") or el.get("center", {}).get("lat")
                    plon = el.get("lon") or el.get("center", {}).get("lon")
                    if plat is None:
                        continue
                    t = {k: v for k, v in (el.get("tags") or {}).items() if k in KEEP_TAGS or k.startswith("addr:")}
                    els.append({"lat": plat, "lon": plon, "tags": t})
                print(f"  map: {len(els)} places downloaded in {time.time() - started:.0f} seconds")
                bc_put("map", {"key": key, "t": time.time(), "els": els})
                return els
            except Exception as e:
                last = e
                print(f"  map: {url.split('/')[2]} didn't answer ({friendly_error(e)}), trying another")
        print("  map: couldn't download the map right now:", friendly_error(last), "- using the quick search instead for now.")
        _map_failed[0] = time.time()
        if c.get("els"):
            return c["els"]                                      # an older copy is better than nothing
        return []


def prefetch_map(lat, lon):
    threading.Thread(target=map_snapshot, args=(lat, lon), daemon=True, name="map").start()


def lookup_area(prof):
    city, region = town_parts(prof.get("town", ""))
    lat, lon = prof.get("lat"), prof.get("lon")
    county, nearby, ok = prof.get("county", ""), [], True
    if not county:
        try:
            res = core.geocode(prof["town"])
            if res:
                best = min(res, key=lambda r: (r["lat"] - (lat or r["lat"])) ** 2 + (r["lon"] - (lon or r["lon"])) ** 2)
                county = best.get("county", "")
        except Exception as e:
            ok = False
            print("  area: county lookup failed:", friendly_error(e))
    if lat is not None:
        els = map_snapshot(lat, lon, wait=False) or []          # not ready yet? use town + county for now
        towns = sorted(((miles(lat, lon, e["lat"], e["lon"]), e["tags"]["name"]) for e in els
                        if e["tags"].get("place") in ("city", "town") and e["tags"].get("name")), key=lambda x: x[0])
        nearby = list(dict.fromkeys(n for _, n in towns if n.lower() != city.lower()))[:8]
        if not els:
            ok = False
    ambiguous = False
    try:                                                   # is there another town with this name in another state?
        same = [r for r in core.geocode(city) if r["name"].lower() == city.lower() and r["country"] == "US"
                and r["region"].lower() != region.lower()]
        ambiguous = bool(same)
    except Exception:
        ambiguous = True                                   # be careful if we can't tell
    return {"city": city, "region": region, "county": county, "nearby": nearby, "ok": ok, "ambiguous": ambiguous}


def area(data, prof):
    cache = bc_get("area") or {}
    max_age = AREA_DAYS * 86400 if cache.get("ok") else 3600            # retry soon if the lookup failed
    if cache.get("town") != prof.get("town") or time.time() - cache.get("t", 0) > max_age or cache.get("v") != 2:
        cache = dict(lookup_area(prof), town=prof.get("town"), t=time.time(), v=2)
        bc_put("area", cache)
    names = [cache["city"]] + ([cache["county"]] if cache.get("county") else []) + cache.get("nearby", [])[:6]
    names = [n for n in dict.fromkeys(names) if n]
    where = "(" + " OR ".join(f'"{n}"' for n in names) + ")"
    region = cache.get("region", "")
    if cache.get("ambiguous") and region:                  # "Springfield" alone means dozens of towns: add the state
        ap = AP_STATE.get(region.lower())
        where += f' ("{region}"' + (f' OR "{ap}"' if ap else "") + ")"
    return {**cache, "names": names, "where": where}


AP_STATE = {"alabama": "Ala.", "arizona": "Ariz.", "arkansas": "Ark.", "california": "Calif.", "colorado": "Colo.", "connecticut": "Conn.",
            "delaware": "Del.", "florida": "Fla.", "georgia": "Ga.", "illinois": "Ill.", "indiana": "Ind.", "kansas": "Kan.", "kentucky": "Ky.",
            "louisiana": "La.", "maryland": "Md.", "massachusetts": "Mass.", "michigan": "Mich.", "minnesota": "Minn.", "mississippi": "Miss.",
            "missouri": "Mo.", "montana": "Mont.", "nebraska": "Neb.", "nevada": "Nev.", "new hampshire": "N.H.", "new jersey": "N.J.",
            "new mexico": "N.M.", "new york": "N.Y.", "north carolina": "N.C.", "north dakota": "N.D.", "oklahoma": "Okla.", "oregon": "Ore.",
            "pennsylvania": "Pa.", "rhode island": "R.I.", "south carolina": "S.C.", "south dakota": "S.D.", "tennessee": "Tenn.", "vermont": "Vt.",
            "virginia": "Va.", "washington": "Wash.", "west virginia": "W.Va.", "wisconsin": "Wis.", "wyoming": "Wyo."}
POSTAL = {v: k for k, v in core.US_STATES.items()}          # "north carolina" -> "NC"


def _state_res(region):
    """Regexes that recognise a state written as 'Massachusetts', 'Mass.' or ', MA'."""
    full = region.lower()
    words = [re.escape(region)] + ([re.escape(AP_STATE[full])] if full in AP_STATE else [])
    named = re.compile(r"\b(?:" + "|".join(words) + r")(?![a-z])", re.I)
    postal = re.compile(r",\s*" + POSTAL[full] + r"\b") if full in POSTAL else None
    return named, postal


def other_state_re(city, region):
    """'Springfield, Mo.' / 'Springfield, Missouri' / 'Springfield, OR' when the person lives in Springfield, Massachusetts."""
    own = region.lower()
    names = [re.escape(n.title()) for n in POSTAL if n != own] + [re.escape(a) for n, a in AP_STATE.items() if n != own]
    postals = [p for n, p in POSTAL.items() if n != own]
    return (re.compile(r"\b" + re.escape(city) + r",?\s+(?:" + "|".join(names) + r")(?![a-z])", re.I),
            re.compile(r"\b" + re.escape(city) + r",\s*(?:" + "|".join(postals) + r")\b"))


def is_local(it, ar):
    """Is this story really about the person's area? Same-named towns elsewhere (Springfield!) are ruled out."""
    text = f"{it['title']} {it.get('summary', '')}"
    full = f"{text} {it.get('source', '')}"
    names = ar["names"]
    city, region = ar["city"], ar.get("region", "")
    if region and other_place(it, ar):
        return False                                       # it's about the other Springfield
    if it.get("geo"):
        return True                                        # from Google's local news for this exact place
    if not any(re.search(r"\b" + re.escape(n) + r"\b", full, re.I) for n in names):
        return False
    if not ar.get("ambiguous"):
        return True
    named, postal = _state_res(region) if region else (None, None)
    ap = AP_STATE.get(region.lower(), "").rstrip(".")
    in_source = re.search(r"\b(?:" + re.escape(region) + (("|" + re.escape(ap)) if len(ap) >= 4 else "") + r")", it.get("source", ""), re.I)
    evidence = ((named and named.search(full)) or (postal and postal.search(text)) or in_source
                or (ar.get("county") and re.search(r"\b" + re.escape(ar["county"]) + r"\b", text, re.I))
                or any(city.lower() not in n.lower() and re.search(r"\b" + re.escape(n) + r"\b", text, re.I) for n in ar.get("nearby", [])[:6]))
    return bool(evidence)


def other_place(it, ar):
    """Does the story name the person's town but put it in another state ('Springfield, Mo.')?"""
    if not ar.get("region"):
        return False
    text = f"{it['title']} {it.get('summary', '')}"
    a, b = other_state_re(ar["city"], ar["region"])
    return bool(a.search(text) or b.search(text))


def mentions(words, text):
    """Does the story mention any of these words or phrases? ('yoga OR "tai chi"'); allows plain endings like -s or -ing."""
    toks = [(a or b).strip("()") for a, b in re.findall(r'"([^"]+)"|(\S+)', words) if (a or b) not in ("OR", "AND")]
    pats = []
    for t in toks:
        if not t:
            continue
        forms = {t}
        if " " not in t:
            forms |= _bases(t)                             # 'movies' also matches 'movie'
        pats.append("|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True)))
    return any(re.search(r"\b(?:" + p + r")(?:s|es|ed|ing|er|ers)?\b", text, re.I) for p in pats)


def geo_news(ar):
    """Google News' local section for the exact place ('Springfield, Massachusetts'), so same-named towns don't mix in."""
    url = "https://news.google.com/rss/headlines/section/geo/" + urllib.parse.quote(f"{ar['city']}, {ar['region']}") + "?hl=en-US&gl=US&ceid=US:en"
    try:
        out = []
        for it in parse_google_rss(_get(url, timeout=15)):
            it["pub"], it["via"], it["geo"] = _pubdate(it["pub"]), "geo", True
            if it["pub"] and it["title"] and verified(it):
                out.append(it)
        return out
    except Exception as e:
        print("  local news feed:", friendly_error(e))
        return []


def chunks(terms, n=4):
    for i in range(0, len(terms), n):
        yield " OR ".join(w for _, w in terms[i:i + n])


# ------------------------------------------------------------------ run many searches at once
def run_searches(queries, days, terms, ar=None):
    """queries: [query] -> merged verified stories tagged with the interests they really mention, plus errors."""
    items, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        geo = pool.submit(geo_news, ar) if ar and ar.get("region") else None
        for found, errs in pool.map(lambda q: search_news(q, days), queries):
            items += found
            errors += errs
        if geo:
            items = geo.result() + items
    today = dt.date.today()
    items = [i for i in items if i["pub"] >= today - dt.timedelta(days=days)]
    merged = merge(items)
    for m in merged:
        text = f"{m['title']} {m['summary']}"
        m["interests"] = [label for label, words in terms if mentions(words, text)]
        m["outlets"] = sorted(m["outlets"])
    total_fail = len(errors) >= 2 * len(queries)
    return merged, (sorted(set(errors)) if total_fail or not merged else [])


def _story(it, **extra):
    return {"id": _id(it["title"]), "title": it["title"], "url": it["url"], "source": it["source"],
            "outlets": it.get("outlets", [it["source"]]), "summary": it.get("summary", "")[:280],
            "published": it["pub"].isoformat(), "interests": it.get("interests", []), **extra}


# ------------------------------------------------------------------ sports: scores, games and team news (ESPN's free feed)
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = [("football", "nfl", "NFL"), ("basketball", "nba", "NBA"), ("baseball", "mlb", "MLB"), ("hockey", "nhl", "NHL"),
           ("basketball", "wnba", "WNBA"), ("soccer", "usa.1", "MLS"), ("football", "college-football", "College football"),
           ("basketball", "mens-college-basketball", "Men's college basketball"),
           ("basketball", "womens-college-basketball", "Women's college basketball")]
SPORT_WORDS = {"football": {"nfl", "college-football"}, "basketball": {"nba", "wnba", "mens-college-basketball", "womens-college-basketball"},
               "baseball": {"mlb"}, "hockey": {"nhl"}, "soccer": {"usa.1"}}
FILLER = {"the", "go", "fan", "fans", "team", "teams", "games", "game", "college", "mens", "womens", "men", "women", "watching", "sports", "of"}


def _norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", str(s).lower().replace("&", " and ").replace("'", ""))
    return re.sub(r"\s+", " ", s).strip()


def team_index(data):
    cache = bc_get("teams") or {}
    if cache.get("teams") and time.time() - cache.get("t", 0) < 14 * 86400:
        return cache["teams"]
    teams = []

    def league(L):
        s, l, label = L
        js = json.loads(_get(f"{ESPN}/{s}/{l}/teams?limit=1000", timeout=20))
        out = []
        for lg in js.get("sports", [{}])[0].get("leagues", []):
            for t in lg.get("teams", []):
                t = t.get("team", t)
                out.append({"s": s, "l": l, "league": label, "id": t.get("id"), "name": t.get("displayName", ""),
                            "nick": t.get("name", ""), "loc": t.get("location", ""), "abbr": t.get("abbreviation", "")})
        return out
    def safe(L):
        try:
            return league(L)
        except Exception as e:
            print(f"  sports: {L[2]} team list failed:", friendly_error(e))
            return []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for res in pool.map(safe, LEAGUES):
            teams += res
    if teams:
        bc_put("teams", {"t": time.time(), "teams": teams})
    return teams


def match_teams(interests, teams):
    """'Carolina Panthers', 'the Panthers', 'Duke basketball', 'UNC' -> ESPN teams (only clear matches)."""
    found = []
    for i in interests:
        words = _norm(i).split()
        hint = set().union(*[SPORT_WORDS[w] for w in words if w in SPORT_WORDS]) if any(w in SPORT_WORDS for w in words) else None
        core_ = " ".join(w for w in words if w not in SPORT_WORDS and w not in FILLER)
        if len(core_) < 3:
            continue
        pool = [t for t in teams if not hint or t["l"] in hint]
        hits = [t for t in pool if core_ in (_norm(t["name"]), _norm(f"{t['loc']} {t['nick']}"))]
        if not hits:                                       # nickname alone, only if it's unique among pro teams
            nick = [t for t in pool if _norm(t["nick"]) == core_ and "college" not in t["l"]]
            hits = nick if len(nick) == 1 else []
        if not hits:                                       # a college by its name or short name ("Duke", "UNC")
            hits = [t for t in pool if "college" in t["l"] and core_ in (_norm(t["loc"]), _norm(t["abbr"]))]
        pro = [t for t in hits if "college" not in t["l"]]
        for t in (pro or hits)[:3]:
            found.append((i, t))
    return found


def _local_dt(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return None


def _score(c):
    s = c.get("score")
    if isinstance(s, dict):
        s = s.get("displayValue", s.get("value"))
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def team_report(interest, t, today):
    js = json.loads(_get(f"{ESPN}/{t['s']}/{t['l']}/teams/{t['id']}/schedule", timeout=20))
    last, games = None, []
    now = dt.datetime.now().astimezone()
    for ev in js.get("events", []):
        when = _local_dt(ev.get("date", ""))
        comp = (ev.get("competitions") or [{}])[0]
        comps = comp.get("competitors", [])
        us = next((c for c in comps if str(c.get("team", {}).get("id")) == str(t["id"])), None)
        them = next((c for c in comps if c is not us), None)
        if not when or not us or not them:
            continue
        opp = them.get("team", {}).get("displayName", "their opponent")
        status = comp.get("status", {}).get("type", {})
        if status.get("completed"):
            a, b = _score(us), _score(them)
            if a is not None and b is not None:
                res = "Won" if a > b else "Lost" if a < b else "Tied"
                last = {"date": when.date().isoformat(), "text": f"{res} {a}–{b} {'against' if us.get('homeAway') == 'home' else 'at'} {opp}"}
        elif when >= now - dt.timedelta(hours=4) and when.date() <= today + dt.timedelta(days=45):
            tv = ", ".join(b.get("media", {}).get("shortName", "") for b in comp.get("broadcasts", []) if b.get("media")) or ""
            tk = ((comp.get("tickets") or [{}])[0].get("links") or [{}])[0].get("href", "") if comp.get("tickets") else ""
            games.append({"id": _id(t["id"], ev.get("id")), "title": f"{t['name']} {'vs.' if us.get('homeAway') == 'home' else 'at'} {opp}", "tickets": tk,
                          "date": when.date().isoformat(), "time": when.strftime("%H:%M") if ev.get("timeValid", True) else "",
                          "place": comp.get("venue", {}).get("fullName", ""), "tv": tv, "home": us.get("homeAway") == "home"})
    articles = []
    try:
        nj = json.loads(_get(f"{ESPN}/{t['s']}/{t['l']}/news?team={t['id']}&limit=5", timeout=15))
        for a in nj.get("articles", []):
            url = a.get("links", {}).get("web", {}).get("href", "")
            pub = _local_dt(a.get("published", ""))
            if url and a.get("headline"):
                articles.append({"id": _id(a["headline"]), "title": a["headline"], "url": url, "source": "ESPN", "outlets": ["ESPN"],
                                 "summary": a.get("description", "")[:280], "published": pub.date().isoformat() if pub else "",
                                 "interests": [interest]})
    except Exception as e:
        print("  sports: news failed:", friendly_error(e))
    return {"interest": interest, "team": t["name"], "league": t["league"], "last": last,
            "next": games[0] if games else None, "games": games[:6], "articles": articles[:5]}


def sports_for(data, interests, today):
    maybe = [i for i in interests if i not in INTEREST_WORDS]          # the ready-made interests are never teams
    teams = team_index(data) if maybe else []
    interests = maybe
    out = []
    for interest, t in match_teams(interests, teams)[:6]:
        try:
            out.append(team_report(interest, t, today))
        except Exception as e:
            print(f"  sports: {t['name']} failed:", friendly_error(e))
    return out


# ------------------------------------------------------------------ official calendars (town, county, libraries, senior center, worship)
TRADITION_WORDS = {"christian": "church OR parish OR congregation", "catholic": "Catholic OR parish OR church",
                   "jewish": "synagogue OR Jewish OR temple OR Chabad", "muslim": "mosque OR Islamic OR masjid",
                   "hindu": "Hindu OR temple OR mandir", "sikh": "Sikh OR gurdwara", "buddhist": "Buddhist OR meditation OR temple",
                   "jain": "Jain OR derasar", "orthodox": "Orthodox OR church OR parish"}
MEETING = re.compile(r"\b(council|commission|board|committee|hearing|town hall|public meeting|work session|advisory)\b", re.I)
CANCELLED = re.compile(r"\b(cancel+ed|postponed)\b", re.I)


def official_sources(data, prof, ar):
    """Websites with calendars for this person: their town, county, nearest libraries and senior center, and place of worship."""
    worship = ((prof.get("faith") or {}).get("place") or {})
    key = json.dumps([prof.get("town"), worship.get("website", ""), "v2"])
    cache = bc_get("calendars") or {}
    fresh_days = 30 if cache.get("sites") else 7
    if cache.get("key") == key and time.time() - cache.get("t", 0) < fresh_days * 86400:
        return cache["sites"]
    sites = []

    def add(name, url, kind):
        if url and url.startswith("http") and all(x["url"].rstrip("/") != url.rstrip("/") for x in sites):
            sites.append({"name": name, "url": url, "kind": kind})
    for nm, kind in [(ar["city"], "town"), (ar.get("county", ""), "county")]:
        if not nm:
            continue
        try:
            got = src.wikidata_site(nm, ar["region"])
            if got:
                add(f"{got[0]} (official website)" if kind == "town" else f"{got[0]} (official website)", got[1], kind)
        except Exception as e:
            print(f"  calendars: couldn't look up the {kind} website:", friendly_error(e))
    lat, lon = prof.get("lat"), prof.get("lon")
    if lat is not None:
        cands = []
        for el in map_snapshot(lat, lon, wait=False) or []:    # never wait for the map; it's used next time if it's still coming
            t = el["tags"]
            web = t.get("website") or t.get("contact:website") or ""
            if not web or not t.get("name"):
                continue
            if t.get("amenity") == "library":
                kind = "library"
            elif t.get("amenity") in ("community_centre", "social_facility") and re.search(r"senior|active adult|aging", t["name"], re.I):
                kind = "senior"
            elif t.get("amenity") == "community_centre":
                kind = "community"
            elif t.get("amenity") == "arts_centre" or t.get("tourism") == "museum":
                kind = "arts"
            else:
                continue
            cands.append((kind, miles(lat, lon, el["lat"], el["lon"]), t["name"], web))
        for kind, n in (("library", 2), ("senior", 2), ("community", 1), ("arts", 1)):
            for _, _, name, web in sorted([c for c in cands if c[0] == kind], key=lambda c: c[1])[:n]:
                add(name, web if web.startswith("http") else "https://" + web, kind)
    if worship.get("website"):
        add(worship.get("name", "Your place of worship"), worship["website"], "worship")
    sites = sites[:8]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for site, feeds in zip(sites, pool.map(lambda x: src.discover(x["url"]), sites)):
            site["feeds"] = feeds
            print(f"  calendars: {site['name']}: {len(feeds)} calendar feed(s) found")
    bc_put("calendars", {"key": key, "t": time.time(), "sites": sites})
    return sites


def eventbrite(prof, ar, terms, today):
    """Organizer-posted local events, kept only if they're in person, nearby, soon, and not sales pitches or nightlife."""
    st = POSTAL.get(ar["region"].lower(), "")
    kws = [l for l, _ in terms if l not in PRESET][:2] + [{"Exercise classes": "fitness", "Arts and crafts": "art", "Music": "concert",
                                                          "Gardening": "gardening", "Cooking": "cooking", "Reading and the library": "books",
                                                          "Learning new things": "workshop", "Games and cards": "games",
                                                          "Walking and nature": "outdoors", "Faith and worship": "spirituality"}.get(l, "")
                                                         for l, _ in terms if l in PRESET][:2]
    raw = src.eventbrite_events(ar["city"], st, [k for k in kws if k], today)
    out, lat, lon = [], prof.get("lat"), prof.get("lon")
    names = [n.lower() for n in ar["names"]]
    for e in raw:
        if e.get("online") or src.SPAM_EVENT.search(f"{e['title']} {e['summary']}") or e["date"] > (today + dt.timedelta(days=45)).isoformat():
            continue
        if e.get("lat") is not None and lat is not None:
            if miles(lat, lon, e["lat"], e["lon"]) > 30:
                continue
        elif not (e.get("region", "").upper() in (st, ar["region"].upper()) or e.get("locality", "").lower() in names):
            continue
        text = f"{e['title']} {e['summary']}"
        out.append({**e, "id": _id("eb", e["url"]), "source": "Eventbrite", "outlets": ["Eventbrite"], "published": "", "kind": "event",
                    "official": False, "listed": True, "tier": "near", "meeting": False,
                    "interests": [label for label, words in terms if mentions(words, text)]})
    return out[:30]


def official_events(data, prof, ar, terms, today):
    sites = [x for x in official_sources(data, prof, ar) if x.get("feeds")]
    jobs = [(site, kind, url) for site in sites for kind, url in site["feeds"][:3]]

    def run(job):
        site, kind, url = job
        try:
            return site, src.calendar_events(kind, url, today, find_dates)
        except Exception as e:
            print(f"  calendars: {site['name']} feed failed:", friendly_error(e))
            return site, []
    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as pool:
        for site, evs in pool.map(run, jobs):
            for e in evs[:30]:
                if not e["title"] or CANCELLED.search(e["title"]):
                    continue
                k = (e["date"], re.sub(r"[^a-z]", "", e["title"].lower())[:30])
                if k in seen:
                    continue
                seen.add(k)
                text = f"{e['title']} {e['summary']}"
                out.append({**e, "id": _id(site["url"], e["date"], e["title"]), "source": site["name"], "outlets": [site["name"]],
                            "published": "", "kind": "event", "official": True, "site_kind": site["kind"],
                            "tier": "worship" if site["kind"] == "worship" else "near",
                            "meeting": bool(MEETING.search(e["title"])), "url": e["url"] or site["url"],
                            "interests": [label for label, words in terms if mentions(words, text)]})
    return out


# ------------------------------------------------------------------ sections
EVENT_WORDS = "event OR festival OR celebration OR concert OR class OR workshop OR fair OR market OR show OR parade OR gathering"
EVENTY = re.compile(r"\b(festival|celebrat|concert|class|workshop|fair|market|show|parade|event|gathering|exhibit|tour|lecture|"
                    r"performance|screening|walk|meeting|bingo|dance|service|lighting|lights|sale|open house|fundraiser|club|tournament|game|"
                    r"holiday|bazaar|carnival|expo|recital|storytime|tasting|potluck|picnic|luncheon|breakfast|supper|social|meetup|"
                    r"vigil|ceremony|reunion|clinic|screening|trivia|yoga|tai chi)\w*", re.I)
GENERIC = re.compile(r"how to watch|where to watch|watch live|tv schedule|sports schedule|complete schedule|live stream|livestream|"
                     r"\bodds\b|betting|\bpicks\b|predictions|stocks?\b|horoscope|best things to do in america|"
                     r"weekend box|deals\b|coupon|\bsale ends|giveaway", re.I)
ANNOUNCE = re.compile(r"\b(will|to host|hosts?|hosting|returns?|set for|scheduled|coming|upcoming|join|celebrat\w*|annual|free|"
                      r"kicks? off|opens?|presents?|invites?|this (weekend|week|saturday|sunday|friday)|tickets?|register|sign up|"
                      r"planned|happening|don't miss|things to do)\b", re.I)
REPORT = re.compile(r"\b(raised|drew|attended|was held|took place|wrapped up|recap|photos:|gallery|approved|voted|budget|lawsuit|"
                    r"election results|arrest|died|charged|sentenced|closing for good|closed)\b", re.I)
VOLUNTEER_CALL = re.compile(r"volunteers? (needed|wanted|sought)|seek(s|ing) volunteers|looking for volunteers|need(s)? volunteers|"
                            r"call for volunteers|sign up to (help|volunteer)|volunteer opportunit|ways to (help|volunteer)|"
                            r"recruit\w* volunteers|become a volunteer|volunteer (day|event|drive|shift)|help needed|"
                            r"volunteers? can|want to volunteer|interested in volunteering", re.I)


def title_interests(it, terms):
    return [label for label, words in terms if mentions(words, it["title"])]


# ------------------------------------------------------------------ is this a good fit for an older adult?
FIT_GOOD = re.compile(r"\b(seniors?|older adults?|55\s*\+|50\s*\+|60\s*\+|retirees?|active adults?|aarp|medicare|caregiv\w*|"
                      r"library|libraries|museum|garden\w*|concert|symphony|orchestra|choir|chorus|recital|opera|jazz|bluegrass|"
                      r"lecture|talk|author|book (club|sale|talk|signing)|poetry|history|historical|heritage|cultural|"
                      r"crafts?|knit\w*|crochet|quilt\w*|watercolor|painting|pottery|art (class|show|exhibit)|exhibit\w*|"
                      r"tai chi|chair yoga|gentle|low[- ]impact|water aerobics|walking (club|group)|nature walk|bird\w*|"
                      r"worship|church|temple|mosque|synagogue|gurdwara|prayer|kirtan|langar|"
                      r"luncheon|potluck|tea|coffee hour|community (dinner|meal|lunch|breakfast)|bingo|bridge club|mahjong|canasta|trivia|"
                      r"film|movie|matinee|theater|theatre|musical|farmers market|craft fair|bazaar|tree lighting|parade|festival|"
                      r"health fair|flu (shot|clinic)|blood pressure|wellness|support group|veterans?|town hall|public meeting|"
                      r"open house|tour|memory caf[eé]|holiday lights|light show|lights display|bright nights|christmas lights|"
                      r"holiday market|carol\w*|craft show|flower show|garden tour|botanical|planetarium|aquarium|zoo)\b", re.I)
FIT_BAD = re.compile(r"\b(nightclub|club night|dj set|rave|edm|after ?party|dance party|bar crawl|pub crawl|21\s*\+|18\s*\+|"
                     r"beer (fest|festival|crawl)|hookah|cannabis|burlesque|"
                     r"5k|10k|half marathon|marathon|triathlon|obstacle|mud run|crossfit|hiit|boot ?camp|paintball|"
                     r"job fair|career fair|hiring event|recruit\w*|speed dating|singles|networking|haunted (house|trail|hayride)|"
                     r"zombie|escape room|poker|casino night|esports|gaming tournament)\b", re.I)
FIT_KIDS = re.compile(r"\b(toddlers?|preschool\w*|babies|baby|infants?|tots|kids? (only|club|camp)|ages? \d{1,2}\s*(-|to)\s*1[0-7]|"
                      r"teens?|tweens?|youth|homeschool\w*|middle school|high school|college fair|sat prep|prom|story ?time|"
                      r"lego|baby ?sign)\b", re.I)
SITE_BONUS = {"senior": 3, "worship": 3, "library": 1, "arts": 1, "community": 1, "town": 1, "county": 1}


def senior_fit(e, interests):
    """Higher = better suited. Senior centers, libraries, culture, faith, gentle activity and their own interests count up;
    nightlife, races, job fairs, late nights and (unless they enjoy family events) kids-only events count down."""
    text = f"{e['title']} {e.get('summary', '')}"
    good = {m.group(0).lower() for m in FIT_GOOD.finditer(f"{text} {e.get('place', '')}")}
    bad = {m.group(0).lower() for m in FIT_BAD.finditer(text)}
    kids = FIT_KIDS.search(text) and not re.search(r"\b(adults?|all ages|grandparents?|seniors?)\b", text, re.I)
    score = min(len(good), 3) + 2 * min(len(e.get("interests", [])), 2) + SITE_BONUS.get(e.get("site_kind", ""), 0) - 3 * len(bad)
    if kids:
        score += 1 if "Grandchildren and family" in interests else -4
    if e.get("kind") == "game":
        score += 3                                               # their own team
    if e.get("meeting"):
        score += 1
    if (e.get("time") or "") >= "21:00":
        score -= 2
    return score


def finish_events(events, prof, terms, today, country, ar):
    """De-duplicate, score how well each suits an older adult, and sort by date."""
    interests = [l for l, _ in terms]
    seen, out = set(), []
    for e in sorted(events, key=lambda e: (e["date"], not e.get("official"), -len(e["outlets"]))):
        k = (e["date"], re.sub(r"[^a-z]", "", e["title"].lower())[:30])
        if k in seen:
            continue
        seen.add(k)
        e["fit"] = senior_fit(e, interests)
        if e["fit"] >= -1:                                       # clearly unsuitable things are left out entirely
            e["good_fit"] = e["fit"] >= 1
            out.append(e)
    special = [dict(c, kind="special") for c in calendar_items(ar["town"], today, country, faith=faith_key(prof))]
    return {"items": out[:100], "special": special}


def parallel(jobs, progress, finish, budget=45):
    """Run look-ups side by side; after each one finishes, show what we have so far."""
    results, errors = {}, []
    pool = ThreadPoolExecutor(max_workers=max(1, len(jobs)))
    futs = {pool.submit(fn): name for name, fn in jobs.items()}
    from concurrent.futures import as_completed, TimeoutError as FT
    try:
        for f in as_completed(futs, timeout=budget):
            name = futs[f]
            try:
                results[name] = f.result()
            except Exception as e:
                results[name] = None
                errors.append(f"{name}: {friendly_error(e)}")
                print(f"  {name} failed:", friendly_error(e))
            if progress:
                progress(finish(results, errors, done=False))
    except FT:
        late = [n for f, n in futs.items() if not f.done()]
        print("  still slow, showing what we have without:", ", ".join(late))
        errors += [f"{n}: took too long" for n in late]
    pool.shutdown(wait=False, cancel_futures=True)
    return finish(results, errors, done=True)


def section_events(data, prof, ar, terms, today, country, gemini=None, progress=None):
    where, state = ar["where"], ar["region"]
    faith = prof.get("faith") or {}
    qs = [f"{where} ({EVENT_WORDS} OR cultural OR heritage OR exhibit OR \"open house\")",
          f'{where} (seniors OR "senior center" OR "older adults" OR library OR museum OR lecture OR concert OR "town hall")']
    place = faith.get("place") or {}                         # may be empty: they picked a faith but no place
    if place.get("name"):
        qs.append(f'"{place["name"]}"')
    if TRADITION_WORDS.get(faith.get("tradition", "")):
        qs.append(f"{where} ({TRADITION_WORDS[faith['tradition']]}) ({EVENT_WORDS})")
    qs += [f"{where} ({c})" for c in list(chunks(terms, 6))[:2]]
    if state and terms:
        qs.append(f'"{state}" ({next(chunks(terms, 6))}) ({EVENT_WORDS})')

    def news_events():
        found, errors = run_searches(qs, 30, terms, ar)
        events = []
        for it in found:
            text = f"{it['title']}. {it['summary']}"
            day = future_date(text, it["pub"], today)
            if not day or not EVENTY.search(text) or GENERIC.search(text) or NEWS_SKIP.search(it["title"]):
                continue
            if not ANNOUNCE.search(text) or REPORT.search(it["title"]):
                continue                                    # it reports on something, rather than inviting people to it
            if is_local(it, ar):
                tier = "near"
            elif state and title_interests(it, terms) and re.search(r"\b" + re.escape(state) + r"\b", text + " " + it["source"], re.I):
                tier = "state"
            else:
                continue
            events.append(_story(it, date=day.isoformat(), time=find_time(text), kind="event", tier=tier))
        return events

    def games():
        out = []
        for rep in sports_for(data, [l for l, _ in terms], today):
            for g in rep["games"]:
                out.append({**g, "kind": "game", "tier": "team", "source": "ESPN", "outlets": ["ESPN"], "url": "",
                            "summary": " · ".join(x for x in [g["place"], ("On " + g["tv"]) if g["tv"] else ""] if x),
                            "published": "", "interests": [rep["interest"]]})
        return out

    def smart():
        out = []
        for e in core.fetch_events(gemini[0], gemini[1], ar["town"], [l for l, _ in terms], today):
            dom = _domain(e["url"])
            if dom and not any(b in dom for b in BLOCKED_DOMAINS):
                out.append({"id": e["id"], "title": e["title"], "url": e["url"], "source": dom, "outlets": [dom],
                            "summary": e.get("why", ""), "published": "", "interests": [], "date": e["date"],
                            "time": core.parse_time(e.get("time", "")), "place": e.get("place", ""), "kind": "event",
                            "tier": "near", "smart": True})
        return out
    jobs = {"news": news_events, "calendars": lambda: official_events(data, prof, ar, terms, today),
            "eventbrite": lambda: eventbrite(prof, ar, terms, today), "games": games}
    if gemini:
        jobs["smart search"] = smart

    def finish(results, errors, done):
        events = [e for v in results.values() if v for e in v]
        res = finish_events(events, prof, terms, today, country, ar)
        res["errors"] = [] if res["items"] else errors
        return res
    return parallel(jobs, progress, finish, budget=50)


def faith_key(prof):
    t = (prof.get("faith") or {}).get("tradition", "")
    return "christian" if t in ("catholic", "orthodox") else ("" if t == "other" else t)


PRESET = set(INTEREST_WORDS)


def section_news(data, prof, ar, terms, today, country, progress=None):
    where, state = ar["where"], ar["region"]
    qs = [f'{where} (community OR library OR "senior center" OR festival OR park OR museum OR seniors OR neighbors OR opening)']
    qs += [f"{where} ({c})" for c in list(chunks(terms, 6))[:2]]
    own = [(l, w) for l, w in terms if l not in PRESET][:4]
    qs += [f"({w})" for _, w in own]                             # news about their own interests (a team, a hobby), near or not
    preset = [(l, w) for l, w in terms if l in PRESET]
    if preset and state:
        qs.append(f'({next(chunks(preset, 6))}) "{state}"')

    def stories():
        found, errors = run_searches(qs, 10, terms, ar)
        out, per = [], {}
        for it in sorted(found, key=lambda x: x["pub"], reverse=True):
            if NEWS_SKIP.search(it["title"]) or GENERIC.search(it["title"]) or it["pub"] < today - dt.timedelta(days=10):
                continue
            if is_local(it, ar):
                out.append(_story(it, group="near"))
            elif other_place(it, ar) or re.search(r"\b" + re.escape(ar["city"]) + r"\b", it["title"], re.I):
                continue                                    # local news from a same-named town elsewhere
            else:
                for label in title_interests(it, terms):       # keep it only under an interest named in the headline
                    if per.get(label, 0) < 6:
                        per[label] = per.get(label, 0) + 1
                        out.append(_story(it, group=label))
                        break
        return out[:80], errors

    def finish(results, errors, done):
        items, errs = results.get("stories") or ([], [])
        return {"items": items, "sports": results.get("sports") or [], "errors": [] if items else errs + errors}
    return parallel({"stories": stories, "sports": lambda: sports_for(data, [l for l, _ in terms], today)}, progress, finish, budget=45)


# Volunteering is grouped into the kinds of help that suit older adults best.
# cat -> (title, one-line about, words in a listing that put it in this group, interests that bring it to the top)
VOL_CATS = {
    "food": ("Food and meals", "Deliver meals, sort food at a pantry, or serve a community dinner.",
             r"food bank|food pantry|pantry|meals? on wheels|\bmeals?\b|soup kitchen|hunger|community kitchen|food drive|groceries|nutrition",
             {"Cooking"}),
    "kids": ("Children and learning", "Read with children, tutor, or be a foster grandparent.",
             r"tutor|tutoring|reading buddy|read to|literacy|mentor|homework|classroom|after[- ]school|library|\bESL\b|English lessons|story ?time",
             {"Grandchildren and family", "Reading and the library", "Learning new things"}),
    "animals": ("Animals", "Walk dogs, cuddle cats, or foster a pet at home.",
                r"animal|shelter pets?|\bdogs?\b|\bcats?\b|kittens?|pupp|\bpets?\b|humane society|\bSPCA\b|rescue|wildlife|horses?|equine",
                set()),
    "community": ("Parks, museums and gardens", "Guide visitors at a museum, help in a park, or grow food in a community garden.",
                  r"\bpark\b|parks|trails?|museum|docent|histor|heritage|preserv|garden|greenway|clean[- ]?up|nature|arboretum|tree planting|"
                  r"\barts?\b|theater|theatre|gallery|zoo|library",
                  {"Gardening", "Walking and nature", "Arts and crafts", "Festivals and culture", "Music"}),
    "home": ("From home", "Phone a lonely neighbor, help someone who is blind by video, or lend a skill online.",
             r"\bremote\b|virtual|from home|online|by phone|phone calls?|telephone|pen ?pal|letter writing|transcri",
             set()),
    "neighbors": ("Helping neighbors", "Give rides, visit people at home or in care, or help with Medicare and taxes.",
                  r"companion|friendly visit|visiting|\bdrivers?\b|rides?\b|transport|hospice|hospital|caregiver|respite|Medicare|tax|"
                  r"older adults|seniors|elderly|nursing home|home repair|habitat",
                  set()),
}
VOL_ORDER = ["food", "kids", "animals", "community", "home", "neighbors"]

# Programs that welcome (or are made for) volunteers 55+ and exist nearly everywhere in the US, by group.
PROGRAMS_55 = [
    {"id": "p-mow", "cat": "food", "title": "Deliver meals and a friendly visit", "org": "Meals on Wheels",
     "what": "Drive a short route once a week or month, bringing a hot meal and a check-in to neighbors.",
     "who": "Flexible; you set your schedule", "how": "Find your local program at mealsonwheelsamerica.org.", "url": "https://www.mealsonwheelsamerica.org"},
    {"id": "p-foodbank", "cat": "food", "title": "Sort food at the food bank", "org": "Your local food bank (Feeding America)",
     "what": "Join a 2-3 hour shift sorting and packing food. Many have seated jobs.",
     "who": "All ages welcome; bring a friend", "how": "Find yours at feedingamerica.org.", "url": "https://www.feedingamerica.org"},
    {"id": "p-fgp", "cat": "kids", "title": "Be a Foster Grandparent", "org": "AmeriCorps Seniors",
     "what": "Spend time each week reading with and mentoring children at a local school or child care center.",
     "who": "Made for people 55 and over", "how": "See programs near you at americorps.gov, or call the Eldercare Locator: 1-800-677-1116.",
     "url": "https://americorps.gov/serve/americorps-seniors", "phone": "1-800-677-1116"},
    {"id": "p-learntobe", "cat": "kids", "title": "Tutor a child online", "org": "Learn To Be",
     "what": "Free one-on-one online tutoring for children in reading or math, from your own computer.",
     "who": "Adults of any age; you choose your hours", "how": "Sign up at learntobe.org.", "url": "https://www.learntobe.org"},
    {"id": "p-library", "cat": "kids", "title": "Help at your library", "org": "Your public library",
     "what": "Read at story time, help adults learn to read, or run the used-book sale.",
     "who": "All ages; ask at the front desk", "how": "Ask at the front desk, or see the library list below.", "url": ""},
    {"id": "p-shelter", "cat": "animals", "title": "Walk dogs or foster a pet", "org": "Your local animal shelter",
     "what": "Walk and play with dogs, keep cats company, or care for a pet at home until it's adopted.",
     "who": "All ages; fostering is great for people who are home a lot", "how": "Call a shelter in the list below and ask about volunteering or fostering.", "url": ""},
    {"id": "p-parks", "cat": "community", "title": "Volunteer in a national or state park", "org": "Volunteer.gov (National Park Service and more)",
     "what": "Greet visitors, lead walks, or help at a visitor center. Many roles are one day a week.",
     "who": "All ages; many retirees", "how": "Search by state at volunteer.gov.", "url": "https://www.volunteer.gov"},
    {"id": "p-docent", "cat": "community", "title": "Be a museum guide (docent)", "org": "Your local museums",
     "what": "Learn about the collection, then show visitors around. Training is provided.",
     "who": "Curious people; many docents are retired", "how": "Call a museum in the list below and ask about docents.", "url": ""},
    {"id": "p-bemyeyes", "cat": "home", "title": "Help someone who is blind, by video", "org": "Be My Eyes",
     "what": "Answer a short video call on your phone to read a label or check a color for someone who can't see.",
     "who": "Anyone with a smartphone; calls take a minute or two", "how": "Get the free Be My Eyes app.", "url": "https://www.bemyeyes.com"},
    {"id": "p-smithsonian", "cat": "home", "title": "Type up old letters and diaries", "org": "Smithsonian Digital Volunteers",
     "what": "Read handwritten historical documents on your computer and type what they say. Any time, any amount.",
     "who": "Anyone who likes history and reading", "how": "Start at transcription.si.edu.", "url": "https://transcription.si.edu"},
    {"id": "p-catchafire", "cat": "home", "title": "Lend your work skills online", "org": "Catchafire",
     "what": "Use what you did at work (writing, money, planning) to help a charity, often in a one-hour call.",
     "who": "Retirees with a career's worth of skills", "how": "Sign up at catchafire.org.", "url": "https://www.catchafire.org"},
    {"id": "p-rsvp", "cat": "neighbors", "title": "AmeriCorps Seniors (RSVP)", "org": "AmeriCorps",
     "what": "Volunteer a few hours a week with local groups: giving rides, visiting, helping neighbors stay independent.",
     "who": "Made for people 55 and over", "how": "See local programs at americorps.gov, or call the Eldercare Locator: 1-800-677-1116.",
     "url": "https://americorps.gov/serve/americorps-seniors", "phone": "1-800-677-1116"},
    {"id": "p-ship", "cat": "neighbors", "title": "Medicare counselor (SHIP)", "org": "State Health Insurance Assistance Program",
     "what": "Learn Medicare inside out, then help other older adults compare their plans. Free training is provided.",
     "who": "Great for retirees; no experience needed", "how": "Find your state's SHIP at shiphelp.org.", "url": "https://www.shiphelp.org"},
    {"id": "p-taxaide", "cat": "neighbors", "title": "Help neighbors with their taxes", "org": "AARP Foundation Tax-Aide",
     "what": "Free tax help for people who need it. Training is in winter; the season runs February to April.",
     "who": "People who like numbers; training provided", "how": "Sign-ups usually open in the fall at aarpfoundation.org.", "url": "https://www.aarpfoundation.org"},
    {"id": "p-ombud", "cat": "neighbors", "title": "Visit nursing home residents (Ombudsman)", "org": "Long-Term Care Ombudsman Program",
     "what": "Visit residents of local care homes, listen, and help speak up for them.",
     "who": "Caring listeners; training provided", "how": "Find your local program at ltcombudsman.org.", "url": "https://ltcombudsman.org"},
]
# Which local places go with each group (from the map)
VOL_PLACES = {"food": ["food"], "kids": ["library"], "animals": ["shelter"], "community": ["arts", "garden"],
              "home": [], "neighbors": ["senior", "hospital"]}

# Listings that aren't for an older volunteer: jobs, internships, students, company team days
NOT_FOR_OLDER = re.compile(r"\bintern(s|ship|ships)?\b|\bjobs?\b|\bpaid\b|stipend|salary|\bhiring\b|employment|fellowship|\bcareers?\b|"
                           r"(teen|student|youth|junior|college|high school) volunteers?|volunteers? (ages?|aged) 1[2-9]|for (high school |college )?students|"
                           r"students (looking|seeking|needing|who need|wanting)|high school students|college students|service hours|"
                           r"young professionals|\bages? 1[2-9]\s*(-|to)|\b1[3-8]\s*(-|to)\s*2[0-9]\b|under (2[0-9]|30)\b|"
                           r"corporate|employees?|team[- ]building|company|\bLLC\b|\bInc\.? volunteer day", re.I)
OLDER_WELCOME = re.compile(r"55\s*\+|50\s*\+|55 and (older|over)|seniors?|older adults?|retirees?|all ages|people over 50|adults", re.I)
RETIREES_IN_TEXT = re.compile(r"(retirees|seniors|older adults)( and [a-z]+)? (are )?welcome|older volunteers|volunteers (aged )?55|volunteers over (50|55)|great for retirees|perfect for retirees", re.I)


def vol_cats_for(text):
    return [c for c in VOL_ORDER if re.search(VOL_CATS[c][2], text, re.I)]


def vol_order(labels):
    """Groups that match their interests come first; the rest keep the usual order."""
    s = set(labels)
    animal = any(ANIMALS.search(l) for l in labels)
    score = {c: len(VOL_CATS[c][3] & s) + (1 if c == "animals" and animal else 0) for c in VOL_ORDER}
    return sorted(VOL_ORDER, key=lambda c: (-score[c], VOL_ORDER.index(c)))


def programs_for(terms, prof):
    if (prof.get("country") or "US") != "US":
        return []
    return [dict(p) for p in PROGRAMS_55]


def vol_groups(labels, progs, places, opps, stories):
    out = []
    for c in vol_order(labels):
        title, about = VOL_CATS[c][:2]
        out.append({"cat": c, "title": title, "about": about,
                    "programs": [p for p in progs if p.get("cat") == c],
                    "places": [p for p in places if p["kind"] in VOL_PLACES[c]][:4],
                    "opps": [o for o in opps if o.get("cat") == c][:8],
                    "stories": [st for st in stories if (st.get("cats") or [None])[0] == c][:4]})
    return out


def _vol_key(o, names):
    t = re.split(r"\s[-–|:]\s|\s\(", o["title"].lower())[0]      # 'Reading Buddy - Morrisville' is the same role
    for n in names:
        t = re.sub(r"\b" + re.escape(n.lower()) + r"\b", "", t)
    t = re.sub(r"\b(volunteers?|opportunit(y|ies)|needed|wanted|help|\d+|in|at|for|the|a|an|and|of|with)\b|[^a-z ]", " ", t)
    return (o["org"].lower().strip(), " ".join(sorted(set(t.split()))))


def section_volunteer(data, prof, ar, terms, today, country, progress=None):
    terms = [t for t in terms if not t[0].lower().startswith("volunteer")]     # everything here is volunteering already
    where = ar["where"]
    qs = [f'{where} ("volunteers needed" OR "volunteer opportunities" OR "seeking volunteers" OR "looking for volunteers" OR "sign up to volunteer" OR volunteers)']
    if terms:
        qs.append(f"{where} volunteer ({next(chunks(terms, 6))})")

    def stories():
        found, errors = run_searches(qs, 30, terms, ar)
        out = []
        for it in found:
            text = f"{it['title']}. {it['summary']}"
            if "volunteer" not in it["title"].lower() or NEWS_SKIP.search(it["title"]) or GENERIC.search(text) or not is_local(it, ar) \
                    or REPORT.search(it["title"]):
                continue
            day = future_date(text, it["pub"], today)
            if day:
                out.append(_story(it, date=day.isoformat(), time=find_time(text), kind="volunteer"))
            elif VOLUNTEER_CALL.search(text) and not has_past_date(text, it["pub"], today) and it["pub"] >= today - dt.timedelta(days=30):
                out.append(_story(it, date="", time="", kind="volunteer"))
        out.sort(key=lambda v: (v["date"] or "9999", -len(v["interests"])))
        return out[:30]

    def places():
        if prof.get("lat") is None:
            return []
        kinds, labels = volunteer_kinds([l for l, _ in terms]), [l for l, _ in terms]
        try:
            return places_overpass(prof["lat"], prof["lon"], kinds, labels, per_kind=3)
        except RuntimeError:                                       # map not available: ask OpenStreetMap's search instead
            return places_nominatim(prof["lat"], prof["lon"], kinds, labels, per_kind=3)

    def opps():
        rows, errs = src.volunteer_opportunities(ar["city"], ar["region"], ar.get("nearby", []), today, find_dates,
                                                 own_state=POSTAL.get(ar["region"].lower(), ""))
        for e in errs:
            print("  volunteer:", e)
        labels = [l for l, _ in terms]
        kept, seen = [], set()
        for o in rows:
            title_bits = f"{o['title']} {o['good_for']} {o['age']} {o['org']}"
            if NOT_FOR_OLDER.search(title_bits) or NOT_FOR_OLDER.search(o["summary"][:160]):
                continue                                   # jobs, internships, roles for students or company teams
            text = f"{o['title']} {o['summary']} {o['causes']} {o['org']}"
            o["cats"] = vol_cats_for(f"{o['title']} {o['causes']} {o['summary']}")
            if o["where"] == "Remote" and "home" not in o["cats"]:
                o["cats"].append("home")
            if not o["cats"]:
                continue                                   # not one of the kinds of help that suit older adults
            key = _vol_key(o, ar["names"])
            if key in seen:
                continue                                   # the same role posted for several towns
            seen.add(key)
            o["cat"] = o["cats"][0]
            o["interests"] = [label for label, words in terms if mentions(words, text)]
            # 'Older adults welcome' only if the listing says who can volunteer, not because it helps seniors
            o["senior_friendly"] = bool(OLDER_WELCOME.search(f"{o['good_for']} {o['age']}") or RETIREES_IN_TEXT.search(o["summary"]))
            near = any(re.search(r"\b" + re.escape(n) + r"\b", f"{o['title']} {o['summary']}", re.I) for n in ar["names"])
            o["score"] = (3 * o["senior_friendly"] + 2 * min(2, len(o["interests"])) + near
                          + (1 if o["recurring"] or "flexible" in f"{o['commitment']} {o['when_text']}".lower() else 0)
                          + (1 if set(VOL_CATS[o["cat"]][3]) & set(labels) else 0))
            o["best"] = o["score"] >= 2
            kept.append(o)
        kept.sort(key=lambda o: (-o["score"], o["deadline"] or o["start"] or "9999"))
        return kept

    worship = (prof.get("faith") or {}).get("place") or {}

    def worship_events():
        if not worship.get("website"):
            return []
        return [e for e in official_events(data, prof, ar, terms, today)
                if e["site_kind"] == "worship" and re.search(r"volunteer|serve|serving|help|food drive|meal|outreach|mission", f"{e['title']} {e['summary']}", re.I)][:8]

    def finish(results, errors, done):
        stories_ = results.get("stories") or []
        for st in stories_:
            st["cats"] = vol_cats_for(f"{st['title']} {st.get('summary', '')}")
        places_ = results.get("places") or []
        opps_ = results.get("opps") or []
        progs = programs_for(terms, prof)
        groups = vol_groups([l for l, _ in terms], progs, places_, opps_, stories_)
        return {"items": stories_, "places": places_, "opps": opps_, "groups": groups,
                "worship": worship, "worship_events": results.get("worship") or [], "programs": progs,
                "errors": []}
    return parallel({"stories": stories, "places": places, "opps": opps, "worship": worship_events}, progress, finish, budget=45)


# ------------------------------------------------------------------ places (OpenStreetMap: Overpass, then Nominatim backup)
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
NOT_A_CENTER = re.compile(r"nursing|assisted|memory care|rehab|skilled|hospice|apartments|living|residen|manor|village|care center", re.I)
ANIMALS = re.compile(r"animal|dog|cat|pet|pupp|kitten|horse|bird", re.I)
# kind -> (label, why, Overpass filters, radius in metres, Nominatim words, interests that unlock it on the Places page,
#          how a volunteer can help there)
PLACE_KINDS = {
    "senior": ("Senior center", "Free classes, lunches, trips and friendly company.",
               ['nwr["amenity"="community_centre"]["community_centre"~"senior"]', 'nwr["amenity"="social_facility"]["social_facility:for"~"senior"]["social_facility"!~"nursing|assisted|group"]',
                'nwr["amenity"="community_centre"]["name"~"senior",i]'], 15000, "senior center", None,
               "Help at lunches, lead a class or game, or keep someone company."),
    "library": ("Library", "Book clubs, talks, computer help and free events.", ['nwr["amenity"="library"]'], 10000, "library", None,
                "Shelve books, help at the book sale, or read with children."),
    "community": ("Community center", "Classes, clubs and town events.", ['nwr["amenity"="community_centre"]'], 8000, "community center", None, ""),
    "garden": ("Community garden", "Grow vegetables and flowers with neighbors.", ['nwr["leisure"="garden"]["garden:type"="community"]'], 12000,
               "community garden", {"Gardening"}, "Plant, weed and harvest, often on weekend mornings."),
    "park": ("Park", "Paths for a gentle walk and fresh air.", ['nwr["leisure"="park"]["name"]'], 3000, "park",
             {"Walking and nature", "Grandchildren and family", "Exercise classes"}, ""),
    "worship": ("Place of worship", "Services, community meals and volunteering.", ['nwr["amenity"="place_of_worship"]["name"]'], 3000, "church",
                {"Faith and worship"}, "Help with community meals, events and visiting."),
    "arts": ("Arts center or museum", "Exhibits, classes and performances.", ['nwr["amenity"="arts_centre"]', 'nwr["tourism"="museum"]'], 10000,
             "museum", {"Arts and crafts", "Learning new things", "Festivals and culture"}, ""),
    "theatre": ("Theater", "Plays, concerts and shows.", ['nwr["amenity"="theatre"]'], 10000, "theatre", {"Music"}, ""),
    "fitness": ("Fitness or recreation center", "Classes like water aerobics, yoga and tai chi.",
                ['nwr["leisure"="sports_centre"]["name"]', 'nwr["leisure"="fitness_centre"]["name"]'], 5000, "recreation center", {"Exercise classes"}, ""),
    "food": ("Food bank or community kitchen", "Gives food to neighbors who need it.",
             ['nwr["social_facility"~"food_bank|soup_kitchen"]', 'nwr["amenity"="food_bank"]'], 20000, "food bank", "volunteer",
             "Sort and pack food, or serve meals. Most welcome volunteers of every age."),
    "shelter": ("Animal shelter", "Cares for dogs, cats and other animals.", ['nwr["amenity"="animal_shelter"]'], 20000, "animal shelter", "volunteer",
                "Walk dogs, cuddle cats, or help at adoption days."),
    "charity": ("Charity shop", "A thrift store that raises money for a cause.", ['nwr["shop"="charity"]'], 8000, "thrift store", "volunteer",
                "Sort donations or help at the counter for a few hours a week."),
    "hospital": ("Hospital", "Many hospitals have a volunteer program.", ['nwr["amenity"="hospital"]["name"]'], 12000, "hospital", "volunteer",
                 "Greet visitors, deliver flowers or help at the gift shop."),
}


def place_kinds(interests):
    s = set(interests)
    return [k for k, v in PLACE_KINDS.items() if v[5] is None or (isinstance(v[5], set) and v[5] & s)]


def volunteer_kinds(interests):
    s = set(interests)
    kinds = ["food", "senior", "library", "shelter", "arts", "garden", "hospital", "charity"]
    if "Faith and worship" in s: kinds.append("worship")
    return kinds


def miles(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 7917.5 * math.asin(math.sqrt(a))


def classify(t):
    """What kind of place is this? The place's own type comes first, so 'Senior Center Community Garden' is a garden."""
    name, amen, leis = t.get("name", ""), t.get("amenity", ""), t.get("leisure", "")
    if leis == "garden": return "garden"
    if leis == "park": return "park"
    if amen == "animal_shelter": return "shelter"
    if t.get("social_facility") in ("food_bank", "soup_kitchen") or amen == "food_bank": return "food"
    if t.get("shop") == "charity": return "charity"
    if amen == "hospital": return "hospital"
    if amen == "library": return "library"
    if amen == "place_of_worship": return "worship"
    if amen == "arts_centre" or t.get("tourism") == "museum": return "arts"
    if amen == "theatre": return "theatre"
    if leis in ("sports_centre", "fitness_centre"): return "fitness"
    senior = re.search(r"senior|active adult|aging|elder", name, re.I) or "senior" in t.get("community_centre", "") \
        or "senior" in t.get("social_facility:for", "")
    if senior and amen in ("community_centre", "social_facility", ""):
        return None if NOT_A_CENTER.search(name) or t.get("social_facility") in ("nursing_home", "assisted_living", "group_home") else "senior"
    if amen == "community_centre": return "community"
    return None


def _for(kind, interests):
    s = set(interests)
    unlock = PLACE_KINDS[kind][5]
    out = sorted(unlock & s) if isinstance(unlock, set) else []
    if kind == "shelter": out += [i for i in interests if ANIMALS.search(i)]
    if kind == "food" and "Cooking" in s: out.append("Cooking")
    if kind == "library" and "Reading and the library" in s: out.append("Reading and the library")
    if kind == "senior" and "Games and cards" in s: out.append("Games and cards")
    return out


def _place(name, kind, lat, lon, plat, plon, t, interests):
    addr = " ".join(x for x in [t.get("addr:housenumber", ""), t.get("addr:street", "")] if x)
    address = ", ".join(x for x in [addr, t.get("addr:city", "")] if x)
    label, why = PLACE_KINDS[kind][:2]
    if kind == "worship" and t.get("religion"):
        label = f"Place of worship ({t['religion'].replace('_', ' ')})"
    return {"id": _id(name, round(plat, 4)), "name": name, "kind": kind, "label": label, "why": why, "address": address,
            "phone": t.get("phone") or t.get("contact:phone") or "", "hours": t.get("opening_hours", ""),
            "miles": round(miles(lat, lon, plat, plon), 1), "lat": plat, "lon": plon, "help": PLACE_KINDS[kind][6],
            "for": _for(kind, interests)}


def pick_places(cands, kinds, per_kind=3):
    out = []
    for k in kinds:
        out += sorted([c for c in cands if c["kind"] == k], key=lambda c: c["miles"])[:per_kind]
    return out


def places_overpass(lat, lon, kinds, interests, per_kind=3):
    """Places of these kinds near home, picked from the one saved map download."""
    els = map_snapshot(lat, lon, wait=False)                   # never wait: if it isn't here yet, the quick search is used
    if not els:
        raise RuntimeError("The map isn't available right now.")
    cands, seen = [], set()
    for el in els:
        t = el["tags"]
        name = t.get("name", "").strip()
        kind = classify(t)
        if not name or kind not in kinds or name.lower() in seen:
            continue
        if miles(lat, lon, el["lat"], el["lon"]) > PLACE_KINDS[kind][3] / 1609:
            continue                                             # farther than we'd suggest for this kind of place
        seen.add(name.lower())
        cands.append(_place(name, kind, lat, lon, el["lat"], el["lon"], t, interests))
    return pick_places(cands, kinds, per_kind)


def places_nominatim(lat, lon, kinds, interests, per_kind=3):
    """Backup: OpenStreetMap's own search, one gentle request per second as its rules ask."""
    d = 0.18
    cands, seen = [], set()
    for k in kinds:
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": PLACE_KINDS[k][4], "format": "jsonv2", "limit": 8, "bounded": 1, "extratags": 1, "addressdetails": 1,
             "viewbox": f"{lon - d},{lat + d},{lon + d},{lat - d}"})
        for r in json.loads(_get(url, timeout=15)):
            name = (r.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            a, x = r.get("address", {}), r.get("extratags") or {}
            t = {"name": name, "addr:housenumber": a.get("house_number", ""), "addr:street": a.get("road", ""),
                 "addr:city": a.get("city") or a.get("town") or a.get("village", ""), "phone": x.get("phone", ""),
                 "opening_hours": x.get("opening_hours", ""), "religion": x.get("religion", "")}
            kind = k if k != "senior" or not NOT_A_CENTER.search(name) else None
            if kind:
                seen.add(name.lower())
                cands.append(_place(name, kind, lat, lon, float(r["lat"]), float(r["lon"]), t, interests))
        time.sleep(1.1)
    return pick_places(cands, kinds, per_kind)


def section_places(lat, lon, interests, worship=None):
    kinds = place_kinds(interests)
    errors = []
    mine = []
    if worship and worship.get("name") and worship.get("lat") is not None:
        mine = [{"id": _id("mine", worship["name"]), "name": worship["name"], "kind": "worship", "label": "Your place of worship",
                 "why": "Where you worship.", "address": worship.get("address", ""), "phone": worship.get("phone", ""), "hours": "",
                 "miles": round(miles(lat, lon, worship["lat"], worship["lon"]), 1), "lat": worship["lat"], "lon": worship["lon"],
                 "help": "", "for": [], "mine": True}]
    try:
        items = places_overpass(lat, lon, kinds, interests)
        if items:
            return {"items": mine + [i for i in items if i["name"].lower() != (worship or {}).get("name", "").lower()], "errors": []}
    except Exception as e:
        errors.append(f"OpenStreetMap (Overpass): {friendly_error(e)}")
    try:
        return {"items": mine + places_nominatim(lat, lon, kinds, interests), "errors": [], "via": "search"}
    except Exception as e:
        errors.append(f"OpenStreetMap (Nominatim): {friendly_error(e)}")
    return {"items": mine, "errors": errors}


# ------------------------------------------------------------------ cached, self-refreshing sections
PARTS = ("events", "volunteer", "news", "places")


_jobs, _jobs_lock, _failed = {}, threading.Lock(), {}


def _key(part, prof, st, today):
    return hashlib.md5(json.dumps([part, prof.get("town", ""), sorted(prof.get("interests", [])),
                                   today.isoformat() if part != "places" else "", bool(st.get("gemini_key")),
                                   prof.get("faith") or {}, "v7"]).encode()).hexdigest()


def _compute(part, data, progress):
    today = dt.date.today()
    prof, st = data.get("profile") or {}, data["settings"]
    terms, country = interest_terms(prof.get("interests", [])), prof.get("country", "US")
    if part == "places":
        return section_places(prof["lat"], prof["lon"], prof.get("interests", []), (prof.get("faith") or {}).get("place"))
    ar = area(data, prof)
    if part == "events":
        gem = (st["gemini_key"], st.get("gemini_model") or "gemini-2.5-flash") if st.get("gemini_key") else None
        res = section_events(data, prof, ar, terms, today, country, gem, progress)
    elif part == "news":
        res = section_news(data, prof, ar, terms, today, country, progress)
    else:
        res = section_volunteer(data, prof, ar, terms, today, country, progress)
    res["area"] = ar["names"]
    return res


def _job(part):
    try:
        data = core.load()
        prof = data.get("profile") or {}
        if not prof.get("town"):
            return
        key = _key(part, prof, data["settings"], dt.date.today())
        old = bc_get(part) or {}
        started = time.time()

        def progress(res):                                         # show partial results while we keep looking
            if res.get("items") or res.get("opps") or res.get("sports") or res.get("places"):
                bc_put(part, {**res, "key": key, "t": old.get("t", 0) if old.get("key") == key else 0, "partial": True,
                              "updated": "just now"})
        res = _compute(part, data, progress)
        print(f"  {part}: done in {time.time() - started:.0f} seconds")
        has = res.get("items") or res.get("opps") or res.get("sports") or res.get("places")
        if has or old.get("key") != key:                           # found nothing at all? try again in 10 minutes, not hours
            bc_put(part, {**res, "key": key, "t": time.time() if has else time.time() - CACHE_MINUTES[part] * 60 + 600, "partial": False,
                          "updated": dt.datetime.now().strftime("%I:%M %p").lstrip("0")})
        else:                                                      # a failed look-up: keep the last good results
            bc_put(part, {**old, "errors": res.get("errors", []), "t": time.time() - CACHE_MINUTES[part] * 60 + 600})
    except Exception as e:
        import traceback
        print(f"  {part} look-up failed:", friendly_error(e))
        traceback.print_exc(limit=3)
        _failed[part] = time.time()                               # don't retry for a few minutes
    finally:
        with _jobs_lock:
            _jobs.pop(part, None)


_slots = threading.Semaphore(2)                                   # background look-ups take turns, two at a time


def _job_bg(part):
    with _slots:
        _job(part)


def start(part, force=False, background=False):
    if not force and time.time() - _failed.get(part, 0) < 300:
        return                                                    # it just failed; wait before trying again
    with _jobs_lock:
        if part in _jobs and _jobs[part].is_alive():
            return
        th = threading.Thread(target=_job_bg if background else _job, args=(part,), daemon=True, name=f"lookup-{part}")
        _jobs[part] = th
        th.start()


def busy(part):
    with _jobs_lock:
        return part in _jobs and _jobs[part].is_alive()


def stale(part, data):
    prof = data.get("profile") or {}
    if not prof.get("town"):
        return False
    c = bc_get(part) or {}
    if part == "places" and c.get("via") == "search" and (bc_get("map") or {}).get("els"):
        return True                                                # the full map has arrived since: it has more places
    return c.get("key") != _key(part, prof, data["settings"], dt.date.today()) or (time.time() - c.get("t", 0)) / 60 > CACHE_MINUTES[part]


def warm_all():
    """Look everything up in the background (at start, after setup, and on a timer), so pages open instantly."""
    data = core.load()
    prof = data.get("profile") or {}
    if prof.get("lat") is not None:
        map_snapshot(prof["lat"], prof["lon"], wait=False)       # starts the map download if we don't have it
    for part in ("events", "volunteer", "places", "news"):
        if stale(part, data):
            start(part, background=True)


def keep_fresh(every_minutes=10):
    def loop():
        while True:
            try:
                warm_all()
            except Exception as e:
                print("  background refresh:", friendly_error(e))
            time.sleep(every_minutes * 60)
    threading.Thread(target=loop, daemon=True, name="lookup-timer").start()


def section(data, part, refresh=False, today=None):
    """Return what we have right away; if it's old (or 'Look again' was pressed) a fresh look-up runs in the background."""
    today = today or dt.date.today()
    prof = data.get("profile") or {}
    town, interests, country = prof.get("town", ""), prof.get("interests", []), prof.get("country", "US")
    if part not in PARTS:
        raise ValueError("unknown section")
    if not town or (part == "places" and prof.get("lat") is None):
        return {"status": "setup", "items": [], "message": "Add your town in Settings so I can look around for you."}
    if refresh or stale(part, data):
        start(part, force=refresh)
    cache = bc_get(part) or {}
    if cache.get("key") != _key(part, prof, data["settings"], today):
        cache = {}                                                 # older results were for a different town or interests
    items = cache.get("items", [])
    if part in ("events", "volunteer"):
        items = [i for i in items if not i.get("date") or i["date"] >= today.isoformat()]    # never show the past
    special = [dict(c, kind="special") for c in calendar_items(town, today, country, faith=faith_key(prof))] if part == "events" else []
    opps = [o for o in cache.get("opps", []) if not o.get("deadline") or o["deadline"] >= today.isoformat()]
    loading = busy(part)
    errs = cache.get("errors", [])
    nothing = not items and not cache.get("places") and not cache.get("sports") and not opps
    programs = cache.get("programs") or (programs_for([t for t in interest_terms(interests) if not t[0].lower().startswith("volunteer")], prof)
                                         if part == "volunteer" else [])
    groups = []
    if part == "volunteer":
        labels = [i for i, _ in interest_terms(interests)]
        opps = [o for o in opps if o.get("cat")]                   # older saved results from before the groups
        groups = vol_groups(labels, programs, cache.get("places", []), opps, items)
    return {"status": "ok", "items": items, "special": special, "places": cache.get("places", []), "opps": opps, "groups": groups,
            "programs": programs, "worship": cache.get("worship") or (prof.get("faith") or {}).get("place") or {},
            "worship_events": [e for e in cache.get("worship_events", []) if e["date"] >= today.isoformat()],
            "sports": cache.get("sports", []), "updated": cache.get("updated", ""), "loading": loading,
            "problem": bool(errs) and nothing and not loading, "details": errs, "town": town, "area": cache.get("area", []),
            "interests": [i for i, _ in interest_terms(interests)], "state": town_parts(town)[1]}


RELIGION_OF = {"christian": "christian", "catholic": "christian", "orthodox": "christian", "jewish": "jewish", "muslim": "muslim",
               "hindu": "hindu", "sikh": "sikh", "buddhist": "buddhist", "jain": "jain"}
GENERIC_WORDS = {"church": "christian", "parish": "christian", "chapel": "christian", "cathedral": "christian", "synagogue": "jewish",
                 "shul": "jewish", "temple": None, "mosque": "muslim", "masjid": "muslim", "islamic": "muslim", "gurdwara": "sikh",
                 "gurudwara": "sikh", "mandir": "hindu", "hindu": "hindu", "jewish": "jewish", "sikh": "sikh", "buddhist": "buddhist",
                 "jain": "jain", "derasar": "jain", "catholic": "christian", "baptist": "christian", "methodist": "christian"}
SAME = {"st": "saint", "ste": "saint", "mt": "mount", "&": "and", "1st": "first", "bapt": "baptist"}


def _words(s):
    return [SAME.get(w, w) for w in re.findall(r"[a-z0-9&]+", s.lower().replace("'", ""))]


def _name_score(query, name):
    """How well does the typed name match this place? 0 = not at all. Handles 'St.'/'Saint', any word order, small typos."""
    import difflib
    q = [w for w in _words(query) if w not in ("the", "of", "in", "at")]
    n = _words(name)
    if not q:
        return 0
    common = {"saint", "mount", "first", "holy", "church", "temple", "congregation", "parish", "chapel", "community", "center",
              "society", "christian", "united", "baptist", "catholic", "jewish", "islamic", "hindu", "sikh", "masjid", "mosque", "gurdwara"}
    special = [w for w in q if w not in common]
    if special and not any(w in n or any(__import__("difflib").SequenceMatcher(None, w, x).ratio() >= 0.8 for x in n if len(x) > 3)
                           for w in special):
        return 0                                           # the distinctive part of the name ("Michael") must match
    hits = 0
    for w in q:
        if w in n:
            hits += 1
        elif any(difflib.SequenceMatcher(None, w, x).ratio() >= 0.8 for x in n if len(x) > 3):
            hits += 0.8
    whole = difflib.SequenceMatcher(None, " ".join(q), " ".join(n)).ratio()
    return hits / len(q) * 0.75 + whole * 0.25


# What to search for near home, by faith (OpenStreetMap's search matches names and kinds of places)
WORSHIP_SEARCH = {"christian": ["church", "chapel", "parish"], "jewish": ["synagogue", "congregation", "temple", "chabad"],
                  "muslim": ["mosque", "masjid", "islamic center"], "hindu": ["temple", "mandir", "hindu"],
                  "sikh": ["gurdwara", "sikh"], "buddhist": ["buddhist", "temple", "zen", "meditation center"], "jain": ["jain", "temple"]}
# Names that go with each faith, for places on the map that don't say their religion
FAITH_NAME = {"hindu": r"temple|mandir|hindu|\bsri\b|\bshri\b|iskcon|krishna|swaminarayan|\bbaps\b|vedic|ganesh|venkateswara|balaji|durga|shiva|hare|vishnu|murugan",
              "jewish": r"synagogue|congregation|temple|chabad|beth|shalom|\bbeit\b|shul|torah|jewish|israel",
              "muslim": r"mosque|masjid|islamic|muslim|\bjamaat?\b|\bisna\b",
              "sikh": r"gurdwara|gurudwara|sikh|sahib",
              "buddhist": r"buddhis|temple|zen|dharma|sangha|vihara|wat\b|meditation",
              "jain": r"jain|derasar",
              "christian": r"church|chapel|parish|cathedral|ministr|fellowship|baptist|methodist|catholic|lutheran|presbyterian|episcopal|assembly"}
CHURCHY = re.compile(r"church|chapel|baptist|methodist|catholic|lutheran|presbyterian|episcopal|ministr|fellowship|assembly of god", re.I)


def _faith_ok(r, want):
    """Does this place belong to their faith? Its religion tag decides; without one, its name."""
    if not want:
        return True
    if r.get("religion"):
        return r["religion"] == want
    name = r.get("name", "")
    if want != "christian" and CHURCHY.search(name):
        return False                                             # 'Temple Baptist Church' is not a Hindu temple
    return bool(re.search(FAITH_NAME.get(want, "$^"), name, re.I))


def _nominatim(params):
    return json.loads(_get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params), timeout=12))


def _osm_rows(q, lat, lon, d, limit=40):
    res = _nominatim({"q": q, "format": "jsonv2", "limit": limit, "extratags": 1, "addressdetails": 1, "bounded": 1,
                      "viewbox": f"{lon - d},{lat + d},{lon + d},{lat - d}"})
    return [r for r in src.find_worship_rows(res) if r["is_worship"]]


def worship_list(lat, lon):
    """Every place of worship OpenStreetMap knows near home ('place of worship' is a kind-of-place search there).
    Two boxes: close by first, then wider. Saved for a week."""
    key = f"{round(lat, 3)},{round(lon, 3)}"
    c = bc_get("worship") or {}
    if c.get("key") == key and time.time() - c.get("t", 0) < 7 * 86400 and c.get("rows"):
        return c["rows"]
    rows = []
    for i, d in enumerate((0.05, 0.15)):                         # about 3 miles, then about 10 miles each way
        if i:
            time.sleep(1.0)                                      # OpenStreetMap asks for one search a second
        try:
            got = _osm_rows("place of worship", lat, lon, d)
        except Exception as e:
            print("  find place: nearby search didn't work:", friendly_error(e))
            continue
        rows += [r for r in got if not any(o["name"] == r["name"] for o in rows)]
        print(f"  find place: {len(got)} places of worship within {round(d * 69)} miles")
    for r in rows:
        r["miles"] = round(miles(lat, lon, r["lat"], r["lon"]), 1)
    rows.sort(key=lambda r: r["miles"])
    if rows:
        bc_put("worship", {"key": key, "t": time.time(), "rows": rows})
    return rows


def _worship_near(words, lat, lon, want):
    """Places of their faith near home: the full nearby list, plus a search by words like 'mandir' or 'gurdwara'."""
    out = [dict(r) for r in worship_list(lat, lon) if _faith_ok(r, want)]
    for w in words:
        time.sleep(1.0)
        try:
            got = _osm_rows(w, lat, lon, 0.15, limit=20)
        except Exception as e:
            print(f"  find place: search for '{w}' didn't work ({friendly_error(e)})")
            continue
        kept = [r for r in got if _faith_ok(r, want) and not any(o["name"] == r["name"] for o in out)]
        out += kept
        print(f"  find place: '{w}': {len(got)} places, {len(kept)} new")
    for r in out:
        r["miles"] = round(miles(lat, lon, r["lat"], r["lon"]), 1)
    out.sort(key=lambda r: r["miles"])
    return out


def find_worship(q, prof, lat=None, lon=None, tradition=""):
    """Setup: find a church, temple, mosque, synagogue or gurdwara near home by name (or list the nearest ones),
    using OpenStreetMap's search. It never waits for the big town map."""
    lat = float(lat) if lat not in (None, "") else prof.get("lat")
    lon = float(lon) if lon not in (None, "") else prof.get("lon")
    q = (q or "").strip()
    want = RELIGION_OF.get(tradition, "")
    generic = {GENERIC_WORDS[w] for w in _words(q) if w in GENERIC_WORDS} - {None}
    if not want and len(generic) == 1:
        want = next(iter(generic))
    only_generic = not q or all(w in GENERIC_WORDS or w in ("the", "of", "in", "near", "my") for w in _words(q))
    words = [w for w in WORSHIP_SEARCH.get(want, []) if w not in ("temple", "congregation")]   # 'temple' finds streets
    results = []
    if lat is None:
        return []
    if only_generic:
        results = _worship_near(words, lat, lon, want)
    else:
        near = worship_list(lat, lon)                            # 1. a close match among the places nearby
        scored = sorted(((_name_score(q, r["name"]), r) for r in near), key=lambda x: (-x[0], x[1]["miles"]))
        results = [dict(r) for sc, r in scored if sc >= 0.45]
        try:                                                     # 2. OpenStreetMap's own name search, in a wider box
            time.sleep(1.0)
            named = _osm_rows(q, lat, lon, 0.3, limit=10)
        except Exception as e:
            print("  find place (name search):", friendly_error(e))
            named = []
        for r in named:
            if not any(o["name"] == r["name"] for o in results):
                r["miles"] = round(miles(lat, lon, r["lat"], r["lon"]), 1)
                results.append(r)
        print(f"  find place: '{q}': {len(results)} found")
        results = [r for r in results if not (want and r.get("religion") and r["religion"] != want)]
    for r in results:
        r.pop("_rank", None)
        r.pop("is_worship", None)
    return results[:8]
