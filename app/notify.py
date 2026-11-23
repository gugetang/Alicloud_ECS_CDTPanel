"""通知：Telegram（走通知专用代理，支持 http 代理 / SOCKS5）+ SMTP + Webhook。

- 事件开关：settings.notify_*，关闭的事件不发。
- 去重：持续故障只在首次发生和恢复时通知（fault registry 由调用方用 db.flags
  持久化，key 如 fault:<account_id> / fault:<domain>）。
- 测试通知使用已保存的配置。
- 每日财报（build_daily_report / send_daily_report）：每天 daily_report_time
  推送一次；Telegram 用结构排版代替调字号（标题加粗、每行冒号后面的值
  加粗、账户间空行 + 分隔线、每账户 ≤7 行、每条 ≤5 个账户、≤4096 字符）。
- 邮件：配了通知专用代理时，SMTP 经代理的 HTTP CONNECT 隧道发
  （直连 smtp.gmail.com 被墙时走代理）；没配代理时直连 SMTP_SSL。
"""
from __future__ import annotations

import html as htmlmod
import http.client
import logging
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse

import requests

from . import settings as cfg

log = logging.getLogger("cdt.notify")

DAILY_TITLE = "[AliCloud每日报告]"


def daily_title(site_name: str = "") -> str:
    """日报标题跟随站点名称：`[{站点名}每日报告]`；没填站点名时沿用
    旧标题 `[AliCloud每日报告]`。"""
    name = (site_name or "").strip() or "AliCloud"
    return f"[{name}每日报告]"
# Telegram 单条消息上限 4096 字符；留余量，单条财报最多 4000 字符、5 个账户
DAILY_MAX_ACCOUNTS_PER_MESSAGE = 5
DAILY_MAX_CHARS = 4000
DAILY_SEPARATOR = "──────"

DOT_EMOJI = {"green": "🟢", "yellow": "🟡", "gray": "⚪", "red": "🔴",
             "green_pulse": "🟢", "red_pulse": "🔴"}


def format_money(amount, currency: str) -> str:
    """金额显示：USD→$、CNY→¥，保留 2 位；无数据时显示 —（不造假）。"""
    if amount is None:
        return "—"
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return "—"
    if (currency or "USD") == "CNY":
        return f"¥{v:.2f}"
    if (currency or "USD") == "USD":
        return f"${v:.2f}"
    return f"{v:.2f} {currency}"


def _account_warnings(a: dict) -> list[str]:
    """财报评论 ⚠️ 的原因（任一异常即 ⚠️，否则 ✅）。"""
    reasons: list[str] = []
    status = (a.get("instance_status") or "").strip()
    if a.get("evicted"):
        rate = a.get("usage_rate")
        reasons.append(f"超阈值已跳出（使用率 {rate}% ≥ 阈值）"
                       if rate is not None else "超阈值已跳出")
    if status == "Stopped":
        reasons.append("已停机")
    elif status and status not in ("Running", "Starting", "Pending"):
        reasons.append("离线")
    elif not status:
        reasons.append("状态未知")
    if a.get("traffic_used_gb") is None:
        reasons.append("流量未知")
    try:
        if a.get("billing_month_cost") is not None and float(a["billing_month_cost"]) < 0:
            reasons.append("费用异常")
    except (TypeError, ValueError):
        pass
    return reasons


def _account_status_text(a: dict) -> tuple[str, str]:
    """财报状态行：(点 emoji, 中文文案)。优先用 _public_account 的新状态点。"""
    dot = (a.get("status_dot") or "").strip()
    label = (a.get("status_label") or "").strip()
    if dot and label:
        return DOT_EMOJI.get(dot, "⚪"), label
    if a.get("evicted"):
        return "⚪", "已跳出"
    status = (a.get("instance_status") or "").strip()
    if status == "Running":
        return "🟢", "在线"
    if status in ("Starting", "Pending"):
        return "🟢", "启动中"
    if status == "Stopping":
        return "🔴", "停止中"
    if status == "Stopped":
        return "🔴", "已停机"
    return "🔴", "离线"


