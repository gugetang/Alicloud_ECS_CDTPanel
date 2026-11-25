"""Telegram 机器人控制：用 Bot API 轮询收命令，控制面板实例与分组。

- 接入：getUpdates 长轮询（timeout=30），走 notify_proxy；offset 存 flags
  （bot_update_offset），重启不丢命令、不重复执行。
- 鉴权：只认绑定的 telegram_chat_id，其他人一律忽略、不回复。
- 开关：bot_enabled=1 才轮询（默认关）；线程随面板启动，开关在通知设置
  Telegram 卡片里，改完 10 秒内生效，不用重启容器。
- 命令：/start /menu /list /groups /timers /update /help
  （/status 已取消；/reboot 已取消；开关机并进 /list：列表只显示名称，
  名称前圆点表示状态，点一台进详情页开关机；分组列表并进 /groups：
  点分组进子菜单，只看成员，不可操作——当前服务实例由负载均衡控制，
  手动切换无效）。
- 列表圆点：绿=在线（含启动中），黄=排队，红=停机/停止中/离线，
  白=离队/未参与/未知/已跳出。
- 开关机按钮：已停机显示“开机”，运行中显示“关闭”，跟面板普通开关机
  同款切换逻辑。
- 实例详情页：开机/关闭切换（跟面板普通开关机一致：关闭是普通停机）、
  节省停机（仅运行中）、更换 EIP（仅 ECS，轻量服务器不显示）、
  定时任务（可编辑子菜单：开关、开机时间、关机时间），
  底部有返回实例列表和返回目录。
- 服务器名称换行显示 IP：名称在上、IP 在下，IP 只显示 A、B 两段，
  C、D 两段用 * 替换（47.243.214.192 → 47.243.*.*），整个机器人系统都这样。
- 每个菜单底部都有返回上级和返回目录：实例列表/分组列表/面板更新/
  帮助回目录；详情页、分组子菜单、定时子菜单回上级 + 回目录。
  返回按钮用图标显示：⬅️ 返回，🏠 返回目录。
- 所有操作复用面板现有逻辑：api.start_account / stop_account /
  replace_eip；定时任务用 db.update_account 改每日定时字段。
"""
from __future__ import annotations

import logging
import re
import threading
import time

import requests

from . import api as api_mod
from . import db, scheduler
from . import update as update_mod
from .aliyun import is_swas_instance_id
from .update import current_version

log = logging.getLogger(__name__)

POLL_TIMEOUT = 30
IDLE_WAIT_SECONDS = 10
ERROR_BACKOFF_SECONDS = 10
OFFSET_FLAG = "bot_update_offset"
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# 站点 logo：显示在站点名称前面，整个机器人系统都这样。
_SITE_LOGO = "☁️"

