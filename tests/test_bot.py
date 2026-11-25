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


def test_list_keyboard_dots_names_masked_ip(monkeypatch):
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "public_ip": "47.243.214.192"},
        {"id": 2, "name": "A2", "status_dot": "yellow", "status_label": "排队中",
         "public_ip": "8.210.121.164"},
        {"id": 3, "name": "A3", "status_dot": "red", "status_label": "已停机",
         "public_ip": "203.0.113.3"},
        {"id": 4, "name": "A4", "status_dot": "gray", "status_label": "离队",
         "public_ip": None},
        {"id": 5, "name": "A5", "status_dot": "green_pulse", "status_label": "启动中",
         "public_ip": "47.243.1.2"},
        {"id": 6, "name": "A6", "status_dot": "red_pulse", "status_label": "停止中",
         "public_ip": "47.243.3.4"},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    text, kb = botmod._cmd_list(_settings(site_name="Alicloud-CDT"), [])
    # 列表标题是站点 logo + 加粗站点名
    assert text == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    btns = [b for row in kb["inline_keyboard"] for b in row]
    # 名称加粗在上、IP 在下；IP 只显示 A、B 两段，C、D 用 * 替换
    assert [b["text"] for b in btns] == [
        "🟢 𝐀𝟏\nIP：47.243.*.*", "🟡 𝐀𝟐\nIP：8.210.*.*",
        "🔴 𝐀𝟑\nIP：203.0.*.*", "⚪ 𝐀𝟒\nIP：-",
        "🟢 𝐀𝟓\nIP：47.243.*.*", "🔴 𝐀𝟔\nIP：47.243.*.*", "🏠 返回目录"]
    assert [b["callback_data"] for b in btns] == [
        "detail:1", "detail:2", "detail:3", "detail:4", "detail:5", "detail:6",
        "menu:menu"]
    # 列表不带状态文字/分组，完整 IP 不出现
    assert "在线" not in text and "214.192" not in text


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
    text, kb = botmod._instance_detail(rows[0],
                                       _settings(site_name="Alicloud-CDT"))
    # 详情页标题是站点 logo + 加粗站点名；实例信息做成双行显示按钮
    # （第 1 行圆点 + 加粗名称，第 2 行 IP，只显示 A、B 两段，C、D 用 *
    # 替换；状态、分组两行已删），点了一点反应都没有
    assert text == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert kb["inline_keyboard"][0] == [{"text": "🟢 𝐀𝟏\nIP：203.0.*.*",
                                         "callback_data": "noop"}]
    btns = [(b["text"], b["callback_data"])
            for row in kb["inline_keyboard"] for b in row]
    # 运行中的 ECS：开关机 + 节省停机 + 更换 EIP + 定时任务 + 返回上级/返回目录
    assert ("关闭", "power:1") in btns
    assert ("节省停机", "powersave:1") in btns
    assert ("更换 EIP", "eip:1") in btns
    assert ("定时任务", "timers:1") in btns
    assert ("返回目录", "menu:menu") not in btns
    assert ("⬅️ 返回", "menu:list") in btns
    assert ("🏠 返回目录", "menu:menu") in btns
    # 已停机：开机，没有节省停机；ECS 仍有更换 EIP 和定时任务
    _, kb2 = botmod._instance_detail(rows[1], _settings())
    btns2 = [(b["text"], b["callback_data"])
             for row in kb2["inline_keyboard"] for b in row]
    assert kb2["inline_keyboard"][0][0]["text"] == "🔴 𝐀𝟐\nIP：203.0.*.*"
    assert ("开机", "power:2") in btns2
    assert not any(t == "节省停机" for t, _ in btns2)
    assert ("更换 EIP", "eip:2") in btns2
    assert ("定时任务", "timers:2") in btns2
    # 轻量服务器：没有更换 EIP，但有定时任务
    _, kb3 = botmod._instance_detail(rows[2], _settings())
    btns3 = [(b["text"], b["callback_data"])
             for row in kb3["inline_keyboard"] for b in row]
    assert kb3["inline_keyboard"][0][0]["text"] == "🟢 𝐒𝐖𝐀𝐒\nIP：203.0.*.*"
    assert ("节省停机", "powersave:3") in btns3
    assert not any(t == "更换 EIP" for t, _ in btns3)
    assert ("定时任务", "timers:3") in btns3
    # /list 带名字参数直接进详情页（标题是 logo + 加粗站点名，信息在双行显示按钮里）
    text3, kb4 = botmod._cmd_list(_settings(site_name="Alicloud-CDT"), ["A2"])
    assert text3 == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert kb4["inline_keyboard"][0][0]["text"] == "🔴 𝐀𝟐\nIP：203.0.*.*"
    assert botmod._cmd_list(_settings(), ["没有这台"])[0] == "找不到实例：没有这台"


