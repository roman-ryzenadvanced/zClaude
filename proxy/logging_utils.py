"""Logging utilities — redaction, request snapshots, rate limiting."""
import json
import os
import re
import threading
import time


_SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "[REDACTED:key]"),
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "[REDACTED:anthropic]"),
    (r"gh[pousr]_[A-Za-z0-9_]{20,}", "[REDACTED:github]"),
    (r"Bearer\s+[A-Za-z0-9._\-]{20,}", "Bearer [REDACTED]"),
]


def _redact(text):
    if not text:
        return text
    for pat, repl in _SECRET_PATTERNS:
        text = re.sub(pat, repl, text)
    return text


def _redact_json(obj):
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(obj)
    return _redact(text)


# These will be set by the caller or imported from config
_REQUESTS_DIR = None
_MAX_SNAPSHOTS = 200


def _init_logging(requests_dir, max_snapshots=200):
    global _REQUESTS_DIR, _MAX_SNAPSHOTS
    _REQUESTS_DIR = requests_dir
    _MAX_SNAPSHOTS = max_snapshots


def save_request_snapshot(request_id, body):
    if not request_id:
        return request_id
    snapshot = {
        "_meta": {
            "request_id": request_id,
            "model": body.get("model", ""),
            "stream": body.get("stream", False),
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "pending",
            "duration_s": None,
            "error": None,
        },
        "request": json.loads(_redact_json(body)),
    }
    path = os.path.join(_REQUESTS_DIR, f"{request_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    _rotate_snapshots()
    return request_id


def update_snapshot_response(request_id, status, duration_s=None, error=None):
    if not request_id:
        return
    path = os.path.join(_REQUESTS_DIR, f"{request_id}.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            snapshot = json.load(f)
        meta = snapshot.get("_meta", {})
        meta["status"] = status
        if duration_s is not None:
            meta["duration_s"] = round(duration_s, 3)
        if error is not None:
            meta["error"] = str(error)[:200]
        snapshot["_meta"] = meta
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _rotate_snapshots():
    try:
        files = sorted(
            [os.path.join(_REQUESTS_DIR, f) for f in os.listdir(_REQUESTS_DIR) if f.endswith(".json")],
            key=os.path.getmtime,
        )
        while len(files) > _MAX_SNAPSHOTS:
            os.remove(files.pop(0))
    except Exception:
        pass


class TokenBucket:
    def __init__(self, capacity=10, refill=1.0):
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill = float(refill)
        self.updated = time.monotonic()
        self.lock = threading.Lock()
    def allow(self, cost=1):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill)
            self.updated = now
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


_rate_buckets = {}
_rate_buckets_lock = threading.Lock()


def _bucket_for_route(route):
    name = route.get("name") or route.get("target_url") or "default"
    with _rate_buckets_lock:
        if name not in _rate_buckets:
            _rate_buckets[name] = TokenBucket(capacity=10, refill=1.0)
        return _rate_buckets[name]
