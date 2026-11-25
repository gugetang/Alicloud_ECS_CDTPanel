"""Telegram 机器人控制：用 Bot API 轮询收命令，控制面板实例与分组。

- 接入：getUpdates 长轮询（timeout=30），走 notify_proxy；offset 存 flags
  （bot_update_offset），重启不丢命令、不重复执行。
- 鉴权：只认绑定的 telegram_chat_id，其他人一律忽略、不回复。
- 开关：bot_enabled=1 才轮询（默认关）；线程随面板启动，开关在通知设置
  Telegram 卡片里，改完 10 秒内生效，不用重启容器。
- 命令：/start /menu /list /status /groups /power /switch /timers /help
  （/reboot 已取消；开关机是单个 /power 切换按钮，不再分启动/停止两个命令）。
- 开关机按钮：已停机显示“开机”，运行中显示“关闭”，跟面板普通开关机
  同款切换逻辑；关闭用节省停机（StopCharging）。
- 所有操作复用面板现有逻辑：api.start_account / stop_account、
  scheduler._DnsOps.set_record、db.update_domain、Notifier 推送。
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from . import api as api_mod
from . import db, scheduler
from . import update as update_mod
from .engine import _switch_rule_text
from .notify import Notifier
from .update import current_version

log = logging.getLogger(__name__)

POLL_TIMEOUT = 30
IDLE_WAIT_SECONDS = 10
ERROR_BACKOFF_SECONDS = 10
OFFSET_FLAG = "bot_update_offset"

# 命令表：/help 与 setMyCommands 共用；/reboot 已取消，不在这里出现。
COMMANDS = [
    ("start", "检查机器人"),
    ("menu", "交互菜单"),
    ("list", "实例列表"),
    ("status", "状态查询"),
    ("groups", "分组列表"),
    ("power", "开关机"),
    ("switch", "切换分组成员"),
    ("timers", "定时任务"),
    ("update", "面板更新"),
    ("help", "帮助"),
]

MODE_LABELS = {"time": "定时轮巡", "load_balance": "负载均衡", "dynamic": "负载均衡"}

_stop = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_commands_registered = False


def start() -> None:
    """启动机器人线程（随调度一起启停，见 main.lifespan）。"""
    global _thread
    with _thread_lock:
        old = _thread
        if old is not None and old.is_alive():
            return
        if old is not None:
            old.join(timeout=10)
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="cdt-bot", daemon=True)
        _thread.start()


def stop(timeout: float = 30) -> None:
    _stop.set()
    t = _thread
    if t is not None:
        t.join(timeout=timeout)


# ---------------------------------------------------------------------------
# 配置与 Bot API
# ---------------------------------------------------------------------------

def _token(settings: dict) -> str:
    return (settings.get("telegram_token") or "").strip()


def _chat_id(settings: dict) -> str:
    return (settings.get("telegram_chat_id") or "").strip()


def _enabled(settings: dict) -> bool:
    return settings.get("bot_enabled") == "1" and bool(_token(settings)) and bool(_chat_id(settings))


def _proxies(settings: dict) -> dict:
    proxy = (settings.get("notify_proxy") or "").strip()
    return {"http": proxy, "https": proxy} if proxy else {}


def _api(method: str, payload: dict, settings: dict, timeout: int = 15):
    """调一次 Bot API；失败返回 None（不抛错，调用方按失败处理）。"""
    token = _token(settings)
    if not token:
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                          json=payload, proxies=_proxies(settings), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.warning("机器人 API %s 请求失败: %s", method, exc)
        return None
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not data.get("ok"):
        log.warning("机器人 API %s 返回失败: %s", method, data)
        return None
    return data.get("result")


def _send(settings: dict, text: str, reply_markup: dict | None = None):
    """给绑定的 Chat 发一条纯文本消息（不用 parse_mode，避免 HTML 解析失败）。"""
    payload: dict = {"chat_id": _chat_id(settings), "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _api("sendMessage", payload, settings)


def _answer_callback(settings: dict, callback_query_id: str, text: str | None = None):
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _api("answerCallbackQuery", payload, settings)


def _register_commands(settings: dict) -> None:
    cmds = [{"command": c, "description": d} for c, d in COMMANDS]
    _api("setMyCommands", {"commands": cmds}, settings)


def _authorized(settings: dict, chat_id) -> bool:
    bound = _chat_id(settings)
    return bool(bound) and str(chat_id) == bound


def _kb(rows: list) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                for row in rows]}


# ---------------------------------------------------------------------------
# 数据：复用面板的账户公开行（状态点/排队位次与面板一致）
# ---------------------------------------------------------------------------

def _public_accounts() -> list[dict]:
    return [api_mod._public_account(a) for a in db.list_accounts()]


def _resolve_account(arg: str) -> dict | None:
    """按序号（/list 里的编号）或名字找实例；找不到或重名返回 None。"""
    rows = _public_accounts()
    arg = (arg or "").strip()
    if not arg:
        return None
    if arg.isdigit():
        i = int(arg)
        return rows[i - 1] if 1 <= i <= len(rows) else None
    exact = [r for r in rows if r["name"] == arg]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [r for r in rows if arg in r["name"]]
    return fuzzy[0] if len(fuzzy) == 1 else None


def _resolve_group(arg: str) -> dict | None:
    groups = db.list_groups()
    arg = (arg or "").strip()
    if not arg:
        return None
    if arg.isdigit():
        i = int(arg)
        return groups[i - 1] if 1 <= i <= len(groups) else None
    exact = [g for g in groups if g["name"] == arg]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [g for g in groups if arg in g["name"]]
    return fuzzy[0] if len(fuzzy) == 1 else None


def _group_members(gid: int) -> list[dict]:
    return [r for r in _public_accounts() if gid in (r.get("group_ids") or [])]


def _group_mode_label(group: dict, settings: dict) -> str:
    mode = ((group.get("switch_mode") or settings.get("switch_mode") or "load_balance")
            .strip().lower())
    return MODE_LABELS.get(mode, "负载均衡")


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def _cmd_start() -> str:
    return f"机器人在线\n面板版本：{current_version()}\n输入 /help 查看命令"


def _menu_keyboard() -> dict:
    return _kb([
        [("实例列表", "menu:list"), ("状态查询", "menu:status")],
        [("分组列表", "menu:groups"), ("定时任务", "menu:timers")],
        [("开关机", "menu:power"), ("切换分组成员", "menu:switch")],
        [("面板更新", "menu:update"), ("帮助", "menu:help")],
    ])


def _cmd_list() -> str:
    rows = _public_accounts()
    if not rows:
        return "暂无实例"
    groups = {g["id"]: g["name"] for g in db.list_groups()}
    lines = ["实例列表"]
    for i, r in enumerate(rows, 1):
        gids = r.get("group_ids") or []
        gname = groups.get(gids[0], "未分组") if gids else "未分组"
        label = r.get("status_label") or r.get("instance_status") or "未知"
        lines.append(f"{i}. {r['name']} — {label} — {r.get('public_ip') or '-'} — {gname}")
    return "\n".join(lines)


def _cmd_status() -> str:
    # 口径与运行概览卡片一致：在线=绿点，排队=黄点，
    # 离队=已跳出或未被任何分组选中，停机=红点（含停止中）。
    rows = _public_accounts()
    total = len(rows)
    online = sum(1 for r in rows if r.get("status_dot") == "green")
    queued = sum(1 for r in rows if r.get("status_dot") == "yellow")
    left = sum(1 for r in rows if r.get("evicted") or r.get("status_label") == "离队")
    stopped = sum(1 for r in rows if r.get("status_dot") in ("red", "red_pulse"))
    lines = ["实例状态", f"服务器总数 {total}", f"在线状态 {online}",
             f"排队 {queued}", f"离队 {left}", f"停机 {stopped}"]
    not_in = sum(1 for r in rows if r.get("status_label") == "未参与")
    if not_in:
        lines.append(f"未参与 {not_in}")
    return "\n".join(lines)


def _cmd_groups(settings: dict) -> str:
    groups = db.list_groups()
    if not groups:
        return "暂无分组"
    domains = db.list_domains()
    accounts = {a["id"]: a for a in db.list_accounts()}
    lines = ["分组列表"]
    for g in groups:
        gid = g["id"]
        doms = [d for d in domains if d.get("group_id") == gid]
        d = sorted(doms, key=lambda x: (x.get("priority", 0), x["id"]))[0] if doms else None
        members = _group_members(gid)
        current = accounts.get(d.get("current_account_id")) if d else None
        lines.append(f"{g['name']}（{_group_mode_label(g, settings)}）")
        lines.append(f"  域名：{d['name'] if d else '无'}")
        if current:
            lines.append(f"  当前服务：{current['name']}")
        elif d and d.get("fallback_active"):
            lines.append("  当前服务：备用地址")
        else:
            lines.append("  当前服务：无")
        names = "、".join(m["name"] for m in members)
        lines.append(f"  成员：{len(members)}" + (f"（{names}）" if names else ""))
    return "\n".join(lines)


def _power_label(a: dict) -> str:
    # 已停机显示“开机”，运行中显示“关闭”，跟面板普通开关机同款切换。
    return "开机" if (a.get("instance_status") or "").strip() == "Stopped" else "关闭"


def _power_keyboard() -> dict:
    rows = _public_accounts()
    return _kb([[(f"{i}. {r['name']}：{_power_label(r)}", f"power:{r['id']}")]
                for i, r in enumerate(rows, 1)])


def _do_power(aid: int) -> str:
    a = db.get_account(aid)
    if not a:
        return "账户不存在"
    stopped = (a.get("instance_status") or "").strip() == "Stopped"
    try:
        if stopped:
            api_mod.start_account(aid, True)
        else:
            # 关闭用节省停机（StopCharging），与面板节省停机按钮一致。
            api_mod.stop_account(aid, {"stop_charging": True}, True)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        return f"{a['name']} {'开机' if stopped else '关闭'}失败：{detail}"
    return f"{a['name']} 已发送{'开机' if stopped else '节省停机'}指令"


def _cmd_power(args: list[str]):
    if args:
        a = _resolve_account(args[0])
        if not a:
            return f"找不到实例：{args[0]}", None
        return _do_power(a["id"]), None
    rows = _public_accounts()
    if not rows:
        return "暂无实例", None
    return "选一台开关机：", _power_keyboard()


def _switch_member_picker(group: dict):
    members = _group_members(group["id"])
    if not members:
        return f"【{group['name']}】没有成员", None
    doms = [d for d in db.list_domains() if d.get("group_id") == group["id"]]
    current_id = None
    if doms:
        d = sorted(doms, key=lambda x: (x.get("priority", 0), x["id"]))[0]
        current_id = d.get("current_account_id")
    rows = []
    for m in members:
        mark = "（当前服务）" if m["id"] == current_id else ""
        rows.append([(f"{m['name']}{mark}", f"switchto:{group['id']}:{m['id']}")])
    return f"【{group['name']}】切换到哪台：", _kb(rows)


def _cmd_switch(args: list[str]):
    groups = db.list_groups()
    if not groups:
        return "暂无分组", None
    if not args:
        return "选一个分组：", _kb([[(g["name"], f"switch:{g['id']}")] for g in groups])
    g = _resolve_group(args[0])
    if not g:
        return f"找不到分组：{args[0]}", None
    return _switch_member_picker(g)


def _do_switch(gid: int, aid: int) -> str:
    settings = db.get_settings()
    groups = {g["id"]: g for g in db.list_groups()}
    group = groups.get(gid)
    if not group:
        return "分组不存在"
    doms = [d for d in db.list_domains() if d.get("group_id") == gid and d.get("enabled")]
    if not doms:
        return f"【{group['name']}】没有启用的域名"
    d = sorted(doms, key=lambda x: (x.get("priority", 0), x["id"]))[0]
    target = db.get_account(aid)
    if not target:
        return "实例不存在"
    if aid not in [m["id"] for m in _group_members(gid)]:
        return f"{target['name']} 不在分组【{group['name']}】里"
    current_id = d.get("current_account_id")
    if current_id == aid:
        return f"{target['name']} 已是【{group['name']}】的当前服务实例"
    if (target.get("instance_status") or "").strip() != "Running":
        return (f"{target['name']} 未运行"
                f"（当前状态：{target.get('instance_status') or '未知'}），请先开机")
    ip = (target.get("public_ip") or "").strip()
    if not ip:
        return f"{target['name']} 没有公网 IP，无法切换"
    # DNS 同步复用调度器的 _DnsOps（Cloudflare 同步、缓存的 zone/record ID）。
    domain_view = scheduler._to_domain_views([d])[0]
    try:
        scheduler._DnsOps(settings).set_record(domain_view, ip)
    except Exception as exc:  # noqa: BLE001
        return f"DNS 同步失败：{exc}"
    # set_record 只返回“是否实际改了记录”：本来就等于新 IP 时也算成功，
    # 用库里 dns_records 的 ip 再校验一次（与换 EIP 同款做法）。
    rec = db.get_dns_record(d["id"])
    if rec and rec.get("ip") and rec.get("ip") != ip:
        return f"DNS 同步失败：记录仍指向 {rec.get('ip')}"
    current = db.get_account(current_id) if current_id else None
    db.update_domain(d["id"], {"fallback_active": 0, "current_account_id": aid,
                               "current_ip": ip})
    mode = (group.get("switch_mode") or settings.get("switch_mode") or "load_balance")
    rule = _switch_rule_text("manual", mode)
    if current:
        body = f"【{group['name']}】{current['name']} 已替换为 {target['name']}（{rule}）"
    elif d.get("fallback_active"):
        body = f"【{group['name']}】备用地址已替换为 {target['name']}（{rule}）"
    else:
        body = f"【{group['name']}】{target['name']} 已接管服务（{rule}）"
    # 手动切换也是一次 A→B：推一条分组动态，并记日志（与调度器 _notify 同款）。
    Notifier(settings).send("group_change", "分组动态", body)
    db.add_event("group_change", "分组动态", body, domain_id=d["id"])
    return body


def _cmd_timers(settings: dict) -> str:
    rows = db.list_accounts()
    lines = ["定时任务"]
    for a in rows:
        if a.get("daily_schedule_enabled"):
            on = a.get("daily_on_time") or "08:00"
            off = a.get("daily_off_time") or "20:00"
            lines.append(f"{a['name']}：{on} 开 / {off} 关")
        else:
            lines.append(f"{a['name']}：未启用")
    lines.append(f"每日推送：{settings.get('daily_report_time') or '00:00'}")
    return "\n".join(lines)


def _cmd_update() -> str:
    """面板更新：有新版本就触发在线更新（与铃铛“立即更新”同一条路）。"""
    st = update_mod.status()
    cur = st.get("current_version") or "dev"
    latest = (st.get("latest_version") or "")[:7]
    if not st.get("update_available"):
        return f"已是最新版本\n当前版本：{cur}"
    r = update_mod.start_update()
    if not r.get("started"):
        msg = r.get("message") or "无法在线更新"
        manual = r.get("manual")
        return f"{msg}" + (f"\n手动更新：{manual}" if manual else "")
    # 更新在后台线程跑，容器随即重启，机器人也跟着重启；这条回复在停机前发出。
    return (f"更新已开始，容器将重启\n当前版本：{cur}\n最新版本：{latest or '—'}"
            "\n机器人随容器重启，稍后恢复")


def _cmd_help() -> str:
    lines = ["可用命令："]
    for cmd, desc in COMMANDS:
        lines.append(f"/{cmd} {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

def _dispatch(settings: dict, cmd: str, args: list[str]):
    if cmd == "start":
        return _cmd_start(), None
    if cmd == "menu":
        return "请选择：", _menu_keyboard()
    if cmd == "list":
        return _cmd_list(), None
    if cmd == "status":
        return _cmd_status(), None
    if cmd == "groups":
        return _cmd_groups(settings), None
    if cmd == "power":
        return _cmd_power(args)
    if cmd == "switch":
        return _cmd_switch(args)
    if cmd == "timers":
        return _cmd_timers(settings), None
    if cmd == "update":
        return _cmd_update(), None
    if cmd == "help":
        return _cmd_help(), None
    return f"未知命令：/{cmd}\n输入 /help 查看命令", None


def _handle_message(settings: dict, message: dict) -> None:
    chat = message.get("chat") or {}
    if not _authorized(settings, chat.get("id")):
        return
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        _send(settings, "请用命令操作，输入 /help 查看命令")
        return
    tokens = text.split()
    cmd = tokens[0][1:].split("@")[0].lower()
    args = tokens[1:]
    reply, markup = _dispatch(settings, cmd, args)
    _send(settings, reply, markup)


def _handle_callback(settings: dict, callback: dict) -> None:
    chat = (callback.get("message") or {}).get("chat") or {}
    if not _authorized(settings, chat.get("id")):
        return
    _answer_callback(settings, callback.get("id") or "")
    data = callback.get("data") or ""
    if data.startswith("menu:"):
        key = data[5:]
        mapping = {"list": "list", "status": "status", "groups": "groups",
                   "timers": "timers", "update": "update", "help": "help"}
        if key == "power":
            reply, markup = _cmd_power([])
        elif key == "switch":
            reply, markup = _cmd_switch([])
        elif key in mapping:
            reply, markup = _dispatch(settings, mapping[key], [])
        else:
            return
        _send(settings, reply, markup)
    elif data.startswith("power:"):
        try:
            aid = int(data[6:])
        except ValueError:
            return
        _send(settings, _do_power(aid))
    elif data.startswith("switch:"):
        try:
            gid = int(data[7:])
        except ValueError:
            return
        g = next((x for x in db.list_groups() if x["id"] == gid), None)
        if not g:
            _send(settings, "分组不存在")
            return
        reply, markup = _switch_member_picker(g)
        _send(settings, reply, markup)
    elif data.startswith("switchto:"):
        try:
            _, gid, aid = data.split(":")
            gid, aid = int(gid), int(aid)
        except ValueError:
            return
        _send(settings, _do_switch(gid, aid))


# ---------------------------------------------------------------------------
# 轮询
# ---------------------------------------------------------------------------

def _poll_once(settings: dict) -> bool:
    try:
        offset = int(db.get_flag(OFFSET_FLAG) or "0")
    except (TypeError, ValueError):
        offset = 0
    result = _api("getUpdates", {"offset": offset, "timeout": POLL_TIMEOUT,
                                 "limit": 100}, settings,
                  timeout=POLL_TIMEOUT + 10)
    if result is None:
        return False
    max_id = offset
    for update in result:
        uid = update.get("update_id", 0)
        max_id = max(max_id, uid + 1)
        try:
            if "message" in update:
                _handle_message(settings, update["message"])
            elif "callback_query" in update:
                _handle_callback(settings, update["callback_query"])
        except Exception as exc:  # noqa: BLE001
            log.exception("机器人处理消息失败: %s", exc)
    if max_id != offset:
        db.set_flag(OFFSET_FLAG, str(max_id))
    return True


def _loop() -> None:
    global _commands_registered
    while not _stop.is_set():
        try:
            settings = db.get_settings()
        except Exception as exc:  # noqa: BLE001
            log.warning("机器人读设置失败: %s", exc)
            _stop.wait(timeout=ERROR_BACKOFF_SECONDS)
            continue
        if not _enabled(settings):
            # 未开启或未配齐 Token/Chat ID：每 10 秒再看一次，开关改完即生效。
            _stop.wait(timeout=IDLE_WAIT_SECONDS)
            continue
        if not _commands_registered:
            _register_commands(settings)
            _commands_registered = True
        if not _poll_once(settings):
            _stop.wait(timeout=ERROR_BACKOFF_SECONDS)
