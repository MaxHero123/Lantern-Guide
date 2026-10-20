"""Structured local sources that work for any town, with no API keys.

1. Volunteer opportunities from Idealist (VolunteerMatch now sends its searches there): real listings with
   organization, dates, deadline, who it's good for, and the link to apply.
2. Official calendars: the town's and county's own websites (found through Wikidata), the nearest libraries
   and senior center (found through OpenStreetMap), and the person's place of worship. Calendars are found
   automatically: WordPress event calendars, iCal (.ics) feeds, RSS calendar feeds, and schema.org events on pages.
Everything here is cached by lantern_board, so each website is visited only every few hours.
"""
import datetime as dt
import hashlib
import html as htmllib
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
_get = None             # set by lantern_board (shared, testable fetcher)
STATE_ABBR = {}         # full state name (lower) -> "nc"; set by lantern_board


def _id(*parts):
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _text(raw):
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|dt|dd|tr|section|article|span|label|time|a)>", "\n", s)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    return "\n".join(x for x in (re.sub(r"[ \t ]+", " ", ln).strip() for ln in s.split("\n")) if x)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ================================================================== 1. volunteer opportunities (Idealist)
SPAM = re.compile(r"court[- ]?order|court[- ]ordered|community service hours|probation|paid survey|earn \$|get paid|crypto|"
                  r"mlm|sweepstake|gift card|work from home and earn", re.I)
SENIOR_OK = re.compile(r"55\s*\+|55 and (older|over)|seniors?\b|older adults?|retirees?|people over 50|50\s*\+|all ages", re.I)
LABELS = ["Time Commitment", "Recurrence", "Age Requirement", "Good For", "Good fit for", "Start Date", "End Date", "Date",
          "Dates", "Application Deadline", "Apply By", "Deadline", "Location", "Volunteers Needed", "Cause Areas", "Available Times",
          "Starts", "Ends", "When"]


def idealist_pages(city, region, nearby):
    """[[first choice, backup], ...] city pages: 'volunteer-in-cary-nc', or 'volunteer-in-cary-north-carolina'."""
    st = STATE_ABBR.get(region.lower(), "")
    out = []
    for c in [city] + [n for n in nearby if city.lower() not in n.lower()][:1]:
        opts = ([f"https://www.idealist.org/en/volunteer-in-{slug(c)}-{st}"] if st else []) + \
               ([f"https://www.idealist.org/en/volunteer-in-{slug(c)}-{slug(region)}"] if region else [])
        if opts:
            out.append(opts)
    return out


def parse_listing_page(raw):
    """City page -> [(url, title)] of volunteer opportunities."""
    h = _text(raw)
    out, seen = [], set()
    for m in re.finditer(r'href="((?:https://www\.idealist\.org)?/[a-z]{2}/volunteer-opportunity/[0-9a-f]{32}[^"#?]*)"[^>]*>(.*?)</a>', h, re.S):
        url = m.group(1) if m.group(1).startswith("http") else "https://www.idealist.org" + m.group(1)
        title = strip_tags(m.group(2)).split("\n")[0].strip()
        if url not in seen:
            seen.add(url)
            out.append((url, title))
    for m in re.finditer(r'"(/[a-z]{2}/volunteer-opportunity/[0-9a-f]{32}[^"\\]*)"', h):   # links inside page data
        url = "https://www.idealist.org" + m.group(1)
        if url not in seen:
            seen.add(url)
            out.append((url, ""))
    return out


def _walk(o, keys, found):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in keys and k not in found and isinstance(v, (str, int, float, list)) and v not in ("", None, []):
                found[k] = v
            _walk(v, keys, found)
    elif isinstance(o, list):
        for v in o[:200]:
            _walk(v, keys, found)


def labeled(lines, label):
    for i, ln in enumerate(lines):
        if ln.lower().rstrip(":") == label.lower():
            return lines[i + 1] if i + 1 < len(lines) else ""
        if ln.lower().startswith(label.lower() + ":"):
            v = ln.split(":", 1)[1].strip()
            return v or (lines[i + 1] if i + 1 < len(lines) else "")
    return ""