def _mask_ip(ip: str | None) -> str:
    """日报里的 IP 只显示 A.B 段，C.D 用 * 代替（与前端 maskIp 一致）；
    无 IP 显示 —，非 IPv4 原样返回。"""
    ip = (ip or "").strip()
    if not ip:
        return "—"
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else ip


def _account_block(a: dict) -> dict:
    """单个账户的财报块：每账户 ≤7 行。返回 {"html": [...行], "text": [...行]}。

    布局（每行图标 + 标签）：名称（👤）/ 状态（💻）/ IP（🌐，只显示 A.B 段）/
    流量（📊）/ 账单（💰）/ 余额（💰）/ 评价（💬，✅/⚠️）。
    冒号后面的值加粗（HTML 版 <b>，标签不加粗）；纯文本版无加粗，内容一致。
    """
    raw_name = str(a.get("name") or "未命名")
    head = f"👤 名称：<b>{htmlmod.escape(raw_name)}</b>"
    head_text = f"👤 名称：{raw_name}"
    emoji, label = _account_status_text(a)
    status_line = f"💻 状态：<b>{label}（{emoji}）</b>"
    status_text = f"💻 状态：{label}（{emoji}）"
    ip = _mask_ip(a.get("public_ip"))
    if a.get("traffic_used_gb") is None:
        traffic_html, traffic_text = "📊 流量：<b>—（未知）</b>", "📊 流量：—（未知）"
    else:
        rate = a.get("usage_rate")
        if rate is None and a.get("quota_gb"):
            try:
                rate = round(float(a["traffic_used_gb"]) / float(a["quota_gb"]) * 100, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                rate = None
        num = (f"{float(a['traffic_used_gb']):.2f} GB ({rate:.2f}%)"
               if rate is not None else f"{float(a['traffic_used_gb']):.2f} GB")
        traffic_html, traffic_text = f"📊 流量：<b>{num}</b>", f"📊 流量：{num}"
    cost = format_money(a.get("billing_month_cost"), a.get("billing_currency") or "USD")
    bal = format_money(a.get("billing_balance"),
                       a.get("billing_balance_currency") or a.get("billing_currency") or "USD")
    reasons = _account_warnings(a)
    comment = "✅" if not reasons else f"⚠️ {'、'.join(reasons)}"
    html_lines = [head, status_line, f"🌐 IP：<b>{htmlmod.escape(ip)}</b>", traffic_html,
                  f"💰 账单：<b>{cost}</b>", f"💰 余额：<b>{bal}</b>", f"💬 评价：<b>{comment}</b>"]
    text_lines = [head_text, status_text, f"🌐 IP：{ip}", traffic_text,
                  f"💰 账单：{cost}", f"💰 余额：{bal}", f"💬 评价：{comment}"]
    return {"html": html_lines, "text": text_lines}


def filter_daily_report_accounts(accounts: list[dict], settings: dict,
                                 *, force: bool = False) -> list[dict]:
    """日报推送账户过滤：实例级“参与日报推送”已取消，全部跟随全局开关。

    全局 daily_report_enabled=1（或 force=True，如“发送测试财报”）时全部包含，
    否则全部排除；实例自身的 daily_report 字段不再读取。"""
    enabled = (settings.get("daily_report_enabled", "1") == "1")
    if force or enabled:
        return list(accounts)
    return []


def build_daily_report(accounts: list[dict], date_str: str,
                       site_name: str = "") -> list[dict]:
    """渲染每日财报，返回消息块列表，每块 {"html", "text", "email_html"}。

    排版规则（Telegram 不支持调字号，用结构代替）：
    标题加粗、每行冒号后面的值加粗（标签不加粗、不要整条全加粗）；
    每账户 ≤7 行，账户间空行 + 分隔线；每条 ≤5 个账户且 ≤4000 字符，
    超出时分条（多条时标题后标注“（第 x/y 部分）”）。
    标题跟随站点名称（site_name 为空时沿用 [AliCloud每日报告]）。
    """
    title = daily_title(site_name)
    blocks = [_account_block(a) for a in accounts]
    # 先按 5 个账户一组分块，再检查字符数，超长则继续拆
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for b in blocks:
        if len(cur) >= DAILY_MAX_ACCOUNTS_PER_MESSAGE:
            chunks.append(cur)
            cur = []
        cur.append(b)
    if cur:
        chunks.append(cur)
    # 字符数兜底：任一块超 4000 字符就按账户再拆
    final: list[list[dict]] = []
    for ch in chunks:
        while ch:
            take = ch[:]
            while take and _chunk_len(take, date_str, title) > DAILY_MAX_CHARS:
                take = take[:-1]
            if not take:  # 单个账户也超长（极端情况）：仍单独发一条
                take = ch[:1]
            final.append(take)
            ch = ch[len(take):]
    out: list[dict] = []
    total = len(final)
    for i, ch in enumerate(final):
        part = f"（第 {i + 1}/{total} 部分）" if total > 1 else ""
        header_html = f"<b>{title}</b>\n日期：{date_str}{part}"
        header_text = f"{title}\n日期：{date_str}{part}"
        body_html = "\n\n" + f"\n\n{DAILY_SEPARATOR}\n\n".join(
            "\n".join(b["html"]) for b in ch)
        body_text = "\n\n" + f"\n\n{DAILY_SEPARATOR}\n\n".join(
            "\n".join(b["text"]) for b in ch)
        email_html = (
            f'<div style="font-size:16px;font-weight:700">{htmlmod.escape(title)}</div>'
            f'<div style="font-size:12px;color:#666">日期：{htmlmod.escape(date_str)}{htmlmod.escape(part)}</div>'
            + "".join(
                f'<div style="font-size:14px;line-height:1.6;margin:12px 0">'
                + "<br>".join(b["html"]) + "</div><hr>"
                for b in ch))
        out.append({"html": header_html + body_html, "text": header_text + body_text,
                    "email_html": email_html, "accounts": len(ch)})
    return out


def _chunk_len(chunk: list[dict], date_str: str, title: str) -> int:
    header = f"<b>{title}</b>\n日期：{date_str}"
    body = "\n\n" + f"\n\n{DAILY_SEPARATOR}\n\n".join("\n".join(b["html"]) for b in chunk)
    return len(header + body)


def _connect_via_proxy(proxy_url: str, host: str, port: int,
                       timeout: float = 15) -> object:
    """经 HTTP 代理的 CONNECT 方法建到 host:port 的 TCP 隧道，返回已通的 socket。
    代理不支持 CONNECT / 拒绝时抛异常（调用方把原因记进 last_errors）。
    只支持 http(s):// 代理；socks5:// 代理不在此走（requests 侧才支持 SOCKS5）。"""
    pu = urlparse(proxy_url)
    proxy_host = pu.hostname or ""
    proxy_port = pu.port or 80
    if not proxy_host:
        raise ValueError(f"代理地址无效：{proxy_url}")
    conn = http.client.HTTPConnection(proxy_host, proxy_port, timeout=timeout)
    conn.connect()
    try:
        conn.putrequest("CONNECT", f"{host}:{port}")
        conn.putheader("Host", f"{host}:{port}")
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        if resp.status != 200:
            raise ConnectionError(
                f"代理 CONNECT {host}:{port} 失败：HTTP {resp.status}")
        return conn.sock
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise


def _smtp_client(host: str, port: int, proxy: str = "",
                 timeout: float = 15) -> smtplib.SMTP:
    """返回已连好的 SMTP 对象（调用方负责 quit()/close()）。
    配了代理时经 CONNECT 隧道 + TLS；没配代理时直连 SMTP_SSL。
    465 端口是隐式 TLS（SMTP_SSL）；经代理时同样先建隧道再包 TLS。"""
    if proxy:
        raw = _connect_via_proxy(proxy, host, port, timeout)
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw, server_hostname=host)
        smtp = smtplib.SMTP(timeout=timeout)
        smtp.sock = sock
        smtp.file = sock.makefile("rb")
        return smtp
    return smtplib.SMTP_SSL(host, port, timeout=timeout)


