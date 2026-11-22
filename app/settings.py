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
    # fixed_time = 仅固定时刻
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
