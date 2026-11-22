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
        r = client.post("/api/auth/login", json={"password": pw})
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
        "/api/auth/login", json={"password": "secret123"}).status_code == 200 else "newpass123"
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"password": cur})
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
    assert client.post("/api/auth/login", json={"password": cur}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "newpass123"}).status_code == 200
    # 登出后 token 失效
    tok = client.post("/api/auth/login", json={"password": "newpass123"}).json()["token"]
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