def test_keyboard_two_per_row_back_row_at_bottom(monkeypatch):
    # 整个机器人系统：内容按钮每行两个；返回按钮固定在最底部一行，
    # 返回在左、返回目录在右；只有返回目录时最底部就一个按钮。
    # 主菜单剩 4 个按钮（定时任务已移到实例详情页），正好两行两列。
    kb = botmod._menu_keyboard()["inline_keyboard"]
    assert [[b["text"] for b in row] for row in kb] == [
        ["实例列表", "分组列表"], ["面板更新", "帮助"]]
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
    # 实例列表：名称加粗在上、IP（已打码）在下，实例每行两个，最底部只有返回目录
    _, kb_list = botmod._cmd_list(_settings(), [])
    assert [[b["text"] for b in row]
            for row in kb_list["inline_keyboard"]] == [
        ["🟢 𝐀𝟏\nIP：203.0.*.*", "🟡 𝐀𝟐\nIP：203.0.*.*"],
        ["🔴 𝐀𝟑\nIP：203.0.*.*"], ["🏠 返回目录"]]
    # 实例详情：最上面是双行显示按钮（圆点 + 加粗名称 / 已打码 IP），
    # 操作按钮每行两个（关闭、节省停机 / 更换 EIP、定时任务），
    # 最底部一行是返回（左）+ 返回目录（右）
    _, kb_det = botmod._instance_detail(rows[0], _settings())
    assert [[b["text"] for b in row]
            for row in kb_det["inline_keyboard"]] == [
        ["🟢 𝐀𝟏\nIP：203.0.*.*"], ["关闭", "节省停机"], ["更换 EIP", "定时任务"],
        ["⬅️ 返回", "🏠 返回目录"]]


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
    assert botmod._do_power(1) == "𝐀𝟏 已发送开机指令"
    assert calls == [("start", 1)]
    # 运行中按“关闭”：普通停机（KeepCharging），不是节省停机；
    # 节省停机是详情页单独的按钮
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Running"})
    assert botmod._do_power(1) == "𝐀𝟏 已发送停机指令"
    assert calls[-1] == ("stop", 1, {"stop_charging": False})


