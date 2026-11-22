"""进程级配置 + 全局调度参数默认值（与截图一致）。

全部可配项存在 SQLite settings 表；环境变量只决定数据目录 / 监听地址。
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "alicloud_ECS_CDT"
VERSION = "0.1.0"

DATA_DIR = Path(os.environ.get("CDT_DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "cdt.db"
MASTER_KEY_PATH = DATA_DIR / "master.key"

LISTEN_HOST = os.environ.get("CDT_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("CDT_LISTEN_PORT", "1999"))
TZ = os.environ.get("TZ", "Asia/Shanghai")

# 全局调度参数默认值（截图值）
DEFAULT_SETTINGS: dict[str, str] = {
    "quota_gb": "200",
    "protection_threshold_pct": "95",
    # 顶层切换模式：time=仅按时间切换；load_balance=仅按负载均衡（差值）；
    # both=两者都启用（默认，保持旧行为）。阈值踢出/保活/首次分配/备用地址
    # 是保护与核心行为，不受切换模式影响，在三种模式下都生效。
    "switch_mode": "both",
    "switch_diff_pct": "10",
    # 切换差值模式：percent 按使用率差值(%)；gb 按已用流量差值(GB)
    "switch_diff_mode": "percent",
    "switch_diff_gb": "10",
    "exclusion_action": "none",          # none | stop | stop_charging
    # 阈值恢复默认拉起：start=恢复时调用开机拉起（默认）；keep_stopped=恢复入池但保持停机。
    # 恢复线 = 保护阈值 - 2%（防抖动滞后值，见 README“阈值恢复默认拉起”）。
    "threshold_recovery_action": "start",
    # API 刷新时间（分钟）：多久查一次阿里云 CDT/ECS/BSS 状态并重新评估。
    # 与旧的 check_interval_minutes 是同一项（已合并，见 README“全局设置”）：
    # 程序只保留一个间隔设置，保存 api_refresh_minutes 时同步写
    # check_interval_minutes（旧值迁移到新字段，读时以新字段为准）。
    "api_refresh_minutes": "5",
    "check_interval_minutes": "5",
    "night_optimize_enabled": "1",
    "night_window_start": "00:00",
    "night_window_end": "04:00",
    "fixed_switch_time": "",              # 可选，如 "03:00"；为空表示不启用固定时刻切换
    # 按时间切换模式："" = 自动（夜间开启时用夜间，固定时刻非空时也用固定）；
    # off = 关闭一切按时间切换；night_only = 仅夜间；interval = 仅间隔；
    # fixed_time = 仅固定时刻。注意：分组层 time 模式未显式设置时默认按
    # interval 走（见 engine._group_config），因为分组界面只剩间隔周期这一项
    "time_switch_mode": "",
    # 间隔切换：1/2/3/4/6/12/24 小时；interval_hours=0 表示用自定义分钟数
    "interval_hours": "6",
    "custom_interval_minutes": "0",
    "fallback_address": "fallback.example.xyz",
    # 时区（IANA）：夜间窗口、固定时刻、间隔锚点、每日开关机、日报推送时间
    # 都按此时区执行。默认取容器环境变量 TZ（compose 里 Asia/Shanghai），
    # UI 里改了以后以 UI 设置为准，不用重启。
    "timezone": TZ,
    "cloudflare_token": "",
    # 站点信息：页眉显示的 logo（文件名，空=无 logo）与简介（空=显示默认副标题）。
    # logo 文件存在数据目录，由 /api/settings/logo 上传/读取/删除。
    "site_intro": "",
    "site_logo": "",
    # DDNS 域名（Zone 名，如 domainname.com）：配了以后直接按它查 Zone，
    # 二级/三级域名不用再靠后两段推算
    "cloudflare_zone": "",
    # Cloudflare 账户邮箱（账号标识，随 Token 一起填）
    "cloudflare_email": "",
    "notify_proxy": "http://127.0.0.1:7070",
    "telegram_enabled": "0",
    "telegram_chat_id": "",
    "telegram_token": "",
    "email_enabled": "0",
    "email_smtp_host": "",
    "email_smtp_port": "465",
    "email_user": "",
    "email_password": "",
    "email_to": "",
    # 第三方登录 OAuth（Google / GitHub）：Client Secret 存加密值，读出时不回显。
    # 未配置时登录页对应按钮置灰；配置方法见 README“登录方式”。
    "google_oauth_client_id": "",
    "google_oauth_client_secret": "",
    "github_oauth_client_id": "",
    "github_oauth_client_secret": "",
    "webhook_enabled": "0",
    "webhook_url": "",
    # 通知事件开关（截图 6 项）
    "notify_traffic_protection": "1",
    "notify_dns_change": "1",
    "notify_fallback": "1",
    "notify_exclusion": "1",
    "notify_error": "1",
    "notify_recovery": "1",
    # 每日财报推送：每天 daily_report_time（默认 00:00）推送一次，
    # 只推已启用的 Telegram/邮件/Webhook 渠道
    "daily_report_enabled": "1",
    "daily_report_time": "00:00",
    "notify_daily_report": "1",
    "scheduler_paused": "0",
    "billing_enabled": "1",
    # 货币结算：概览“当前费用”卡片按此货币 + 汇率换算成单一货币显示。
    # 汇率 = 1 USD 兑多少 CNY；默认值 6.6719 是 2026-11-22 的市价，
    # 用户可在设置里改；日报仍按各账户币种显示，不换算。
    "settlement_currency": "USD",
    "exchange_rate_usd_cny": "6.6719",
}

EVENT_TOGGLE_KEYS = {
    "traffic_protection": "notify_traffic_protection",
    "dns_change": "notify_dns_change",
    "fallback": "notify_fallback",
    "exclusion": "notify_exclusion",
    "error": "notify_error",
    "recovery": "notify_recovery",
    "daily_report": "notify_daily_report",
}


def ensure_dirs() -> None:
    """建好数据目录，并确认可写。

    容器以 UID 10001 运行；宿主机上 Docker 自动创建的挂载目录属主是 root，
    容器写不进去时 sqlite3.connect 会报 OperationalError: unable to open
    database file。这里提前探测可写性，报错时直接给出修复命令，而不是让
    用户去猜一个没头没尾的 sqlite 报错。
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"数据目录 {DATA_DIR} 创建失败（{e}）：请确认挂载的数据卷目录存在且"
            "容器用户（UID 10001）可写；宿主机执行："
            "sudo chown -R 10001:10001 /opt/docker/cdt-alicloud_ecs_cdtpanel"
            " 后重启容器。"
        ) from e
    probe = DATA_DIR / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise RuntimeError(
            f"数据目录 {DATA_DIR} 不可写（{e}）：容器以 UID 10001 运行，但该目录"
            "属主是 root（Docker 自动创建的挂载目录默认属主就是 root）。"
            "宿主机执行：sudo chown -R 10001:10001 "
            "/opt/docker/cdt-alicloud_ecs_cdtpanel，然后重启容器："
            "docker compose restart（或 docker restart Alicloud_ECS_CDTPanel）。"
        ) from e
