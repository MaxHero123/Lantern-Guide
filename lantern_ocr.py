"""Reading words in photos with Apple's own text recognizer (the one behind Live Text), right on this Mac.

Much more accurate than browser-based reading, and the picture never leaves the computer.
Needs macOS and the 'pyobjc-framework-Vision' package (installed by requirements.txt on a Mac).
On other computers, available() is False and the app falls back to the reader built into the browser.
"""
import sys
import threading

_lock = threading.Lock()
_vision = None
_error = None


def _load():
    global _vision, _error
    if _vision is not None or _error is not None:
        return _vision
    if sys.platform != "darwin":
        _error = "Apple's text reader only works on a Mac."
        return None
    try:
        import Vision                      # noqa: F401  (pyobjc-framework-Vision)
        from Foundation import NSData      # noqa: F401
        _vision = (Vision, NSData)
    except Exception as e:                 # not installed
        _error = f"Apple's text reader isn't installed ({e.__class__.__name__}). Run: pip install -r requirements.txt"
    return _vision


def available():
    return _load() is not None


def why_not():
    _load()
    return _error


def read(data: bytes):
    """Image bytes (jpeg/png) -> (text, confidence 0-100). Lines are put back in reading order."""
    mods = _load()
    if not mods:
        raise RuntimeError(_error)
    Vision, NSData = mods
    with _lock:                            # one picture at a time is plenty
        ns = NSData.dataWithBytes_length_(data, len(data))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns, {})
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(True)
        try:
            req.setAutomaticallyDetectsLanguage_(True)      # macOS 13 and newer
        except Exception:
            pass
        res = handler.performRequests_error_([req], None)
        ok = res[0] if isinstance(res, tuple) else res
        if not ok:
            raise RuntimeError(f"Apple's text reader couldn't read this picture ({res[1] if isinstance(res, tuple) else ''})")
        boxes = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            c = cands[0]
            bb = obs.boundingBox()             # 0-1 coordinates, origin at the bottom left
            top = 1.0 - (bb.origin.y + bb.size.height)
            boxes.append({"top": top, "left": bb.origin.x, "h": max(bb.size.height, 1e-4),
                          "text": str(c.string()), "conf": float(c.confidence())})
    return arrange(boxes)


def arrange(boxes):
    """Group recognized pieces into lines (same height on the page), then add blank lines between paragraphs."""
    if not boxes:
        return "", 0.0
    boxes.sort(key=lambda b: (b["top"], b["left"]))
    lines, cur = [], [boxes[0]]
    for b in boxes[1:]:
        ref = cur[0]
        if abs(b["top"] - ref["top"]) < ref["h"] * 0.5:
            cur.append(b)
        else:
            lines.append(cur)
            cur = [b]
    lines.append(cur)
    out, prev = [], None
    for ln in lines:
        ln.sort(key=lambda b: b["left"])
        top, h = min(b["top"] for b in ln), max(b["h"] for b in ln)
        if prev is not None and top - prev > h * 1.9:
            out.append("")
        out.append("   ".join(b["text"] for b in ln))
        prev = top + h
    conf = 100 * sum(b["conf"] for b in boxes) / len(boxes)
    return "\n".join(out).strip(), round(conf, 1)
