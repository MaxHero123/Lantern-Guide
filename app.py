"""Lantern Guide: lights the way for you.

Run:   python app.py              then open http://localhost:8000
       python app.py --phone      also lets your iPhone use it (same Wi-Fi); scan the code in Settings
Messages you check are judged by the scam model on THIS computer; they are never sent anywhere.
"""
import json
import mimetypes
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import lantern_board as board
import lantern_ocr as ocr
import lantern_core as core

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
MODEL_DIR = os.environ.get("LANTERN_MODELS", os.path.join(ROOT, "models"))
CLASSIC_ONLY = os.environ.get("LANTERN_CLASSIC_ONLY", "0") == "1"
PORT = int(os.environ.get("PORT", "8000"))
PHONE_PORT = int(os.environ.get("PHONE_PORT", "8443"))
PHONE = "--phone" in sys.argv or os.environ.get("LANTERN_PHONE") == "1"
PHONE_URL = None          # set when phone mode starts
PHONE_KEY = None

_engine, _engine_error, _engine_lock = None, None, threading.Lock()
VERDICT_NAMES = {0: "normal", 1: "junk", 2: "scam", 3: "unsure", 4: "notice"}


def engine():
    """Load the scam model once, the first time it's needed."""
    global _engine, _engine_error
    with _engine_lock:
        if _engine is None and _engine_error is None:
            try:
                from nana_engine import NanaEngine
                _engine = NanaEngine(MODEL_DIR, classic_only=CLASSIC_ONLY)
                print(f"Scam model loaded: {_engine.model_name} ({_engine.mode})")
                for w in _engine.warnings:
                    print("  note:", w)
            except Exception as e:
                _engine_error = f"{e.__class__.__name__}: {e}"
                print("Scam model could not load:", _engine_error)
    return _engine


