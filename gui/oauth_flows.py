"""Unified OAuth flows — Google PKCE, Codebuff, Kiro.

Shared logic extracted from EditEndpointDialog and LauncherWin to
eliminate duplication. Each function performs the core OAuth logic and
accepts callbacks for GUI integration.
"""
import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from codex_launcher_lib import (
    PROXY_CONFIG_DIR, OAUTH_SECRETS_PATH, HOME, UA,
    load_oauth_secrets, open_url,
)


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════

def google_oauth_build_params(oauth_provider="google-cli"):
    """Build all OAuth parameters (URLs, PKCE, scopes) for Google flow.

    Returns dict with: client_id, client_secret, scopes, port, redirect_uri,
    callback_path, provider_kind, state, verifier, challenge, auth_url, token_path.
    """
    is_antigravity = oauth_provider == "google-antigravity"
    token_path = str(PROXY_CONFIG_DIR / (
        "google-antigravity-oauth-token.json" if is_antigravity else "google-cli-oauth-token.json"))

    _sec = load_oauth_secrets().get("antigravity" if is_antigravity else "gemini_cli", {})
    client_id = _sec.get("client_id",
        os.environ.get("ZCLAUDE_GEMINI_CLIENT_ID", ""))
    client_secret = _sec.get("client_secret",
        os.environ.get("ZCLAUDE_GEMINI_CLIENT_SECRET", ""))

    if is_antigravity:
        scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
        ]
        port = 51121
        redirect_uri = f"http://localhost:{port}/oauth-callback"
        callback_path = "/oauth-callback"
        provider_kind = "antigravity"
    else:
        scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
        callback_path = "/oauth2callback"
        provider_kind = "cli"

    state = secrets.token_hex(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    scope_str = " ".join(scopes)
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={urllib.parse.quote(scope_str)}"
        f"&access_type=offline"
        f"&prompt=select_account%20consent"
        f"&state={state}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )

    return {
        "client_id": client_id, "client_secret": client_secret,
        "scopes": scopes, "port": port, "redirect_uri": redirect_uri,
        "callback_path": callback_path, "provider_kind": provider_kind,
        "state": state, "verifier": verifier, "challenge": challenge,
        "auth_url": auth_url, "token_path": token_path,
        "is_antigravity": is_antigravity,
    }


def google_oauth_exchange_code(params, code):
    """Exchange authorization code for tokens. Returns token dict."""
    token_data = urllib.parse.urlencode({
        "code": code, "client_id": params["client_id"],
        "client_secret": params["client_secret"],
        "redirect_uri": params["redirect_uri"],
        "grant_type": "authorization_code",
        "code_verifier": params["verifier"],
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = urllib.request.urlopen(req, timeout=30)
    tokens = json.loads(resp.read())
    tokens["client_id"] = params["client_id"]
    tokens["client_secret"] = params["client_secret"]
    tokens["provider_kind"] = params["provider_kind"]
    tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
    return tokens


def oauth_discover_project(access_token, token_path, tokens):
    """Discover Google Cloud project for the token. Returns project_id string."""
    project_id = ""
    try:
        lr = urllib.request.Request(
            "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {access_token}",
                     "User-Agent": "google-api-nodejs-client/9.15.1"})
        lresp = urllib.request.urlopen(lr, timeout=15)
        ldata = json.loads(lresp.read())
        p = ldata.get("cloudaicompanionProject", "")
        if isinstance(p, dict):
            project_id = p.get("id", "")
        elif isinstance(p, str):
            project_id = p
    except Exception:
        pass
    if not project_id:
        return ""
    try:
        test_url = f"https://cloudcode-pa.googleapis.com/v1internal:listModels?project={project_id}"
        test_req = urllib.request.Request(test_url,
            headers={"Authorization": f"Bearer {access_token}",
                     "User-Agent": "google-api-nodejs-client/9.15.1"})
        urllib.request.urlopen(test_req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 403 and "SERVICE_DISABLED" in (e.read().decode()[:500]):
            try:
                list_req = urllib.request.Request(
                    "https://cloudresourcemanager.googleapis.com/v1/projects?filter=lifecycleState:ACTIVE",
                    headers={"Authorization": f"Bearer {access_token}"})
                list_resp = urllib.request.urlopen(list_req, timeout=15)
                projects = json.loads(list_resp.read()).get("projects", [])
                for proj in projects:
                    pid = proj.get("projectId", "")
                    if not pid or pid == project_id:
                        continue
                    try:
                        t2 = urllib.request.Request(
                            f"https://cloudcode-pa.googleapis.com/v1internal:listModels?project={pid}",
                            headers={"Authorization": f"Bearer {access_token}",
                                     "User-Agent": "google-api-nodejs-client/9.15.1"})
                        urllib.request.urlopen(t2, timeout=10)
                        project_id = pid
                        break
                    except Exception:
                        continue
            except Exception:
                pass
    tokens["project_id"] = project_id
    with open(token_path, "w") as f:
        json.dump(tokens, f, indent=2)
    return project_id


def save_google_tokens(params, tokens):
    """Save Google OAuth tokens to disk and discover project."""
    token_path = params["token_path"]
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w") as f:
        json.dump(tokens, f, indent=2)
    oauth_discover_project(tokens["access_token"], token_path, tokens)
    return tokens


def get_google_models(is_antigravity):
    """Return model list for Google provider."""
    from codex_launcher_lib import ANTIGRAVITY_MODELS
    if is_antigravity:
        return list(ANTIGRAVITY_MODELS)
    return ["gemini-2.5-flash", "gemini-2.5-pro"]


# ═══════════════════════════════════════════════════════════════════════
# Codebuff shared helpers
# ═══════════════════════════════════════════════════════════════════════

def codebuff_save_credentials(user_data):
    """Save Codebuff credentials to disk. Returns creds dict."""
    cb_creds_path = str(HOME / ".config" / "manicode" / "credentials.json")
    os.makedirs(os.path.dirname(cb_creds_path), exist_ok=True)
    creds = {"default": {
        "id": user_data.get("id", ""),
        "name": user_data.get("name", ""),
        "email": user_data.get("email", ""),
        "authToken": user_data.get("authToken", ""),
        "fingerprintId": user_data.get("fingerprintId", ""),
        "fingerprintHash": user_data.get("fingerprintHash", ""),
    }}
    with open(cb_creds_path, "w") as f:
        json.dump(creds, f, indent=2)
    return creds


def codebuff_request_login():
    """Start Codebuff login flow. Returns (login_url, fp_id, fp_hash, expires_at) or raises."""
    import uuid
    fp_id = str(uuid.uuid4())
    body = json.dumps({"fingerprintId": fp_id}).encode()
    req = urllib.request.Request("https://www.codebuff.com/api/auth/cli/code",
        data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
    resp = urllib.request.urlopen(req, timeout=30)
    rdata = json.loads(resp.read())
    login_url = rdata.get("loginUrl", "") or rdata.get("login_url", "")
    fp_hash = rdata.get("fingerprintHash", "") or rdata.get("fingerprint_hash", "")
    expires_at = rdata.get("expiresAt", 0) or rdata.get("expires_at", 0)
    if not login_url:
        raise ValueError("No login URL received from Codebuff")
    return login_url, fp_id, fp_hash, expires_at


def codebuff_poll_status(fp_id, fp_hash, expires_at, timeout=300):
    """Poll Codebuff for login completion. Returns user dict or None."""
    poll_url = (
        f"https://www.codebuff.com/api/auth/cli/status?"
        f"fingerprintId={urllib.parse.quote(fp_id)}"
        f"&fingerprintHash={urllib.parse.quote(fp_hash)}"
        f"&expiresAt={expires_at}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            pr = urllib.request.Request(poll_url, headers={"User-Agent": UA})
            pd = json.loads(urllib.request.urlopen(pr, timeout=10).read())
            if pd.get("user", {}).get("authToken"):
                return pd["user"]
        except Exception:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════
# Kiro shared helpers
# ═══════════════════════════════════════════════════════════════════════

def kiro_register_client():
    """Register OIDC client for Kiro. Returns (client_id, client_secret) or raises."""
    from codex_launcher_lib import KIRO_SSO_OIDC_ENDPOINT, KIRO_START_URL, KIRO_SCOPES, KIRO_ISSUER_URL
    reg_body = json.dumps({
        "clientName": "kiro-oauth-client",
        "clientType": "public",
        "scopes": KIRO_SCOPES,
        "grantTypes": [
            "urn:ietf:params:oauth:grant-type:device_code",
            "refresh_token",
        ],
        "issuerUrl": KIRO_ISSUER_URL,
    }).encode()
    reg_req = urllib.request.Request(
        f"{KIRO_SSO_OIDC_ENDPOINT}/client/register",
        data=reg_body, headers={"Content-Type": "application/json"})
    reg_resp = urllib.request.urlopen(reg_req, timeout=30)
    reg_data = json.loads(reg_resp.read())
    client_id = reg_data.get("clientId", "")
    client_secret = reg_data.get("clientSecret", "")
    if not client_id:
        raise ValueError("No clientId received from Kiro OIDC")
    return client_id, client_secret


def kiro_start_device_auth(client_id, client_secret):
    """Start device authorization. Returns dict with deviceCode, userCode, verificationUri, etc."""
    from codex_launcher_lib import KIRO_SSO_OIDC_ENDPOINT, KIRO_START_URL
    auth_body = json.dumps({
        "clientId": client_id,
        "clientSecret": client_secret,
        "startUrl": KIRO_START_URL,
    }).encode()
    auth_req = urllib.request.Request(
        f"{KIRO_SSO_OIDC_ENDPOINT}/device_authorization",
        data=auth_body, headers={"Content-Type": "application/json"})
    auth_resp = urllib.request.urlopen(auth_req, timeout=30)
    return json.loads(auth_resp.read())


def kiro_poll_token(client_id, client_secret, device_code, interval=5, timeout=600):
    """Poll for Kiro device token. Returns token dict or raises."""
    from codex_launcher_lib import KIRO_SSO_OIDC_ENDPOINT
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        try:
            poll_body = json.dumps({
                "clientId": client_id,
                "clientSecret": client_secret,
                "deviceCode": device_code,
                "grantType": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode()
            poll_req = urllib.request.Request(
                f"{KIRO_SSO_OIDC_ENDPOINT}/token",
                data=poll_body, headers={"Content-Type": "application/json"})
            poll_resp = urllib.request.urlopen(poll_req, timeout=30)
            return json.loads(poll_resp.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                err_code = json.loads(err_body).get("error", "")
            except Exception:
                err_code = ""
            if err_code == "authorization_pending":
                continue
            elif err_code == "slow_down":
                time.sleep(5)
                continue
            elif err_code == "expired_token":
                raise ValueError("Code expired. Please try again.")
            else:
                raise ValueError(f"Kiro auth error: {err_body[:200]}")
        except Exception:
            continue
    raise TimeoutError("Timed out waiting for Kiro authorization.")


def kiro_extract_email(access_token):
    """Extract email from Kiro JWT access token."""
    try:
        payload_b64 = access_token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        jwt_payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return jwt_payload.get("email", jwt_payload.get("username", ""))
    except Exception:
        return ""


def kiro_save_token(access_token, refresh_token, client_id=None, client_secret=None,
                    expires_in=3600, email="", provider_kind="kiro-builder-id",
                    profile_arn=None):
    """Save Kiro token to disk. Returns token dict."""
    token_path = str(PROXY_CONFIG_DIR / "kiro-oauth-token.json")
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    token_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + expires_in,
        "region": "us-east-1",
        "email": email,
        "provider_kind": provider_kind,
    }
    if client_id:
        token_data["client_id"] = client_id
    if client_secret:
        token_data["client_secret"] = client_secret
    if profile_arn:
        token_data["profileArn"] = profile_arn
    with open(token_path, "w") as f:
        json.dump(token_data, f, indent=2)
    return token_data


def kiro_validate_refresh_token(raw_token):
    """Validate a Kiro refresh token by calling refreshToken endpoint.

    Returns dict with access_token, refresh_token, expires_in, email, profile_arn.
    Raises on failure.
    """
    from codex_launcher_lib import KIRO_AUTH_SERVICE
    data = json.dumps({"refreshToken": raw_token}).encode()
    req = urllib.request.Request(f"{KIRO_AUTH_SERVICE}/refreshToken",
        data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    access_token = result.get("accessToken", "")
    if not access_token:
        raise ValueError("No access token in response")
    refresh_token = result.get("refreshToken", raw_token)
    expires_in = result.get("expiresIn", 3600)
    email = kiro_extract_email(access_token)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "email": email,
        "profile_arn": result.get("profileArn", ""),
    }


# ═══════════════════════════════════════════════════════════════════════
# Kiro OAuth full GUI flow (device code + import token)
# ═══════════════════════════════════════════════════════════════════════

def kiro_oauth_flow(parent_widget, on_token=None):
    """Show Kiro OAuth dialog with device code + import token methods.

    Args:
        parent_widget: Parent tk.Toplevel or tk.Tk for the dialog.
        on_token: Optional callback(access_token) invoked on success.
                  If None, the dialog simply saves the token to disk.
    """
    import tkinter as tk
    from tkinter import ttk, messagebox
    from codex_launcher_lib import open_url

    dlg = tk.Toplevel(parent_widget)
    dlg.title("Kiro — AWS CodeWhisperer Login")
    dlg.geometry("560x420")
    dlg.transient(parent_widget)
    dlg.grab_set()

    tk.Label(dlg, text="Kiro (AWS CodeWhisperer)", font=("Segoe UI", 12, "bold")).pack(padx=16, pady=(12, 4), anchor="w")
    tk.Label(dlg, text="Choose an authentication method:", foreground="gray").pack(padx=16, anchor="w")

    btn_frame = ttk.Frame(dlg)
    btn_frame.pack(padx=16, pady=(12, 8), fill="x")

    status_var = tk.StringVar(value="")
    status_lbl = tk.Label(dlg, textvariable=status_var, wraplength=500, justify="left")
    status_lbl.pack(padx=16, pady=(4, 4), anchor="w")

    code_var = tk.StringVar(value="")
    code_lbl = tk.Label(dlg, textvariable=code_var, font=("Consolas", 14, "bold"),
                         foreground="#0066cc", wraplength=500)
    code_lbl.pack(padx=16, pady=(4, 4), anchor="w")

    link_var = tk.StringVar(value="")
    link_lbl = tk.Label(dlg, textvariable=link_var, fg="blue", cursor="hand2", wraplength=500)
    link_lbl.pack(padx=16, anchor="w")

    # ── Method 1: AWS Builder ID (Device Code Flow) ──
    def _start_device_code():
        status_var.set("Registering OIDC client...")
        code_var.set("")
        link_var.set("")

        def _thread():
            try:
                client_id, client_secret = kiro_register_client()

                auth_data = kiro_start_device_auth(client_id, client_secret)
                device_code = auth_data.get("deviceCode", "")
                user_code = auth_data.get("userCode", "")
                verification_uri = auth_data.get("verificationUri", "")
                verification_uri_complete = auth_data.get("verificationUriComplete", verification_uri)
                interval = auth_data.get("interval", 5)

                if not device_code or not user_code:
                    parent_widget.after(0, lambda: status_var.set("Error: No device code received"))
                    return

                def _show_code():
                    status_var.set("Enter this code in your browser:")
                    code_var.set(user_code)
                    link_var.set(verification_uri_complete)
                    link_lbl.bind("<Button-1>", lambda e: open_url(verification_uri_complete))
                parent_widget.after(0, _show_code)
                open_url(verification_uri_complete)

                poll_data = kiro_poll_token(client_id, client_secret, device_code, interval=interval, timeout=600)

                access_token = poll_data.get("accessToken", "")
                refresh_token = poll_data.get("refreshToken", "")
                expires_in = poll_data.get("expiresIn", 3600)
                email = kiro_extract_email(access_token)

                kiro_save_token(access_token, refresh_token, client_id, client_secret,
                                expires_in, email, "kiro-builder-id")

                def _success():
                    status_var.set(f"Authorized! Logged in as {email or 'OK'}")
                    code_var.set("")
                    link_var.set("")
                    if on_token:
                        on_token(access_token)
                    parent_widget.after(2000, dlg.destroy)
                parent_widget.after(0, _success)
            except Exception as e:
                parent_widget.after(0, lambda: status_var.set(f"Error: {str(e)[:200]}"))

        threading.Thread(target=_thread, daemon=True).start()

    ttk.Button(btn_frame, text="AWS Builder ID", command=_start_device_code).pack(side="left", padx=(0, 8))

    # ── Method 2: Import Token ──
    import_frame = ttk.Frame(dlg)
    import_frame.pack(padx=16, pady=(8, 4), fill="x")
    ttk.Label(import_frame, text="Or paste a refresh token:").pack(anchor="w")
    import_entry = ttk.Entry(import_frame, width=60, show="*")
    import_entry.pack(fill="x", pady=(4, 4))

    def _import_token():
        raw = import_entry.get().strip()
        if not raw:
            messagebox.showwarning("Kiro", "Paste a refresh token first.", parent=dlg)
            return
        if not raw.startswith("aor"):
            messagebox.showwarning("Kiro", "Token should start with 'aor' (AWS OIDC format).", parent=dlg)
            return
        status_var.set("Validating token...")

        def _validate():
            try:
                result = kiro_validate_refresh_token(raw)
                access_token = result["access_token"]
                email = result["email"]
                kiro_save_token(access_token, result["refresh_token"],
                                expires_in=result["expires_in"], email=email,
                                provider_kind="kiro-imported",
                                profile_arn=result.get("profile_arn"))

                def _success():
                    status_var.set(f"Token imported! Logged in as {email or 'OK'}")
                    import_entry.delete(0, "end")
                    if on_token:
                        on_token(access_token)
                    parent_widget.after(2000, dlg.destroy)
                parent_widget.after(0, _success)
            except Exception as e:
                parent_widget.after(0, lambda: status_var.set(f"Validation failed: {str(e)[:200]}"))

        threading.Thread(target=_validate, daemon=True).start()

    ttk.Button(import_frame, text="Import Token", command=_import_token).pack(anchor="w")


# ═══════════════════════════════════════════════════════════════════════
# Unified Google and Codebuff GUI popup flows
# ═══════════════════════════════════════════════════════════════════════

def google_oauth_flow(parent_widget, oauth_provider="google-cli", on_token=None):
    """Show Google OAuth dialog with web server callback loop."""
    import tkinter as tk
    from tkinter import ttk
    import http.server
    from codex_launcher_lib import open_url

    params = google_oauth_build_params(oauth_provider)

    oauth_dlg = tk.Toplevel(parent_widget)
    oauth_dlg.title(f"OAuth: {'Antigravity' if params['is_antigravity'] else 'Gemini CLI'}")
    oauth_dlg.geometry("520x220")
    oauth_dlg.transient(parent_widget)
    oauth_dlg.grab_set()

    tk.Label(oauth_dlg, text=f"Authenticating {'Antigravity' if params['is_antigravity'] else 'Gemini CLI'}",
             font=("Segoe UI", 11, "bold")).pack(padx=16, pady=(12, 0), anchor="w")
    tk.Label(oauth_dlg, text=f"Using OAuth credentials from {OAUTH_SECRETS_PATH}", foreground="gray").pack(padx=16, anchor="w")

    link_lbl = tk.Label(oauth_dlg, text="Click here to open Google authorization", fg="blue", cursor="hand2")
    link_lbl.pack(padx=16, pady=(8, 0), anchor="w")
    link_lbl.bind("<Button-1>", lambda e: open_url(params["auth_url"]))

    status_var = tk.StringVar(value="Opening browser...")
    tk.Label(oauth_dlg, textvariable=status_var).pack(padx=16, pady=(8, 0), anchor="w")

    code_holder = [None]
    error_holder = [None]

    class OAuthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self2):
            qs = urllib.parse.urlparse(self2.path).query
            p = urllib.parse.parse_qs(qs)
            if self2.path.find(params["callback_path"]) == -1:
                self2.send_response(302)
                self2.send_header("Location", "https://developers.google.com/gemini-code-assist/auth_failure_gemini")
                self2.end_headers()
                error_holder[0] = "unexpected request"
                return
            if "code" in p:
                if p.get("state", [None])[0] != params["state"]:
                    self2.send_response(400)
                    self2.send_header("Content-Type", "text/html")
                    self2.end_headers()
                    self2.wfile.write(b"<html><body><h2>CSRF state mismatch.</h2></body></html>")
                    error_holder[0] = "CSRF state mismatch"
                    return
                code_holder[0] = p["code"][0]
                self2.send_response(302)
                self2.send_header("Location", "https://developers.google.com/gemini-code-assist/auth_success_gemini")
                self2.end_headers()
            else:
                error_holder[0] = p.get("error", ["unknown"])[0]
                self2.send_response(302)
                self2.send_header("Location", "https://developers.google.com/gemini-code-assist/auth_failure_gemini")
                self2.end_headers()
        def log_message(self2, fmt, *args):
            pass

    try:
        bind_host = "localhost" if params["is_antigravity"] else "127.0.0.1"
        server = http.server.HTTPServer((bind_host, params["port"]), OAuthHandler)
    except OSError:
        status_var.set(f"Port {params['port']} already in use -- close other apps and retry.")
        return

    def wait_for_code():
        deadline = time.time() + 120
        while code_holder[0] is None and error_holder[0] is None and time.time() < deadline:
            server.handle_request()
        server.server_close()
        if code_holder[0]:
            try:
                tokens = google_oauth_exchange_code(params, code_holder[0])
                save_google_tokens(params, tokens)
                found_models = get_google_models(params["is_antigravity"])
                if found_models:
                    tokens["available_models"] = found_models
                    with open(params["token_path"], "w") as f3:
                        json.dump(tokens, f3, indent=2)
                
                email = tokens.get("email", "")
                proj = tokens.get("project_id", "")
                parent_widget.after(0, lambda: status_var.set(f"Authorized! Project: {proj or 'OK'}"))
                if on_token:
                    parent_widget.after(0, lambda: on_token(tokens.get("access_token", "")))
                parent_widget.after(2000, oauth_dlg.destroy)
            except Exception as e:
                parent_widget.after(0, lambda: status_var.set(f"Failed: {str(e)[:200]}"))
        else:
            parent_widget.after(0, lambda: status_var.set(f"Failed: {error_holder[0] or 'No code received'}"))

    threading.Thread(target=wait_for_code, daemon=True).start()
    open_url(params["auth_url"])


def codebuff_oauth_flow(parent_widget, on_token=None):
    """Show Codebuff / Freebuff login dialog."""
    import tkinter as tk
    from tkinter import ttk
    from codex_launcher_lib import open_url

    oauth_dlg = tk.Toplevel(parent_widget)
    oauth_dlg.title("Freebuff / Codebuff Login")
    oauth_dlg.geometry("520x240")
    oauth_dlg.transient(parent_widget)
    oauth_dlg.grab_set()

    tk.Label(oauth_dlg, text="Sign in with GitHub via Codebuff", font=("Segoe UI", 11, "bold")).pack(padx=16, pady=(12, 0), anchor="w")
    status_var = tk.StringVar(value="Requesting login URL...")
    tk.Label(oauth_dlg, textvariable=status_var).pack(padx=16, pady=(8, 0), anchor="w")

    link_lbl = tk.Label(oauth_dlg, text="", fg="blue", cursor="hand2")
    link_lbl.pack(padx=16, anchor="w")

    result = {"success": False, "user": None, "error": None}

    def _thread():
        try:
            login_url, fp_id, fp_hash, expires_at = codebuff_request_login()
            def _set():
                status_var.set("Open this URL in your browser to log in:")
                link_lbl.configure(text=login_url)
                link_lbl.bind("<Button-1>", lambda e: open_url(login_url))
            parent_widget.after(0, _set)
            open_url(login_url)
            user = codebuff_poll_status(fp_id, fp_hash, expires_at, timeout=300)
            if user:
                result["success"] = True
                result["user"] = user
                parent_widget.after(0, _done)
                return
            result["error"] = "Timed out"
        except Exception as e:
            result["error"] = str(e)[:200]
        parent_widget.after(0, _done)

    def _done():
        if result["success"] and result["user"]:
            u = result["user"]
            codebuff_save_credentials(u)
            status_var.set(f"Logged in as {u.get('email', 'OK')}")
            link_lbl.configure(text="")
            if on_token:
                on_token(u.get("authToken", ""))
            parent_widget.after(2000, oauth_dlg.destroy)
        else:
            status_var.set(f"Failed: {result.get('error', 'unknown')}")

    threading.Thread(target=_thread, daemon=True).start()

