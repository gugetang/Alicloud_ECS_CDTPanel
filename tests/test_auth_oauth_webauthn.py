"""首次登录 / OAuth / WebAuthn 指纹登录。"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def auth_headers(client):
    """取一个可用的管理员 token（不依赖用例执行顺序）：
    先试当前密码，再试初始密码，最后走首次设置。"""
    for pw in ("newpass123", "secret123"):
        r = client.post("/api/auth/login", json={"username": "管理员", "password": pw})
        if r.status_code == 200:
            return {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_status_unconfigured(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["admin_configured"] is False
    assert r.json()["oauth"] == {"google": False, "github": False}
    assert r.json()["webauthn"] is False


def test_setup_validation_and_flow(client):
    r = client.post("/api/auth/setup", json={"password": "123"})
    assert r.status_code == 400  # 密码太短
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token
    # 重复设置拒绝
    assert client.post("/api/auth/setup", json={"password": "other123"}).status_code == 400
    # 状态：已配置 + 管理员名
    s = client.get("/api/auth/status").json()
    assert s["admin_configured"] is True
    assert s["admin_name"] == "管理员"
    # 未带 token 访问受保护接口 → 401
    assert client.get("/api/status").status_code == 401
    # 带 token 正常
    r = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_login_and_change_password(client):
    h = auth_headers(client)
    # 改密码前先确认当前密码：用例按顺序跑时是 secret123，单独跑时也一样
    cur = "secret123" if client.post(
        "/api/auth/login", json={"username": "管理员", "password": "secret123"}).status_code == 200 else "newpass123"
    assert client.post("/api/auth/login", json={"username": "管理员", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "管理员", "password": cur})
    assert r.status_code == 200
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    # 改密码：当前密码错 → 400
    assert client.post("/api/auth/change-password",
                       json={"current_password": "nope", "new_password": "newpass123"},
                       headers=h).status_code == 400
    r = client.post("/api/auth/change-password",
                    json={"current_password": cur, "new_password": "newpass123"},
                    headers=h)
    assert r.status_code == 200
    # 新密码能登录，旧密码不行
    assert client.post("/api/auth/login", json={"username": "管理员", "password": cur}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "管理员", "password": "newpass123"}).status_code == 200
    # 登出后 token 失效
    tok = client.post("/api/auth/login", json={"username": "管理员", "password": "newpass123"}).json()["token"]
    client.post("/api/auth/logout", cookies={"cdt_session": tok})
    assert client.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_oauth_login_requires_config(client):
    # 未配置 → 400
    assert client.get("/api/auth/oauth/google/login").status_code == 400
    assert client.get("/api/auth/oauth/github/login").status_code == 400
    h = auth_headers(client)
    # 配置 Google（Secret 存明文兼容，_oauth_configured 能读出）
    r = client.put("/api/settings", json={
        "google_oauth_client_id": "google-id-123",
        "google_oauth_client_secret": "google-secret-123"}, headers=h)
    assert r.status_code == 200
    # GET 不回显 Secret，但有 configured 标志
    s = client.get("/api/settings", headers=h).json()
    assert "google_oauth_client_secret" not in s
    assert s["google_oauth_configured"] is True
    # 登录跳转到 Google 授权页
    r = client.get("/api/auth/oauth/google/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=google-id-123" in r.headers["location"]
    # 回调 state 非法 → 跳回首页带错
    r = client.get("/api/auth/oauth/google/callback?code=x&state=bad", follow_redirects=False)
    assert r.status_code == 302
    assert "oauth_error=invalid_state" in r.headers["location"]


def test_webauthn_begin_and_finish_errors(client):
    h = auth_headers(client)
    # 未注册任何指纹 → authenticate/begin 400
    assert client.post("/api/auth/webauthn/authenticate/begin", json={}).status_code == 400
    # register/begin 返回 options（含 challenge）
    r = client.post("/api/auth/webauthn/register/begin", json={}, headers=h)
    assert r.status_code == 200
    assert r.json()["challenge"]
    assert r.json()["rp"]["id"]
    # 伪造凭证 → 400（格式错误或验证失败）
    assert client.post("/api/auth/webauthn/register/finish",
                       json={"credential": {"id": "x", "response": {}}},
                       headers=h).status_code == 400
    assert client.post("/api/auth/webauthn/authenticate/finish",
                       json={"credential": {"id": "x", "response": {}}}).status_code == 400
    # 列表为空
    assert client.get("/api/auth/webauthn/list", headers=h).json() == []


def test_notify_channel_param(client):
    h = auth_headers(client)
    r = client.post("/api/notify/test", json={"event": "error", "channel": "email"}, headers=h)
    assert r.status_code == 200 and r.json()["sent"] == []  # 邮件未启用
    r = client.post("/api/notify/test-daily", json={"channel": "telegram"}, headers=h)
    assert r.status_code == 200 and isinstance(r.json()["sent"], list)

def _current_password(client):
    """当前管理员密码（本文件前面的用例可能已把它改成 newpass123）。"""
    name = client.get("/api/auth/status").json().get("admin_name") or ""
    for pw in ("newpass123", "secret123"):
        body = {"password": pw}
        if name:
            body["username"] = name
        if client.post("/api/auth/login", json=body).status_code == 200:
            return pw
    raise AssertionError("no working password")


def test_login_username_checked(client):
    """登录页账号框：账号是真校验，不是摆设。"""
    from app import db

    auth_headers(client)  # 确保已走首次设置
    cur = _current_password(client)
    name = client.get("/api/auth/status").json()["admin_name"]
    assert name == "管理员"
    # 账号正确 → 200
    r = client.post("/api/auth/login", json={"username": name, "password": cur})
    assert r.status_code == 200 and r.json()["token"]
    # 账号错误 → 401，且提示与密码错误同一条，不泄露是哪一项错了
    r = client.post("/api/auth/login", json={"username": "不是管理员", "password": cur})
    assert r.status_code == 401
    assert r.json()["detail"] == "账号或密码错误"
    r = client.post("/api/auth/login", json={"username": name, "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "账号或密码错误"
    # 成功登录一次，清掉上面两次失败计数（5 次失败会锁定 15 分钟）
    assert client.post("/api/auth/login", json={"username": name, "password": cur}).status_code == 200
    # 未设置 admin_name（老用户）：只校验密码，不带账号能登录，错账号也不拦
    db.set_flag("admin_name", "")
    try:
        assert client.post("/api/auth/login", json={"password": cur}).status_code == 200
        assert client.post("/api/auth/login", json={"username": "随便填", "password": cur}).status_code == 200
    finally:
        db.set_flag("admin_name", name)


# ---------------------------------------------------------------------------
# 回归：指纹登录（WebAuthn）与 GitHub 登录
# ---------------------------------------------------------------------------
import base64 as _b64
import hashlib as _hashlib
import json as _json

import cbor2 as _cbor2
from cryptography.hazmat.primitives import hashes as _hashes
from cryptography.hazmat.primitives.asymmetric import ec as _ec


def _b64e(b: bytes) -> str:
    return _b64.urlsafe_b64encode(b).decode().rstrip("=")


def test_webauthn_full_roundtrip(client):
    """软件认证器走完注册+登录：注册带 transports 时登录 begin 不能 500，
    登录 finish 必须校验通过并返回可用 token。"""
    h = auth_headers(client)
    host = "cdt.kadi.eu.org"
    origin = "https://cdt.kadi.eu.org"
    hh = {"Host": host, "Origin": origin, "X-Forwarded-Proto": "https",
          "X-Forwarded-For": "1.2.3.4", **h}
    # 注册
    r = client.post("/api/auth/webauthn/register/begin", json={}, headers=hh)
    assert r.status_code == 200, r.text
    opts = r.json()
    assert opts["rp"]["id"] == host
    priv = _ec.generate_private_key(_ec.SECP256R1())
    pub = priv.public_key().public_numbers()
    cose_key = {1: 2, 3: -7, -1: 1, -2: pub.x.to_bytes(32, "big"),
                -3: pub.y.to_bytes(32, "big")}
    cred_id = os.urandom(16)
    rp_hash = _hashlib.sha256(host.encode()).digest()
    auth_data = (rp_hash + bytes([0x45]) + (0).to_bytes(4, "big")
                 + b"\x00" * 16 + len(cred_id).to_bytes(2, "big") + cred_id
                 + _cbor2.dumps(cose_key))
    client_data = {"type": "webauthn.create", "challenge": opts["challenge"],
                   "origin": origin, "crossOrigin": False}
    client_data_json = _json.dumps(client_data, separators=(",", ":")).encode()
    att_obj = _cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
    credential = {"id": _b64e(cred_id), "rawId": _b64e(cred_id), "type": "public-key",
                  "response": {"clientDataJSON": _b64e(client_data_json),
                               "attestationObject": _b64e(att_obj),
                               "transports": ["internal"]}}
    r = client.post("/api/auth/webauthn/register/finish",
                    json={"credential": credential, "label": "指纹"}, headers=hh)
    assert r.status_code == 200, r.text
    # 登录 begin：transports 是字符串存库的，旧代码在这里 500
    r = client.post("/api/auth/webauthn/authenticate/begin", json={},
                    headers={"Host": host, "Origin": origin,
                             "X-Forwarded-Proto": "https",
                             "X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200, r.text
    opts = r.json()
    assert opts["allowCredentials"][0]["transports"] == ["internal"]
    # 登录 finish：WebAuthn 断言签名是 DER 编码，直接传给校验
    client_data = {"type": "webauthn.get", "challenge": opts["challenge"],
                   "origin": origin, "crossOrigin": False}
    client_data_json = _json.dumps(client_data, separators=(",", ":")).encode()
    auth_data = rp_hash + bytes([0x05]) + (1).to_bytes(4, "big")
    sig = priv.sign(auth_data + _hashlib.sha256(client_data_json).digest(),
                    _ec.ECDSA(_hashes.SHA256()))
    credential = {"id": _b64e(cred_id), "rawId": _b64e(cred_id), "type": "public-key",
                  "response": {"clientDataJSON": _b64e(client_data_json),
                               "authenticatorData": _b64e(auth_data),
                               "signature": _b64e(sig)}}
    r = client.post("/api/auth/webauthn/authenticate/finish",
                    json={"credential": credential},
                    headers={"Host": host, "Origin": origin,
                             "X-Forwarded-Proto": "https",
                             "X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert client.get("/api/status",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_github_login_redirect_uri_https_without_forwarded_proto(client):
    """反代没带 X-Forwarded-Proto 时，GitHub 回调地址也得是 https，
    不能回退成 http（否则 GitHub 报 redirect_uri_mismatch）。"""
    h = auth_headers(client)
    client.put("/api/settings", json={
        "github_oauth_client_id": "Iv1.test123",
        "github_oauth_client_secret": "secret-test"}, headers=h)
    r = client.get("/api/auth/oauth/github/login",
                   headers={"Host": "cdt.kadi.eu.org", "X-Forwarded-For": "1.2.3.4"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert ("redirect_uri=https%3A%2F%2Fcdt.kadi.eu.org%2Fapi%2Fauth%2F"
            "oauth%2Fgithub%2Fcallback") in r.headers["location"]
    # Referer 是本站登录页时也一样
    r = client.get("/api/auth/oauth/github/login",
                   headers={"Host": "cdt.kadi.eu.org",
                            "Referer": "https://cdt.kadi.eu.org/"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert "redirect_uri=https%3A%2F%2Fcdt.kadi.eu.org" in r.headers["location"]


def test_github_callback_reuses_login_redirect_uri(client):
    """回调换 token 必须用登录时发给 GitHub 的那个 redirect_uri，
    跳回的地址也用登录时那个 origin（回调的 Referer 是 github.com，
    不能拿它现算）。"""
    from unittest.mock import patch
    from urllib.parse import parse_qs, urlparse

    h = auth_headers(client)
    client.put("/api/settings", json={
        "github_oauth_client_id": "Iv1.test123",
        "github_oauth_client_secret": "secret-test"}, headers=h)
    r = client.get("/api/auth/oauth/github/login",
                   headers={"Host": "cdt.kadi.eu.org", "X-Forwarded-For": "1.2.3.4"},
                   follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    with patch("app.oauth.exchange_code",
               return_value={"access_token": "tok"}) as m_ex, \
         patch("app.oauth.fetch_userinfo",
               return_value={"sub": "123", "email": "a@b.c", "name": "octo"}):
        r = client.get(
            f"/api/auth/oauth/github/callback?code=code123&state={state}",
            headers={"Host": "cdt.kadi.eu.org",
                     "Referer": "https://github.com/"},
            follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith("https://cdt.kadi.eu.org/?token="), loc
    token = loc.split("token=")[1]
    assert client.get("/api/status",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert m_ex.call_args.args[4] == (
        "https://cdt.kadi.eu.org/api/auth/oauth/github/callback")


# ---------------------------------------------------------------------------
# 回归：Google / GitHub 分渠道绑定 + 解绑
# ---------------------------------------------------------------------------
def _oauth_login(client, provider, sub, name="OAuth User"):
    """走一遍 OAuth 登录回调（mock 换 token/取用户信息），返回回调响应。"""
    from unittest.mock import patch
    from urllib.parse import parse_qs, urlparse

    h = auth_headers(client)
    client.put("/api/settings", json={
        f"{provider}_oauth_client_id": "Iv1.test123",
        f"{provider}_oauth_client_secret": "secret-test"}, headers=h)
    r = client.get(f"/api/auth/oauth/{provider}/login",
                   headers={"Host": "cdt.kadi.eu.org", "X-Forwarded-For": "1.2.3.4"},
                   follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    with patch("app.oauth.exchange_code",
               return_value={"access_token": "tok"}), \
         patch("app.oauth.fetch_userinfo",
               return_value={"sub": sub, "email": "a@b.c", "name": name}):
        return client.get(
            f"/api/auth/oauth/{provider}/callback?code=code123&state={state}",
            headers={"Host": "cdt.kadi.eu.org",
                     "Referer": "https://github.com/"},
            follow_redirects=False)


def test_oauth_providers_bind_independently(client):
    """Google 和 GitHub 各绑各的：绑了 Google 之后 GitHub 也能登；
    同渠道换账号仍拒绝，不同渠道互不干扰。"""
    from app import db

    db.delete_flag("admin_oauth_identity:google")
    db.delete_flag("admin_oauth_identity:github")
    db.delete_flag("admin_oauth_identity")
    r = _oauth_login(client, "google", "google-1")
    assert r.status_code == 302 and "/?token=" in r.headers["location"]
    r = _oauth_login(client, "github", "github-1")
    assert r.status_code == 302 and "/?token=" in r.headers["location"]
    r = _oauth_login(client, "google", "google-2")
    assert r.status_code == 302 and "oauth_error=identity_mismatch" in r.headers["location"]
    r = _oauth_login(client, "google", "google-1")
    assert r.status_code == 302 and "/?token=" in r.headers["location"]


def test_oauth_legacy_single_binding_migrates(client):
    """旧版单绑定 admin_oauth_identity=google:xxx：Google 用旧身份能登，
    之后自动迁到分渠道键；GitHub 第一次登直接绑新渠道。"""
    from app import db

    db.delete_flag("admin_oauth_identity:google")
    db.delete_flag("admin_oauth_identity:github")
    db.set_flag("admin_oauth_identity", "google:legacy-1")
    r = _oauth_login(client, "google", "legacy-1")
    assert r.status_code == 302 and "/?token=" in r.headers["location"]
    assert db.get_flag("admin_oauth_identity:google") == "legacy-1"
    assert db.get_flag("admin_oauth_identity") == ""
    r = _oauth_login(client, "github", "github-9")
    assert r.status_code == 302 and "/?token=" in r.headers["location"]


def test_oauth_unbind_and_rebind(client):
    """解绑后该渠道能重新绑定新账号；解绑接口要登录才能调；
    设置接口返回各渠道的绑定状态。"""
    from app import db

    h = auth_headers(client)
    db.set_flag("admin_oauth_identity:github", "github-1")
    r = client.post("/api/auth/oauth/github/unbind", headers=h)
    assert r.status_code == 200 and r.json()["bound"] is False
    assert db.get_flag("admin_oauth_identity:github") == ""
    s = client.get("/api/settings", headers=h).json()
    assert s["github_oauth_bound"] is False
    r = _oauth_login(client, "github", "github-2")
    assert r.status_code == 302 and "/?token=" in r.headers["location"]
    s = client.get("/api/settings", headers=h).json()
    assert s["github_oauth_bound"] is True
    assert client.post("/api/auth/oauth/github/unbind").status_code == 401


def test_oauth_unbind_keeps_one_admin_login(client):
    """解绑不许把最后一种管理员登录方式也解掉，否则面板回到首次设置状态。
    注意：本用例必须放在文件最后——它会清掉密码和指纹。"""
    from app import db
    from app.security import hash_password

    h = auth_headers(client)
    # 只留 Google 一个绑定：清掉密码和指纹
    db.delete_flag("admin_password_hash")
    for c in client.get("/api/auth/webauthn/list", headers=h).json():
        client.delete(f"/api/auth/webauthn/{c['credential_id']}", headers=h)
    db.set_flag("admin_oauth_identity:google", "g1")
    db.set_flag("admin_oauth_identity:github", "h1")
    # 还剩 Google，能解绑 GitHub
    r = client.post("/api/auth/oauth/github/unbind", headers=h)
    assert r.status_code == 200
    # 只剩 Google 一个了，再解绑 Google 必须拒绝
    r = client.post("/api/auth/oauth/google/unbind", headers=h)
    assert r.status_code == 400
    assert db.get_flag("admin_oauth_identity:google") == "g1"
    # 恢复密码，避免夹具收尾状态异常
    db.set_flag("admin_password_hash", hash_password("newpass123"))