def parse_opportunity(raw, url, today, find_dates):
    """Detail page -> a row for the chart, or None if it's spam or already over."""
    h = _text(raw)
    meta = lambda p: (re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % p, h) or [None, ""])[1]
    title = htmllib.unescape(meta("og:title") or strip_tags((re.search(r"(?is)<h1[^>]*>(.*?)</h1>", h) or [None, ""])[1]))
    title = re.sub(r"\s*[|\-–]\s*Idealist\s*$", "", title).strip()
    title = re.sub(r"\s*[-–:|]\s*Volunteer(ing)? Opportunit(y|ies)\s*$|^Volunteer(ing)? Opportunit(y|ies)\s*[-–:|]\s*", "", title, flags=re.I).strip()
    desc = strip_tags(htmllib.unescape(htmllib.unescape(meta("og:description") or meta("description")))).replace("\n", " ")
    desc = re.split(r"(?<=[.!?])\s", desc, maxsplit=2)
    desc = " ".join(desc[:2])[:180]                      # the first sentence or two is plenty
    lines = strip_tags(h).split("\n")
    fields = {lab: labeled(lines, lab) for lab in LABELS}
    data = {}
    for blob in re.findall(r'(?is)<script[^>]*(?:id="__NEXT_DATA__"|type="application/(?:ld\+)?json")[^>]*>(.*?)</script>', h):
        try:
            _walk(json.loads(blob), {"orgName", "organizationName", "startDate", "endDate", "applicationDeadline", "goodFor",
                                     "timeCommitment", "hiringOrganization", "datePosted", "validThrough", "remoteOk",
                                     "locationType", "description"}, data)
        except ValueError:
            pass
    org = ""
    m = re.search(r'href="(?:https://www\.idealist\.org)?/[a-z]{2}/(?:nonprofit|organization)/[^"]+"[^>]*>(.*?)</a>', h, re.S)
    if m:
        org = strip_tags(m.group(1)).split("\n")[0]
    org = org or str(data.get("orgName") or data.get("organizationName") or "")
    if isinstance(data.get("hiringOrganization"), (dict,)):
        org = org or data["hiringOrganization"].get("name", "")
    if not title or SPAM.search(title) or SPAM.search(desc[:400]):
        return None
    pub = today
    when_text = " ".join(x for x in [fields["Start Date"], fields["Date"], fields["Dates"], fields["Starts"], fields["When"],
                                       str(data.get("startDate", ""))] if x)
    start = None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(data.get("startDate", "")))
    if m:
        start = dt.date.fromisoformat(m.group(1))
    elif when_text:
        ds = sorted(find_dates(when_text, pub))
        start = ds[0] if ds else None
    end_text = " ".join(x for x in [fields["End Date"], fields["Ends"], str(data.get("endDate", ""))] if x)
    end = None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(data.get("endDate", "")))
    if m:
        end = dt.date.fromisoformat(m.group(1))
    elif end_text:
        ds = sorted(find_dates(end_text, pub))
        end = ds[-1] if ds else None
    dl_text = " ".join(x for x in [fields["Application Deadline"], fields["Apply By"], fields["Deadline"],
                                     str(data.get("applicationDeadline", "")), str(data.get("validThrough", ""))] if x)
    deadline = None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", dl_text)
    if m:
        deadline = dt.date.fromisoformat(m.group(1))
    elif dl_text:
        ds = sorted(find_dates(dl_text, pub))
        deadline = ds[0] if ds else None
    recurring = bool(re.search(r"recurring|ongoing|weekly|monthly|flexible", " ".join([fields["Recurrence"], fields["Time Commitment"]]), re.I))
    last_day = end or start
    if (deadline and deadline < today) or (last_day and last_day < today and not recurring):
        return None                                            # over already
    loc_text = " ".join([fields["Location"]] + [ln for ln in lines if re.search(r"must be in or near|associated location|located in", ln, re.I)])
    loc_states = sorted(set(re.findall(r",\s*([A-Z]{2})\b", loc_text)))
    anywhere = bool(re.search(r"\bremote\b|\bvirtual\b|from home", loc_text, re.I))
    good = fields["Good For"] or fields["Good fit for"] or (", ".join(data["goodFor"]) if isinstance(data.get("goodFor"), list) else str(data.get("goodFor", "")))
    blob = " ".join([title, desc, good, fields["Age Requirement"]])
    loc_type = "Remote" if re.search(r"\bremote\b|\bvirtual\b|from home", " ".join([fields["Location"], title]), re.I) else \
        "Hybrid" if re.search(r"\bhybrid\b", " ".join([fields["Location"], title]), re.I) else "In person"
    return {"id": _id(url), "title": title[:140], "org": org[:100], "url": url, "summary": desc[:260],
            "start": start.isoformat() if start else "", "end": end.isoformat() if end else "",
            "deadline": deadline.isoformat() if deadline else "", "recurring": recurring,
            "commitment": (fields["Time Commitment"] or str(data.get("timeCommitment", "")))[:60],
            "when_text": (fields["Recurrence"] or fields["Available Times"])[:60], "age": fields["Age Requirement"][:40],
            "good_for": good[:80], "senior_friendly": bool(SENIOR_OK.search(blob)), "where": loc_type,
            "causes": fields["Cause Areas"][:100], "source": "Idealist", "states": loc_states, "anywhere": anywhere}


