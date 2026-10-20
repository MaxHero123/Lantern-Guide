"""Plain-language checks that sit next to the scam model.

The model judges how a message is written. These rules check facts the model can't see, and explain them simply:
- what the message asks you to do (pay with gift cards, share a code, let someone control your computer...)
- where its links really go, compared with the company it claims to be from
They never point at single words, because a word on its own proves nothing.
"""
import re
import unicodedata

# company -> (how people write its name, its real web addresses)
BRANDS = {
    "Google": (r"google|gmail|youtube", ["google.com", "gmail.com", "youtube.com", "g.co", "withgoogle.com", "googleusercontent.com"]),
    "Apple": (r"apple|icloud|itunes|app store", ["apple.com", "icloud.com", "apple.co"]),
    "Amazon": (r"amazon|prime video", ["amazon.com", "amazon.co.uk", "amazon.ca", "a.co", "amzn.to", "amzn.com", "amazonaws.com"]),
    "PayPal": (r"paypal", ["paypal.com", "paypal.me"]),
    "Microsoft": (r"microsoft|outlook|office 365|hotmail|xbox", ["microsoft.com", "live.com", "outlook.com", "office.com", "microsoftonline.com", "xbox.com"]),
    "Netflix": (r"netflix", ["netflix.com"]),
    "Facebook": (r"facebook|meta|instagram|whatsapp", ["facebook.com", "fb.com", "meta.com", "instagram.com", "whatsapp.com"]),
    "the Postal Service": (r"\busps\b|postal service", ["usps.com", "usps.gov"]),
    "UPS": (r"\bups\b", ["ups.com"]),
    "FedEx": (r"fedex", ["fedex.com"]),
    "DHL": (r"\bdhl\b", ["dhl.com"]),
    "the IRS": (r"\birs\b|internal revenue", ["irs.gov"]),
    "Social Security": (r"social security|\bssa\b", ["ssa.gov"]),
    "Medicare": (r"medicare", ["medicare.gov", "cms.gov"]),
    "Chase": (r"\bchase\b", ["chase.com"]),
    "Bank of America": (r"bank of america|\bbofa\b", ["bankofamerica.com", "bofa.com"]),
    "Wells Fargo": (r"wells fargo", ["wellsfargo.com"]),
    "Capital One": (r"capital one", ["capitalone.com"]),
    "Venmo": (r"venmo", ["venmo.com"]),
    "Zelle": (r"zelle", ["zellepay.com", "zelle.com"]),
    "Cash App": (r"cash ?app", ["cash.app", "squareup.com"]),
    "Walmart": (r"walmart", ["walmart.com"]),
    "Costco": (r"costco", ["costco.com"]),
    "eBay": (r"\bebay\b", ["ebay.com"]),
    "AT&T": (r"\bat&t\b|\batt\b", ["att.com"]),
    "Verizon": (r"verizon", ["verizon.com"]),
    "T-Mobile": (r"t-mobile|tmobile", ["t-mobile.com"]),
    "Xfinity": (r"xfinity|comcast", ["xfinity.com", "comcast.com", "comcast.net"]),
}
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "ow.ly", "is.gd", "rb.gy", "cutt.ly", "shorturl.at", "tiny.cc", "s.id", "rebrand.ly", "t.ly"}
TWO_PART_TLDS = {"co.uk", "com.au", "co.nz", "co.in", "com.br", "co.za", "com.mx", "co.jp"}

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')]+|\b(?:[a-z0-9-]+\.)+(?:com|net|org|gov|co|us|io|me|info|biz|xyz|top|app|link|live|shop|site|online|ly|gl|to|cc|ca|uk)\b(?:/[^\s<>\"')]*)?", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")


def _plain(text):
    return unicodedata.normalize("NFKC", str(text))


def domain_of(url):
    host = re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip().lower()).split("/")[0].split("?")[0].split(":")[0].rstrip(".")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def links(text):
    t = EMAIL_RE.sub(" ", _plain(text))                    # an email address is not a link
    return [domain_of(u) for u in URL_RE.findall(t)]


