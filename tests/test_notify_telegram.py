"""Telegram 推送修复：测试通知跳过事件开关、失败原因回传、连通性自检、
登录页 OAuth 图标与主题色边框。"""
from pathlib import Path

import pytest

from app.notify import Notifier, check_telegram


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""

    def json(self):
        return self._payload


def _settings(**over):
    s = {"telegram_enabled": "1", "telegram_token": "tok123",
         "telegram_chat_id": "123456789", "notify_proxy": "",
         "notify_error": "1"}
    s.update(over)
    return s


def test_send_test_bypasses_event_toggle(monkeypatch):
    """“发送测试通知”走 event=error：即使 notify_error 开关关了，
    force=True 的测试也要照发（以前被开关拦住，点了也收不到）。"""
    posted: list = []

    def fake_post(url, json=None, proxies=None, timeout=None):
        posted.append(url)
        return FakeResp(200, {"ok": True})

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    n = Notifier(_settings(notify_error="0"))
    # 非 force：事件开关关了，不发
    assert n.send("error", "t", "b") == []
    assert posted == []
    # force（测试）：跳过事件开关，照发
    assert n.send("error", "t", "b", channel="telegram", force=True) == ["telegram"]
    assert len(posted) == 1


def test_telegram_error_detail_returned(monkeypatch):
    """Telegram API 返回非 200 时，把 description 带回来（以前只返回
    False，Token 错 / chat_id 错 / 代理问题全都看不出来）。"""
    def fake_post(url, json=None, proxies=None, timeout=None):
        return FakeResp(401, {"ok": False, "description": "Unauthorized"},
                        text='{"ok":false,"description":"Unauthorized"}')

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    n = Notifier(_settings())
    ok, detail = n._telegram("hi")
    assert ok is False
    assert "401" in detail and "Unauthorized" in detail
    # send() 把失败原因记到 last_errors，供接口回给前端
    assert n.send("error", "t", "b", channel="telegram", force=True) == []
    assert "telegram" in n.last_errors and "Unauthorized" in n.last_errors["telegram"]


def test_telegram_missing_config(monkeypatch):
    n = Notifier(_settings(telegram_token="", telegram_chat_id=""))
    ok, detail = n._telegram("hi")
    assert ok is False and "未配置" in detail


def test_check_telegram_no_config():
    r = check_telegram(_settings(telegram_token="", telegram_chat_id=""))
    assert r["ok"] is False
    assert r["steps"][0]["name"] == "配置" and r["steps"][0]["ok"] is False


def test_check_telegram_ok(monkeypatch):
    def fake_get(url, params=None, proxies=None, timeout=None):
        if url.endswith("/getMe"):
            return FakeResp(200, {"ok": True,
                                  "result": {"username": "cdt_bot", "first_name": "CDT"}})
        if url.endswith("/getChat"):
            return FakeResp(200, {"ok": True,
                                  "result": {"id": 123456789, "first_name": "Me"}})
        return FakeResp(200, {})

    monkeypatch.setattr("app.notify.requests.get", fake_get)
    r = check_telegram(_settings())
    assert r["ok"] is True
    names = [s["name"] for s in r["steps"]]
    assert names == ["配置", "Token", "Chat ID"]
    assert "cdt_bot" in r["steps"][1]["detail"]


def test_check_telegram_bad_token(monkeypatch):
    def fake_get(url, params=None, proxies=None, timeout=None):
        return FakeResp(401, {"ok": False, "description": "Unauthorized"},
                        text='{"ok":false}')

    monkeypatch.setattr("app.notify.requests.get", fake_get)
    r = check_telegram(_settings())
    assert r["ok"] is False
    assert r["steps"][-1]["name"] == "Token" and r["steps"][-1]["ok"] is False


def test_check_telegram_bad_chat(monkeypatch):
    def fake_get(url, params=None, proxies=None, timeout=None):
        if url.endswith("/getMe"):
            return FakeResp(200, {"ok": True, "result": {"username": "cdt_bot"}})
        return FakeResp(400, {"ok": False, "description": "Bad Request: chat not found"},
                        text='{"ok":false}')

    monkeypatch.setattr("app.notify.requests.get", fake_get)
    r = check_telegram(_settings())
    assert r["ok"] is False
    assert r["steps"][-1]["name"] == "Chat ID" and "chat not found" in r["steps"][-1]["detail"]


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def test_api_test_notify_bypasses_toggle(tmpdb, monkeypatch):
    """接口层：notify_error 开关关了，POST /api/notify/test 仍要发 TG。"""
    from fastapi.testclient import TestClient
    from app.main import create_app

    def fake_post(url, json=None, proxies=None, timeout=None):
        return FakeResp(200, {"ok": True})

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    tmpdb.set_settings({"telegram_enabled": "1", "telegram_token": "tok123",
                           "telegram_chat_id": "123", "notify_error": "0"})
    client = TestClient(create_app())
    r = client.post("/api/notify/test", json={"event": "error", "channel": "telegram"})
    assert r.status_code == 200
    assert r.json()["sent"] == ["telegram"]
    assert r.json()["errors"] == {}