# Unicode 粗体映射：Telegram 按钮文字不支持 HTML/Markdown，只能用字符级
# 粗体（拉丁字母/数字）；中文等没有粗体变体，原样返回。
_BOLD_MAP = {**{c: chr(0x1D400 + ord(c) - 65)
               for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
             **{c: chr(0x1D41A + ord(c) - 97)
               for c in "abcdefghijklmnopqrstuvwxyz"},
             **{c: chr(0x1D7CE + ord(c) - 48) for c in "0123456789"}}


def _bold(text) -> str:
    """名称加粗：拉丁字母/数字转 Unicode 粗体，中文等原样返回。"""
    return "".join(_BOLD_MAP.get(ch, ch) for ch in str(text))


def _mask_ip(ip) -> str:
    """公网 IP 只显示 A、B 两段，C、D 两段用 * 替换（隐私）：
    47.243.214.192 → 47.243.*.*；没有 IP 或不是 IPv4 原样返回。"""
    if not ip:
        return "-"
    parts = str(ip).strip().split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.*.*"
    return str(ip)

# 命令表：/help 与 setMyCommands 共用；/status、/power、/switch 已取消
# （开关机并进 /list，切换分组成员并进 /groups 的子菜单），不再出现。
COMMANDS = [
    ("start", "检查机器人"),
    ("menu", "交互菜单"),
    ("list", "实例列表"),
    ("groups", "分组列表"),
    ("timers", "定时任务"),
    ("update", "面板更新"),
    ("help", "帮助"),
]

# 列表名称前的状态圆点：与面板 status_dot 一致。
STATUS_DOTS = {"green": "🟢", "green_pulse": "🟢", "yellow": "🟡",
               "red": "🔴", "red_pulse": "🔴"}

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


def _edit(settings: dict, chat_id, message_id: int, text: str,
          reply_markup: dict | None = None) -> bool:
    """就地改一条已发消息的文字和按钮（editMessageText），不新发气泡。
    改不动（消息太旧/已删）返回 False，调用方再发新消息。
    内容没变时 Telegram 会报 message is not modified，也返回 False。"""
    payload: dict = {"chat_id": chat_id, "message_id": message_id,
                     "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _api("editMessageText", payload, settings) is not None


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


def _pair_rows(buttons: list) -> list:
    """内容按钮每行两个（最后一行不足两个就剩一个）。"""
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


def _back_row(back_cb: str | None = None) -> list:
    """返回按钮固定在最底部一行：返回在左、返回目录在右；只有返回目录时就一个按钮。"""
    row = []
    if back_cb:
        row.append(("⬅️ 返回", back_cb))
    row.append(("🏠 返回目录", "menu:menu"))
    return row


def _kb(rows: list) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                for row in rows]}


def _kb_sig(markup: dict | None):
    """把键盘转成可比较的签名：只取每行每个按钮的文字与回调，忽略
    Telegram 回包里可能多出的字段。没键盘返回空元组。"""
    if not markup:
        return ()
    return tuple(
        tuple((b.get("text"), b.get("callback_data")) for b in row)
        for row in (markup.get("inline_keyboard") or []))


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

def _cmd_start(settings: dict) -> str:
    # 开头是站点 logo + 加粗站点名，再是机器人在线、版本、命令提示。
    return (f"{_site_title(settings)}\n机器人在线\n"
            f"面板版本：{current_version()}\n输入 /help 查看命令")


def _menu_keyboard() -> dict:
    # 主菜单：内容按钮每行两个。定时任务已移到实例详情页（更换 EIP 后面），
    # 这里不再放，主菜单剩 4 个按钮，正好两行两列。
    return _kb(_pair_rows([
        ("实例列表", "menu:list"),
        ("分组列表", "menu:groups"),
        ("面板更新", "menu:update"),
        ("帮助", "menu:help"),
    ]))


def _status_dot(r: dict) -> str:
    return STATUS_DOTS.get(r.get("status_dot") or "", "⚪")


def _site_name(settings: dict) -> str:
    """页面标题跟随站点名（全局设置里的站点名称）。"""
    return (settings.get("site_name") or "CDT 面板").strip()


def _site_title(settings: dict) -> str:
    """页面标题：站点 logo + 加粗站点名，整个机器人系统都这样。"""
    return f"{_SITE_LOGO} {_bold(_site_name(settings))}"


def _cmd_list(settings: dict, args: list[str]):
    """实例列表：名称在上、IP 在下（IP 只显示 A、B 两段，C、D 用 * 替换），
    名称前圆点表示状态；点一台进详情页开关机。标题跟随站点名。"""
    if args:
        a = _resolve_account(args[0])
        if not a:
            return f"找不到实例：{args[0]}", None
        return _instance_detail(a, settings)
    rows = _public_accounts()
    if not rows:
        return "暂无实例", None
    kb_rows = _pair_rows([
        (f"{_status_dot(r)} {_bold(r['name'])}\nIP：{_mask_ip(r.get('public_ip'))}",
         f"detail:{r['id']}")
        for r in rows])
    kb_rows.append(_back_row())
    return _site_title(settings), _kb(kb_rows)


def _back_to_menu() -> dict:
    return _kb([[("🏠 返回目录", "menu:menu")]])