def volunteer_opportunities(city, region, nearby, today, find_dates, limit=24, own_state=""):
    """Returns (rows, errors)."""
    links, errors = [], []

    def city_links(opts):
        for page in opts:                                  # the short state code first, the full name only if needed
            try:
                got = parse_listing_page(_get(page, timeout=12))
                if got:
                    return got, None
            except Exception as e:
                last = f"Idealist ({page.rsplit('/', 1)[-1]}): {e.__class__.__name__}"
        return [], (locals().get("last") or "")
    with ThreadPoolExecutor(max_workers=4) as pool:
        for got, err in pool.map(city_links, idealist_pages(city, region, nearby)):
            links += [l for l in got if l[0] not in {x[0] for x in links}]
            if err and not got:
                errors.append(err)
    links = [l for l in links if not SPAM.search(l[1] or "")][: limit * 2]

    def one(link):
        try:
            return parse_opportunity(_get(link[0], timeout=20), link[0], today, find_dates)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = [r for r in pool.map(one, links) if r]
    far = (today + dt.timedelta(days=150)).isoformat()

    def nearby_ok(r):
        if own_state and r["states"] and own_state not in r["states"] and not r["anywhere"]:
            return False                                   # it's in another state
        if r["start"] and r["start"] > far and not r["recurring"]:
            return False                                   # months away
        return True
    kept = [r for r in rows if nearby_ok(r)]
    print(f"  volunteer: Idealist listed {len(links)}, read {len(rows)}, kept {len(kept)} nearby")
    rows = kept
    if links and not rows and not errors:
        errors.append("Idealist: listings found but none could be read")
    return rows[:limit], errors


