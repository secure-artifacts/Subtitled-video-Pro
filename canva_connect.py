# ==========================================
# 文件名: canva_connect.py
# Canva Connect API OAuth 与素材上传工具
# ==========================================
import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

import requests

CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_ASSET_UPLOAD_URL = "https://api.canva.com/rest/v1/asset-uploads"
DEFAULT_CANVA_REDIRECT_URI = "http://127.0.0.1:3001/oauth/redirect"
DEFAULT_CANVA_SCOPES = "asset:read asset:write"


def generate_pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def generate_state():
    return secrets.token_urlsafe(24)


def build_authorization_url(client_id, redirect_uri, scope, code_challenge, state):
    params = {
        "response_type": "code",
        "client_id": str(client_id or "").strip(),
        "redirect_uri": str(redirect_uri or DEFAULT_CANVA_REDIRECT_URI).strip(),
        "scope": str(scope or DEFAULT_CANVA_SCOPES).strip(),
        "code_challenge": str(code_challenge or "").strip(),
        "code_challenge_method": "S256",
        "state": str(state or "").strip(),
    }
    return f"{CANVA_AUTHORIZE_URL}?{urlencode(params)}"


def _basic_auth_header(client_id, client_secret):
    raw = f"{str(client_id or '').strip()}:{str(client_secret or '').strip()}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def exchange_authorization_code(client_id, client_secret, code, code_verifier, redirect_uri):
    data = {
        "grant_type": "authorization_code",
        "code": str(code or "").strip(),
        "code_verifier": str(code_verifier or "").strip(),
        "redirect_uri": str(redirect_uri or DEFAULT_CANVA_REDIRECT_URI).strip(),
    }
    headers = {
        "Authorization": _basic_auth_header(client_id, client_secret),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    res = requests.post(CANVA_TOKEN_URL, headers=headers, data=data, timeout=30)
    if res.status_code >= 400:
        raise RuntimeError(f"Canva token exchange failed {res.status_code}: {res.text[:500]}")
    return res.json()


def refresh_access_token(client_id, client_secret, refresh_token):
    data = {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token or "").strip(),
    }
    headers = {
        "Authorization": _basic_auth_header(client_id, client_secret),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    res = requests.post(CANVA_TOKEN_URL, headers=headers, data=data, timeout=30)
    if res.status_code >= 400:
        raise RuntimeError(f"Canva token refresh failed {res.status_code}: {res.text[:500]}")
    return res.json()


def store_token_response(config, token_data):
    cfg = dict(config or {})
    token_data = dict(token_data or {})
    if token_data.get("access_token"):
        cfg["canva_access_token"] = token_data.get("access_token")
    if token_data.get("refresh_token"):
        cfg["canva_refresh_token"] = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in") or 0)
    if expires_in > 0:
        cfg["canva_token_expires_at"] = int(time.time()) + max(60, expires_in - 60)
    if token_data.get("scope"):
        cfg["canva_scope_granted"] = token_data.get("scope")
    cfg["canva_token_updated_at"] = int(time.time())
    return cfg


def ensure_access_token(config):
    cfg = dict(config or {})
    access_token = str(cfg.get("canva_access_token") or "").strip()
    expires_at = int(cfg.get("canva_token_expires_at") or 0)
    if access_token and (not expires_at or expires_at > int(time.time()) + 30):
        return access_token, cfg
    refresh_token = str(cfg.get("canva_refresh_token") or "").strip()
    if not refresh_token:
        raise RuntimeError("Canva is not authorized yet.")
    token_data = refresh_access_token(cfg.get("canva_client_id"), cfg.get("canva_client_secret"), refresh_token)
    cfg = store_token_response(cfg, token_data)
    return str(cfg.get("canva_access_token") or "").strip(), cfg


def upload_asset(config, file_path, asset_name=None, timeout=180):
    token, cfg = ensure_access_token(config)
    path = str(file_path or "")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    name = str(asset_name or os.path.basename(path) or "subtitle.webm").strip()
    metadata = {
        "name_base64": base64.b64encode(name.encode("utf-8")).decode("ascii"),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "Asset-Upload-Metadata": json.dumps(metadata, ensure_ascii=False),
    }
    with open(path, "rb") as f:
        res = requests.post(CANVA_ASSET_UPLOAD_URL, headers=headers, data=f, timeout=timeout)
    if res.status_code >= 400:
        raise RuntimeError(f"Canva asset upload failed {res.status_code}: {res.text[:500]}")
    try:
        payload = res.json()
    except Exception:
        payload = {"raw": res.text}
    return payload, cfg