def _instance_detail(a: dict, settings: dict):
    """实例详情页：标题跟随站点名；实例信息做成双行显示按钮（只显示，
    不可点）：第 1 行圆点 + 名称，第 2 行 IP（只显示 A、B 两段，C、D
    用 * 替换）；状态、分组两行已删。
    下面是开关机、节省停机、更换 EIP、定时任务 + 返回实例列表/返回目录。
    更换 EIP 只对 ECS 实例显示（轻量服务器没有 EIP）；定时任务在更换
    EIP 后面，点开是可编辑子菜单（开关、开机时间、关机时间）。"""
    rows = _public_accounts()
    r = next((x for x in rows if x["id"] == a["id"]), a)
    info = (f"{_status_dot(r)} {_bold(r['name'])}"
            f"\nIP：{_mask_ip(r.get('public_ip'))}")
    actions = [(_power_label(r), f"power:{r['id']}")]
    if (r.get("instance_status") or "").strip() == "Running":
        actions.append(("节省停机", f"powersave:{r['id']}"))
    if not is_swas_instance_id(r.get("instance_id")):
        actions.append(("更换 EIP", f"eip:{r['id']}"))
    actions.append(("定时任务", f"timers:{r['id']}"))
    kb_rows = [[(info, "noop")]]
    kb_rows.extend(_pair_rows(actions))
    kb_rows.append(_back_row("menu:list"))
    return _site_title(settings), _kb(kb_rows)


def _power_label(a: dict) -> str:
    # 已停机显示“开机”，运行中显示“关闭”，跟面板普通开关机同款切换。
    return "开机" if (a.get("instance_status") or "").strip() == "Stopped" else "关闭"


def _do_power(aid: int) -> str:
    """普通开关机（跟面板普通开关机一致）：已停机→开机；运行中→普通停机
    （KeepCharging，停机后继续计费）。节省停机是单独的按钮（_do_power_save）。"""
    a = db.get_account(aid)
    if not a:
        return "账户不存在"
    stopped = (a.get("instance_status") or "").strip() == "Stopped"
    try:
        if stopped:
            api_mod.start_account(aid, True)
        else:
            api_mod.stop_account(aid, {"stop_charging": False}, True)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        return f"{_bold(a['name'])} {'开机' if stopped else '停机'}失败：{detail}"
    return f"{_bold(a['name'])} 已发送{'开机' if stopped else '停机'}指令"


def _do_power_save(aid: int) -> str:
    """节省停机（StopCharging，停机后停止计费）：仅运行中可用。"""
    a = db.get_account(aid)
    if not a:
        return "账户不存在"
    if (a.get("instance_status") or "").strip() != "Running":
        return f"{_bold(a['name'])} 未运行，无需节省停机"
    try:
        api_mod.stop_account(aid, {"stop_charging": True}, True)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        return f"{_bold(a['name'])} 节省停机失败：{detail}"
    return f"{_bold(a['name'])} 已发送节省停机指令"


def _do_replace_eip(aid: int) -> str:
    """更换 EIP：复用面板的 replace_eip（申请新 EIP → 解绑旧 → 绑新
    → 带宽一致 → 刷新 IP/DNS → 释放旧；失败自动回滚）。成功/失败的
    日志和推送由 replace_eip 自己记，这里只回一条结果。"""
    a = db.get_account(aid)
    if not a:
        return "账户不存在"
    try:
        out = api_mod.replace_eip(aid)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        return f"{_bold(a['name'])} 更换 EIP 失败：{detail}"
    msg = (f"{_bold(a['name'])} EIP 已更换：{_mask_ip(out.get('old_ip'))}"
           f" → {_mask_ip(out.get('new_ip'))}")
    if out.get("dns_domains"):
        msg += f"；DNS 已切：{', '.join(out['dns_domains'])}"
    if out.get("warning"):
        msg += f"；{out['warning']}"
    return msg


def _pending_timer_key(chat_id) -> str:
    """等用户回复 HH:MM 的暂存键：值是“实例 id:on/off”。"""
    return f"bot_pending_timer:{chat_id}"


