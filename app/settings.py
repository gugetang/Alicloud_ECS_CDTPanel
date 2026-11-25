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

# 部署安全：只接受经过反代的请求（见 main.py 的 _ProxyOnlyMiddleware）。
# compose 默认开（CDT_PROXY_ONLY=1）；本地直接跑、测试默认不开。
PROXY_ONLY = os.environ.get("CDT_PROXY_ONLY", "") == "1"

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
    # 动态切换间隔（分钟）：动态模式的默认间隔，分组未填时继承这里；
    # 默认 30，最少 30（小于 30 按 30 算，见 engine.from_settings）。
    "dynamic_interval_minutes": "30",
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
    "site_name": "CDT 面板",
    "site_intro": "",
    "site_logo": "",
    # DDNS 域名（Zone 名，如 domainname.com）：配了以后直接按它查 Zone，
    # 二级/三级域名不用再靠后两段推算
    "cloudflare_zone": "",
    # Cloudflare 账户邮箱（账号标识，随 Token 一起填）
    "cloudflare_email": "",
    # 默认直连，不假设本地有代理：旧默认值 http://127.0.0.1:7070 会让
    # 没有跑该代理的部署上 Telegram 通知全部失败（连接被拒）。
    "notify_proxy": "",
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
    # 通知事件开关：实例状态（启动/停机/删除/EIP 更换）/ 阈值报警 / 面板更新，
    # 默认全部开启。流量保护、DNS 指向变化、备用地址、排除操作、查询及操作
    # 异常、故障恢复只记日志、不推送（见 LOG_ONLY_EVENTS）。
    "notify_instance_status": "1",
    "notify_threshold_alarm": "1",
    # 面板更新：发现新版本时推送一次（每个版本只推一次）
    "notify_update_available": "1",
    # 每日推送：每天 daily_report_time（默认 00:00）推送一次，
    # 只推已启用的 Telegram/邮件渠道
    "daily_report_enabled": "1",
    "daily_report_time": "00:00",
    # 数据清理：每天在 cleanup_time（默认 03:00）自动清理一次过期日志
    # （事件/流量采样）+ SQLite VACUUM + WAL 检查点 + 数据目录临时文件；
    # 非法值归一化回 "03:00"（见 db.norm_cleanup_time）。
    "cleanup_time": "03:00",
    "notify_daily_report": "1",
    "scheduler_paused": "0",
    "billing_enabled": "1",
    # 货币结算：概览“当前费用”卡片按此货币 + 汇率换算成单一货币显示。
    # 汇率 = 1 USD 兑多少 CNY；默认值 6.6719 是 2026-11-22 的市价，
    # 用户可在设置里改；日报仍按各账户币种显示，不换算。
    "settlement_currency": "USD",
    "exchange_rate_usd_cny": "6.6719",
    # 云端备份（WebDAV）：webdav_url 填 WebDAV 服务器地址，如
    # https://oplist.alicloud.ru/dav（Alist 的地址是 http[s]://域名:端口/dav）；
    # webdav_backup_dir 填备份目录，如 /GoogleDrive/Backup/1panel；备份文件
    # 名固定为 cdt-backup.json，完整地址 = 地址 + 目录 + 文件名。
    # 备份目录留空时，webdav_url 按旧方式直接当作完整文件地址
    # （如 https://…/backups/cdt-backup.json），兼容旧配置。
    # 密码与邮箱密码一样只存库里，API 读设置时不回显（见 api._mask）。
    "webdav_enabled": "0",
    "webdav_url": "",
    "webdav_backup_dir": "",
    "webdav_username": "",
    "webdav_password": "",
}

# 只记日志、不推送的事件：调度器/引擎照常记 db 事件（日志统计里能看到），
# Notifier.enabled() 对它们返回 False，所以不发 Telegram/邮件。
# force=True 的手动测试通知不受影响（见 notify.send）。
LOG_ONLY_EVENTS = {
    "traffic_protection",
    "dns_change",
    "fallback",
    "exclusion",
    "error",
    "recovery",
}

EVENT_TOGGLE_KEYS = {
    # 实例状态：启动、停机（含节省停机）、删除、EIP 更换（成功与失败）
    # 共用一个开关 notify_instance_status。
    "instance_start": "notify_instance_status",
    "instance_stop": "notify_instance_status",
    "instance_delete": "notify_instance_status",
    "eip_replace": "notify_instance_status",
    "threshold_alarm": "notify_threshold_alarm",
    "update_available": "notify_update_available",
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
