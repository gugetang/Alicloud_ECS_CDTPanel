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


def test_command_list_no_status_power_switch_reboot():
    cmds = [c for c, _ in botmod.COMMANDS]
    assert cmds == ["start", "menu", "list", "groups", "timers", "update", "help"]
    assert "status" not in cmds
    assert "power" not in cmds
    assert "switch" not in cmds
    assert "reboot" not in cmds
    assert "start_instance" not in cmds
    assert "stop" not in cmds
    help_text = botmod._cmd_help()
    assert "/list 实例列表" in help_text
    assert "/groups 分组列表" in help_text
    assert "/update 面板更新" in help_text
    assert "/status" not in help_text
    assert "/power" not in help_text
    assert "/switch" not in help_text
    assert "/reboot" not in help_text
    kb = botmod._menu_keyboard()
    data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert "menu:list" in data and "menu:groups" in data and "menu:update" in data
    assert "menu:status" not in data
    assert "menu:power" not in data
    assert "menu:switch" not in data


def test_wrong_chat_ignored(monkeypatch):
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda *a, **k: sent.append(a))
    botmod._handle_message(_settings(), {"chat": {"id": 999}, "text": "/list"})
    botmod._handle_callback(_settings(), {"id": "cb1", "data": "menu:list",
                                          "message": {"chat": {"id": 999}}})
    assert sent == []


def test_right_chat_dispatches_list(monkeypatch):
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append((t, m)))
    monkeypatch.setattr(botmod, "_public_accounts", lambda: [])
    botmod._handle_message(_settings(), {"chat": {"id": 123456789}, "text": "/list"})
    assert sent and sent[0][0] == "暂无实例"


def test_list_keyboard_dots_only_names(monkeypatch):
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线"},
        {"id": 2, "name": "A2", "status_dot": "yellow", "status_label": "排队中"},
        {"id": 3, "name": "A3", "status_dot": "red", "status_label": "已停机"},
        {"id": 4, "name": "A4", "status_dot": "gray", "status_label": "离队"},
        {"id": 5, "name": "A5", "status_dot": "green_pulse", "status_label": "启动中"},
        {"id": 6, "name": "A6", "status_dot": "red_pulse", "status_label": "停止中"},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    text, kb = botmod._cmd_list([])
    assert text.startswith("实例列表")
    btns = [b for row in kb["inline_keyboard"] for b in row]
    assert [b["text"] for b in btns] == [
        "🟢 A1", "🟡 A2", "🔴 A3", "⚪ A4", "🟢 A5", "🔴 A6"]
    assert [b["callback_data"] for b in btns] == [
        "detail:1", "detail:2", "detail:3", "detail:4", "detail:5", "detail:6"]
    # 列表只显示名称，不带状态文字/IP/分组
    assert "在线" not in text and "203.0" not in text


def test_instance_detail_page(monkeypatch):
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "instance_status": "Running", "public_ip": "203.0.113.1",
         "group_ids": [2]},
        {"id": 2, "name": "A2", "status_dot": "red", "status_label": "已停机",
         "instance_status": "Stopped", "public_ip": "203.0.113.2",
         "group_ids": [2]},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    monkeypatch.setattr(botmod.db, "list_groups",
                        lambda: [{"id": 2, "name": "Premium"}])
    text, kb = botmod._instance_detail(rows[0])
    assert text == "🟢 A1\n状态：在线\nIP：203.0.113.1\n分组：Premium"
    btns = [b for row in kb["inline_keyboard"] for b in row]
    assert ("关闭", "power:1") in [(b["text"], b["callback_data"]) for b in btns]
    assert ("返回实例列表", "menu:list") in [(b["text"], b["callback_data"])
                                             for b in btns]
    # 已停机显示开机
    text2, kb2 = botmod._instance_detail(rows[1])
    assert "🔴 A2\n状态：已停机" in text2
    assert any(b["text"] == "开机" and b["callback_data"] == "power:2"
               for row in kb2["inline_keyboard"] for b in row)
    # /list 带名字参数直接进详情页
    text3, _ = botmod._cmd_list(["A2"])
    assert text3.startswith("🔴 A2")
    assert botmod._cmd_list(["没有这台"])[0] == "找不到实例：没有这台"


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