def _timers_detail(a: dict, settings: dict):
    """实例定时任务子菜单（可编辑）：定时开关、开机时间、关机时间。
    点“开机时间/关机时间”后直接回复 HH:MM 即可修改；
    点“定时开关”切换启用/停用。返回回实例详情页。"""
    rows = _public_accounts()
    r = next((x for x in rows if x["id"] == a["id"]), a)
    cur = db.get_account(a["id"]) or {}
    enabled = bool(cur.get("daily_schedule_enabled"))
    on = cur.get("daily_on_time") or "08:00"
    off = cur.get("daily_off_time") or "20:00"
    info = (f"{_status_dot(r)} {_bold(r['name'])}"
            f"\n定时：{'已开启' if enabled else '未启用'}　{on} 开 / {off} 关")
    actions = [
        (f"定时开关：{'开启' if enabled else '关闭'}",
         f"timer_toggle:{r['id']}"),
        (f"开机时间：{on}", f"timer_set_on:{r['id']}"),
        (f"关机时间：{off}", f"timer_set_off:{r['id']}"),
    ]
    kb_rows = [[(info, "noop")]]
    kb_rows.extend(_pair_rows(actions))
    kb_rows.append(_back_row(f"detail:{r['id']}"))
    return _site_title(settings), _kb(kb_rows)


def _do_timer_toggle(aid: int) -> str:
    """切换实例的每日定时开关机。"""
    cur = db.get_account(aid)
    if not cur:
        return "账户不存在"
    enabled = 0 if cur.get("daily_schedule_enabled") else 1
    try:
        db.update_account(aid, {"daily_schedule_enabled": enabled})
    except ValueError as exc:
        return f"{_bold(cur['name'])} 定时开关切换失败：{exc}"
    return f"{_bold(cur['name'])} 定时任务已{'开启' if enabled else '关闭'}"


def _handle_timer_input(settings: dict, chat_id, pending: str, text: str) -> None:
    """处理定时时间的回复：校验 HH:MM，保存后回到定时子菜单。
    pending 的值是“实例 id:on/off”。"""
    key = _pending_timer_key(chat_id)
    try:
        aid_s, field = pending.split(":", 1)
        aid = int(aid_s)
    except ValueError:
        db.delete_flag(key)
        return
    if not _HHMM_RE.match((text or "").strip()):
        _send(settings, "时间格式不对，请用 HH:MM 回复（例如 08:00）")
        return
    text = text.strip()
    cur = db.get_account(aid)
    if not cur:
        db.delete_flag(key)
        _send(settings, "账户不存在")
        return
    other_key = "daily_off_time" if field == "on" else "daily_on_time"
    if cur.get("daily_schedule_enabled") and (cur.get(other_key) or "") == text:
        _send(settings, "开机时间与关机时间不能相同，请重新回复")
        return
    try:
        db.update_account(aid, {
            "daily_on_time" if field == "on" else "daily_off_time": text})
    except ValueError as exc:
        _send(settings, f"保存失败：{exc}")
        return
    db.delete_flag(key)
    reply, markup = _timers_detail(cur, settings)
    _send(settings, reply, markup)


def _cmd_groups(settings: dict, args: list[str]):
    """分组列表：只显示分组名；点一个分组进子菜单看成员（只显示，不可操作）。
    标题跟随站点名。"""
    groups = db.list_groups()
    if not groups:
        return "暂无分组", None
    if args:
        g = _resolve_group(args[0])
        if not g:
            return f"找不到分组：{args[0]}", None
        return _group_detail(g, settings)
    kb_rows = _pair_rows([(_bold(g["name"]), f"group:{g['id']}") for g in groups])
    kb_rows.append(_back_row())
    return _site_title(settings), _kb(kb_rows)