def test_do_power_save(monkeypatch):
    calls = []
    monkeypatch.setattr(botmod.api_mod, "stop_account",
                        lambda aid, body, _: calls.append(("stop", aid, body)))
    monkeypatch.setattr(botmod.db, "get_account",
                        lambda aid: {"id": aid, "name": "A1",
                                     "instance_status": "Running"})
    assert botmod._do_power_save(1) == "𝐀𝟏 已发送节省停机指令"
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
        "𝐀𝟏 EIP 已更换：203.0.*.* → 203.0.*.*；DNS 已切：premium.example.xyz")
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
    text, kb = botmod._cmd_groups(_settings(site_name="Alicloud-CDT"), [])
    # 分组列表标题是站点 logo + 加粗站点名
    assert text == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    btns = [b for row in kb["inline_keyboard"] for b in row]
    # 库里除了 Premium 还有默认分组（id=1，init_db 自带）
    assert [(b["text"], b["callback_data"]) for b in btns] == [
        ("默认分组", "group:1"), ("𝐏𝐫𝐞𝐦𝐢𝐮𝐦", f"group:{g['id']}"),
        ("🏠 返回目录", "menu:menu")]
    # 子菜单：标题跟随站点名；分组信息做成双行显示按钮（第 1 行【分组名】
    # （模式），第 2 行域名；“当前服务”“成员”两行已删）+ 成员做成按钮但
    # 不可点（在线/排队中，带圆点；noop 回调，点了一点反应都没有）
    # + 返回上级/返回目录（图标，最底部一行）
    text2, kb2 = botmod._group_detail(g, _settings(site_name="Alicloud-CDT"))
    assert text2 == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert "当前服务" not in text2 and "成员：" not in text2
    assert "A1" not in text2 and "A2" not in text2
    btns2 = [b for row in kb2["inline_keyboard"] for b in row]
    # 分组信息是双行显示按钮，成员是按钮（不可选中）：键盘里是信息按钮 +
    # 成员按钮 + 返回
    assert [(b["text"], b["callback_data"]) for b in btns2] == [
        ("【𝐏𝐫𝐞𝐦𝐢𝐮𝐦】（负载均衡）\n域名：premium.example.xyz", "noop"),
        ("🟢 𝐀𝟏\nIP：203.0.*.*", "noop"), ("🟡 𝐀𝟐\nIP：203.0.*.*", "noop"),
        ("⬅️ 返回", "menu:groups"), ("🏠 返回目录", "menu:menu")]
    # 信息按钮独占一行，成员按钮每行两个，返回在最底部一行（左）+ 返回目录（右）
    assert [[b["text"] for b in row]
            for row in kb2["inline_keyboard"]] == [
        ["【𝐏𝐫𝐞𝐦𝐢𝐮𝐦】（负载均衡）\n域名：premium.example.xyz"],
        ["🟢 𝐀𝟏\nIP：203.0.*.*", "🟡 𝐀𝟐\nIP：203.0.*.*"],
        ["⬅️ 返回", "🏠 返回目录"]]
    # /groups 带名字参数直接进子菜单（标题跟随站点名，信息在双行显示按钮里）
    text3, kb3 = botmod._cmd_groups(_settings(site_name="Alicloud-CDT"),
                                     ["Premium"])
    assert text3 == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert kb3["inline_keyboard"][0][0]["text"] == (
        "【𝐏𝐫𝐞𝐦𝐢𝐮𝐦】（负载均衡）\n域名：premium.example.xyz")
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
    botmod._handle_callback(_settings(site_name="Alicloud-CDT"),
                            {"id": "cb1", "data": "menu:menu",
                             "message": {"chat": {"id": 123456789},
                                         "message_id": 99,
                                         "text": "实例列表"}})
    assert not sent
    # 主菜单标题是站点 logo + 加粗站点名（不再是“请选择：”）
    assert edited and edited[0][0] == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    data = [b["callback_data"] for row in edited[0][1]["inline_keyboard"] for b in row]
    assert "menu:list" in data and "menu:groups" in data
    # 改不动（比如消息太旧）就发新消息
    monkeypatch.setattr(botmod, "_edit", lambda *a, **k: False)
    botmod._handle_callback(_settings(site_name="Alicloud-CDT"),
                            {"id": "cb1b", "data": "menu:menu",
                             "message": {"chat": {"id": 123456789},
                                         "message_id": 99,
                                         "text": "实例列表"}})
    assert sent and sent[0][0] == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
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


