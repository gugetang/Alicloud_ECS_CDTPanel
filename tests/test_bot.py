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
        "🟢 A1", "🟡 A2", "🔴 A3", "⚪ A4", "🟢 A5", "🔴 A6", "🏠 返回目录"]
    assert [b["callback_data"] for b in btns] == [
        "detail:1", "detail:2", "detail:3", "detail:4", "detail:5", "detail:6",
        "menu:menu"]
    # 列表只显示名称，不带状态文字/IP/分组
    assert "在线" not in text and "203.0" not in text


def test_instance_detail_page(monkeypatch):
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "instance_status": "Running", "public_ip": "203.0.113.1",
         "group_ids": [2], "instance_id": "i-aaa"},
        {"id": 2, "name": "A2", "status_dot": "red", "status_label": "已停机",
         "instance_status": "Stopped", "public_ip": "203.0.113.2",
         "group_ids": [2], "instance_id": "i-bbb"},
        {"id": 3, "name": "SWAS", "status_dot": "green", "status_label": "在线",
         "instance_status": "Running", "public_ip": "203.0.113.3",
         "group_ids": [2],
         "instance_id": "123e4567-e89b-12d3-a456-426614174000"},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    monkeypatch.setattr(botmod.db, "list_groups",
                        lambda: [{"id": 2, "name": "Premium"}])
    text, kb = botmod._instance_detail(rows[0])
    assert text == "🟢 A1\n状态：在线\nIP：203.0.113.1\n分组：Premium"
    btns = [(b["text"], b["callback_data"])
            for row in kb["inline_keyboard"] for b in row]
    # 运行中的 ECS：开关机 + 节省停机 + 更换 EIP + 返回上级/返回目录
    assert ("关闭", "power:1") in btns
    assert ("节省停机", "powersave:1") in btns
    assert ("更换 EIP", "eip:1") in btns
    assert ("返回目录", "menu:menu") not in btns
    assert ("⬅️ 返回", "menu:list") in btns
    assert ("🏠 返回目录", "menu:menu") in btns
    # 已停机：开机，没有节省停机；ECS 仍有更换 EIP
    _, kb2 = botmod._instance_detail(rows[1])
    btns2 = [(b["text"], b["callback_data"])
             for row in kb2["inline_keyboard"] for b in row]
    assert ("开机", "power:2") in btns2
    assert not any(t == "节省停机" for t, _ in btns2)
    assert ("更换 EIP", "eip:2") in btns2
    # 轻量服务器：没有更换 EIP
    _, kb3 = botmod._instance_detail(rows[2])
    btns3 = [(b["text"], b["callback_data"])
             for row in kb3["inline_keyboard"] for b in row]
    assert ("节省停机", "powersave:3") in btns3
    assert not any(t == "更换 EIP" for t, _ in btns3)
    # /list 带名字参数直接进详情页
    text3, _ = botmod._cmd_list(["A2"])
    assert text3.startswith("🔴 A2")
    assert botmod._cmd_list(["没有这台"])[0] == "找不到实例：没有这台"


def test_keyboard_two_per_row_back_row_at_bottom(monkeypatch):
    # 整个机器人系统：内容按钮每行两个；返回按钮固定在最底部一行，
    # 返回在左、返回目录在右；只有返回目录时最底部就一个按钮。
    kb = botmod._menu_keyboard()["inline_keyboard"]
    assert [[b["text"] for b in row] for row in kb] == [
        ["实例列表", "分组列表"], ["定时任务", "面板更新"], ["帮助"]]
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "instance_status": "Running", "public_ip": "203.0.113.1",
         "group_ids": [2], "instance_id": "i-aaa"},
        {"id": 2, "name": "A2", "status_dot": "yellow", "status_label": "排队中",
         "instance_status": "Running", "public_ip": "203.0.113.2",
         "group_ids": [2], "instance_id": "i-bbb"},
        {"id": 3, "name": "A3", "status_dot": "red", "status_label": "已停机",
         "instance_status": "Stopped", "public_ip": "203.0.113.3",
         "group_ids": [2], "instance_id": "i-ccc"},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    monkeypatch.setattr(botmod.db, "list_groups",
                        lambda: [{"id": 2, "name": "Premium"}])
    # 实例列表：实例每行两个，最底部只有返回目录
    _, kb_list = botmod._cmd_list([])
    assert [[b["text"] for b in row]
            for row in kb_list["inline_keyboard"]] == [
        ["🟢 A1", "🟡 A2"], ["🔴 A3"], ["🏠 返回目录"]]
    # 实例详情：操作按钮每行两个，最底部一行是返回（左）+ 返回目录（右）
    _, kb_det = botmod._instance_detail(rows[0])
    assert [[b["text"] for b in row]
            for row in kb_det["inline_keyboard"]] == [
        ["关闭", "节省停机"], ["更换 EIP"], ["⬅️ 返回", "🏠 返回目录"]]


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
    # 运行中按“关闭”：普通停机（KeepCharging），不是节省停机；
    # 节省停机是详情页单独的按钮
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Running"})
    assert botmod._do_power(1) == "A1 已发送停机指令"
    assert calls[-1] == ("stop", 1, {"stop_charging": False})