def test_api_telegram_check(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    def fake_get(url, params=None, proxies=None, timeout=None):
        if url.endswith("/getMe"):
            return FakeResp(200, {"ok": True, "result": {"username": "cdt_bot"}})
        return FakeResp(200, {"ok": True, "result": {"id": 123, "first_name": "Me"}})

    monkeypatch.setattr("app.notify.requests.get", fake_get)
    tmpdb.set_settings({"telegram_token": "tok123", "telegram_chat_id": "123"})
    client = TestClient(create_app())
    r = client.get("/api/notify/telegram-check")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------- 登录页：OAuth 图标 / 主题色边框 / 去掉（未配置） ----------------

def test_login_oauth_icons_and_no_unconfigured():
    tsx = (Path(__file__).resolve().parent.parent / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # Google / GitHub 登录按钮带图标，不再有“（未配置）”后缀
    assert "使用 Google 登录" in tsx and "使用 GitHub 登录" in tsx
    assert "（未配置）" not in tsx
    # Google G 多色 logo 与 GitHub mark 都在登录按钮里
    assert 'viewBox="0 0 48 48"' in tsx and "#FFC107" in tsx
    assert 'viewBox="0 0 16 16"' in tsx and "M8 0C3.58 0" in tsx


def test_login_button_shows_theme_border():
    css = (Path(__file__).resolve().parent.parent / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 登录按钮不再被盖成透明边框，.btn 自带的 2px 主题色边框直接显示
    assert ".auth-wrap .btn { border-color:transparent; }" not in css
    # 输入框聚焦仍是主题色边框
    assert ".auth-wrap input:focus" in css and "border-color:var(--accent)" in css


# ---------------- 邮件失败原因回传（与 Telegram 同款） ----------------

def test_email_error_surfaces_reason(monkeypatch):
    """_email 返回 (ok, detail)：缺配置给可读原因，SMTP 异常把异常文本带回来，
    而不是只返回 False 让前端猜“未启用或未配置”。"""
    import smtplib

    n = Notifier({"email_enabled": "1", "email_smtp_host": "", "email_to": ""})
    ok, detail = n._email("t", "b")
    assert ok is False and "未配置" in detail

    class BoomSMTP:
        def __init__(self, *a, **k):
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    n2 = Notifier({"email_enabled": "1", "email_smtp_host": "smtp.gmail.com",
                   "email_smtp_port": "465", "email_user": "a@gmail.com",
                   "email_password": "wrong", "email_to": "b@gmail.com"})
    monkeypatch.setattr("app.notify.smtplib.SMTP_SSL", BoomSMTP)
    ok, detail = n2._email("t", "b")
    assert ok is False
    assert "535" in detail or "not accepted" in detail.lower()

    # send() 把邮件失败原因记进 last_errors，接口层能返回给前端
    sent = n2.send("error", "t", "b", channel="email", force=True)
    assert sent == []
    assert "email" in n2.last_errors
    assert "535" in n2.last_errors["email"] or "not accepted" in n2.last_errors["email"].lower()


def test_notify_test_api_returns_email_error(tmpdb, monkeypatch):
    """POST /api/notify/test 返回 errors.email，页面显示真实 SMTP 原因。"""
    from fastapi.testclient import TestClient
    from app.main import create_app

    class BoomSMTP:
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("Connection refused")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("app.notify.smtplib.SMTP_SSL", BoomSMTP)
    tmpdb.set_settings({
        "email_enabled": "1", "email_smtp_host": "smtp.gmail.com",
        "email_smtp_port": "465", "email_user": "a@gmail.com",
        "email_password": "x", "email_to": "b@gmail.com",
    })
    client = TestClient(create_app())
    r = client.post("/api/notify/test", json={"event": "error", "channel": "email"})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] == []
    assert "email" in body["errors"]
    assert "Connection refused" in body["errors"]["email"]


# ---------------- 邮件走代理（SMTP 经 CONNECT 隧道） ----------------

def test_email_via_proxy_connect(monkeypatch):
    """配了通知专用代理时，SMTP 经代理的 CONNECT 隧道发（直连 Gmail 被墙时
    走代理）；没配代理时直连 SMTP_SSL。"""
    from app.notify import Notifier

    class FakeSock:
        def makefile(self, *a, **k):
            import io
            return io.BytesIO(b"220 fake\r\n")
        def close(self):
            pass

    class FakeSMTP:
        def __init__(self, *a, **k):
            self.sock = None
            self.file = None
            self.ehlo_called = False
            self.login_called = False
            self.sent = False
        def ehlo(self, *a, **k):
            self.ehlo_called = True
            return (250, b"OK")
        def login(self, *a, **k):
            self.login_called = True
            return (235, b"OK")
        def send_message(self, *a, **k):
            self.sent = True
            return {}
        def quit(self, *a, **k):
            return (221, b"OK")
        def close(self, *a, **k):
            pass

    made = {}

    def fake_connect(proxy_url, host, port, timeout=15):
        made["proxy"] = (proxy_url, host, port)
        return FakeSock()

    class FakeCtx:
        def wrap_socket(self, sock, server_hostname=None):
            made["tls_host"] = server_hostname
            return sock

    smtp_obj = FakeSMTP()
    monkeypatch.setattr("app.notify._connect_via_proxy", fake_connect)
    monkeypatch.setattr("app.notify.ssl.create_default_context", lambda: FakeCtx())
    monkeypatch.setattr("app.notify.smtplib.SMTP", lambda *a, **k: smtp_obj)

    n = Notifier({"email_enabled": "1", "email_smtp_host": "smtp.gmail.com",
                  "email_smtp_port": "465", "email_user": "a@gmail.com",
                  "email_password": "x", "email_to": "b@gmail.com",
                  "notify_proxy": "http://127.0.0.1:7070"})
    ok, detail = n._email("t", "b")
    assert ok is True, detail
    assert made["proxy"] == ("http://127.0.0.1:7070", "smtp.gmail.com", 465)
    assert made["tls_host"] == "smtp.gmail.com"
    assert smtp_obj.ehlo_called and smtp_obj.login_called and smtp_obj.sent


def test_email_direct_when_no_proxy(monkeypatch):
    """没配代理时走直连 SMTP_SSL（旧行为不变）。"""
    from app.notify import Notifier

    class FakeSMTPSSL:
        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port
        def ehlo(self, *a, **k):
            return (250, b"OK")
        def login(self, *a, **k):
            return (235, b"OK")
        def send_message(self, *a, **k):
            return {}
        def quit(self, *a, **k):
            return (221, b"OK")
        def close(self, *a, **k):
            pass

    seen = {}
    def fake_ssl(host, port, timeout=None):
        seen["direct"] = (host, port)
        return FakeSMTPSSL(host, port, timeout)
    monkeypatch.setattr("app.notify.smtplib.SMTP_SSL", fake_ssl)

    n = Notifier({"email_enabled": "1", "email_smtp_host": "smtp.gmail.com",
                  "email_smtp_port": "465", "email_user": "a@gmail.com",
                  "email_password": "x", "email_to": "b@gmail.com",
                  "notify_proxy": ""})
    ok, detail = n._email("t", "b")
    assert ok is True, detail
    assert seen["direct"] == ("smtp.gmail.com", 465)


def test_email_proxy_connect_failure_surfaces(monkeypatch):
    """代理 CONNECT 失败时，邮件报错要提到代理（让用户知道是代理不通，
    而不是 Gmail 本身的问题）。"""
    from app.notify import Notifier

    def boom(proxy_url, host, port, timeout=15):
        raise ConnectionError("代理 CONNECT smtp.gmail.com:465 失败：HTTP 502")

    monkeypatch.setattr("app.notify._connect_via_proxy", boom)
    n = Notifier({"email_enabled": "1", "email_smtp_host": "smtp.gmail.com",
                  "email_smtp_port": "465", "email_user": "a@gmail.com",
                  "email_password": "x", "email_to": "b@gmail.com",
                  "notify_proxy": "http://127.0.0.1:7070"})
    ok, detail = n._email("t", "b")
    assert ok is False
    assert "代理" in detail and "502" in detail
