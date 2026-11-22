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
