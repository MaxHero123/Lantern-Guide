# Lantern Guide
*Lights the way for you.*

A companion app for older adults:

- **Check a message:** paste a text or email, or use a screenshot, and get a clear verdict (scam, junk, not sure, or normal), with the reasons why.
- **Read small print:** take a photo of a recipe, medicine label or letter, see it big, and hear it read aloud. There's also a live camera magnifier.
- **My day:** medicines, meals and activities, with reminders 15 minutes before and when it's time.
- **What's on nearby:** local events, volunteering and programs matched to your interests, plus the weather and what to wear.

## Privacy
- Messages are judged by Lantern Guide's own trained scam model **on this computer**. They are never sent to any AI company.
- Photos and screenshots are read **on your own computer** (Apple's text reader on a Mac, Tesseract.js in the browser elsewhere) and never sent to any company.
- Answers are saved only in `data/lantern_data.json` on this computer.
- To find news, events and places, only the **town and interests** are used in searches (Google News, OpenStreetMap).

## Run it on your computer
1. Install Python 3.10+ (python.org).
2. Copy the model files into `models/` (see `models/PUT_MODEL_FILES_HERE.txt`).
3. In `requirements.txt`, set `scikit-learn==` to the `sklearn_version` shown in `models/nana_config.json`.
4. In a terminal, inside this folder:
   ```
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
5. The app opens at **http://localhost:8000**. Press Ctrl+C in the terminal to stop it.

**Classic model only** (smaller and faster, no torch needed): `LANTERN_CLASSIC_ONLY=1 python app.py`

## Events, volunteering, news and places (no key needed, any town)
Everything is looked up for the person's own town, county and nearby towns, and refreshes itself every few hours.
- **Volunteering, grouped by the kinds of help that suit older adults:** food and meals, children and learning, animals,
  parks/museums/gardens, from home, and helping neighbors. Each group shows real openings from Idealist (VolunteerMatch is
  now part of it), local places that take helpers (food banks, shelters, libraries, museums) and national programs for 55+
  (Meals on Wheels, AmeriCorps Seniors, SHIP, Be My Eyes and more). Jobs, internships, roles for students and repeats are removed.
- **Official calendars:** the app finds the town's and county's official websites (Wikidata), the nearest libraries and
  senior center (OpenStreetMap) and the person's place of worship, then reads their event calendars automatically
  (WordPress event calendars, iCal feeds, CivicPlus/RSS calendars, schema.org events). This brings in local things like
  library talks, classes, festivals and public meetings.
- **News:** Google News and Bing News, real news organizations only. Interests are searched with related words
  (for example "movies" also finds film, cinema and Hollywood).
- **Eventbrite:** classes and gatherings posted by local organizers, with nightlife and sales events left out.
- **Sports teams** (an interest like "Carolina Panthers"): scores, next game and team news from ESPN's public feed.
- **Faith:** if someone picks worship, setup asks which faith and where they worship (saved only on this computer).
  Their holy days, and events and ways to help at their place of worship, are shown.
- Optional: a free Gemini key (Settings > Optional extra) adds an AI web search for dated events.

## Reading words in photos
On a Mac, Lantern Guide uses Apple's own on-device text reader (the one behind Live Text), which is far more accurate.
It comes with `pip install -r requirements.txt` (package `pyobjc-framework-Vision`). Photos never leave the computer.
On other computers it uses the reader built into the browser.

## Text size and colors
Settings has three text sizes (Small, Medium, Large) and four color schemes (Lantern, Clear white, Garden, Plum and gold).
Every scheme keeps text at a contrast of at least 4.5 to 1, and buttons are at least 44 pixels tall.

## Using it on an iPhone
Start it with `python app.py --phone`, then open Settings > Use on your iPhone and scan the code with the iPhone camera
(same Wi-Fi). The phone link is encrypted and needs the private key in the code, so only phones you scan can open it.

## Files
| file | what it does |
|---|---|
| `app.py` | small web server (Python standard library only) |
| `nana_engine.py` | loads the trained models and makes the scam decision exactly as tested in Colab v4 |
| `lantern_core.py` | weather + what-to-wear tips, optional Gemini event search, saving answers |
| `lantern_board.py` | the key-free nearby board: calendar, local news, places, volunteering |
| `static/` | the web page: `index.html`, `style.css`, `app.js` |
| `models/` | your trained model files (not included) |
| `data/` | saved answers (created automatically) |

| `lantern_sources.py` | volunteer listings, official calendars, Eventbrite, place-of-worship search |
| `lantern_rules.py` | scam warning signs: copycat websites, gift cards, urgent threats and more |
| `lantern_ocr.py` | Apple's on-device text reader (Mac) |

Data sources (all free, no keys): Open-Meteo (weather), built-in holiday calendar, Google News and Bing News RSS, OpenStreetMap
(Overpass and Nominatim), Wikidata, Idealist, Eventbrite, ESPN's public scores feed, Tesseract.js. Optional: Google Gemini.
