"""Pure utility functions — no side effects, no external state."""
import hashlib
import re
import time

def safe_name(name):
    base = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name).strip("._-") or "endpoint"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"

def _profile_slug(name):
    return "".join(ch if ch.isalnum() else "-" for ch in name).strip("-") or "default"


def label_for_backend(backend_type):
    return {
        "openai-compat": "OpenAI-compatible",
        "anthropic": "Anthropic",
        "command-code": "Command Code",
        "freebuff": "Freebuff (Free AI)",
        "native": "Native",
    }.get(backend_type, backend_type)


def normalize_model_id(text):
    value = text.strip().lower()
    if not value:
        return ""
    value = value.replace("/", "-")
    value = value.replace("+", "plus")
    value = "".join(ch if ch.isalnum() or ch in ".-" else "-" for ch in value)
    while "--" in value:
        value = value.replace("--", "-")
    return value.strip("-.")


def normalize_base_url(url):
    base = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def parse_model_list(text):
    out = []
    seen = set()
    for raw in text.replace(",", "\n").splitlines():
        mid = normalize_model_id(raw)
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def now_utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fmt_tok(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _fmt_dur(s):
    if s >= 3600:
        return f"{s/3600:.1f}h"
    if s >= 60:
        return f"{s/60:.1f}m"
    return f"{s:.1f}s"


def _status_pill(success_rate, fail_pct):
    _U = _usage_theme()
    if fail_pct > 0.15:
        return ("ERR", _U["red"])
    if fail_pct > 0.05:
        return ("WARN", _U["yellow"])
    return ("OK", _U["green"])


def _usage_theme():
    return {
        "base": "#0C0E16", "surface0": "#161928", "surface1": "#1E2235",
        "surface2": "#2A2F47", "text": "#E4E6F0", "subtext": "#B0B4C8",
        "dim": "#5C6180", "accent": "#7EB8F7", "blue": "#5DA4E8",
        "sapphire": "#4EC5C1", "green": "#59D4A0", "yellow": "#F0C75E",
        "red": "#F06A77", "peach": "#F09860", "teal": "#4EC5C1",
        "lavender": "#A899F0", "sky": "#70C8E8", "maroon": "#C44B5C",
        "flamingo": "#E878B0", "rosewater": "#F0D0C0",
        "model_palette": ["#F09860", "#4EC5C1", "#5DA4E8", "#59D4A0",
                          "#F0C75E", "#A899F0", "#70C8E8", "#E878B0",
                          "#C44B5C", "#F0D0C0", "#7EB8F7", "#F06A77"],
    }