def test_do_power_save(monkeypatch):
    calls = []
    monkeypatch.setattr(botmod.api_mod, "stop_account",
                        lambda aid, body, _: calls.append(("stop", aid, body)))
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Running"})
    assert botmod._do_power_save(1) == "A1 已发送节省停机指令"
    assert calls == [("stop", 1, {"stop_charging": True})]
    # 已停机不用节省停机
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Stopped"})
    assert "无需节省停机" in botmod._do_power_save(1)


def test_do_replace_eip(monkeypatch):
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1"})
    monkeypatch.setattr(botmod.api_mod, "replace_eip",
                        lambda aid: {"old_ip": "203.0.113.1",
                                     "new_ip": "203.0.113.9",
                                     "dns_domains": ["premium.example.xyz"],
                                     "warning": ""})
    assert botmod._do_replace_eip(1) == (
        "A1 EIP 已更换：203.0.113.1 → 203.0.113.9；DNS 已切：premium.example.xyz")
    # 失败时把后端原因原样回过去
    from fastapi import HTTPException
    monkeypatch.setattr(botmod.api_mod, "replace_eip",
                        lambda aid: (_ for _ in ()).throw(
                            HTTPException(400, "轻量应用服务器没有 EIP，不能更换 EIP")))
    assert "轻量应用服务器没有 EIP" in botmod._do_replace_eip(1)


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
                                    "public_ip": "203.0.113.1",
                                    "traffic_used_gb": 10, "quota_gb": 200,
                                    "ddns_enabled": 1})
    tmpdb.update_account(a2["id"], {"instance_status": "Running",
                                    "public_ip": "203.0.113.2",
                                    "traffic_used_gb": 20, "quota_gb": 200,
                                    "ddns_enabled": 1})
    tmpdb.update_domain(d["id"], {"current_account_id": a1["id"],
                                  "current_ip": "203.0.113.1"})
    return g, a1, a2, d


def test_groups_keyboard_and_detail(tmpdb, monkeypatch):
    g, a1, a2, d = _make_group(tmpdb)
    text, kb = botmod._cmd_groups(_settings(), [])
    assert text.startswith("分组列表")
    btns = [b for row in kb["inline_keyboard"] for b in row]
    # 库里除了 Premium 还有默认分组（id=1，init_db 自带）
    assert [(b["text"], b["callback_data"]) for b in btns] == [
        ("默认分组", "group:1"), ("Premium", f"group:{g['id']}"),
        ("🏠 返回目录", "menu:menu")]
    # 子菜单：分组信息 + 成员只显示（在线/排队中，带圆点，当前服务有标记，
    # 不可点——负载均衡控制，手动切换无效）+ 返回上级/返回目录（图标）
    text2, kb2 = botmod._group_detail(g, _settings())
    assert "【Premium】（负载均衡）" in text2
    assert "域名：premium.example.xyz" in text2
    assert "当前服务：A1" in text2
    assert "🟢 A1（当前服务）" in text2
    assert "🟡 A2" in text2
    btns2 = [b for row in kb2["inline_keyboard"] for b in row]
    # 成员不是按钮：键盘里只有返回
    assert [(b["text"], b["callback_data"]) for b in btns2] == [
        ("⬅️ 返回", "menu:groups"), ("🏠 返回目录", "menu:menu")]
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


