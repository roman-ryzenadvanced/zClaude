"""Endpoint health checks — streaming, tool calls, auth."""
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from lib.constants import UA, PROXY_CONFIG_DIR
from lib.utils import normalize_base_url
from lib.model_fetcher import fetch_models_for_endpoint

# ═══════════════════════════════════════════════════════════════════════
# Doctor checks
# ═══════════════════════════════════════════════════════════════════════

def _doctor_check_streaming(base_url, key, bt, model, add):
    if bt == "anthropic":
        test_url = f"{base_url}/v1/messages"
        headers = {"User-Agent": UA, "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = json.dumps({"model": model or "claude-3-5-haiku-20241022", "max_tokens": 1, "stream": True,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
    else:
        test_url = f"{base_url}/chat/completions"
        headers = {"User-Agent": UA, "Authorization": f"Bearer {key}", "content-type": "application/json"}
        body = json.dumps({"model": model, "max_tokens": 1, "stream": True,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
    try:
        req = urllib.request.Request(test_url, data=body, headers=headers, method="POST")
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=20)
        content_type = resp.headers.get("content-type", "")
        first_chunk = resp.read(512)
        lat = (time.time() - t0) * 1000
        is_sse = "text/event-stream" in content_type or first_chunk.startswith(b"data:")
        if is_sse:
            add("Streaming support", True, f"SSE OK in {lat:.0f}ms")
        else:
            add("Streaming support", False, f"Expected SSE, got {content_type[:60]}")
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read(200).decode(errors="replace")
        except Exception:
            pass
        if e.code == 429:
            add("Streaming support", None, "Rate limited (skipped)")
        elif e.code in (400, 404, 422):
            add("Streaming support", False, f"HTTP {e.code}: {body_text[:80]}")
        else:
            add("Streaming support", False, f"HTTP {e.code}")
    except Exception as e:
        add("Streaming support", False, str(e)[:100])


def _doctor_check_toolcall(base_url, key, bt, model, add):
    tool = {"type": "function", "function": {"name": "test_tool", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}}
    if bt == "anthropic":
        test_url = f"{base_url}/v1/messages"
        headers = {"User-Agent": UA, "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = json.dumps({"model": model or "claude-3-5-haiku-20241022", "max_tokens": 50, "stream": False,
                           "tools": [tool], "messages": [{"role": "user", "content": "Use the test_tool with x=hello"}]}).encode()
    else:
        test_url = f"{base_url}/chat/completions"
        headers = {"User-Agent": UA, "Authorization": f"Bearer {key}", "content-type": "application/json"}
        body = json.dumps({"model": model, "max_tokens": 50, "stream": False, "tools": [tool],
                           "messages": [{"role": "user", "content": "Use the test_tool with x=hello"}]}).encode()
    try:
        req = urllib.request.Request(test_url, data=body, headers=headers, method="POST")
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=30)
        raw = resp.read()
        lat = (time.time() - t0) * 1000
        payload = json.loads(raw)
        has_tools = False
        if bt == "anthropic":
            for block in (payload.get("content") or []):
                if block.get("type") == "tool_use":
                    has_tools = True
                    break
        else:
            choices = payload.get("choices") or []
            for ch in choices:
                if (ch.get("message", {}).get("tool_calls")):
                    has_tools = True
                    break
        if has_tools:
            add("Tool-call support", True, f"Tool call received in {lat:.0f}ms")
        else:
            add("Tool-call support", None, f"Responded but no tool_call ({lat:.0f}ms)")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            add("Tool-call support", None, "Rate limited (skipped)")
        elif e.code in (400, 404, 422):
            err_body = ""
            try:
                err_body = e.read(200).decode(errors="replace")
            except Exception:
                pass
            add("Tool-call support", False, f"HTTP {e.code}: {err_body[:80]}")
        else:
            add("Tool-call support", False, f"HTTP {e.code}")
    except Exception as e:
        add("Tool-call support", False, str(e)[:100])