# ================================================================== 2. official calendars
def wikidata_site(name, region, kind_words=("town", "city", "village", "county", "borough", "municipality", "township")):
    """'Cary', 'North Carolina' -> ('Cary', 'https://www.townofcary.org')."""
    q = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "wbsearchentities", "search": name, "language": "en", "format": "json", "type": "item", "limit": 10})
    for r in json.loads(_get(q, timeout=15)).get("search", []):
        d = (r.get("description") or "").lower()
        if region and region.lower() not in d:
            continue
        if not any(w in d for w in kind_words):
            continue
        ent = json.loads(_get(f"https://www.wikidata.org/wiki/Special:EntityData/{r['id']}.json", timeout=15))
        claims = ent.get("entities", {}).get(r["id"], {}).get("claims", {})
        for c in claims.get("P856", []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(v, str) and v.startswith("http"):
                return r.get("label", name), v
    return None


def _abs(base, href):
    return urllib.parse.urljoin(base, htmllib.unescape(href))


def _same_site(a, b):
    na, nb = urllib.parse.urlparse(a).netloc.lower().removeprefix("www."), urllib.parse.urlparse(b).netloc.lower().removeprefix("www.")
    return na == nb or na.endswith("." + nb) or nb.endswith("." + na)


def find_feeds_in_page(h, base):
    feeds, pages = [], []
    for m in re.finditer(r'<link[^>]+rel="alternate"[^>]*>', h, re.I):
        tag = m.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        typ = re.search(r'type="([^"]+)"', tag)
        if href and typ and ("calendar" in typ.group(1) or re.search(r"event|calendar", tag, re.I)):
            feeds.append(("ics" if "calendar" in typ.group(1) else "rss", _abs(base, href.group(1))))
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', h, re.I | re.S):
        href, label = m.group(1), strip_tags(m.group(2)).lower()
        low = href.lower()
        if low.startswith("webcal:"):
            feeds.append(("ics", "https:" + href[7:]))
        elif re.search(r"\.ics(\?|$)|[?&]ical=1|/ical/|icalendar|format=ical", low):
            feeds.append(("ics", _abs(base, href)))
        elif re.search(r"rssfeed\.aspx\?.*modid=58|calendar.*rss|events.*rss|rss.*(calendar|event)", low):
            feeds.append(("rss", _abs(base, href)))
        elif (re.search(r"/(events?|calendars?)(/|$|\?|\.)", low) or label in ("events", "calendar", "event calendar", "calendar of events",
              "community calendar", "upcoming events", "programs & events", "programs and events")) and not low.startswith(("mailto:", "tel:", "#", "javascript")):
            pages.append(_abs(base, href))
    for m in re.finditer(r'calendar\.google\.com/calendar/(?:u/\d/)?(?:embed|ical)?[^"\'\s]*?[?&](?:src|cid)=([^"&\'\s]+)', h):
        cal = urllib.parse.unquote(htmllib.unescape(m.group(1)))
        if "@" not in cal and re.fullmatch(r"[A-Za-z0-9+/=_-]+", cal):         # cid= is base64 of the calendar address
            try:
                import base64
                cal = base64.b64decode(cal + "=" * (-len(cal) % 4)).decode()
            except Exception:
                pass
        feeds.append(("ics", f"https://calendar.google.com/calendar/ical/{urllib.parse.quote(cal)}/public/basic.ics"))
    for m in re.finditer(r'tockify\.com/(?:[a-z]+/)?([a-z0-9_.-]+[a-z0-9])', h, re.I):
        if m.group(1).lower() not in ("api", "tkf2", "assets", "embed", "js"):
            feeds.append(("ics", f"https://tockify.com/api/feeds/ics/{m.group(1)}"))
    for m in re.finditer(r'webName["\']?\s*[:=]\s*["\']([a-z0-9_-]+)["\']', h, re.I):          # Trumba calendars
        if "trumba" in h.lower():
            feeds.append(("ics", f"https://www.trumba.com/calendars/{m.group(1)}.ics"))
    for m in re.finditer(r'https?://([a-z0-9-]+\.libcal\.com)', h, re.I):                       # LibCal (libraries)
        feeds.append(("libcal", f"https://{m.group(1).lower()}"))
    if re.search(r"localist", h, re.I):
        o = urllib.parse.urlparse(base)
        feeds.append(("localist", f"{o.scheme}://{o.netloc}"))
    if re.search(r"civicplus|/RSSFeed\.aspx|Calendar\.aspx\?EID=", h, re.I):
        o = urllib.parse.urlparse(base)
        feeds.append(("rss", f"{o.scheme}://{o.netloc}/RSSFeed.aspx?ModID=58&CID=All-calendar.xml"))
    if jsonld_events(h, base, dt.date.today(), probe=True):
        feeds.append(("jsonld", base))
    hosts = ("libcal.com", "libnet.info", "bibliocommons.com", "trumba.com", "localist.com", "tockify.com", "librarycalendar.com",
             "assabetinteractive.com", "evanced.info", "calendarwiz.com", "churchcenter.com", "recdesk.com")
    return feeds, [p for p in dict.fromkeys(pages) if _same_site(p, base) or any(h_ in urllib.parse.urlparse(p).netloc for h_ in hosts)]


def discover(site):
    """Find the calendar feeds a website offers. Returns [(kind, url)]."""
    o = urllib.parse.urlparse(site)
    origin = f"{o.scheme or 'https'}://{o.netloc}"
    feeds = []
    try:                                                         # WordPress "The Events Calendar" (very common)
        js = json.loads(_get(origin + "/wp-json/tribe/events/v1/events?per_page=1", timeout=12))
        if isinstance(js, dict) and "events" in js:
            feeds.append(("tribe", origin + "/wp-json/tribe/events/v1/events"))
    except Exception:
        pass
    try:
        home = _text(_get(site, timeout=15))
    except Exception:
        return feeds
    f, pages = find_feeds_in_page(home, site)
    feeds += f
    for p in pages[:2]:
        if len(feeds) >= 3:
            break
        try:
            f2, _ = find_feeds_in_page(_text(_get(p, timeout=15)), p)
            feeds += f2
        except Exception:
            pass
    seen, out = set(), []
    for k, u in feeds:
        if u not in seen:
            seen.add(u)
            out.append((k, u))
    return out[:5]


# ---------- parsers
def _date_time(s):
    """'20261020T183000Z' / '2026-10-20T18:30:00-04:00' / '2026-10-20 18:30:00' / '20261020' -> (date, 'HH:MM' or '')."""
    s = str(s or "").strip()
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})(?:[T ](\d{2}):?(\d{2}))?", s)
    if not m:
        return None, ""
    d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not m.group(4):
        return d, ""
    t = dt.datetime(d.year, d.month, d.day, int(m.group(4)), int(m.group(5)))
    if s.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", s[10:]):
        try:
            iso = s.replace("Z", "+00:00")
            if re.match(r"\d{8}T", iso):
                iso = f"{iso[:4]}-{iso[4:6]}-{iso[6:8]}T{iso[9:11]}:{iso[11:13]}:00+00:00"
            t = dt.datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)
            d = t.date()
        except ValueError:
            pass
    return d, t.strftime("%H:%M")