def _group_detail(group: dict, settings: dict):
    """分组子菜单：标题跟随站点名；分组信息做成单行显示按钮（只显示，
    不可点）：【组名（加粗）】（模式，不加粗），域名不显示（太长会被截断）。
    成员只列在线/排队中的（带状态圆点），由负载均衡控制当前服务实例，
    手动切换无效，所以成员按钮点了一点反应都没有（noop 回调）。
    “当前服务”“成员”这两行字已删掉。"""
    gid = group["id"]
    members = _group_members(gid)
    info = f"【{_bold(group['name'])}】（{_group_mode_label(group, settings)}）"
    kb_rows = [[(info, "noop")]]
    if not members:
        kb_rows.append([("该分组没有成员", "noop")])
        kb_rows.append(_back_row("menu:groups"))
        return _site_title(settings), _kb(kb_rows)
    # 只显示在线/排队中的成员（带状态圆点）；已停机/离队/离线的不显示。
    shown = [m for m in members
             if m.get("status_label") in ("在线", "排队中")]
    if not shown:
        kb_rows.append([("暂无在线或排队中的成员", "noop")])
        kb_rows.append(_back_row("menu:groups"))
        return _site_title(settings), _kb(kb_rows)
    # 成员做成按钮但不可选中：名称在上、IP 在下（IP 只显示 A、B 两段，
    # C、D 用 * 替换），点一下只消掉加载圈，不做任何事（noop 回调）。
    kb_rows.extend(_pair_rows([
        (f"{_status_dot(m)} {_bold(m['name'])}\nIP：{_mask_ip(m.get('public_ip'))}",
         "noop")
        for m in shown]))
    kb_rows.append(_back_row("menu:groups"))
    return _site_title(settings), _kb(kb_rows)


def _cmd_timers(settings: dict) -> str:
    rows = db.list_accounts()
    lines = ["定时任务"]
    for a in rows:
        if a.get("daily_schedule_enabled"):
            on = a.get("daily_on_time") or "08:00"
            off = a.get("daily_off_time") or "20:00"
            lines.append(f"{_bold(a['name'])}：{on} 开 / {off} 关")
        else:
            lines.append(f"{_bold(a['name'])}：未启用")
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
        return _cmd_start(settings), None
    if cmd == "menu":
        # 主菜单标题就是站点名（跟随全局设置里的站点名称）。
        return _site_title(settings), _menu_keyboard()
    if cmd == "list":
        return _cmd_list(settings, args)
    if cmd == "groups":
        return _cmd_groups(settings, args)
    if cmd == "timers":
        return _cmd_timers(settings), _back_to_menu()
    if cmd == "update":
        return _cmd_update(), _back_to_menu()
    if cmd == "help":
        return _cmd_help(), _back_to_menu()
    return f"未知命令：/{cmd}\n输入 /help 查看命令", None


def _handle_message(settings: dict, message: dict) -> None:
    chat = message.get("chat") or {}
    if not _authorized(settings, chat.get("id")):
        return
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")
    pending = db.get_flag(_pending_timer_key(chat_id))
    if text.startswith("/"):
        # 命令优先：取消未完成的定时时间输入，再处理命令。
        if pending:
            db.delete_flag(_pending_timer_key(chat_id))
        tokens = text.split()
        cmd = tokens[0][1:].split("@")[0].lower()
        args = tokens[1:]
        reply, markup = _dispatch(settings, cmd, args)
        _send(settings, reply, markup)
        return
    if pending:
        # 正在等用户回复一个 HH:MM 定时时间。
        _handle_timer_input(settings, chat_id, pending, text)
        return
    _send(settings, "请用命令操作，输入 /help 查看命令")