def run_endpoint_doctor(endpoint):
    """Comprehensive health checks for an endpoint. Returns [(name, ok, detail), ...].
    ok: True=pass, False=fail, None=warn/skip."""
    checks = []
    def add(name, ok, detail=""):
        checks.append((name, ok, detail))

    url = normalize_base_url(endpoint.get("base_url") or "")
    key = (endpoint.get("api_key") or "").strip()
    bt = endpoint.get("backend_type", "openai-compat")
    model = endpoint.get("default_model") or (endpoint.get("models", [""])[0] if endpoint.get("models") else "")

    parsed = urllib.parse.urlparse(url)
    has_url = bool(parsed.scheme and parsed.netloc)
    add("URL format", has_url, url if has_url else "Missing scheme or host")
    if not has_url:
        return checks

    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        t0 = time.time()
        addrs = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        dns_ms = (time.time() - t0) * 1000
        add("DNS resolution", True, f"{addrs[0][4][0]} ({dns_ms:.0f}ms)")
    except socket.gaierror as e:
        add("DNS resolution", False, str(e))
        return checks

    try:
        t0 = time.time()
        sock = socket.create_connection((host, port), timeout=10)
        tcp_ms = (time.time() - t0) * 1000
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            try:
                ssock = ctx.wrap_socket(sock, server_hostname=host)
                tls_ms = (time.time() - t0) * 1000
                add("TLS connection", True, f"TCP {tcp_ms:.0f}ms + handshake {tls_ms:.0f}ms")
                ssock.close()
            except ssl.SSLError as e:
                add("TLS certificate", False, str(e)[:120])
                sock.close()
                return checks
        else:
            add("TCP connection", True, f"{tcp_ms:.0f}ms")
            sock.close()
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        add("TCP connection", False, str(e)[:100])
        return checks

    if bt == "anthropic":
        add("/models endpoint", None, "Anthropic has no /models endpoint — testing via /messages")
        try:
            t0 = time.time()
            msg_url = f"{url}/v1/messages"
            body = json.dumps({"model": model or "claude-3-5-haiku-20241022", "max_tokens": 1,
                               "messages": [{"role": "user", "content": "hi"}]}).encode()
            req = urllib.request.Request(msg_url, data=body, headers={
                "User-Agent": UA, "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, method="POST")
            urllib.request.urlopen(req, timeout=15)
            lat = (time.time() - t0) * 1000
            add("Auth valid", True, f"Responded in {lat:.0f}ms")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                add("Auth valid", False, f"HTTP {e.code} — check API key")
            elif e.code == 400:
                add("Auth valid", True, "Authenticated (model or param error)")
            else:
                add("Auth valid", False, f"HTTP {e.code}")
        except Exception as e:
            add("Auth valid", False, str(e)[:100])
    elif bt.startswith("gemini-oauth"):
        token_name = "google-antigravity-oauth-token.json" if "antigravity" in bt else "google-cli-oauth-token.json"
        token_path = PROXY_CONFIG_DIR / token_name
        if token_path.exists():
            try:
                td = json.loads(token_path.read_text(encoding="utf-8"))
                exp = td.get("expires_at", 0)
                if exp > time.time():
                    remaining = exp - time.time()
                    add("OAuth token", True, f"Valid ({remaining / 60:.0f} min remaining)")
                else:
                    add("OAuth token", False, "Token expired — re-login required")
            except Exception as e:
                add("OAuth token", False, str(e)[:80])
        else:
            add("OAuth token", False, f"No token file ({token_name})")
        try:
            t0 = time.time()
            ids, err = fetch_models_for_endpoint(endpoint)
            lat = (time.time() - t0) * 1000
            if ids:
                add("Network reachable", True, f"{lat:.0f}ms")
                add("/models endpoint", True, f"{len(ids)} models ({lat:.0f}ms)")
                if model:
                    add("Selected model exists", model in ids,
                        model if model in ids else f"'{model}' not in {ids[:5]}...")
            elif err and ("401" in str(err) or "403" in str(err)):
                add("Network reachable", True, f"{lat:.0f}ms")
                add("Auth valid", False, str(err)[:100])
            else:
                add("Network reachable", False, str(err or "no response")[:100])
        except Exception as e:
            add("Network", False, str(e)[:100])
    else:
        try:
            t0 = time.time()
            ids, err = fetch_models_for_endpoint(endpoint)
            lat = (time.time() - t0) * 1000
            if ids:
                add("Network reachable", True, f"{lat:.0f}ms")
                add("Auth valid", True)
                add("/models endpoint", True, f"{len(ids)} models ({lat:.0f}ms)")
                if model:
                    add("Selected model exists", model in ids,
                        model if model in ids else f"'{model}' not found in {len(ids)} models")
                else:
                    add("Selected model", False, "No model selected")
            elif err and ("401" in str(err) or "403" in str(err)):
                add("Network reachable", True, f"{lat:.0f}ms")
                add("Auth valid", False, "HTTP 401/403 — check API key")
            elif err and "429" in str(err):
                add("Network reachable", True, f"{lat:.0f}ms")
                add("Auth valid", True, "Authenticated but rate-limited")
                add("/models endpoint", None, "Rate limited — skipped")
            else:
                add("Network reachable", False, str(err or "no response")[:100])
        except Exception as e:
            add("Network", False, str(e)[:100])

    if bt not in ("native", "command-code"):
        _doctor_check_streaming(url, key, bt, model, add)

    if bt not in ("native", "command-code"):
        _doctor_check_toolcall(url, key, bt, model, add)

    return checks