# short names that copycat web addresses use ("usps-redeliver.top", "paypa1-secure.com")
TOKENS = {"Google": ["google", "gmail"], "Apple": ["apple", "icloud", "itunes"], "Amazon": ["amazon", "amzn"], "PayPal": ["paypal"],
          "Microsoft": ["microsoft", "outlook", "office365"], "Netflix": ["netflix"], "Facebook": ["facebook", "instagram", "whatsapp"],
          "the Postal Service": ["usps"], "UPS": ["ups"], "FedEx": ["fedex"], "DHL": ["dhl"], "the IRS": ["irs"], "Social Security": ["ssa", "socialsecurity"],
          "Medicare": ["medicare"], "Chase": ["chase"], "Bank of America": ["bankofamerica", "bofa"], "Wells Fargo": ["wellsfargo"],
          "Capital One": ["capitalone"], "Venmo": ["venmo"], "Zelle": ["zelle"], "Cash App": ["cashapp"], "Walmart": ["walmart"],
          "Costco": ["costco"], "eBay": ["ebay"], "AT&T": ["att"], "Verizon": ["verizon"], "T-Mobile": ["tmobile"], "Xfinity": ["xfinity", "comcast"]}


def looks_like(domain, brand):
    """Is this web address dressed up to look like the company's? (long names anywhere, short ones as a separate part)"""
    label = domain.rsplit(".", 1)[0]
    variants = {label.translate(str.maketrans("013457@$", "oieastas")), label.translate(str.maketrans("013457@$", "olea5tas"))}
    for v in variants:
        letters, parts = re.sub(r"[^a-z]", "", v), re.split(r"[-._]", v)
        for tok in TOKENS.get(brand, []):
            if (len(tok) >= 4 and tok in letters) or tok in parts:
                return True
    return False


def brands_claimed(text):
    t = _plain(text).lower()
    return [b for b, (rx, _) in BRANDS.items() if re.search(rx, t)]


def is_official(domain, brand):
    return any(domain == d or domain.endswith("." + d) for d in BRANDS[brand][1])


def official_for_any(domain):
    return next((b for b in BRANDS if is_official(domain, b)), None)


def _unleet(s):
    return s.translate(str.maketrans("013457@$", "oieastas"))


# what the message asks you to do: (name, strong?, plain-language reason, pattern)
ASKS = [
    ("gift_cards", True, "It asks you to pay with gift cards. Real companies and government offices never ask for that.",
     r"\b(gift ?cards?|itunes|google ?play|steam|apple) (cards?|codes?)\b.*\b(buy|send|pay|codes?|purchase)\b|\b(buy|send|pay|purchase)\b.*\bgift ?cards?\b"),
    ("remote_access", True, "It asks to connect to or control your computer or phone.",
     r"\b(anydesk|teamviewer|team viewer|remote(ly)? (access|connect|desktop)|connect to your (computer|device|pc)|technician can connect)\b"),
    ("asks_secrets", True, "It asks you to send a password, a security code, or card or bank details.",
     r"\b(send|share|confirm|reply with|tell me|give me|text (me|us|back))\b.{0,40}\b(pin|password|passcode|otp|one[- ]time|6 digit code|verification code|security code|cvv|card (number|details)|bank (details|account number)|routing number|social security number|ssn|medicare (number|id))\b"),
    ("secrecy", True, "It asks you to keep it secret from your family.",
     r"\b(don'?t|do not) tell (mom|dad|mum|anyone|anybody|the family)|keep (this|it) (between us|secret)"),
    ("new_number_money", True, "Someone says they have a new phone number and asks for money.",
     r"(new (phone|number)|lost my phone|broke my phone|smashed my phone|phone (is )?(broken|dead))[\s\S]{0,160}\b(send|money|pay|bill|zelle|venmo|cash ?app|transfer|wire|\$\d)"),
    ("crypto_or_wire", True, "It asks for cryptocurrency, a wire transfer, or moving money to a \"safe account\".",
     r"\b(bitcoin|btc|usdt|crypto(currency)?|western union|moneygram|wire (the )?(money|transfer)|safe account)\b.{0,60}\b(send|pay|deposit|transfer|move|invest)\b|\b(send|pay|deposit|transfer|move|invest)\b.{0,60}\b(bitcoin|btc|usdt|crypto|western union|moneygram|safe account)\b"),
    ("fee_to_receive", True, "It asks you to pay a fee to get a prize, refund or package.",
     r"\b(processing|registration|release|redelivery|re-delivery|customs|shipping|delivery|clearance)\s+(fee|charge|cost|duty)s?\b[\s\S]{0,80}\b(prize|won|win|refund|grant|package|parcel|claim)\b|\b(prize|won|grant|refund)\b[\s\S]{0,80}\b(processing|registration|release)\s+(fee|charge)|\b(pay|small)\b.{0,30}\b(fee|\$\d+\.\d\d)\b.{0,60}\b(package|parcel|delivery|redeliver)"),
    ("threat", False, "It threatens arrest, legal trouble, or shutting off your account or service.",
     r"\b(arrest|warrant|lawsuit|legal action|police case|jail|will be suspended|will be (closed|blocked|deactivated|terminated)|shut ?off|disconnect(ed)? (today|now))\b"),
    ("pressure", False, "It pushes you to act right away.",
     r"\b(urgent(ly)?|immediately|right away|asap|within \d+ (hours?|minutes?)|today only|final (notice|warning)|act now|expires? (today|soon|tonight))\b"),
    ("prize", False, "It says you won something you didn't enter.",
     r"\b(you('ve| have)? (won|been selected)|congratulations?,? you)\b"),
    ("call_number", False, "It asks you to call a phone number in the message instead of the number you already have for that company.",
     r"\b(call|contact|reach)\b.{0,30}(\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})"),
]
_ASKS = [(n, s, w, re.compile(p, re.I)) for n, s, w, p in ASKS]
SAFE_SECRET = re.compile(r"\b(do not|don't|dont|never|will not|won't)\s+(share|disclose|give|tell|send)|\bwe (will )?never (ask|call)", re.I)
REASSURING = re.compile(r"if this was you,? (you don'?t need to|no (further )?action)|you don'?t need to do anything|if you (did|made) this", re.I)