def _handle_callback(settings: dict, callback: dict) -> None:
    msg = callback.get("message") or {}
    chat = msg.get("chat") or {}
    if not _authorized(settings, chat.get("id")):
        return
    cb_id = callback.get("id") or ""
    _answer_callback(settings, cb_id)
    data = callback.get("data") or ""
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    current_text = msg.get("text") or ""

    def show(text: str, markup: dict | None):
        # 导航就地改同一条消息，不新发气泡；文字和按钮都没变才跳过
        # （各页标题都跟随站点名，文字相同是常态，只比文字会把换页
        # 误判成没变化，导致点列表/分组/实例都没反应）。
        # 没消息可改或改不动（消息太旧）再发新消息。
        if text == current_text and _kb_sig(markup) == _kb_sig(msg.get("reply_markup")):
            return
        if message_id and _edit(settings, chat_id, message_id, text, markup):
            return
        _send(settings, text, markup)

    def refresh_detail(aid: int):
        a = next((x for x in _public_accounts() if x["id"] == aid), None)
        if a:
            reply, markup = _instance_detail(a, settings)
            show(reply, markup)

    def refresh_timers(aid: int):
        a = next((x for x in _public_accounts() if x["id"] == aid), None)
        if a:
            reply, markup = _timers_detail(a, settings)
            show(reply, markup)

    # 点任何按钮都取消未完成的定时时间输入（timer_set 两个分支会重新设）。
    if not (data.startswith("timer_set_on:")
            or data.startswith("timer_set_off:")):
        db.delete_flag(_pending_timer_key(chat_id))

    if data == "noop":
        # 展示用按钮（分组子菜单的成员）：点了一点反应都没有。
        return
    if data.startswith("menu:"):
        key = data[5:]
        mapping = {"menu": "menu", "list": "list", "groups": "groups",
                   "timers": "timers", "update": "update", "help": "help"}
        if key in mapping:
            reply, markup = _dispatch(settings, mapping[key], [])
        else:
            return
        show(reply, markup)
    elif data.startswith("detail:"):
        try:
            aid = int(data[7:])
        except ValueError:
            return
        a = next((x for x in _public_accounts() if x["id"] == aid), None)
        if not a:
            show("实例不存在", None)
            return
        reply, markup = _instance_detail(a, settings)
        show(reply, markup)
    elif data.startswith("power:"):
        try:
            aid = int(data[6:])
        except ValueError:
            return
        _answer_callback(settings, cb_id, _do_power(aid))
        refresh_detail(aid)
    elif data.startswith("powersave:"):
        try:
            aid = int(data[10:])
        except ValueError:
            return
        _answer_callback(settings, cb_id, _do_power_save(aid))
        refresh_detail(aid)
    elif data.startswith("eip:"):
        try:
            aid = int(data[4:])
        except ValueError:
            return
        _answer_callback(settings, cb_id, _do_replace_eip(aid))
        refresh_detail(aid)
    elif data.startswith("timers:"):
        try:
            aid = int(data[7:])
        except ValueError:
            return
        a = next((x for x in _public_accounts() if x["id"] == aid), None)
        if not a:
            show("实例不存在", None)
            return
        reply, markup = _timers_detail(a, settings)
        show(reply, markup)
    elif data.startswith("timer_toggle:"):
        try:
            aid = int(data[13:])
        except ValueError:
            return
        _answer_callback(settings, cb_id, _do_timer_toggle(aid))
        refresh_timers(aid)
    elif data.startswith("timer_set_on:"):
        try:
            aid = int(data[13:])
        except ValueError:
            return
        a = next((x for x in _public_accounts() if x["id"] == aid), None)
        if not a:
            return
        db.set_flag(_pending_timer_key(chat_id), f"{aid}:on")
        _send(settings, f"请直接回复 {_bold(a['name'])} 的开机时间，格式 HH:MM"
                        "（例如 08:00）；发送其他命令可取消。")
    elif data.startswith("timer_set_off:"):
        try:
            aid = int(data[14:])
        except ValueError:
            return
        a = next((x for x in _public_accounts() if x["id"] == aid), None)
        if not a:
            return
        db.set_flag(_pending_timer_key(chat_id), f"{aid}:off")
        _send(settings, f"请直接回复 {_bold(a['name'])} 的关机时间，格式 HH:MM"
                        "（例如 20:00）；发送其他命令可取消。")
    elif data.startswith("group:"):
        try:
            gid = int(data[6:])
        except ValueError:
            return
        g = next((x for x in db.list_groups() if x["id"] == gid), None)
        if not g:
            show("分组不存在", None)
            return
        reply, markup = _group_detail(g, settings)
        show(reply, markup)


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