def test_groups_keyboard_and_detail(tmpdb, monkeypatch):
    g, a1, a2, d = _make_group(tmpdb)
    text, kb = botmod._cmd_groups(_settings(), [])
    assert text.startswith("分组列表")
    btns = [b for row in kb["inline_keyboard"] for b in row]
    # 库里除了 Premium 还有默认分组（id=1，init_db 自带）
    assert [(b["text"], b["callback_data"]) for b in btns] == [
        ("默认分组", "group:1"), ("Premium", f"group:{g['id']}")]
    # 子菜单：分组信息 + 成员按钮（当前服务有标记）+ 返回
    text2, kb2 = botmod._group_detail(g, _settings())
    assert "【Premium】（负载均衡）" in text2
    assert "域名：premium.example.xyz" in text2
    assert "当前服务：A1" in text2
    btns2 = [b for row in kb2["inline_keyboard"] for b in row]
    assert ("A1（当前服务）", f"switchto:{g['id']}:{a1['id']}") in [
        (b["text"], b["callback_data"]) for b in btns2]
    assert ("A2", f"switchto:{g['id']}:{a2['id']}") in [
        (b["text"], b["callback_data"]) for b in btns2]
    assert ("返回分组列表", "menu:groups") in [(b["text"], b["callback_data"])
                                               for b in btns2]
    # /groups 带名字参数直接进子菜单
    text3, _ = botmod._cmd_groups(_settings(), ["Premium"])
    assert "【Premium】" in text3
    assert botmod._cmd_groups(_settings(), ["没有这组"])[0] == "找不到分组：没有这组"


def test_poll_once_advances_offset(tmpdb, monkeypatch):
    tmpdb.set_settings({"telegram_token": "tok123", "telegram_chat_id": "123456789",
                        "bot_enabled": "1"})
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append(t))
    monkeypatch.setattr(botmod, "_public_accounts", lambda: [])

    def fake_api(method, payload, settings, timeout=15):
        if method == "getUpdates":
            return [{"update_id": 41,
                     "message": {"chat": {"id": 123456789}, "text": "/list"}}]
        return True

    monkeypatch.setattr(botmod, "_api", fake_api)
    assert botmod._poll_once(_settings()) is True
    assert tmpdb.get_flag(botmod.OFFSET_FLAG) == "42"
    assert sent and sent[0] == "暂无实例"


def test_cmd_update_no_update(monkeypatch):
    monkeypatch.setattr(botmod.update_mod, "status", lambda: {
        "current_version": "v1.1.0", "latest_version": "abc1234",
        "update_available": False})
    assert botmod._cmd_update() == "已是最新版本\n当前版本：v1.1.0"


def test_cmd_update_starts(monkeypatch):
    monkeypatch.setattr(botmod.update_mod, "status", lambda: {
        "current_version": "v1.1.0-2-gabc1234", "latest_version": "def5678abc",
        "update_available": True})
    monkeypatch.setattr(botmod.update_mod, "start_update",
                        lambda: {"started": True, "message": "更新已开始，容器将重启"})
    reply = botmod._cmd_update()
    assert reply.startswith("更新已开始，容器将重启\n当前版本：v1.1.0-2-gabc1234\n最新版本：def5678")
    assert "机器人随容器重启" in reply


def test_cmd_update_no_socket(monkeypatch):
    monkeypatch.setattr(botmod.update_mod, "status", lambda: {
        "current_version": "v1.1.0", "latest_version": "def5678",
        "update_available": True})
    monkeypatch.setattr(botmod.update_mod, "start_update", lambda: {
        "started": False, "message": "未检测到 Docker socket，无法在线更新",
        "manual": "docker compose pull && docker compose up -d"})
    reply = botmod._cmd_update()
    assert "未检测到 Docker socket" in reply
    assert "docker compose pull && docker compose up -d" in reply


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
