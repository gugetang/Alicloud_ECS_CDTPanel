"""Google / GitHub OAuth 第三方登录（授权码模式）。

流程：登录页点按钮 → GET /api/auth/oauth/{provider}/login → 302 到对方授权页
→ 对方回调 GET /api/auth/oauth/{provider}/callback?code&state → 换 token、
取用户信息 → 首次登录绑定为管理员身份，之后只允许同一身份登录 → 发
cdt 会话 token，302 回前端 /?token=...。

Client ID / Secret 由用户在 Google Cloud Console / GitHub Settings 里创建
OAuth 应用后填到全局设置“登录方式”里（Secret 加密存储，读出不回显）；
未配置时登录页对应按钮置灰。回调地址固定为 {origin}/api/auth/oauth/{provider}/callback，
建 OAuth 应用时按这个填（origin 即面板访问地址，如 http://服务器:1999）。
"""
from __future__ import annotations

import secrets
import time
import urllib.parse

import httpx

PROVIDERS = ("google", "github")

AUTHORIZE_URLS = {
    "google": "https://accounts.google.com/o/oauth2/v2/auth",
    "github": "https://github.com/login/oauth/authorize",
}
TOKEN_URLS = {
    "google": "https://oauth2.googleapis.com/token",
    "github": "https://github.com/login/oauth/access_token",
}
USERINFO_URLS = {
    "google": "https://openidconnect.googleapis.com/v1/userinfo",
    "github": "https://api.github.com/user",
}
SCOPES = {
    "google": "openid email profile",
    "github": "user:email",
}

# state -> {"provider": str, "created": float,
#            "redirect_uri": str, "origin": str}，10 分钟有效，防 CSRF。
# 登录时把 redirect_uri 和 origin 一起记下来：回调换 token 必须用登录时
# 那个 redirect_uri（GitHub 要求两次一致），成功/失败跳回的地址也用
# 登录时那个——两次请求的头可能不一样（回调是从 github.com 跳回来的），
# 各自现算可能算出两个地址。
_states: dict[str, dict] = {}


def new_state(provider: str, redirect_uri: str, origin: str) -> str:
    state = secrets.token_urlsafe(24)
    _states[state] = {"provider": provider, "created": time.time(),
                      "redirect_uri": redirect_uri, "origin": origin}
    return state


def take_state(state: str, provider: str) -> dict | None:
    """消耗 state；成功返回登录时记下的记录，否则返回 None。"""
    rec = _states.pop(state, None)
    if not rec:
        return None
    if rec.get("provider") != provider:
        return None
    if time.time() - rec.get("created", 0) > 600:
        return None
    return rec


def authorize_url(provider: str, client_id: str, redirect_uri: str) -> str:
    origin = "{scheme}://{netloc}".format(
        scheme=urllib.parse.urlparse(redirect_uri).scheme,
        netloc=urllib.parse.urlparse(redirect_uri).netloc)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES[provider],
        "state": new_state(provider, redirect_uri, origin),
    }
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "select_account"
    return AUTHORIZE_URLS[provider] + "?" + urllib.parse.urlencode(params)


def exchange_code(provider: str, code: str, client_id: str,
                  client_secret: str, redirect_uri: str) -> dict:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if provider == "google":
        data["grant_type"] = "authorization_code"
    with httpx.Client(timeout=20) as client:
        r = client.post(
            TOKEN_URLS[provider], data=data,
            headers={"Accept": "application/json"})
        r.raise_for_status()
        tok = r.json()
    access = tok.get("access_token")
    if not access:
        raise ValueError("换取 access_token 失败")
    return tok


def fetch_userinfo(provider: str, access_token: str) -> dict:
    with httpx.Client(timeout=20) as client:
        r = client.get(
            USERINFO_URLS[provider],
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"})
        r.raise_for_status()
        info = r.json()
    if provider == "google":
        return {"sub": str(info.get("sub") or ""),
                "email": str(info.get("email") or ""),
                "name": str(info.get("name") or info.get("email") or "")}
    # GitHub：公开邮箱可能为空，再取 /user/emails 补一个主邮箱
    email = str(info.get("email") or "")
    if not email:
        with httpx.Client(timeout=20) as client:
            r = client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {access_token}",
                         "Accept": "application/json"})
            if r.status_code == 200:
                for e in r.json():
                    if e.get("primary"):
                        email = str(e.get("email") or "")
                        break
                if not email and r.json():
                    email = str(r.json()[0].get("email") or "")
    return {"sub": str(info.get("id") or ""),
            "email": email,
            "name": str(info.get("login") or info.get("name") or "")}