def parse_ics(raw, today, horizon=60):
    text = re.sub(r"\r?\n[ \t]", "", _text(raw))                 # unfold long lines
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        f = {}
        for ln in block.strip().splitlines():
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            f.setdefault(k.split(";")[0].upper(), v.replace("\\,", ",").replace("\\n", " ").replace("\\;", ";").strip())
        d, t = _date_time(f.get("DTSTART"))
        if not d:
            continue
        rr = f.get("RRULE", "")
        if d < today and rr:                                     # simple repeating events: next time it happens
            step = {"DAILY": 1, "WEEKLY": 7}.get((re.search(r"FREQ=(\w+)", rr) or [None, ""])[1])
            until = re.search(r"UNTIL=(\d{8})", rr)
            if step:
                n = (today - d).days // step + (1 if (today - d).days % step else 0)
                d = d + dt.timedelta(days=n * step)
                if until and d > dt.date(int(until.group(1)[:4]), int(until.group(1)[4:6]), int(until.group(1)[6:8])):
                    continue
        if not (today <= d <= today + dt.timedelta(days=horizon)):
            continue
        out.append({"title": f.get("SUMMARY", "")[:140], "date": d.isoformat(), "time": t, "place": f.get("LOCATION", "")[:120],
                    "url": f.get("URL", ""), "summary": f.get("DESCRIPTION", "")[:260]})
    return out


def parse_tribe(raw, today):
    js = json.loads(_text(raw))
    out = []
    for e in js.get("events", []):
        d, t = _date_time(e.get("start_date"))
        if not d or d < today:
            continue
        v = e.get("venue") or {}
        place = ", ".join(x for x in [v.get("venue", ""), v.get("address", ""), v.get("city", "")] if x) if isinstance(v, dict) else ""
        out.append({"title": strip_tags(e.get("title", "")).replace("\n", " ")[:140], "date": d.isoformat(),
                    "time": "" if e.get("all_day") else t, "place": place[:120], "url": e.get("url", ""),
                    "summary": strip_tags(e.get("excerpt") or e.get("description") or "").replace("\n", " ")[:260]})
    return out