def test_same_title_different_keyboard_still_edits(monkeypatch):
    # 回归：各页标题都跟随站点名，文字相同是常态；只比文字会把换页
    # 误判成没变化，导致点列表/分组/实例都没反应。文字相同但按钮
    # 不同时仍要就地改消息；文字和按钮都相同才跳过。
    rows = [
        {"id": 1, "name": "A1", "status_dot": "green", "status_label": "在线",
         "instance_status": "Running", "public_ip": "203.0.113.1",
         "group_ids": [2], "instance_id": "i-aaa"},
    ]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    sent = []
    edited = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append((t, m)))
    monkeypatch.setattr(botmod, "_edit",
                        lambda s, c, mid, t, m=None: edited.append((t, m)) or True)
    monkeypatch.setattr(botmod, "_answer_callback", lambda *a, **k: None)
    settings = _settings(site_name="Alicloud-CDT")
    # 主菜单（标题=站点名）点实例列表：文字不变，但按钮变了，必须改消息
    menu_kb = botmod._menu_keyboard()
    botmod._handle_callback(settings,
                            {"id": "cb1", "data": "menu:list",
                             "message": {"chat": {"id": 123456789},
                                         "message_id": 99,
                                         "text": "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓",
                                         "reply_markup": menu_kb}})
    assert not sent
    assert edited and edited[0][0] == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    data = [b["callback_data"] for row in edited[0][1]["inline_keyboard"] for b in row]
    assert "detail:1" in data
    # 已在列表页再点实例列表：文字和按钮都没变，才跳过（不改也不发）
    edited.clear()
    sent.clear()
    _text, list_kb = botmod._cmd_list(settings, [])
    botmod._handle_callback(settings,
                            {"id": "cb2", "data": "menu:list",
                             "message": {"chat": {"id": 123456789},
                                         "message_id": 99,
                                         "text": "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓",
                                         "reply_markup": list_kb}})
    assert not sent and not edited
    # 列表页点一台实例进详情：文字仍是站点名，但按钮变了，必须改消息
    botmod._handle_callback(settings,
                            {"id": "cb3", "data": "detail:1",
                             "message": {"chat": {"id": 123456789},
                                         "message_id": 99,
                                         "text": "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓",
                                         "reply_markup": list_kb}})
    assert not sent
    assert edited and edited[0][0] == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    data = [b["callback_data"] for row in edited[0][1]["inline_keyboard"] for b in row]
    assert "power:1" in data


def test_group_detail_only_online_or_queued(monkeypatch):
    # 分组子菜单只显示在线/排队中的成员（带圆点）；已停机/离队的不显示。
    # 成员做成按钮但不可选中（noop 回调，点了一点反应都没有）。
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
    text, kb = botmod._group_detail(g, _settings(site_name="Alicloud-CDT"))
    # 标题是站点 logo + 加粗站点名；成员不在正文里（在按钮里），分组信息在双行显示按钮里
    assert text == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert "A1" not in text and "A2" not in text
    assert "A3" not in text
    assert "A4" not in text
    info = kb["inline_keyboard"][0][0]
    assert info["text"].startswith("【𝐏𝐫𝐞𝐦𝐢𝐮𝐦】（负载均衡）\n域名：")
    assert info["callback_data"] == "noop"
    assert [(b["text"], b["callback_data"])
            for row in kb["inline_keyboard"][1:] for b in row] == [
        ("🟢 𝐀𝟏\nIP：203.0.*.*", "noop"), ("🟡 𝐀𝟐\nIP：203.0.*.*", "noop"),
        ("⬅️ 返回", "menu:groups"), ("🏠 返回目录", "menu:menu")]
    # 全是在线/排队中之外的成员：提示做成显示按钮 + 只有返回按钮
    for r in rows:
        r["status_label"] = "已停机"
        r["status_dot"] = "red"
    text2, kb2 = botmod._group_detail(g, _settings(site_name="Alicloud-CDT"))
    assert text2 == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert [(b["text"], b["callback_data"])
            for row in kb2["inline_keyboard"] for b in row] == [
        (kb2["inline_keyboard"][0][0]["text"], "noop"),
        ("暂无在线或排队中的成员", "noop"),
        ("⬅️ 返回", "menu:groups"), ("🏠 返回目录", "menu:menu")]


def test_mask_ip_and_bold_and_site_title():
    # IP 只显示 A、B 两段，C、D 用 * 替换
    assert botmod._mask_ip("47.243.214.192") == "47.243.*.*"
    assert botmod._mask_ip("8.210.121.164") == "8.210.*.*"
    assert botmod._mask_ip("203.0.113.1") == "203.0.*.*"
    assert botmod._mask_ip(None) == "-"
    assert botmod._mask_ip("") == "-"
    # 不是 IPv4 的原样返回
    assert botmod._mask_ip("2001:db8::1") == "2001:db8::1"
    # 名称加粗：拉丁字母/数字转 Unicode 粗体，中文原样返回
    assert botmod._bold("Alicloud-CDT") == "𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert botmod._bold("A1") == "𝐀𝟏"
    assert botmod._bold("Premium") == "𝐏𝐫𝐞𝐦𝐢𝐮𝐦"
    assert botmod._bold("默认分组") == "默认分组"
    # 页面标题：站点 logo + 加粗站点名
    assert botmod._site_title(_settings(site_name="Alicloud-CDT")) == (
        "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓")
    assert botmod._site_title(_settings()) == "☁️ 𝐂𝐃𝐓 面板"