class Notifier:
    def __init__(self, settings: dict):
        self.s = settings
        # 最近一次发送各渠道的失败原因（渠道名 → 错误描述），供 /notify/test
        # 等接口回给前端展示；成功时该渠道不在其中。
        self.last_errors: dict[str, str] = {}

    def enabled(self, event: str) -> bool:
        key = cfg.EVENT_TOGGLE_KEYS.get(event)
        return self.s.get(key, "1") == "1" if key else True

    def _proxies(self) -> dict:
        proxy = (self.s.get("notify_proxy") or "").strip()
        if not proxy:
            return {}
        return {"http": proxy, "https": proxy}

    def send(self, event: str, title: str, body: str,
             channel: str | None = None, force: bool = False) -> list[str]:
        """按已启用的渠道发送，返回实际发送的渠道名。开关关闭时返回 []。
        channel 指定时只发该渠道（telegram/email/webhook），用于通知设置里
        各渠道框体右下角的“发送测试”。force=True（测试）时跳过事件开关——
        测试是用户主动点的，不应被事件开关拦住（否则“发送测试通知”点了也
        收不到，还误以为 TG 配置有问题）。失败原因记在 self.last_errors。"""
        self.last_errors = {}
        if not force and not self.enabled(event):
            return []
        sent: list[str] = []
        text = f"{title}\n{body}".strip()
        if (channel is None or channel == "telegram") and self.s.get("telegram_enabled") == "1":
            ok, detail = self._telegram(text)
            if ok:
                sent.append("telegram")
            elif detail:
                self.last_errors["telegram"] = detail
        if (channel is None or channel == "email") and self.s.get("email_enabled") == "1":
            ok, detail = self._email(title, text)
            if ok:
                sent.append("email")
            elif detail:
                self.last_errors["email"] = detail
        if (channel is None or channel == "webhook") and self.s.get("webhook_enabled") == "1":
            if self._webhook(title, text):
                sent.append("webhook")
        return sent

    def send_daily_report(self, accounts: list[dict], date_str: str,
                          *, force: bool = False,
                          skip_global_switch: bool = False,
                          channel: str | None = None) -> list[str]:
        """发送每日财报。返回实际发送的渠道名。

        渠道复用现有的 Telegram/邮件/Webhook 开关（只推已启用的渠道）。
        force=False 时要求 daily_report_enabled=1 且 notify_daily_report=1；
        force=True（“发送测试财报”）跳过这两个开关，但仍只走已启用的渠道。
        skip_global_switch=True（调度器已按全局开关过滤过）时只跳过
        全局 daily_report_enabled 检查，事件开关 notify_daily_report 仍生效。
        """
        if not force:
            if not skip_global_switch and self.s.get("daily_report_enabled", "1") != "1":
                return []
            if not self.enabled("daily_report"):
                return []
        if not accounts:
            return []
        self.last_errors = {}
        title = daily_title(self.s.get("site_name") or "")
        chunks = build_daily_report(accounts, date_str,
                                    self.s.get("site_name") or "")
        sent: set[str] = set()
        for ch in chunks:
            if (channel is None or channel == "telegram") and self.s.get("telegram_enabled") == "1":
                ok, detail = self._telegram_html(ch["html"])
                if ok:
                    sent.add("telegram")
                elif detail:
                    self.last_errors["telegram"] = detail
            if (channel is None or channel == "email") and self.s.get("email_enabled") == "1":
                ok, detail = self._email_html(title, ch["text"], ch["email_html"])
                if ok:
                    sent.add("email")
                elif detail:
                    self.last_errors["email"] = detail
            if (channel is None or channel == "webhook") and self.s.get("webhook_enabled") == "1":
                if self._webhook(title, ch["text"]):
                    sent.add("webhook")
        return sorted(sent)

    def _telegram(self, text: str) -> tuple[bool, str]:
        """返回 (是否成功, 失败原因)。失败原因优先取 Telegram API 返回的
        description（如 Unauthorized / chat not found），便于定位是 Token
        错、chat_id 错还是代理连不上；以前只返回 False，原因全被吞掉。"""
        token = (self.s.get("telegram_token") or "").strip()
        chat_id = (self.s.get("telegram_chat_id") or "").strip()
        if not token or not chat_id:
            return False, "Token 或 Chat ID 未配置"
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                proxies=self._proxies(), timeout=15)
            if r.status_code == 200:
                return True, ""
            detail = _tg_error_detail(r)
            log.warning("Telegram 发送失败 HTTP %s: %s", r.status_code, detail)
            return False, detail
        except Exception as exc:
            log.warning("Telegram 发送失败: %s", exc)
            return False, _proxy_hint(str(exc)[:200])

    def _telegram_html(self, html_text: str) -> tuple[bool, str]:
        """Telegram HTML 模式：只用 <b> 加粗标题与关键数字，不用超大 emoji。"""
        token = (self.s.get("telegram_token") or "").strip()
        chat_id = (self.s.get("telegram_chat_id") or "").strip()
        if not token or not chat_id:
            return False, "Token 或 Chat ID 未配置"
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": html_text, "parse_mode": "HTML"},
                proxies=self._proxies(), timeout=15)
            if r.status_code == 200:
                return True, ""
            detail = _tg_error_detail(r)
            log.warning("Telegram 发送失败 HTTP %s: %s", r.status_code, detail)
            return False, detail
        except Exception as exc:
            log.warning("Telegram 发送失败: %s", exc)
            return False, _proxy_hint(str(exc)[:200])

    def _email(self, subject: str, text: str, html_body: str | None = None) -> tuple[bool, str]:
        """返回 (是否成功, 失败原因)。以前只返回 False，SMTP 登录失败、
        连不上服务器这类原因全被吞掉，前端只能显示“该渠道未启用或未配置”；
        现在把原因带回来（与 _telegram 同款）。
        配了通知专用代理时走代理的 CONNECT 隧道（直连 Gmail 被墙时用代理），
        没配代理时直连 SMTP_SSL。"""
        host = (self.s.get("email_smtp_host") or "").strip()
        to = (self.s.get("email_to") or "").strip()
        if not host or not to:
            return False, "SMTP 服务器或收件邮箱未配置"
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.s.get("email_user") or ""
            msg["To"] = to
            msg.set_content(text)
            if html_body:
                msg.add_alternative(html_body, subtype="html")
            port = int(self.s.get("email_smtp_port") or 465)
            proxy = (self.s.get("notify_proxy") or "").strip()
            smtp = _smtp_client(host, port, proxy)
            try:
                smtp.ehlo()
                user = self.s.get("email_user") or ""
                if user:
                    smtp.login(user, self.s.get("email_password") or "")
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
                try:
                    smtp.close()
                except Exception:
                    pass
            return True, ""
        except Exception as exc:
            log.warning("邮件发送失败: %s", exc)
            return False, f"邮件发送失败：{str(exc)[:200]}"

    def _email_html(self, subject: str, text: str, html_body: str) -> tuple[bool, str]:
        """邮件 HTML 版：标题 16px / 正文 14px / 辅助 12px，不用浏览器默认大标题。"""
        doc = ("<html><body style=\"font-family:system-ui,sans-serif;color:#111;"
               "max-width:560px;margin:0 auto\">"
               f"{html_body}"
               "<p style=\"font-size:12px;color:#666\">由 alicloud_ECS_CDT 每日推送</p>"
               "</body></html>")
        return self._email(subject, text, html_body=doc)

    def _webhook(self, title: str, text: str) -> bool:
        url = (self.s.get("webhook_url") or "").strip()
        if not url:
            return False
        try:
            r = requests.post(url, json={"title": title, "text": text},
                              proxies=self._proxies(), timeout=15)
            return r.status_code < 300
        except Exception as exc:
            log.warning("Webhook 发送失败: %s", exc)
            return False