def parse_calendar_rss(raw, today, find_dates, horizon=60):
    import xml.etree.ElementTree as ET
    txt = _text(raw)
    try:
        root = ET.fromstring(re.sub(r"&(?!#?\w+;)", "&amp;", txt))
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        vals = {child.tag.split("}")[-1].lower(): (child.text or "") for child in it}
        title = strip_tags(vals.get("title", "")).replace("\n", " ")
        desc = strip_tags(vals.get("description", "")).replace("\n", " ")
        when = vals.get("eventdates", "") + " " + vals.get("eventtimes", "")
        pub = today
        ds = sorted(x for x in find_dates(f"{when} {title} {desc}", pub) if today <= x <= today + dt.timedelta(days=horizon))
        if not ds:
            continue
        tm = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s?m", f"{vals.get('eventtimes', '')} {desc}", re.I)
        t = f"{int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == 'p' else 0):02d}:{int(tm.group(2) or 0):02d}" if tm else ""
        out.append({"title": title[:140], "date": ds[0].isoformat(), "time": t, "place": vals.get("location", "")[:120],
                    "url": vals.get("link", "").strip(), "summary": desc[:260]})
    return out


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def jsonld_events(h, base, today, probe=False):
    out = []

    def visit(o):
        if isinstance(o, list):
            for x in o:
                visit(x)
        elif isinstance(o, dict):
            typ = o.get("@type", "")
            typ = " ".join(typ) if isinstance(typ, list) else str(typ)
            if "Event" in typ and o.get("startDate"):
                d, t = _date_time(o.get("startDate"))
                if d and (probe or d >= today):
                    loc = o.get("location") or {}
                    if isinstance(loc, list):
                        loc = loc[0] if loc else {}
                    loc = loc if isinstance(loc, dict) else {"name": str(loc)}
                    adr = loc.get("address") or {}
                    adr = adr if isinstance(adr, dict) else {"streetAddress": str(adr)}
                    geo = loc.get("geo") or {}
                    org = o.get("organizer") or {}
                    org = (org[0] if isinstance(org, list) and org else org) if not isinstance(org, str) else {"name": org}
                    online = "Online" in str(o.get("eventAttendanceMode", "")) or "VirtualLocation" in str(loc.get("@type", ""))
                    out.append({"title": strip_tags(str(o.get("name", ""))).replace("\n", " ")[:140], "date": d.isoformat(), "time": t,
                                "place": ", ".join(x for x in [loc.get("name", ""), adr.get("addressLocality", "")] if x)[:120],
                                "url": _abs(base, o.get("url", "") or base),
                                "summary": strip_tags(str(o.get("description", ""))).replace("\n", " ")[:260],
                                "locality": str(adr.get("addressLocality", "")), "region": str(adr.get("addressRegion", "")),
                                "lat": _f(geo.get("latitude")), "lon": _f(geo.get("longitude")), "online": online,
                                "organizer": str(org.get("name", "")) if isinstance(org, dict) else ""})
            for k in ("@graph", "itemListElement", "item", "subEvent", "events"):
                if k in o:
                    visit(o[k])
    for blob in re.findall(r'(?is)<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', h):
        try:
            visit(json.loads(blob.strip()))
        except ValueError:
            continue
    return out


def parse_libcal(base, today):
    """LibCal (used by many public libraries): try its public event list, then its RSS."""
    out = []
    try:
        js = json.loads(_text(_get(f"{base}/ajax/calendar/list?c=-1&date=0000-00-00&perpage=60&page=1&audience=&cats=&camps=&inc=0", timeout=20)))
        for e in js.get("results", []):
            d, t = _date_time(e.get("startdt") or e.get("start"))
            if d and d >= today:
                out.append({"title": strip_tags(e.get("title", ""))[:140], "date": d.isoformat(), "time": "" if e.get("all_day") else t,
                            "place": strip_tags(str(e.get("location", "")))[:120], "url": e.get("url", ""),
                            "summary": strip_tags(e.get("shortdesc") or e.get("description") or "").replace("\n", " ")[:260]})
    except Exception:
        pass
    return out