def test_back_buttons_and_action_callbacks(monkeypatch):
    # 返回目录（menu:menu）回到主菜单：就地改同一条消息，不新发气泡
    sent = []
    edited = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append((t, m)))
    monkeypatch.setattr(botmod, "_edit",
                        lambda s, c, mid, t, m=None: edited.append((t, m)) or True)
    monkeypatch.setattr(botmod, "_answer_callback", lambda *a, **k: None)
    botmod._handle_callback(_settings(), {"id": "cb1", "data": "menu:menu",
                                          "message": {"chat": {"id": 123456789},
                                                      "message_id": 99,
                                                      "text": "实例列表"}})
    assert not sent
    assert edited and edited[0][0] == "请选择："
    data = [b["callback_data"] for row in edited[0][1]["inline_keyboard"] for b in row]
    assert "menu:list" in data and "menu:groups" in data
    # 改不动（比如消息太旧）就发新消息
    monkeypatch.setattr(botmod, "_edit", lambda *a, **k: False)
    botmod._handle_callback(_settings(), {"id": "cb1b", "data": "menu:menu",
                                          "message": {"chat": {"id": 123456789},
                                                      "message_id": 99,
                                                      "text": "实例列表"}})
    assert sent and sent[0][0] == "请选择："
    # 节省停机与更换 EIP 的回调走到对应的处理函数，结果用 toast 回，不新发气泡
    calls = []
    monkeypatch.setattr(botmod, "_do_power_save",
                        lambda aid: calls.append(("save", aid)) or "ok-save")
    monkeypatch.setattr(botmod, "_do_replace_eip",
                        lambda aid: calls.append(("eip", aid)) or "ok-eip")
    botmod._handle_callback(_settings(), {"id": "cb2", "data": "powersave:7",
                                          "message": {"chat": {"id": 123456789}}})
    botmod._handle_callback(_settings(), {"id": "cb3", "data": "eip:8",
                                          "message": {"chat": {"id": 123456789}}})
    assert calls == [("save", 7), ("eip", 8)]
    # 定时任务/帮助也带返回目录（图标）
    reply, markup = botmod._dispatch(_settings(), "help", [])
    assert reply.startswith("可用命令：")
    assert markup["inline_keyboard"] == [[{"text": "🏠 返回目录",
                                           "callback_data": "menu:menu"}]]


def test_group_detail_only_online_or_queued(monkeypatch):
    # 分组子菜单只显示在线/排队中的成员（带圆点）；已停机/离队的不显示。
    # 成员是纯文本行，不是按钮（负载均衡控制，不可操作）。
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "instance_status": "Running", "public_ip": "203.0.113.1",
         "group_ids": [2], "instance_id": "i-aaa"},
        {"id": 2, "name": "A2", "status_dot": "yellow", "status_label": "排队中",
         "instance_status": "Running", "public_ip": "203.0.113.2",
         "group_ids": [2], "instance_id": "i-bbb"},
        {"id": 3, "name": "A3", "status_dot": "red", "status_label": "已停机",
         "instance_status": "Stopped", "public_ip": "203.0.113.3",
         "group_ids": [2], "instance_id": "i-ccc"},
        {"id": 4, "name": "A4", "status_dot": "gray", "status_label": "离队",
         "instance_status": "Running", "public_ip": "203.0.113.4",
         "group_ids": [2], "instance_id": "i-ddd"},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    g = {"id": 2, "name": "Premium", "switch_mode": "load_balance"}
    text, kb = botmod._group_detail(g, _settings())
    assert "🟢 A1" in text
    assert "🟡 A2" in text
    assert "A3" not in text
    assert "A4" not in text
    assert [(b["text"], b["callback_data"])
            for row in kb["inline_keyboard"] for b in row] == [
        ("⬅️ 返回", "menu:groups"), ("🏠 返回目录", "menu:menu")]
    # 全是在线/排队中之外的成员：提示 + 只有返回按钮
    for r in rows:
        r["status_label"] = "已停机"
        r["status_dot"] = "red"
    text2, kb2 = botmod._group_detail(g, _settings())
    assert "暂无在线或排队中的成员" in text2
    assert [(b["text"], b["callback_data"])
            for row in kb2["inline_keyboard"] for b in row] == [
        ("⬅️ 返回", "menu:groups"), ("🏠 返回目录", "menu:menu")]
