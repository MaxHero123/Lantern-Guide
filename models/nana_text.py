
import re, unicodedata

_TLDS = ("com|net|org|co|uk|us|ly|io|me|info|biz|xyz|top|tk|gd|do|cc|in|to|gl|gy|ws|site|online|app|link|"
         "live|shop|club|vip|cn|ru|ng|au|ca|de|fr|es|nl|be|ie|nz|za|my|ph|pk|id|sg|mobi|icu|cfd|sbs|bond|win|click")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+(?:" + _TLDS + r")\b(?:/\S*)?", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_NUMBER = re.compile(r"\+?\(?\d(?:[\d\s().-]*\d){6,}")

def mask(text):
    t = _EMAIL.sub(" <EMAIL> ", str(text))
    t = _URL.sub(" <URL> ", t)
    t = _NUMBER.sub(" <NUMBER> ", t)
    return re.sub(r"\s+", " ", t).strip()

LOOKALIKES = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
              "ѕ": "s", "ԁ": "d", "ɡ": "g", "Α": "A", "Β": "B", "Ε": "E", "Κ": "K", "Μ": "M", "Ο": "O",
              "Ρ": "P", "Τ": "T", "Χ": "X", "Ν": "N"}
LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))

def _unleet(m):
    w = m.group()
    if re.search(r"[A-Za-z]", w) and re.search(r"[013457@$]", w):
        return "".join(LEET.get(ch, ch) for ch in w)
    return w

def normalize(text):
    t = unicodedata.normalize("NFKC", str(text)).translate(ZERO_WIDTH)
    t = "".join(LOOKALIKES.get(ch, ch) for ch in t)
    t = re.sub(r"\b(?:[A-Za-z][.\-_ ]){2,}[A-Za-z]\b", lambda m: re.sub(r"[.\-_ ]", "", m.group()), t)
    t = re.sub(r"(?<![<\w])[A-Za-z0-9@$]+(?![\w>])", _unleet, t)
    return t

def classic_prep(text):
    return normalize(mask(text)).lower()

def transformer_prep(text):
    return normalize(mask(text))

# ---------------- red flags: (name, strong?, explanation, pattern) ----------------
_SAFE = re.compile(r"\b(do not|don't|dont|never|will not|won't)\s+(share|disclose|give|tell|send)|\bwe (will )?never (ask|call)", re.I)
RED_FLAGS = [
    ("gift_cards", True, "Asks for payment in gift cards. Real companies and governments never do this.",
     r"\b(gift ?cards?|itunes|google ?play|steam|apple) (cards?|codes?)\b.*\b(buy|send|pay|codes?|purchase)\b|\b(buy|send|pay|purchase)\b.*\bgift ?cards?\b"),
    ("remote_access", True, "Asks to connect to or control your computer or phone.",
     r"\b(anydesk|teamviewer|team viewer|remote(ly)? (access|connect|desktop)|connect to your (computer|device|pc)|technician can connect)\b"),
    ("asks_secrets", True, "Asks you to send or confirm a PIN, password, code, card or bank details.",
     r"\b(send|share|confirm|verify|enter|provide|reply with|tell me|give me|update)\b.{0,40}\b(pin|password|passcode|otp|one[- ]time|6 digit code|verification code|cvv|card (number|details)|bank (details|account number)|routing number|social security number|ssn|medicare (number|id))\b"),
    ("secrecy", True, "Asks you to keep it secret from family.",
     r"\b(don'?t|do not) tell (mom|dad|mum|anyone|anybody|the family)|keep (this|it) (between us|secret)"),
    ("new_number_money", True, "Claims to be family on a new number and asks for money.",
     r"(new (phone|number)|lost my phone|broke my phone|smashed my phone|phone (is )?(broken|dead))[\s\S]{0,160}\b(send|money|pay|bill|zelle|venmo|cash ?app|transfer|wire|\$\d)"),
    ("crypto_or_wire", True, "Asks for crypto, wire transfer, or a 'safe account'.",
     r"\b(bitcoin|btc|usdt|crypto(currency)?|western union|moneygram|wire (the )?(money|transfer)|safe account)\b.{0,60}\b(send|pay|deposit|transfer|move|invest)\b|\b(send|pay|deposit|transfer|move|invest)\b.{0,60}\b(bitcoin|btc|usdt|crypto|western union|moneygram|safe account)\b"),
    ("fee_to_receive", True, "Asks you to pay a fee to receive a prize, refund or package.",
     r"\b(processing|registration|release|redelivery|re-delivery|customs|shipping|delivery|clearance)\s+(fee|charge|cost|duty)s?\b[\s\S]{0,80}\b(prize|won|win|refund|grant|package|parcel|claim)\b|\b(prize|won|grant|refund)\b[\s\S]{0,80}\b(processing|registration|release)\s+(fee|charge)"),
    ("threat", False, "Threatens arrest, legal action, or shutting off a service.",
     r"\b(arrest|warrant|lawsuit|legal action|police case|jail|suspend(ed)?|block(ed)?|deactivat\w*|terminat\w*|disconnect\w*|shut ?off|clos(ed|ure))\b"),
    ("urgency", False, "Creates time pressure.",
     r"\b(urgent(ly)?|immediately|right away|asap|within \d+ (hours?|minutes?)|today only|final (notice|warning)|act now|expires? (today|soon|tonight))\b"),
    ("prize", False, "Says you won something you didn't enter.",
     r"\b(you('ve| have)? (won|been selected)|winner|congratulations?)\b"),
    ("link", False, "Contains a link. Never tap links in unexpected texts; go to the company's app or website yourself.",
     r"<URL>"),
]
_RF = [(n, s, e, re.compile(p, re.I)) for n, s, e, p in RED_FLAGS]

def red_flags(text):
    t = mask(normalize(text))
    hits = []
    for name, strong, why, rx in _RF:
        if rx.search(t):
            if name == "asks_secrets" and _SAFE.search(t):
                continue            # "never share this code" is what REAL services say
            hits.append((name, strong, why))
    return hits