def _tg_error_detail(resp) -> str:
    """从 Telegram API 的错误响应里抠出可读原因。"""
    try:
        data = resp.json()
        desc = (data.get("description") or "").strip()
        if desc:
            return f"Telegram API {resp.status_code}：{desc}"
    except Exception:
        pass
    body = (resp.text or "").strip()[:200]
    return f"Telegram API {resp.status_code}：{body}" if body else f"Telegram API {resp.status_code}"


def _proxy_hint(exc_text: str) -> str:
    """连接类异常的提示：代理连不上是最常见原因，点明检查代理。"""
    low = exc_text.lower()
    if "proxy" in low or "connection" in low or "connect" in low or "timed out" in low:
        return (f"连接失败（{exc_text}），请检查通知专用代理 "
                "http://127.0.0.1:7070 是否在运行")
    return exc_text


def check_telegram(settings: dict) -> dict:
    """Telegram 连通性自检，分步给结论（供设置页“检测连接”用）：
    1) Token / Chat ID 是否配了；2) 代理是否通；3) Token 是否有效（getMe）；
    4) Chat ID 是否找得到（getChat，需先给机器人发 /start）。"""
    token = (settings.get("telegram_token") or "").strip()
    chat_id = (settings.get("telegram_chat_id") or "").strip()
    proxy = (settings.get("notify_proxy") or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    steps: list[dict] = []
    if not token or not chat_id:
        steps.append({"name": "配置", "ok": False,
                      "detail": "Token 或 Chat ID 未配置，请先在 Telegram 框体里填好"})
        return {"ok": False, "steps": steps}
    steps.append({"name": "配置", "ok": True, "detail": "Token 与 Chat ID 已配置"})
    if proxy:
        try:
            r = requests.get("https://api.telegram.org", proxies=proxies, timeout=8)
            steps.append({"name": "代理", "ok": True,
                          "detail": f"代理 {proxy} 可达（api.telegram.org 返回 {r.status_code}）"})
        except Exception as exc:
            steps.append({"name": "代理", "ok": False,
                          "detail": f"代理 {proxy} 连不上：{str(exc)[:150]}，请检查代理服务是否在运行"})
            return {"ok": False, "steps": steps}
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe",
                         proxies=proxies, timeout=10)
        data = r.json() if r.status_code == 200 else {}
        if r.status_code == 200 and data.get("ok"):
            bot = data.get("result") or {}
            steps.append({"name": "Token", "ok": True,
                          "detail": f"Token 有效，机器人是 @{bot.get('username') or bot.get('first_name') or '未知'}"})
        else:
            steps.append({"name": "Token", "ok": False,
                          "detail": _tg_error_detail(r) + "，请检查 Token 是否填错"})
            return {"ok": False, "steps": steps}
    except Exception as exc:
        steps.append({"name": "Token", "ok": False, "detail": _proxy_hint(str(exc)[:150])})
        return {"ok": False, "steps": steps}
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getChat",
                         params={"chat_id": chat_id}, proxies=proxies, timeout=10)
        data = r.json() if r.status_code == 200 else {}
        if r.status_code == 200 and data.get("ok"):
            chat = data.get("result") or {}
            title = chat.get("title") or chat.get("username") or chat.get("first_name") or chat_id
            steps.append({"name": "Chat ID", "ok": True,
                          "detail": f"Chat ID 有效，对应“{title}”"})
        else:
            steps.append({"name": "Chat ID", "ok": False,
                          "detail": _tg_error_detail(r) + "，请先给机器人发 /start 再试，或检查 Chat ID"})
            return {"ok": False, "steps": steps}
    except Exception as exc:
        steps.append({"name": "Chat ID", "ok": False, "detail": _proxy_hint(str(exc)[:150])})
        return {"ok": False, "steps": steps}
    return {"ok": True, "steps": steps}