def parse_localist(base, today):
    js = json.loads(_text(_get(f"{base}/api/2/events?days=60&pp=60", timeout=20)))
    out = []
    for wrap in js.get("events", []):
        e = wrap.get("event", wrap)
        inst = ((e.get("event_instances") or [{}])[0] or {}).get("event_instance", {})
        d, t = _date_time(inst.get("start"))
        if d and d >= today:
            out.append({"title": e.get("title", "")[:140], "date": d.isoformat(), "time": "" if inst.get("all_day") else t,
                        "place": e.get("location_name", "")[:120], "url": e.get("localist_url") or e.get("url", ""),
                        "summary": strip_tags(e.get("description_text") or "").replace("\n", " ")[:260]})
    return out


def calendar_events(kind, url, today, find_dates):
    if kind == "libcal":
        return parse_libcal(url, today)
    if kind == "localist":
        return parse_localist(url, today)
    if kind == "tribe":
        return parse_tribe(_get(url + "?" + urllib.parse.urlencode({"per_page": 40, "start_date": today.isoformat()}), timeout=20), today)
    raw = _get(url, timeout=20)
    if kind == "ics":
        return parse_ics(raw, today)
    if kind == "rss":
        return parse_calendar_rss(raw, today, find_dates)
    return jsonld_events(_text(raw), url, today)


# ================================================================== 3. finding a place of worship by name
def find_worship(q, lat, lon, osm_search):
    """Search OpenStreetMap near home for a church/temple/mosque... by name. osm_search(params) -> json list."""
    if not q.strip():
        return []
    d = 0.6
    params = {"q": q, "format": "jsonv2", "limit": 8, "extratags": 1, "addressdetails": 1}
    if lat is not None:
        params.update({"viewbox": f"{float(lon) - d},{float(lat) + d},{float(lon) + d},{float(lat) - d}", "bounded": 1})
    res = osm_search(params)
    if not res and lat is not None:
        params.pop("bounded", None)
        res = osm_search(params)
    out = find_worship_rows(res)
    out.sort(key=lambda p: not p["is_worship"])
    return out[:6]


def find_worship_rows(res):
    out = []
    for r in res:
        a, x = r.get("address", {}), r.get("extratags") or {}
        name = (r.get("name") or (r.get("display_name", "").split(",")[0])).replace(";", " / ")
        if not name:
            continue
        addr = ", ".join(v for v in [" ".join(w for w in [a.get("house_number", ""), a.get("road", "")] if w),
                                     a.get("city") or a.get("town") or a.get("village", ""), a.get("state", "")] if v)
        site = x.get("website") or x.get("contact:website") or x.get("url") or ""
        out.append({"name": name, "address": addr, "lat": float(r["lat"]), "lon": float(r["lon"]),
                    "website": site if not site or site.startswith("http") else "https://" + site,
                    "phone": x.get("phone") or x.get("contact:phone") or "", "religion": x.get("religion", ""),
                    "denomination": x.get("denomination", ""), "is_worship": r.get("type") == "place_of_worship"})
    return out


# ================================================================== 4. Eventbrite (organizer-posted local events: classes, gatherings, meetups)
SPAM_EVENT = re.compile(r"\b(networking|speed dating|singles|bar crawl|pub crawl|club night|nightclub|rave|after ?party|real estate|"
                        r"investing|investor|crypto|forex|trading|wealth|passive income|mlm|webinar|masterclass for entrepreneurs|"
                        r"bootcamp|hiring event|job fair|career fair|21\+|18\+|hookah|cannabis|brunch party|day party|tour package|"
                        r"timeshare|sales training|make money)\b", re.I)


def eventbrite_events(city, state_postal, keywords, today, limit_pages=3):
    st = (state_postal or "").lower()
    if not st:
        return []
    base = f"https://www.eventbrite.com/d/{st}--{slug(city)}"
    pages = [f"{base}/events/", f"{base}/classes/"] + [f"{base}/{slug(k)}/" for k in keywords[:2]]
    out, seen = [], set()
    for p in pages[:limit_pages + 1]:
        try:
            for e in jsonld_events(_text(_get(p, timeout=20)), p, today):
                key = (e["date"], e["title"].lower())
                if key not in seen:
                    seen.add(key)
                    out.append(e)
        except Exception:
            continue
    return out
