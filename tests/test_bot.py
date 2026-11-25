"""Telegram 机器人控制：鉴权、命令、开关机切换、手动切分组、轮询。"""
from pathlib import Path

import pytest

from app import bot as botmod


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _settings(**over):
    s = {"telegram_enabled": "1", "telegram_token": "tok123",
         "telegram_chat_id": "123456789", "notify_proxy": "",
         "bot_enabled": "1", "switch_mode": "load_balance"}
    s.update(over)
    return s


def test_bot_default_off():
    from app.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["bot_enabled"] == "0"


def test_command_list_has_power_and_switch_no_reboot():
    cmds = [c for c, _ in botmod.COMMANDS]
    assert "power" in cmds and "switch" in cmds
    assert "reboot" not in cmds
    assert "start_instance" not in cmds
    assert "stop" not in cmds
    help_text = botmod._cmd_help()
    assert "/power 开关机" in help_text
    assert "/switch 切换分组成员" in help_text
    assert "/reboot" not in help_text


def test_wrong_chat_ignored(monkeypatch):
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda *a, **k: sent.append(a))
    botmod._handle_message(_settings(), {"chat": {"id": 999}, "text": "/status"})
    botmod._handle_callback(_settings(), {"id": "cb1", "data": "menu:status",
                                          "message": {"chat": {"id": 999}}})
    assert sent == []


def test_right_chat_dispatches_status(monkeypatch):
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append(t))
    monkeypatch.setattr(botmod, "_public_accounts", lambda: [])
    botmod._handle_message(_settings(), {"chat": {"id": 123456789}, "text": "/status"})
    assert sent and sent[0].startswith("实例状态\n服务器总数 0")


def test_status_counts(monkeypatch):
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "evicted": False},
        {"id": 2, "name": "A2", "status_dot": "yellow", "status_label": "排队中",
         "evicted": False},
        {"id": 3, "name": "A3", "status_dot": "gray", "status_label": "离队",
         "evicted": False},
        {"id": 4, "name": "A4", "status_dot": "red", "status_label": "已停机",
         "evicted": False},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    assert botmod._cmd_status() == (
        "实例状态\n服务器总数 4\n在线状态 1\n排队 1\n离队 1\n停机 1")
    # 已跳出也算离队（与运行概览一致）
    rows[1] = dict(rows[1], evicted=True, status_label="已跳出", status_dot="gray")
    assert "离队 2" in botmod._cmd_status()


def test_power_label():
    assert botmod._power_label({"instance_status": "Stopped"}) == "开机"
    assert botmod._power_label({"instance_status": "Running"}) == "关闭"


def test_do_power_toggle(monkeypatch):
    calls = []
    monkeypatch.setattr(botmod.api_mod, "start_account",
                        lambda aid, _: calls.append(("start", aid)))
    monkeypatch.setattr(botmod.api_mod, "stop_account",
                        lambda aid, body, _: calls.append(("stop", aid, body)))
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Stopped"})
    assert botmod._do_power(1) == "A1 已发送开机指令"
    assert calls == [("start", 1)]
    # 运行中按“关闭”：走节省停机（StopCharging）
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Running"})
    assert botmod._do_power(1) == "A1 已发送节省停机指令"
    assert calls[-1] == ("stop", 1, {"stop_charging": True})


def _make_group(tmpdb):
    g = tmpdb.create_group({"name": "Premium", "switch_mode": "load_balance"})
    a1 = tmpdb.create_account({"name": "A1", "group_id": g["id"],
                               "instance_id": "i-1", "access_key_id": "LTAI-x",
                               "access_key_secret_enc": "enc"})
    a2 = tmpdb.create_account({"name": "A2", "group_id": g["id"],
                               "instance_id": "i-2", "access_key_id": "LTAI-x",
                               "access_key_secret_enc": "enc"})
    d = tmpdb.create_domain({"name": "premium.example.xyz", "group_id": g["id"]})
    tmpdb.update_account(a1["id"], {"instance_status": "Running",
                                    "public_ip": "203.0.113.1"})
    tmpdb.update_account(a2["id"], {"instance_status": "Running",
                                    "public_ip": "203.0.113.2"})
    tmpdb.update_domain(d["id"], {"current_account_id": a1["id"],
                                  "current_ip": "203.0.113.1"})
    return g, a1, a2, d


def test_switch_happy_path(tmpdb, monkeypatch):
    g, a1, a2, d = _make_group(tmpdb)
    dns_calls = []

    class FakeDns:
        def __init__(self, settings):
            pass

        def set_record(self, domain, ip):
            dns_calls.append((domain.name, ip))
            return True

    monkeypatch.setattr(botmod.scheduler, "_DnsOps", FakeDns)
    sent = []

    class FakeNotifier:
        def __init__(self, settings):
            pass

        def send(self, event, title, body):
            sent.append((event, title, body))
            return ["telegram"]

    monkeypatch.setattr(botmod, "Notifier", FakeNotifier)
    reply = botmod._do_switch(g["id"], a2["id"])
    assert reply == "【Premium】A1 已替换为 A2（负载均衡：手动切换）"
    assert dns_calls == [("premium.example.xyz", "203.0.113.2")]
    assert tmpdb.get_domain(d["id"])["current_account_id"] == a2["id"]
    assert sent == [("group_change", "分组动态", reply)]
    events = tmpdb.list_events(limit=10)
    assert any(e["title"] == "分组动态" and "A1 已替换为 A2" in e["detail"]
               for e in events)


def test_switch_validation(tmpdb):
    g, a1, a2, d = _make_group(tmpdb)
    # 目标不在分组里
    other = tmpdb.create_account({"name": "B1", "instance_id": "i-9",
                                  "access_key_id": "LTAI-x",
                                  "access_key_secret_enc": "enc"})
    tmpdb.update_account(other["id"], {"instance_status": "Running",
                                       "public_ip": "203.0.113.9"})
    assert "不在分组" in botmod._do_switch(g["id"], other["id"])
    # 目标已是当前服务实例
    assert "已是" in botmod._do_switch(g["id"], a1["id"])
    # 目标未运行
    tmpdb.update_account(a2["id"], {"instance_status": "Stopped"})
    assert "未运行" in botmod._do_switch(g["id"], a2["id"])


def test_poll_once_advances_offset(tmpdb, monkeypatch):
    tmpdb.set_settings({"telegram_token": "tok123", "telegram_chat_id": "123456789",
                        "bot_enabled": "1"})
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append(t))
    monkeypatch.setattr(botmod, "_public_accounts", lambda: [])

    def fake_api(method, payload, settings, timeout=15):
        if method == "getUpdates":
            return [{"update_id": 41,
                     "message": {"chat": {"id": 123456789}, "text": "/status"}}]
        return True

    monkeypatch.setattr(botmod, "_api", fake_api)
    assert botmod._poll_once(_settings()) is True
    assert tmpdb.get_flag(botmod.OFFSET_FLAG) == "42"
    assert sent and sent[0].startswith("实例状态")


def test_bot_toggle_in_telegram_card():
    tsx = (Path(__file__).resolve().parent.parent / "web" / "src" / "App.tsx").read_text(
        encoding="utf-8")
    card = tsx[tsx.index("function ChannelCard"):tsx.index("function LoginSecurity")]
    assert 'settings.bot_enabled === "1"' in card
    assert 't("机器人控制")' in card
    assert 'bot_enabled: v ? "1" : "0"' in card
    i18n = (Path(__file__).resolve().parent.parent / "web" / "src" / "i18n.ts").read_text(
        encoding="utf-8")
    assert "机器人控制" in i18n