def test_cmd_start_has_logo_and_bold_site_name(monkeypatch):
    monkeypatch.setattr(botmod, "current_version", lambda: "v1.1.0-64-g7599b4a")
    text = botmod._cmd_start(_settings(site_name="Alicloud-CDT"))
    lines = text.split("\n")
    # 第一行是站点 logo + 加粗站点名，再是机器人在线、版本、命令提示
    assert lines[0] == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    assert lines[1] == "机器人在线"
    assert lines[2] == "面板版本：v1.1.0-64-g7599b4a"
    assert lines[3] == "输入 /help 查看命令"


def _make_timer_account(tmpdb):
    a = tmpdb.create_account({"name": "A1", "instance_id": "i-1",
                              "access_key_id": "LTAI-x",
                              "access_key_secret_enc": "enc"})
    tmpdb.update_account(a["id"], {"instance_status": "Running",
                                   "public_ip": "47.243.214.192",
                                   "traffic_used_gb": 10, "quota_gb": 200,
                                   "ddns_enabled": 1,
                                   "daily_schedule_enabled": 0,
                                   "daily_on_time": "08:00",
                                   "daily_off_time": "20:00"})
    return a


def test_timers_detail_submenu(tmpdb, monkeypatch):
    a = _make_timer_account(tmpdb)
    rows = [{"id": a["id"], "name": "A1", "status_dot": "green",
             "status_label": "在线", "instance_status": "Running",
             "public_ip": "47.243.214.192", "group_ids": [],
             "instance_id": "i-1"}]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    text, kb = botmod._timers_detail(rows[0], _settings(site_name="Alicloud-CDT"))
    assert text == "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"
    # 信息按钮：圆点 + 加粗名称 / 定时状态 + 开关时间；操作按钮每行两个；
    # 返回回实例详情页（左）+ 返回目录（右）
    assert [[b["text"] for b in row]
            for row in kb["inline_keyboard"]] == [
        ["🟢 𝐀𝟏\n定时：未启用　08:00 开 / 20:00 关"],
        ["定时开关：关闭", "开机时间：08:00"], ["关机时间：20:00"],
        ["⬅️ 返回", "🏠 返回目录"]]
    assert [[b["callback_data"] for b in row]
            for row in kb["inline_keyboard"]] == [
        ["noop"],
        [f"timer_toggle:{a['id']}", f"timer_set_on:{a['id']}"],
        [f"timer_set_off:{a['id']}"],
        [f"detail:{a['id']}", "menu:menu"]]
    # 开启后开关按钮变“开启”，信息行也变
    tmpdb.update_account(a["id"], {"daily_schedule_enabled": 1})
    text2, kb2 = botmod._timers_detail(rows[0], _settings())
    assert kb2["inline_keyboard"][0][0]["text"] == (
        "🟢 𝐀𝟏\n定时：已开启　08:00 开 / 20:00 关")
    assert kb2["inline_keyboard"][1][0]["text"] == "定时开关：开启"


def test_timer_toggle(tmpdb):
    a = _make_timer_account(tmpdb)
    assert botmod._do_timer_toggle(a["id"]) == "𝐀𝟏 定时任务已开启"
    assert tmpdb.get_account(a["id"])["daily_schedule_enabled"] == 1
    assert botmod._do_timer_toggle(a["id"]) == "𝐀𝟏 定时任务已关闭"
    assert tmpdb.get_account(a["id"])["daily_schedule_enabled"] == 0
    assert botmod._do_timer_toggle(99999) == "账户不存在"