def review(text):
    """-> dict with what the rules found. 'strong' items alone are enough to call something a scam."""
    t = _plain(text)
    found = []
    for name, strong, why, rx in _ASKS:
        if rx.search(t):
            if name == "asks_secrets" and SAFE_SECRET.search(t):
                continue                                  # "never share this code" is what real services say
            found.append((name, strong, why))
    doms = links(t)
    sender_doms = [m.group(1).lower() for m in re.finditer(r"^\s*from:.*?" + EMAIL_RE.pattern, t, re.I | re.M)]
    claimed = brands_claimed(t)
    link_notes, mismatch = [], []
    for d in dict.fromkeys(doms):
        if d in SHORTENERS:
            mismatch.append((d, None))
            continue
        for b in claimed:
            if not is_official(d, b) and not official_for_any(d) and looks_like(d, b):
                mismatch.append((d, b))                   # g00gle-login.net, paypa1-secure.com, usps-redeliver.top...
    for d in sender_doms:
        for b in claimed:
            if not is_official(d, b) and not official_for_any(d) and looks_like(d, b):
                mismatch.append((d, b))
    for d, b in mismatch:
        if b:
            found.append(("bad_link", True, f"It says it's from {b}, but the link goes to {d}, which is not {b}'s real website."))
        else:
            found.append(("short_link", False, f"Its link ({d}) hides where it really goes."))
    official = [d for d in doms if any(is_official(d, b) for b in claimed)]
    all_official = bool(doms) and len(official) == len(doms) and bool(claimed)
    return {"found": found, "strong": [f for f in found if f[1]], "links": doms, "claimed": claimed,
            "all_links_official": all_official, "reassuring": bool(REASSURING.search(t)),
            "asks": [f for f in found if f[0] in ("gift_cards", "remote_access", "asks_secrets", "crypto_or_wire", "fee_to_receive", "new_number_money")]}


def good_signs(r):
    """Reasons a message looks fine, only ones that are actually true."""
    out = []
    if r["all_links_official"]:
        names = ", ".join(sorted({d for d in r["links"]}))
        out.append(f"Its link goes to {r['claimed'][0]}'s real website ({names}).")
    if not r["asks"]:
        out.append("It doesn't ask for money, passwords, codes or personal details.")
    if not any(f[0] in ("pressure", "threat") for f in r["found"]):
        out.append("It doesn't pressure or threaten you.")
    return out


SECURITY = re.compile(r"sign[- ]?in|log[- ]?in|password|security|account|verify|suspicious|device", re.I)


def safe_steps(brand, domain, text=""):
    if SECURITY.search(text):
        return (f"To be extra safe, don't use the links in it. Open the {brand} app, or type {domain} into your browser yourself, "
                f"and check your account's recent activity there. If you don't recognise something, change your password there.")
    return f"If you'd like to check, open the {brand} app or type {domain} into your browser yourself, instead of tapping the link."