def public_state(data):
    s = dict(data["settings"])
    s["has_key"] = bool(s.pop("gemini_key", ""))
    return {"profile": data["profile"], "done": data["done"], "added_events": data["added_events"], "settings": s}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("  " + fmt % args + "\n")

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # -------------------------------------------------------------- phone access: only with the secret key from the QR code
    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def allowed(self, q):
        if self.is_local():
            return True
        cookie = self.headers.get("Cookie", "")
        if PHONE_KEY and f"lantern_key={PHONE_KEY}" in cookie:
            return True
        if PHONE_KEY and q.get("key") == PHONE_KEY:                 # first visit from the QR code: remember this phone
            self.send_response(302)
            self.send_header("Set-Cookie", f"lantern_key={PHONE_KEY}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/")
            self.end_headers()
            return None
        body = ("<!doctype html><meta name=viewport content='width=device-width'><body style='font:20px system-ui;padding:24px'>"
                "<h1>Lantern Guide</h1><p>To use Lantern Guide on this phone, open <b>Settings</b> on the computer and scan the "
                "code under <b>Use on your iPhone</b>.</p>").encode()
        self.send_response(403); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        return False

    # -------------------------------------------------------------- pages
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        ok = self.allowed(q)
        if not ok:
            return
        try:
            if u.path == "/api/state":
                return self.send_json(public_state(core.load()))
            if u.path == "/api/model":
                e = engine()
                return self.send_json({"ok": e is not None, "error": _engine_error,
                                       "model": e.model_name if e else None, "notes": e.warnings if e else []})
            if u.path == "/api/geocode":
                return self.send_json({"results": core.geocode(q.get("q", ""))})
            if u.path == "/api/weather":
                prof = core.load().get("profile") or {}
                if "lat" not in prof:
                    return self.send_json({"error": "Add your town in Settings to see the weather."}, 400)
                return self.send_json(core.get_weather(prof["lat"], prof["lon"]))
            if u.path == "/api/section":
                return self.send_json(board.section(core.load(), q.get("part", ""), refresh=q.get("refresh") == "1"))
            if u.path == "/api/ocr_status":
                return self.send_json({"available": ocr.available(), "reason": ocr.why_not()})
            if u.path == "/api/prefetch":                     # a town was just chosen: start downloading its map
                if q.get("lat") and q.get("lon"):
                    board.prefetch_map(float(q["lat"]), float(q["lon"]))
                return self.send_json({"ok": True})
            if u.path == "/api/findplace":
                return self.send_json({"results": board.find_worship(q.get("q", ""), core.load().get("profile") or {},
                                                                     q.get("lat"), q.get("lon"), q.get("trad", ""))})
            if u.path == "/api/phone":
                if not self.is_local():
                    return self.send_json({"enabled": True, "on_phone": True})
                return self.send_json({"enabled": PHONE_URL is not None, "url": PHONE_URL})
            return self.static(u.path)
        except Exception as e:
            return self.send_json({"error": f"{e.__class__.__name__}: {e}"}, 500)

    def static(self, path):
        path = "/index.html" if path in ("", "/") else path
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            self.send_response(404); self.end_headers(); return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(full)[0] or "application/octet-stream") +
                         ("; charset=utf-8" if full.endswith((".html", ".js", ".css", ".svg")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------------------- actions
    def do_POST(self):
        u = urlparse(self.path)
        if not self.allowed({}):
            return
        if u.path == "/api/ocr":                         # a photo to read: raw image bytes, read on this computer
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= 25_000_000:
                    return self.send_json({"error": "That picture is too big."}, 400)
                data = self.rfile.read(n)
                if not ocr.available():
                    return self.send_json({"available": False, "reason": ocr.why_not()})
                text, conf = ocr.read(data)
                return self.send_json({"available": True, "text": text, "confidence": conf, "engine": "apple"})
            except Exception as e:
                return self.send_json({"available": True, "error": f"{e.__class__.__name__}: {e}"}, 500)
        try:
            b = self.body()
            data = core.load()
            if u.path == "/api/check":
                text = str(b.get("text", "")).strip()
                if not text:
                    return self.send_json({"error": "Type or paste a message first."}, 400)
                e = engine()
                if e is None:
                    return self.send_json({"error": "The scam checker isn't set up yet: copy the model files into the "
                                                    "'models' folder (see README).", "detail": _engine_error}, 503)
                v = e.check(text[:5000], b.get("sender", "unknown"))
                return self.send_json({"verdict": VERDICT_NAMES[v.code], "scam": v.scam, "junk": v.junk,
                                       "normal": v.normal, "reasons": v.reasons, "family_check": v.family_check,
                                       "good": v.good, "steps": v.steps})
            if u.path == "/api/profile":
                data["profile"] = b.get("profile")
                data["events_cache"] = {}
                data.pop("board_cache", None)
                board.reset_cache()
                core.save(data)
                threading.Thread(target=board.warm_all, daemon=True).start()     # start looking things up right away
                return self.send_json(public_state(data))
            if u.path == "/api/settings":
                for k in ("text_size", "gemini_model", "theme"):
                    if k in b: data["settings"][k] = str(b[k])
                if "gemini_key" in b:
                    data["settings"]["gemini_key"] = str(b["gemini_key"]).strip()
                core.save(data)
                return self.send_json(public_state(data))
            if u.path == "/api/done":
                day = str(b["date"]); item = str(b["id"])
                lst = data["done"].setdefault(day, [])
                if b.get("done", True) and item not in lst: lst.append(item)
                if not b.get("done", True) and item in lst: lst.remove(item)
                for old in sorted(data["done"])[:-14]:          # keep two weeks of history
                    data["done"].pop(old)
                core.save(data)
                return self.send_json({"done": data["done"]})
            if u.path == "/api/add_event":
                ev = b.get("event") or {}
                if ev.get("id"):
                    data["added_events"] = [x for x in data["added_events"] if x["id"] != ev["id"]] + [ev]   # add or update
                if b.get("remove"):
                    data["added_events"] = [x for x in data["added_events"] if x["id"] != b["remove"]]
                core.save(data)
                return self.send_json({"added_events": data["added_events"]})
            if u.path == "/api/reset":
                core.save(json.loads(json.dumps(core.DEFAULT)))
                board.reset_cache(keep=())
                return self.send_json({"ok": True})
            return self.send_json({"error": "unknown action"}, 404)
        except Exception as e:
            return self.send_json({"error": f"{e.__class__.__name__}: {e}"}, 500)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def start_phone_server():
    """A second, encrypted (https) address so an iPhone on the same Wi-Fi can use the camera, magnifier and reminders."""
    global PHONE_URL, PHONE_KEY
    ip = lan_ip()
    if not ip:
        print("  Phone mode: this computer doesn't seem to be on Wi-Fi.")
        return
    os.makedirs(core.DATA_DIR, exist_ok=True)
    keyf, certf, tokf = (os.path.join(core.DATA_DIR, n) for n in ("phone_key.pem", "phone_cert.pem", "phone_token.txt"))
    if not os.path.exists(tokf):
        with open(tokf, "w") as f:
            f.write(secrets.token_urlsafe(12))
    PHONE_KEY = open(tokf).read().strip()
    stamp = os.path.join(core.DATA_DIR, "phone_ip.txt")
    if not os.path.exists(certf) or not os.path.exists(stamp) or open(stamp).read().strip() != ip:
        cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", keyf, "-out", certf, "-days", "800",
               "-subj", "/CN=Lantern Guide", "-addext", f"subjectAltName=IP:{ip}"]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except Exception:
            try:                                        # older openssl without -addext
                subprocess.run(cmd[:-2], check=True, capture_output=True)
            except Exception as e:
                print("  Phone mode: couldn't make a security certificate:", e)
                return
        with open(stamp, "w") as f:
            f.write(ip)
    srv = ThreadingHTTPServer(("0.0.0.0", PHONE_PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certf, keyf)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    PHONE_URL = f"https://{ip}:{PHONE_PORT}/?key={PHONE_KEY}"
    print(f"  iPhone: open Settings in Lantern Guide on this computer and scan the code.\n  (Or type {PHONE_URL})\n")


if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()          # warm up the model in the background
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  Lantern Guide is running. Open {url} in your browser.\n  (Press Ctrl+C here to stop it.)\n")
    if PHONE:
        start_phone_server()
    board.keep_fresh()                     # look up events, news, volunteering and places in the background
    if os.environ.get("LANTERN_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