def test_timer_set_input(tmpdb, monkeypatch):
    a = _make_timer_account(tmpdb)
    sent = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append((t, m)))
    # 格式不对：提示重输，不保存、不清暂存
    tmpdb.set_flag(botmod._pending_timer_key(123456789), f"{a['id']}:on")
    botmod._handle_timer_input(_settings(), 123456789, f"{a['id']}:on", "8点")
    assert sent and "格式不对" in sent[-1][0]
    assert tmpdb.get_account(a["id"])["daily_on_time"] == "08:00"
    assert tmpdb.get_flag(botmod._pending_timer_key(123456789)) == f"{a['id']}:on"
    # 开机时间与关机时间相同（已启用时）：提示重输
    tmpdb.update_account(a["id"], {"daily_schedule_enabled": 1})
    botmod._handle_timer_input(_settings(), 123456789, f"{a['id']}:on", "20:00")
    assert sent and "不能相同" in sent[-1][0]
    assert tmpdb.get_account(a["id"])["daily_on_time"] == "08:00"
    # 合法：保存、清暂存、回定时子菜单（子菜单按钮里显示新时间）
    botmod._handle_timer_input(_settings(), 123456789, f"{a['id']}:on", "09:30")
    assert tmpdb.get_account(a["id"])["daily_on_time"] == "09:30"
    assert not tmpdb.get_flag(botmod._pending_timer_key(123456789))
    assert sent and "开机时间：09:30" in str(sent[-1][1])
    # 关机时间也一样
    botmod._handle_timer_input(_settings(), 123456789, f"{a['id']}:off", "22:15")
    assert tmpdb.get_account(a["id"])["daily_off_time"] == "22:15"
    assert "关机时间：22:15" in str(sent[-1][1])


def test_timer_callbacks(tmpdb, monkeypatch):
    a = _make_timer_account(tmpdb)
    rows = [{"id": a["id"], "name": "A1", "status_dot": "green",
             "status_label": "在线", "instance_status": "Running",
             "public_ip": "47.243.214.192", "group_ids": [],
             "instance_id": "i-1"}]
    monkeypatch.setattr(botmod, "_public_accounts", lambda: rows)
    sent = []
    edited = []
    monkeypatch.setattr(botmod, "_send", lambda s, t, m=None: sent.append((t, m)))
    monkeypatch.setattr(botmod, "_edit",
                        lambda s, c, mid, t, m=None: edited.append((t, m)) or True)
    monkeypatch.setattr(botmod, "_answer_callback", lambda *x, **k: None)
    base = {"chat": {"id": 123456789}, "message_id": 99,
            "text": "☁️ 𝐀𝐥𝐢𝐜𝐥𝐨𝐮𝐝-𝐂𝐃𝐓"}
    # 详情页点“定时任务”进子菜单（就地改消息）
    botmod._handle_callback(_settings(site_name="Alicloud-CDT"),
                            {"id": "cb1", "data": f"timers:{a['id']}",
                             "message": dict(base)})
    assert edited and "定时开关" in str(edited[0][1])
    # 点“开机时间”：暂存等回复，并新发一条提示
    botmod._handle_callback(_settings(),
                            {"id": "cb2", "data": f"timer_set_on:{a['id']}",
                             "message": dict(base)})
    assert tmpdb.get_flag(botmod._pending_timer_key(123456789)) == f"{a['id']}:on"
    assert sent and "开机时间" in sent[-1][0] and "HH:MM" in sent[-1][0]
    # 点别的按钮会取消未完成的输入
    botmod._handle_callback(_settings(),
                            {"id": "cb3", "data": f"timer_toggle:{a['id']}",
                             "message": dict(base)})
    assert not tmpdb.get_flag(botmod._pending_timer_key(123456789))
    assert tmpdb.get_account(a["id"])["daily_schedule_enabled"] == 1
    # 普通文字消息在等输入时当作时间处理；命令会取消输入
    tmpdb.set_flag(botmod._pending_timer_key(123456789), f"{a['id']}:off")
    botmod._handle_message(_settings(),
                           {"chat": {"id": 123456789}, "text": "23:00"})
    assert tmpdb.get_account(a["id"])["daily_off_time"] == "23:00"
    tmpdb.set_flag(botmod._pending_timer_key(123456789), f"{a['id']}:off")
    botmod._handle_message(_settings(),
                           {"chat": {"id": 123456789}, "text": "/menu"})
    assert not tmpdb.get_flag(botmod._pending_timer_key(123456789))
