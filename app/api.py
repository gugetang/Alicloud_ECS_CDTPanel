"""REST API。"""
from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import logging
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from . import db, engine, oauth, scheduler, update as updater
from .settings import DATA_DIR
from .notify import Notifier
from .security import (check_session, clear_login_fails, hash_password,
                       login_locked, new_session, record_login_fail,
                       revoke_session, verify_password, encrypt_secret,
                       decrypt_secret)

router = APIRouter(prefix="/api")

log = logging.getLogger("cdt.api")

# DDNS 设置时区下拉：常用 IANA 时区（值 + 中文标签）。前端从
# GET /api/timezones 取，加载失败时用内置列表回退。
TIMEZONES: list[dict[str, str]] = [
    {"value": "Asia/Shanghai", "label": "上海 Asia/Shanghai"},
    {"value": "Asia/Hong_Kong", "label": "香港 Asia/Hong_Kong"},
    {"value": "Asia/Taipei", "label": "台北 Asia/Taipei"},
    {"value": "Asia/Singapore", "label": "新加坡 Asia/Singapore"},
    {"value": "Asia/Tokyo", "label": "东京 Asia/Tokyo"},
    {"value": "Asia/Seoul", "label": "首尔 Asia/Seoul"},
    {"value": "UTC", "label": "UTC 协调世界时"},
    {"value": "Europe/London", "label": "伦敦 Europe/London"},
    {"value": "Europe/Berlin", "label": "柏林 Europe/Berlin"},
    {"value": "Europe/Paris", "label": "巴黎 Europe/Paris"},
    {"value": "America/New_York", "label": "纽约 America/New_York"},
    {"value": "America/Chicago", "label": "芝加哥 America/Chicago"},
    {"value": "America/Denver", "label": "丹佛 America/Denver"},
    {"value": "America/Los_Angeles", "label": "洛杉矶 America/Los_Angeles"},
    {"value": "Australia/Sydney", "label": "悉尼 Australia/Sydney"},
]


def _rate_and_threshold(row: dict, settings: dict | None = None,
                        evicted_ids: set[int] | None = None) -> tuple[float | None, float, bool]:
    """用量使用率 / 生效保护阈值 / 是否已踢出（与引擎 is_allocatable 一致）。
    settings / evicted_ids 不传时现场查（单次调用兼容旧行为）；列表接口
    应一次查好传入，避免每账户各一次 get_settings + get_flag 的 N+1。"""
    if settings is None:
        settings = db.get_settings()
    quota = row.get("quota_gb")
    if quota is None:
        try:
            quota = float(settings.get("quota_gb", 200))
        except (TypeError, ValueError):
            quota = 200.0
    rate = (round(row["traffic_used_gb"] / quota * 100, 2)
            if row.get("traffic_used_gb") is not None and quota else None)
    try:
        threshold = float(row.get("protection_threshold_pct")
                          if row.get("protection_threshold_pct") is not None
                          else settings.get("protection_threshold_pct", 95))
    except (TypeError, ValueError):
        threshold = 95.0
    evicted = bool(rate is not None and rate >= threshold)
    if not evicted and rate is not None:
        # 恢复滞后（与引擎一致）：上一轮已踢出的账户，只有回落到恢复线
        # （阈值 - 2%）以下才算恢复；介于恢复线与阈值之间仍视为已踢出，
        # 避免状态点在阈值上下反复横跳。
        if evicted_ids is not None:
            previously_evicted = row["id"] in evicted_ids
        else:
            try:
                previously_evicted = bool(db.get_flag(f"evicted:{row['id']}"))
            except Exception:
                previously_evicted = False
        if (previously_evicted
                and rate >= threshold - engine.RECOVERY_HYSTERESIS_PCT):
            evicted = True
    return rate, threshold, evicted


def _status_context(accounts_raw: list[dict] | None = None,
                    settings: dict | None = None,
                    evicted_ids: set[int] | None = None) -> tuple[dict[int, str], dict[int, int]]:
    """当前域名指向的账户（serving_map: account_id -> 域名）与排队位次。

    排队位次：同一分组内 Running、流量已知、未踢出、未被任何域名指向的
    可分配账户，按使用率升序排，第 1 位最先接替。返回 (serving_map, queue_ranks)。
    settings / evicted_ids 不传时现场查；列表接口应一次查好传入。
    每账户的使用率只算一次（旧代码在过滤与排序里各算一次）。"""
    if settings is None:
        settings = db.get_settings()
    if evicted_ids is None:
        evicted_ids = db.list_evicted_account_ids()
    serving_map: dict[int, str] = {}
    try:
        for d in db.list_domains():
            cid = d.get("current_account_id")
            if cid:
                serving_map.setdefault(int(cid), d.get("name") or "")
    except Exception:
        serving_map = {}
    if accounts_raw is None:
        try:
            accounts_raw = db.list_accounts()
        except Exception:
            accounts_raw = []
    by_group: dict[int, list[dict]] = {}
    rates: dict[int, tuple[float | None, bool]] = {}
    for row in accounts_raw:
        rate, _t, evicted = _rate_and_threshold(row, settings, evicted_ids)
        rates[row["id"]] = (rate, evicted)
        if (row.get("instance_status") or "").strip() != "Running":
            continue
        if row.get("ddns_enabled") in (0, False):
            continue  # 未选中参与 DDNS，不进排队
        if rate is None or evicted or row["id"] in serving_map:
            continue
        by_group.setdefault(row.get("group_id") or 1, []).append(row)
    ranks: dict[int, int] = {}
    for rows in by_group.values():
        ordered = sorted(rows, key=lambda r: (
            rates[r["id"]][0] or 0.0,
            r.get("priority", 0), r["id"]))
        for i, r in enumerate(ordered, 1):
            ranks[r["id"]] = i
    return serving_map, ranks


def _public_account(a: dict, serving_map: dict[int, str] | None = None,
                    queue_ranks: dict[int, int] | None = None,
                    serving_ids: set[int] | None = None,
                    settings: dict | None = None,
                    evicted_ids: set[int] | None = None,
                    membership: dict[int, list[int]] | None = None,
                    groups: dict[int, dict] | None = None) -> dict:
    """公开账户行（去掉 AK Secret，补算使用率/状态点/排队位次）。
    settings / evicted_ids 不传时现场查；列表接口应一次查好传入，
    并把 serving_map + queue_ranks 一起传入，避免每账户重复推导。
    membership（多对多成员关系）不传时现场查，列表接口应一次查好传入。
    groups（分组 dict 映射）不传时现场按所属分组查，用于阈值策略取值。"""
    out = dict(a)
    out.pop("access_key_secret_enc", None)
    out["has_secret"] = bool(a.get("access_key_secret_enc"))
    # 多对多成员关系：一个实例可同时属于多个分组；未填时回退主分组
    if membership is not None:
        _gids = membership.get(int(a["id"]), [])
    else:
        try:
            _gids = db.get_account_group_ids(int(a["id"]))
        except Exception:
            _gids = []
    out["group_ids"] = _gids or ([a["group_id"]] if a.get("group_id") is not None else [])
    # 流量显示保留 2 位小数（用量条/卡片/列表/日报都直接用它）。
    if out.get("traffic_used_gb") is not None:
        out["traffic_used_gb"] = round(float(out["traffic_used_gb"]), 2)
    if settings is None:
        settings = db.get_settings()
    # 原始值（NULL=跟随系统）：前端表单据此判断“跟随系统/自定义”；
    # 下面 quota_gb / protection_threshold_pct 仍是生效值（实例级或全局），
    # 卡片/列表/用量条直接用生效值，不用改。
    out["quota_gb_raw"] = a.get("quota_gb")
    out["protection_threshold_pct_raw"] = a.get("protection_threshold_pct")
    quota = a.get("quota_gb")
    if quota is None:
        try:
            quota = float(settings.get("quota_gb", 200))
        except (TypeError, ValueError):
            quota = 200.0
        out["quota_gb"] = quota
    rate, threshold, evicted = _rate_and_threshold(a, settings, evicted_ids)
    out["usage_rate"] = rate
    out["evicted"] = evicted
    out["protection_threshold_pct"] = (
        a.get("protection_threshold_pct")
        if a.get("protection_threshold_pct") is not None else threshold)
    # 阈值策略取所属分组（空=跟随全局），不再读实例自身的覆盖值；
    # 分组不存在/查不到时按跟随全局处理。所属分组：主分组 group_id 优先，
    # 其次多对多成员分组的首个，再次默认分组 1。
    _gids = out.get("group_ids") or []
    _gid = _gids[0] if _gids else (a.get("group_id") or 1)
    _g = None
    if groups is not None:
        _g = groups.get(int(_gid)) if _gid is not None else None
    else:
        try:
            _g = db.get_group(int(_gid)) if _gid is not None else None
        except Exception:
            _g = None
    out["exclusion_action_effective"] = engine._norm_exclusion(
        (_g.get("exclusion_action") if _g else None)
        or settings.get("exclusion_action") or "none")
    out["recovery_action_effective"] = (
        (_g.get("recovery_action") if _g else None)
        or settings.get("threshold_recovery_action") or "start")
    out["switch_diff_mode_effective"] = (
        a.get("switch_diff_mode") or settings.get("switch_diff_mode") or "percent")
    # 停机原因小字（卡片上显示“手动停机/阈值停机，保活不拉起”）：
    # 只有已停机且标记为手动/阈值停机时才显示。
    _sr = (a.get("stop_reason") or "").strip().lower()
    if (a.get("instance_status") or "").strip() == "Stopped" \
            and _sr in ("manual", "threshold"):
        out["stop_reason_label"] = (
            f"{db.STOP_REASON_LABELS[_sr]}，保活不拉起")
    else:
        out["stop_reason_label"] = ""
    # 状态点（新映射，见 README“状态点”一节；优先级固定，一个账户只显示一个点）：
    # 1. 启动中 green_pulse：Starting/Pending（高亮闪烁绿点）
    # 2. 停止中 red_pulse：Stopping（顺延定义，用户未要求，闪烁红点）
    # 3. 停机/离线 red：Stopped 或状态未知/查询不到；若同时已跳出，detail 同时写“已跳出且已停机”
    # 4. 已跳出 gray：Running 且达保护阈值（被踢出池子，不再参与 DDNS）
    # 5. 离队 gray：Running、未踢出、但未被任何分组选中（不在任何分组里，不参与域名分配）
    # 6. 在线 green：Running、未踢出、流量已知、且正被某个域名指向（正在服务）
    # 7. 未参与 gray：Running、未踢出、但未选中参与 DDNS（分组管理里可点选加入）
    # 8. 排队中 yellow：Running、未踢出、已选中参与、可分配、但当前未被任何域名指向（池内排队待命）
    # 9. 未知 gray：Running 但流量未知（保守不动）
    # status_dot 枚举：green | yellow | gray | red | green_pulse | red_pulse
    if serving_map is None and serving_ids is None:
        serving_map, queue_ranks = _status_context(None, settings, evicted_ids)
    elif serving_map is None:
        serving_map = {i: "" for i in (serving_ids or set())}
    if queue_ranks is None:
        _sm, queue_ranks = _status_context(None, settings, evicted_ids)
        serving_map = serving_map or _sm
    out["serving"] = a["id"] in serving_map
    out["serving_domain"] = serving_map.get(a["id"]) or None
    # 未被任何分组选中的实例不在任何池子里，没有排队位次
    out["queue_rank"] = (queue_ranks.get(a["id"])
                         if out["serving"] is False and (out.get("group_ids") or [])
                         else None)
    status = (a.get("instance_status") or "").strip()
    evicted_note = (f"；同时已跳出池子（使用率 {rate}% ≥ 阈值 {threshold:.0f}%）"
                    if evicted and rate is not None else "")
    if status in ("Starting", "Pending"):
        out["status_dot"], out["status_label"] = "green_pulse", "启动中"
        out["status_detail"] = "实例启动中，正在拉起"
    elif status == "Stopping":
        out["status_dot"], out["status_label"] = "red_pulse", "停止中"
        out["status_detail"] = "实例停止中"
    elif status == "Stopped":
        out["status_dot"], out["status_label"] = "red", "已停机"
        out["status_detail"] = f"实例已停机（离线）{evicted_note}"
    elif status != "Running":
        out["status_dot"], out["status_label"] = "red", "离线"
        out["status_detail"] = f"实例状态未知 / 查询不到{evicted_note}"
    elif evicted:
        out["status_dot"], out["status_label"] = "gray", "已跳出"
        out["status_detail"] = (
            f"使用率 {rate}% ≥ 阈值 {threshold:.0f}%，"
            "已跳出池子不再参与 DDNS；用量回落后自动恢复")
    elif not (out.get("group_ids") or []):
        # 未被任何分组选中：不在任何分组里，不参与域名分配，全站显示离队
        # （2026-11-24 用户纠正：SWAS-KR 未被任何分组选中，应是离队，
        # 其它位置也应是离队；这是对 21:22“成员按钮显示真实状态”的反转，
        # 那次改反了——成员区灰点是对的，其它位置黄点才是错的）
        out["status_dot"], out["status_label"] = "gray", "离队"
        out["status_detail"] = "未被任何分组选中，不参与域名分配；在分组管理里点选即可加入"
    elif a.get("ddns_enabled") in (0, False) and not out["serving"]:
        out["status_dot"], out["status_label"] = "gray", "未参与"
        out["status_detail"] = "未选中参与 DDNS，不参与域名分配；在分组管理里点选即可加入"
    elif rate is None:
        out["status_dot"], out["status_label"] = "gray", "未知"
        out["status_detail"] = "实例运行中，但流量未知（保守不动）"
    elif out["serving"]:
        out["status_dot"], out["status_label"] = "green", "在线"
        dom = serving_map.get(a["id"]) or ""
        out["status_detail"] = f"当前在线，正在为域名 {dom} 提供服务" if dom else "当前在线，正在为域名提供服务"
    else:
        rank = queue_ranks.get(a["id"])
        out["status_dot"], out["status_label"] = "yellow", "排队中"
        out["status_detail"] = (
            f"池内排队待命，第 {rank} 位（按使用率升序），可随时接替"
            if rank else "池内排队待命，可随时接替")
    return out


def _admin_required(request: Request) -> bool:
    """已设置管理员密码或绑定第三方登录时要求登录；未设置时放行（首次向导）。"""
    settings = db.get_settings()
    stored = db.get_flag("admin_password_hash") or _any_oauth_bound()
    if not stored:
        return True
    token = request.cookies.get("cdt_session") or \
        (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not check_session(token):
        raise HTTPException(status_code=401, detail="未登录")
    return True


@router.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _rp_id(request: Request) -> str:
    return (request.headers.get("host") or "localhost").split(":")[0]


def _is_public_domain(host: str) -> bool:
    """Host 是不是公网域名（不是 localhost / IP）：是则默认走 https。"""
    h = host.split(":")[0].lower()
    if h in ("localhost", "") or "." not in h:
        return False
    try:
        ipaddress.ip_address(h)
        return False
    except ValueError:
        return True


def _origin(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    # 整页导航（如点 GitHub 登录按钮跳登录接口）没有 Origin 头，但
    # Referer 就是本站登录页；只采纳与 Host 同源的 Referer，避免用上外站地址。
    referer = request.headers.get("referer") or ""
    if referer:
        parts = urllib.parse.urlparse(referer)
        host = request.headers.get("host") or ""
        if parts.scheme in ("http", "https") and parts.netloc == host:
            return f"{parts.scheme}://{parts.netloc}"
    host = request.headers.get("host") or "localhost:1999"
    scheme = request.headers.get("x-forwarded-proto")
    if not scheme:
        # 反代没带 X-Forwarded-Proto 时：公网域名默认 https（面板部署在
        # 带 TLS 的反代后面），本机/IP 默认 http。以前这里一律回退 http，
        # GitHub OAuth 应用若按 https 注册，回调地址就会对不上而登录失败。
        scheme = "https" if _is_public_domain(host) else "http"
    return f"{scheme}://{host}"


def _oauth_configured(settings: dict, provider: str) -> bool:
    cid = (settings.get(f"{provider}_oauth_client_id") or "").strip()
    sec = decrypt_secret(settings.get(f"{provider}_oauth_client_secret") or "")
    return bool(cid and sec)


def _oauth_bound_key(provider: str) -> str:
    """该渠道的绑定键：Google 和 GitHub 各绑各的，互不干扰。"""
    return f"admin_oauth_identity:{provider}"


def _oauth_bound(provider: str) -> str:
    """该渠道已绑定的第三方身份 sub；旧版单绑定（admin_oauth_identity）
    属于该渠道时自动迁移到分渠道键上。"""
    bound = db.get_flag(_oauth_bound_key(provider))
    if bound:
        return bound
    legacy = db.get_flag("admin_oauth_identity")
    if legacy.startswith(provider + ":"):
        sub = legacy[len(provider) + 1:]
        if sub:
            db.set_flag(_oauth_bound_key(provider), sub)
            db.delete_flag("admin_oauth_identity")
            return sub
    return ""


def _any_oauth_bound() -> bool:
    if db.get_flag("admin_oauth_identity"):
        return True
    return any(db.get_flag(_oauth_bound_key(p)) for p in oauth.PROVIDERS)


def _oauth_redirect_uri(request: Request, provider: str) -> str:
    return f"{_origin(request)}/api/auth/oauth/{provider}/callback"


@router.get("/auth/status")
def auth_status():
    """登录页用：管理员是否已设置、各登录方式是否可用。"""
    settings = db.get_settings()
    return {
        "admin_configured": bool(db.get_flag("admin_password_hash")
                                 or _any_oauth_bound()),
        "admin_name": db.get_flag("admin_name") or "",
        "site_name": (settings.get("site_name") or "").strip() or "CDT 面板",
        "site_logo": _display_logo_path() is not None,  # 登录页用：有 logo（含默认）才显示
        "oauth": {p: _oauth_configured(settings, p) for p in oauth.PROVIDERS},
        "webauthn": bool(db.list_webauthn_credentials()),
    }


@router.post("/auth/setup")
def auth_setup(body: dict):
    if db.get_flag("admin_password_hash"):
        raise HTTPException(400, "管理员已设置")
    password = (body.get("password") or "").strip()
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    name = (body.get("admin_name") or "").strip()
    if name:
        db.set_flag("admin_name", name[:64])
    db.set_flag("admin_password_hash", hash_password(password))
    token = new_session()
    return {"token": token}


@router.post("/auth/login")
def auth_login(body: dict, request: Request):
    stored = db.get_flag("admin_password_hash")
    if not stored:
        raise HTTPException(400, "尚未设置管理员密码")
    key = request.client.host if request.client else "unknown"
    if login_locked(key):
        raise HTTPException(429, "登录失败次数过多，请 15 分钟后再试")
    # 管理员账号校验：已设置 admin_name 时必须一致（不一致与密码错误
    # 返回同一条提示，不泄露是哪一项错了）；未设置时（老用户）只校验密码。
    username = (body.get("username") or body.get("admin_name") or "").strip()
    configured_name = (db.get_flag("admin_name") or "").strip()
    if configured_name and username != configured_name:
        record_login_fail(key)
        raise HTTPException(401, "账号或密码错误")
    if not verify_password(body.get("password") or "", stored):
        record_login_fail(key)
        raise HTTPException(401, "账号或密码错误")
    clear_login_fails(key)
    return {"token": new_session()}


@router.post("/auth/change-password")
def auth_change_password(body: dict, _: bool = Depends(_admin_required)):
    """修改管理员密码（登录与安全卡片用）。首次设置（无旧密码）时不校验当前密码。"""
    stored = db.get_flag("admin_password_hash")
    new = (body.get("new_password") or "").strip()
    if len(new) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if stored:
        cur = (body.get("current_password") or "").strip()
        if not verify_password(cur, stored):
            raise HTTPException(400, "当前密码不正确")
    db.set_flag("admin_password_hash", hash_password(new))
    return {"ok": True}


@router.post("/auth/logout")
def auth_logout(request: Request):
    revoke_session(request.cookies.get("cdt_session"))
    return {"ok": True}


# ---------------- Google / GitHub OAuth ----------------

@router.get("/auth/oauth/{provider}/login")
def oauth_login(provider: str, request: Request):
    if provider not in oauth.PROVIDERS:
        raise HTTPException(404, "不支持的登录方式")
    settings = db.get_settings()
    if not _oauth_configured(settings, provider):
        raise HTTPException(400, "未配置该登录方式的 Client ID / Secret，请先在全局设置“登录方式”里填写")
    client_id = (settings.get(f"{provider}_oauth_client_id") or "").strip()
    url = oauth.authorize_url(
        provider, client_id, _oauth_redirect_uri(request, provider))
    return RedirectResponse(url, status_code=302)


@router.get("/auth/oauth/{provider}/callback")
def oauth_callback(provider: str, request: Request):
    if provider not in oauth.PROVIDERS:
        raise HTTPException(404, "不支持的登录方式")
    code = request.query_params.get("code") or ""
    state = request.query_params.get("state") or ""
    err = request.query_params.get("error") or ""
    origin = _origin(request)
    if err:
        return RedirectResponse(
            f"{origin}/?oauth_error={err}", status_code=302)
    state_rec = oauth.take_state(state, provider)
    if not code or not state_rec:
        return RedirectResponse(
            f"{origin}/?oauth_error=invalid_state", status_code=302)
    # 换 token 和跳回都用登录时记下的地址，不再现算：回调是从 GitHub 跳
    # 回来的，Referer/头和登录时不一样，现算可能算出另一个地址。
    redirect_uri = state_rec["redirect_uri"]
    origin = state_rec["origin"]
    settings = db.get_settings()
    client_id = (settings.get(f"{provider}_oauth_client_id") or "").strip()
    client_secret = decrypt_secret(
        settings.get(f"{provider}_oauth_client_secret") or "")
    try:
        tok = oauth.exchange_code(
            provider, code, client_id, client_secret, redirect_uri)
        info = oauth.fetch_userinfo(provider, tok["access_token"])
    except Exception as e:
        return RedirectResponse(
            f"{origin}/?oauth_error={type(e).__name__}", status_code=302)
    sub = str(info["sub"])
    bound = _oauth_bound(provider)
    if bound and bound != sub:
        return RedirectResponse(
            f"{origin}/?oauth_error=identity_mismatch", status_code=302)
    if not bound:
        # 首次用该渠道登录：只绑定这个渠道，不影响另一个渠道
        db.set_flag(_oauth_bound_key(provider), sub)
        if info.get("name") and not db.get_flag("admin_name"):
            db.set_flag("admin_name", info["name"][:64])
    token = new_session()
    return RedirectResponse(f"{origin}/?token={token}", status_code=302)


@router.post("/auth/oauth/{provider}/unbind")
def oauth_unbind(provider: str, _: bool = Depends(_admin_required)):
    """解绑该渠道的第三方登录：下次用该渠道登录会重新绑定。
    至少保留一种管理员登录方式（密码、指纹、另一渠道绑定任一还在才能
    解绑），否则解绑后面板回到首次设置状态，人人都能进。"""
    if provider not in oauth.PROVIDERS:
        raise HTTPException(404, "未知的登录渠道")
    if not _oauth_bound(provider):
        return {"ok": True, "bound": False}
    others = bool(db.get_flag("admin_password_hash")
                  or db.list_webauthn_credentials()
                  or any(_oauth_bound(p) for p in oauth.PROVIDERS if p != provider))
    if not others:
        raise HTTPException(400, "至少保留一种管理员登录方式")
    db.delete_flag(_oauth_bound_key(provider))
    legacy = db.get_flag("admin_oauth_identity")
    if legacy.startswith(provider + ":"):
        db.delete_flag("admin_oauth_identity")
    return {"ok": True, "bound": False}


# ---------------- WebAuthn 指纹 / 安全密钥 ----------------

# challenge -> {"kind": "register"|"authenticate", "created": float}，5 分钟有效
_webauthn_challenges: dict[str, dict] = {}


def _store_challenge(challenge: bytes, kind: str) -> str:
    key = _b64url_encode(challenge)
    _webauthn_challenges[key] = {"kind": kind, "created": time.time()}
    return key


def _take_challenge(challenge: bytes, kind: str) -> bool:
    rec = _webauthn_challenges.pop(_b64url_encode(challenge), None)
    if not rec or rec.get("kind") != kind:
        return False
    return time.time() - rec.get("created", 0) <= 300


def _challenge_from_credential(credential: dict) -> bytes:
    """从凭证的 clientDataJSON 里取出 challenge（base64url 编码的 JSON）。"""
    import json as _json
    raw = (credential.get("response") or {}).get("clientDataJSON") or ""
    payload = _json.loads(base64.urlsafe_b64decode(
        raw + "=" * (-len(raw) % 4)).decode())
    return base64.urlsafe_b64decode(
        payload.get("challenge", "") + "=" * (-len(payload.get("challenge", "")) % 4))


@router.post("/auth/webauthn/register/begin")
def webauthn_register_begin(_: bool = Depends(_admin_required),
                            request: Request = None):
    from webauthn import generate_registration_options
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorAttachment, AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor, ResidentKeyRequirement,
        UserVerificationRequirement)

    rp_id = _rp_id(request)
    user_id = secrets.token_bytes(16)
    existing = [PublicKeyCredentialDescriptor(id=_b64url_decode(c["credential_id"]))
                for c in db.list_webauthn_credentials()]
    options = generate_registration_options(
        rp_id=rp_id, rp_name="Alicloud ECS CDTPanel",
        user_id=user_id,
        user_name=db.get_flag("admin_name") or "admin",
        user_display_name=db.get_flag("admin_name") or "管理员",
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=existing)
    _store_challenge(options.challenge, "register")
    import json as _json
    return _json.loads(options_to_json(options))


@router.post("/auth/webauthn/register/finish")
def webauthn_register_finish(body: dict, _: bool = Depends(_admin_required),
                             request: Request = None):
    from webauthn import verify_registration_response
    from webauthn.helpers import base64url_to_bytes

    credential = body.get("credential") or {}
    try:
        challenge = _challenge_from_credential(credential)
    except Exception:
        raise HTTPException(400, "凭证格式错误")
    if not _take_challenge(challenge, "register"):
        raise HTTPException(400, "验证已过期，请重新开始注册")
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge,
            expected_origin=_origin(request), expected_rp_id=_rp_id(request),
            require_user_verification=False)
    except Exception as e:
        raise HTTPException(400, f"指纹验证失败：{e}")
    transports = ",".join(
        (credential.get("response") or {}).get("transports") or [])
    db.add_webauthn_credential(
        credential_id=_b64url_encode(verified.credential_id),
        public_key=_b64url_encode(verified.credential_public_key),
        sign_count=verified.sign_count, transports=transports,
        label=(body.get("label") or "指纹").strip()[:32] or "指纹")
    return {"ok": True}


@router.post("/auth/webauthn/authenticate/begin")
def webauthn_authenticate_begin(request: Request):
    from webauthn import generate_authentication_options
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorTransport, PublicKeyCredentialDescriptor)

    creds = db.list_webauthn_credentials()
    if not creds:
        raise HTTPException(400, "尚未注册指纹，请先用密码登录后注册")
    allow = []
    for c in creds:
        # 库里存的是 "internal,usb" 这样的字符串；py_webauthn 的
        # options_to_json 会对每个 transport 调 .value，传字符串会
        # 直接 500，所以要先转成 AuthenticatorTransport 枚举。
        transports = []
        for t in (c.get("transports") or "").split(","):
            t = t.strip()
            if not t:
                continue
            try:
                transports.append(AuthenticatorTransport(t))
            except ValueError:
                continue
        allow.append(PublicKeyCredentialDescriptor(
            id=_b64url_decode(c["credential_id"]),
            transports=transports or None))
    options = generate_authentication_options(
        rp_id=_rp_id(request), allow_credentials=allow)
    _store_challenge(options.challenge, "authenticate")
    import json as _json
    return _json.loads(options_to_json(options))


@router.post("/auth/webauthn/authenticate/finish")
def webauthn_authenticate_finish(body: dict, request: Request):
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    credential = body.get("credential") or {}
    try:
        challenge = _challenge_from_credential(credential)
    except Exception:
        raise HTTPException(400, "凭证格式错误")
    if not _take_challenge(challenge, "authenticate"):
        raise HTTPException(400, "验证已过期，请重新开始")
    cred_id = (credential.get("id") or "")
    stored = next((c for c in db.list_webauthn_credentials()
                   if c["credential_id"] == cred_id), None)
    if not stored:
        raise HTTPException(400, "未知的指纹凭证")
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge,
            expected_origin=_origin(request), expected_rp_id=_rp_id(request),
            credential_public_key=_b64url_decode(stored["public_key"]),
            credential_current_sign_count=int(stored.get("sign_count") or 0),
            require_user_verification=False)
    except Exception as e:
        raise HTTPException(400, f"指纹验证失败：{e}")
    db.update_webauthn_sign_count(cred_id, verified.new_sign_count)
    return {"token": new_session()}


@router.get("/auth/webauthn/list")
def webauthn_list(_: bool = Depends(_admin_required)):
    return [{"credential_id": c["credential_id"], "label": c.get("label") or "",
             "created_at": c.get("created_at") or 0}
            for c in db.list_webauthn_credentials()]


@router.delete("/auth/webauthn/{credential_id}")
def webauthn_delete(credential_id: str, _: bool = Depends(_admin_required)):
    db.delete_webauthn_credential(credential_id)
    return {"ok": True}


def _public_group(g: dict, serving_map: dict[int, str] | None = None,
                  queue_ranks: dict[int, int] | None = None,
                  settings: dict | None = None,
                  evicted_ids: set[int] | None = None,
                  accounts_raw: list[dict] | None = None) -> dict:
    """公开分组（含该组账户的公开行）。accounts_raw 不传时现场查；
    列表接口应把已查好的全量账户传进来，避免每分组各一次 list_accounts。
    settings / evicted_ids 同理一次查好传入。"""
    out = dict(g)
    if accounts_raw is None:
        accounts_raw = db.list_accounts()
    accounts = [a for a in accounts_raw if (a.get("group_id") or 1) == g["id"]]
    dom = db.group_domain(g["id"])
    out["account_count"] = len(accounts)
    out["account_ids"] = [a["id"] for a in accounts]
    out["domain"] = dom
    out["domain_id"] = dom["id"] if dom else None
    if serving_map is None or queue_ranks is None:
        serving_map, queue_ranks = _status_context(
            accounts_raw, settings, evicted_ids)
    out["accounts"] = [_public_account(x, serving_map, queue_ranks,
                                       settings=settings,
                                       evicted_ids=evicted_ids,
                                       groups={g["id"]: g})
                       for x in accounts]
    out["evicted_account_ids"] = [
        a["id"] for a in out["accounts"] if a.get("evicted")]
    return out


@router.get("/groups")
def list_groups(_: bool = Depends(_admin_required)):
    settings = db.get_settings()
    evicted_ids = db.list_evicted_account_ids()
    raw = db.list_accounts()
    serving_map, queue_ranks = _status_context(raw, settings, evicted_ids)
    return [_public_group(g, serving_map, queue_ranks, settings, evicted_ids, raw)
            for g in db.list_groups()]


@router.post("/groups")
def create_group(body: dict, _: bool = Depends(_admin_required)):
    try:
        return _public_group(db.create_group(body))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/groups/{gid}")
def update_group(gid: int, body: dict, _: bool = Depends(_admin_required)):
    try:
        row = db.update_group(gid, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "分组不存在")
    return _public_group(row)


@router.delete("/groups/{gid}")
def delete_group(gid: int, _: bool = Depends(_admin_required)):
    try:
        db.delete_group(gid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/groups/reorder")
def reorder_groups(body: dict, _: bool = Depends(_admin_required)):
    # body: {"order": [group_id, ...]} 按数组顺序重排 priority
    for i, gid in enumerate(body.get("order") or []):
        db.update_group(int(gid), {"priority": i})
    return db.list_groups()


def _build_status_payload() -> dict:
    """组装概览数据（登录态 /api/status 与公开 /api/public/status 共用）。"""
    settings = db.get_settings()
    evicted_ids = db.list_evicted_account_ids()
    raw_accounts = db.list_accounts()
    serving_map, queue_ranks = _status_context(raw_accounts, settings, evicted_ids)
    _groups = {int(g["id"]): g for g in db.list_groups()}
    accounts = [_public_account(a, serving_map, queue_ranks, settings=settings,
                                evicted_ids=evicted_ids, groups=_groups)
                for a in raw_accounts]
    domains = db.list_domains()
    total_used = sum(a["traffic_used_gb"] for a in accounts
                     if a.get("traffic_used_gb") is not None)
    known = sum(1 for a in accounts if a.get("traffic_used_gb") is not None)
    total_cost = {}
    for a in accounts:
        if a.get("billing_month_cost") is not None:
            cur = a.get("billing_currency") or "USD"
            total_cost[cur] = round(total_cost.get(cur, 0) + a["billing_month_cost"], 6)
    events = db.list_events(20)
    month = scheduler._now().strftime("%Y-%m")
    # 刷新时间 0=不刷新，相当于调度已暂停（见 scheduler._refresh_minutes）。
    _refresh_min = scheduler._refresh_minutes(settings)
    return {
        "switch_mode": settings.get("switch_mode") or "both",
        "scheduler": {
            "paused": settings.get("scheduler_paused") == "1" or _refresh_min == 0,
            # API 刷新时间（已与旧 check_interval_minutes 合并为同一项）
            "api_refresh_minutes": _refresh_min,
            "check_interval_minutes": int(settings.get("check_interval_minutes", 60)),
            "last_run": scheduler.last_run(),
        },
        "month": month,
        "total_used_gb": round(total_used, 2),
        "accounts_known": known,
        "accounts_total": len(accounts),
        "total_cost": total_cost,
        "accounts": accounts,
        "domains": domains,
        "groups": [_public_group(g, serving_map, queue_ranks, settings,
                                 evicted_ids, raw_accounts)
                   for g in db.list_groups()],
        "events": events,
        "evicted_account_ids": [a["id"] for a in accounts if a.get("evicted")],
        "dns_confirmed": sum(1 for d in domains if d.get("current_ip")),
        "dns_total": len(domains),
        # 上次数据清理（定时/手动）：flags.cleanup_last 存 JSON，无记录为 None
        "cleanup": _last_cleanup(),
    }


@router.get("/status")
def status(_: bool = Depends(_admin_required)):
    return _build_status_payload()


_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _mask_public_ip(ip):
    """公开接口的 IP 脱敏：与前端 web/src/App.tsx 的 maskIp 同规则
    （IPv4 只留 A.B 段，C.D 用 * 代替；非 IPv4 原样返回；空值不动，
    前端显示仍是原来的 “—”）。"""
    if not ip:
        return ip
    parts = str(ip).split(".")
    return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else str(ip)


def _mask_public_instance_id(iid):
    """公开接口的实例 ID 脱敏：与前端 maskInstanceId 同规则
    （i- 开头取前 8 位，其它取前 6 位，后面 ***；空值不动）。"""
    if not iid:
        return iid
    s = str(iid)
    return f"{s[:8]}***" if s.startswith("i-") else f"{s[:6]}***"


def _mask_public_text(text):
    """事件标题/正文里的 IPv4 按同一规则打码。"""
    if not text:
        return text
    return _IPV4_RE.sub(lambda m: _mask_public_ip(m.group(0)), str(text))


def _narrow_public_account(a: dict) -> None:
    """公开接口的账户行收窄：AK ID 不给，IP 与实例 ID 按页面规则打码。
    只改公开接口返回的这份 payload，不动登录态。"""
    a.pop("access_key_id", None)
    if a.get("public_ip"):
        a["public_ip"] = _mask_public_ip(a["public_ip"])
    if a.get("instance_id"):
        a["instance_id"] = _mask_public_instance_id(a["instance_id"])


def _narrow_public_payload(payload: dict) -> dict:
    """公开 payload 收窄：账户行（概览 accounts 与分组内 accounts）去 AK ID、
    IP 与实例 ID 打码；域名 current_ip 打码；事件标题/正文里的 IP 打码。
    其余字段（名称、流量、费用、状态等）不变，概览显示与登录态一致。"""
    for a in payload.get("accounts") or []:
        _narrow_public_account(a)
    for g in payload.get("groups") or []:
        for a in g.get("accounts") or []:
            _narrow_public_account(a)
        dom = g.get("domain")
        if isinstance(dom, dict) and dom.get("current_ip"):
            dom["current_ip"] = _mask_public_ip(dom["current_ip"])
    for d in payload.get("domains") or []:
        if d.get("current_ip"):
            d["current_ip"] = _mask_public_ip(d["current_ip"])
    for e in payload.get("events") or []:
        if e.get("title"):
            e["title"] = _mask_public_text(e["title"])
        if e.get("detail"):
            e["detail"] = _mask_public_text(e["detail"])
    return payload


@router.get("/public/status")
def public_status():
    """公开只读概览接口：管理员明确决定，未登录也可看服务器状态/流量/费用。
    返回与 /api/status 相同的概览数据，但经 _narrow_public_payload 收窄：
    AK ID 不给，IP 与实例 ID 按页面显示规则打码（域名与事件里的 IP 同理），
    另加概览渲染要用的公开设置字段；不含任何凭据与完整设置。
    反代限制（_ProxyOnlyMiddleware）仍适用，只挡直连 IP 访问，不挡域名访问。"""
    payload = _narrow_public_payload(_build_status_payload())
    settings = db.get_settings()
    payload["settings"] = {
        "site_name": (settings.get("site_name") or "").strip() or "CDT 面板",
        "site_logo": _display_logo_path() is not None,
        "site_intro": settings.get("site_intro") or "",
        "exchange_rate_usd_cny": settings.get("exchange_rate_usd_cny"),
        "settlement_currency": settings.get("settlement_currency") or "USD",
        "protection_threshold_pct": settings.get("protection_threshold_pct"),
    }
    return payload


def _last_cleanup() -> Any:
    raw = db.get_flag("cleanup_last")
    if not raw:
        return None
    try:
        import json
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except Exception:
        return None


@router.get("/accounts")
def list_accounts(_: bool = Depends(_admin_required)):
    settings = db.get_settings()
    evicted_ids = db.list_evicted_account_ids()
    raw = db.list_accounts()
    serving_map, queue_ranks = _status_context(raw, settings, evicted_ids)
    membership: dict[int, list[int]] = {}
    for row in db.list_account_groups():
        membership.setdefault(int(row["account_id"]), []).append(int(row["group_id"]))
    _groups = {int(g["id"]): g for g in db.list_groups()}
    return [_public_account(a, serving_map, queue_ranks, settings=settings,
                            evicted_ids=evicted_ids, membership=membership,
                            groups=_groups) for a in raw]


@router.post("/accounts")
def create_account(body: dict, _: bool = Depends(_admin_required)):
    data = dict(body)
    if data.get("access_key_secret"):
        data["access_key_secret_enc"] = encrypt_secret(data.pop("access_key_secret"))
    data.setdefault("access_key_secret_enc", "")
    try:
        return _public_account(db.create_account(data))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/accounts/{aid}")
def update_account(aid: int, body: dict, _: bool = Depends(_admin_required)):
    data = dict(body)
    if data.get("access_key_secret"):
        data["access_key_secret_enc"] = encrypt_secret(data.pop("access_key_secret"))
    old = db.get_account(aid)
    try:
        row = db.update_account(aid, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "账户不存在")
    # 改名记事件（旧名→新名）；改名不影响实例 ID、AK、分组与历史数据
    if old and data.get("name") and (old.get("name") or "").strip() != (row.get("name") or "").strip():
        db.add_event("account", "服务器改名",
                     f"{old.get('name')} → {row.get('name')}", account_id=aid)
    return _public_account(row)


@router.delete("/accounts/{aid}")
def delete_account(aid: int, _: bool = Depends(_admin_required)):
    a = db.get_account(aid)
    db.delete_account(aid)
    if a:
        db.add_event("info", "删除实例", f"{a['name']} 已删除", account_id=aid)
        # 推送：实例删除（走实例状态开关，默认开）
        Notifier(db.get_settings()).send(
            "instance_delete", "实例删除", f"{a['name']} 已删除")
    return {"ok": True}


@router.post("/accounts/reorder")
def reorder_accounts(body: dict, _: bool = Depends(_admin_required)):
    # body: {"order": [account_id, ...]} 按数组顺序重排 priority
    for i, aid in enumerate(body.get("order") or []):
        db.update_account(int(aid), {"priority": i})
    return db.list_accounts()


def _account_client(aid: int):
    """取账户行 + 阿里云客户端（概览卡片/列表的操作按钮用）。"""
    from .aliyun import AliyunClient, AliyunError

    a = db.get_account(aid)
    if not a:
        raise HTTPException(404, "账户不存在")
    secret = decrypt_secret(a.get("access_key_secret_enc") or "")
    if not a.get("access_key_id") or not secret or not a.get("instance_id"):
        raise HTTPException(400, "该账户未配齐 AccessKey / 实例 ID")
    return a, AliyunClient(a, secret), AliyunError


def _public_row(row: dict) -> dict:
    out = dict(row)
    out.pop("access_key_secret_enc", None)
    return out


@router.post("/accounts/{aid}/refresh")
def refresh_account(aid: int, _: bool = Depends(_admin_required)):
    """刷新单个账户的云状态（DescribeInstances + CDT 流量 + BSS）。"""
    try:
        row = scheduler.refresh_one_account(aid)
    except ValueError:
        raise HTTPException(404, "账户不存在")
    return _public_row(row)


@router.post("/accounts/{aid}/start")
def start_account(aid: int, _: bool = Depends(_admin_required)):
    a, client, AliyunError = _account_client(aid)
    try:
        client.start_instance()
    except AliyunError as exc:
        raise HTTPException(400, f"启动失败：{exc}")
    db.add_event("info", "手动启动", f"{a['name']} 已发送启动指令", account_id=aid)
    # 推送：实例启动，原因=手动操作（开关默认开）
    Notifier(db.get_settings()).send(
        "instance_start", "实例启动", f"{a['name']} 已启动，原因：手动操作")
    row = scheduler.refresh_one_account(aid)
    # 手动启动成功 → 清停机原因标记，保活恢复默认拉起
    db.clear_stop_reason(aid)
    return _public_row(db.get_account(aid))


@router.post("/accounts/{aid}/restart")
def restart_account(aid: int, _: bool = Depends(_admin_required)):
    a, client, AliyunError = _account_client(aid)
    try:
        client.reboot_instance()
    except AliyunError as exc:
        raise HTTPException(400, f"重启失败：{exc}")
    db.add_event("info", "手动重启", f"{a['name']} 已发送重启指令", account_id=aid)
    return _public_row(scheduler.refresh_one_account(aid))


@router.post("/accounts/{aid}/stop")
def stop_account(aid: int, body: dict | None = None, _: bool = Depends(_admin_required)):
    # stop_charging：True=节省停机（电源键，默认，StoppedMode=StopCharging，停机后停止计费），
    # False=普通停机（普通开关机键，KeepCharging，停机后继续计费）。
    # 轻量服务器没有节省停机概念，aliyun.py 里回退为普通停机。
    stop_charging = True if body is None else bool(body.get("stop_charging", True))
    a, client, AliyunError = _account_client(aid)
    action_name = "节省停机" if stop_charging else "普通停机"
    try:
        client.stop_instance(stop_charging=stop_charging)
    except AliyunError as exc:
        raise HTTPException(400, f"{action_name}失败：{exc}")
    db.add_event("info", f"手动{action_name}", f"{a['name']} 已发送{action_name}指令", account_id=aid)
    # 推送：实例停机，原因=手动操作（开关默认开）
    Notifier(db.get_settings()).send(
        "instance_stop", "实例停机",
        f"{a['name']} 已停机（{action_name}），原因：手动操作")
    row = scheduler.refresh_one_account(aid)
    # 手动停机成功 → 记 manual，保活不拉起，直到手动启动/定时开机/
    # 阈值恢复拉起/刷新发现 Running/下一次 API 刷新显示用量在阈值内时清除。
    db.set_stop_reason(aid, "manual")
    return _public_row(db.get_account(aid))


def _refresh_dns_for_account(aid: int, new_ip: str) -> list[str]:
    """换 EIP 第 ⑤ 步：把当前由该实例服务的域名指向新 IP。

    只动 domains 表里 current_account_id 是这台实例的域名（调度器维护
    的“当前服务者”），复用调度器的 _DnsOps.set_record（Cloudflare
    同步、缓存的 zone/record ID、失败记事件），不另起一套 DNS 逻辑。
    返回实际指向新 IP 的域名；一个都没成功就抛错，调用方回滚。
    set_record 只返回“是否实际改了记录”：记录本来就等于新 IP 时
    返回 False 也不算失败，用库里 dns_records 的 ip 再校验一次。"""
    settings = db.get_settings()
    dns = scheduler._DnsOps(settings)
    views = {d.id: d for d in scheduler._to_domain_views(db.list_domains())}
    done: list[str] = []
    for row in db.list_domains():
        if (row.get("current_account_id") or 0) != aid:
            continue
        view = views.get(row["id"])
        if view is None:
            continue
        if (row.get("current_ip") or "") == new_ip:
            done.append(row["name"])
            continue
        dns.set_record(view, new_ip)
        rec = db.get_dns_record(row["id"])
        if rec and (rec.get("ip") or "") == new_ip:
            done.append(row["name"])
        else:
            raise ValueError(f"DNS 同步失败：{row['name']} 未指向新 IP {new_ip}")
    return done


def _rollback_replace_eip(client, iid: str, old_alloc: str, old_ip: str,
                          new_alloc: str, state: dict) -> str:
    """换 EIP 中途失败的回滚（新 EIP 一定释放掉，避免空占配额与费用）：
    新 EIP 已绑上就先解绑再释放；旧 EIP 已解绑就尝试重新绑回，
    绑回成功实例恢复用旧 IP，绑回失败则实例当前无公网 IP。
    旧 EIP 在本流程里只在最后一步释放，中途失败一律不释放旧 EIP。
    返回给前端看的中文状态说明。"""
    notes: list[str] = []
    if state.get("new_associated"):
        try:
            client.unassociate_eip(new_alloc, iid)
            notes.append("新 EIP 已解绑")
        except Exception:  # noqa: BLE001
            notes.append("新 EIP 解绑失败")
    try:
        client.release_eip(new_alloc)
        notes.append("新 EIP 已释放")
    except Exception:  # noqa: BLE001
        notes.append("新 EIP 释放失败，请到阿里云控制台手动释放")
    if state.get("old_unassociated"):
        try:
            client.associate_eip(old_alloc, iid)
            notes.append(f"旧 EIP {old_ip} 已重新绑回，实例恢复使用旧 IP")
        except Exception:  # noqa: BLE001
            notes.append(f"旧 EIP {old_ip} 未释放但已解绑，实例当前无公网 IP，请手动处理")
    else:
        notes.append(f"旧 EIP {old_ip} 未动，实例仍使用旧 IP")
    return "；".join(notes)


@router.post("/accounts/{aid}/replace-eip")
def replace_eip(aid: int, _: bool = Depends(_admin_required)):
    """更换一台 ECS 实例的 EIP，严格按以下顺序（用户定）：
    ① 申请新 EIP → ② 解绑旧 EIP → ③ 绑定新 EIP → ④ 带宽与旧 EIP 一致
    → ⑤ 刷新实例 IP 并把 DNS 指向新 IP → ⑤b 共享带宽：旧 EIP 若在共享
    带宽包里，先把旧 EIP 移出包、再把新 EIP 加进同一个包 → ⑥ 释放旧 EIP。
    仅 ECS：轻量服务器没有 EIP、实例当前未绑 EIP 的都直接报明确错误。
    ① 成功后后面任何一步失败都回滚（释放新 EIP、旧 EIP 尽量绑回），
    旧 EIP 只在 DNS 已切走后才释放；释放旧 EIP 失败不推翻已成功的结果，
    只在返回里给 warning。⑤b 放在 DNS 已切走后：失败也只告警不回滚，
    新 IP 已生效，提示用户手动把新 EIP 加进共享带宽包。"""
    from .aliyun import is_swas_instance_id

    a, client, AliyunError = _account_client(aid)
    iid = a.get("instance_id") or ""
    if is_swas_instance_id(iid):
        raise HTTPException(400, "轻量应用服务器没有 EIP，不能更换 EIP")
    try:
        old = client.describe_eip_for_instance()
    except AliyunError as exc:
        raise HTTPException(400, f"查询当前 EIP 失败：{exc}")
    if not old or not old.get("allocation_id") or not old.get("ip"):
        raise HTTPException(400, "该实例当前未绑定 EIP，不能更换 EIP")
    old_ip, old_alloc = old["ip"], old["allocation_id"]
    old_bw = old.get("bandwidth")
    old_pkg = old.get("bandwidth_package_id")
    log.info("更换 EIP %s：旧 %s（带宽 %s，共享带宽包 %s）",
             a["name"], old_ip, old_bw, old_pkg or "无")
    # ① 申请新 EIP（失败则旧 IP 一点没动）
    try:
        new = client.allocate_eip(bandwidth=old_bw)
    except AliyunError as exc:
        raise HTTPException(400, f"申请新 EIP 失败：{exc}（旧 IP 未动）")
    new_ip, new_alloc = new["ip"], new["allocation_id"]
    log.info("更换 EIP %s：已申请新 EIP %s", a["name"], new_ip)
    state = {"old_unassociated": False, "new_associated": False}
    try:
        # ② 解绑旧 EIP
        client.unassociate_eip(old_alloc, iid)
        state["old_unassociated"] = True
        # ③ 绑定新 EIP
        client.associate_eip(new_alloc, iid)
        state["new_associated"] = True
        # ④ 带宽与旧 EIP 一致（查不到旧带宽时申请用的就是接口默认值，跳过）
        if old_bw:
            client.set_eip_bandwidth(new_alloc, old_bw)
        # ⑤ 刷新实例 IP，并把 DNS 指向新 IP
        row = scheduler.refresh_one_account(aid)
        if (row.get("public_ip") or "") != new_ip:
            raise AliyunError(
                f"刷新后实例 IP 仍为 {row.get('public_ip') or '空'}，不是新 EIP {new_ip}")
        dns_domains = _refresh_dns_for_account(aid, new_ip)
    except Exception as exc:  # noqa: BLE001
        note = _rollback_replace_eip(client, iid, old_alloc, old_ip,
                                     new_alloc, state)
        # 回滚后重新刷新一次，让库里 IP 与实例实际 IP 一致。
        try:
            scheduler.refresh_one_account(aid)
        except Exception:  # noqa: BLE001
            pass
        log.warning("更换 EIP %s 失败：%s；%s", a["name"], exc, note)
        # 记日志 + 推送：更换失败（走 eip_replace 事件，即实例状态开关；
        # 回滚结果写进正文）
        db.add_event("error", "更换 EIP 失败",
                     f"{a['name']} 更换 EIP 失败：{exc}；{note}", account_id=aid)
        Notifier(db.get_settings()).send(
            "eip_replace", "更换 EIP 失败", f"{a['name']} 更换 EIP 失败：{exc}；{note}")
        raise HTTPException(400, f"更换 EIP 失败：{exc}；{note}")
    # ⑤b 共享带宽：旧 EIP 若在共享带宽包里，先把旧 EIP 移出包（避免包内
    # EIP 数量超限）、再把新 EIP 加进同一个包。放在 DNS 已切走后：失败只
    # 告警，不回滚（新 IP 已生效）；不在包里时跳过。
    warnings: list[str] = []
    pkg_note = ""
    if old_pkg:
        try:
            client.remove_eip_from_bandwidth_package(old_pkg, old_alloc)
        except AliyunError as exc:
            warnings.append(f"旧 EIP 移出共享带宽包失败（{exc}），请手动移出")
        try:
            client.add_eip_to_bandwidth_package(old_pkg, new_alloc)
            pkg_note = f"；新 EIP 已加入共享带宽包 {old_pkg}"
            log.info("更换 EIP %s：新 EIP %s 已加入共享带宽包 %s",
                     a["name"], new_ip, old_pkg)
        except AliyunError as exc:
            warnings.append(f"新 EIP 加入共享带宽包失败（{exc}），请手动加入")
    # ⑥ 释放旧 EIP（DNS 已切走；释放失败只告警，不推翻已成功的结果）
    try:
        client.release_eip(old_alloc)
    except AliyunError as exc:
        warnings.append(f"旧 EIP {old_ip} 释放失败（{exc}），请手动释放")
    warning = "；".join(warnings)
    if warning:
        log.warning("更换 EIP %s：%s", a["name"], warning)
    db.add_event("info", "更换 EIP",
                 f"{a['name']} EIP 已更换：{old_ip} → {new_ip}"
                 + pkg_note
                 + (f"；{warning}" if warning else ""),
                 account_id=aid)
    # 推送：EIP 已更换（开关默认开；DNS 域名与旧 EIP 释放告警写进正文）
    Notifier(db.get_settings()).send(
        "eip_replace", "EIP 已更换",
        f"{a['name']} EIP 已更换：{old_ip} → {new_ip}"
        + (f"；DNS 已切：{', '.join(dns_domains)}" if dns_domains else "")
        + pkg_note
        + (f"；{warning}" if warning else ""))
    out = _public_row(db.get_account(aid))
    out.update({"old_ip": old_ip, "new_ip": new_ip,
                "dns_domains": dns_domains, "warning": warning})
    return out


@router.get("/domains")
def list_domains(_: bool = Depends(_admin_required)):
    return db.list_domains()


@router.post("/domains")
def create_domain(body: dict, _: bool = Depends(_admin_required)):
    try:
        return db.create_domain(body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/domains/{did}")
def update_domain(did: int, body: dict, _: bool = Depends(_admin_required)):
    try:
        row = db.update_domain(did, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "域名不存在")
    return row


@router.post("/groups/{gid}/bind-domain")
def bind_domain_to_group(gid: int, body: dict, _: bool = Depends(_admin_required)):
    # body: {"domain_id": id}：把域名绑到该分组（单选；该分组原有域名自动解绑）
    try:
        row = db.bind_domain_to_group(int(body.get("domain_id") or 0), gid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "域名不存在")
    return row


@router.post("/domains/reorder")
def reorder_domains(body: dict, _: bool = Depends(_admin_required)):
    # body: {"order": [domain_id, ...]} 按数组顺序重排 priority
    for i, did in enumerate(body.get("order") or []):
        db.update_domain(int(did), {"priority": i})
    return db.list_domains()


@router.delete("/domains/{did}")
def delete_domain(did: int, _: bool = Depends(_admin_required)):
    db.delete_domain(did)
    return {"ok": True}


@router.get("/timezones")
def list_timezones(_: bool = Depends(_admin_required)):
    """时区下拉列表 + 当前值（DDNS 设置卡片用）。"""
    return {"current": db.get_settings().get("timezone") or "Asia/Shanghai",
            "timezones": TIMEZONES}


@router.get("/settings")
def get_settings(_: bool = Depends(_admin_required)):
    s = db.get_settings()
    s["cloudflare_configured"] = bool(s.get("cloudflare_token"))
    s["email_configured"] = bool(s.get("email_password"))
    s["telegram_configured"] = bool(s.get("telegram_token"))
    # site_logo_default：没上传 logo 但仓库默认 logo 存在时为真，
    # 前端用它判断页眉/设置页是否显示默认 logo；删除按钮只认已上传的 site_logo。
    s["site_logo_default"] = _logo_path() is None and _default_logo_path() is not None
    s["google_oauth_configured"] = _oauth_configured(s, "google")
    s["github_oauth_configured"] = _oauth_configured(s, "github")
    s["google_oauth_bound"] = bool(_oauth_bound("google"))
    s["github_oauth_bound"] = bool(_oauth_bound("github"))
    s.pop("cloudflare_token", None)
    s.pop("telegram_token", None)
    s.pop("google_oauth_client_secret", None)
    s.pop("github_oauth_client_secret", None)
    return s


@router.put("/settings")
def put_settings(body: dict, _: bool = Depends(_admin_required)):
    allowed = set(db.get_settings().keys()) | {
        "cloudflare_token", "telegram_token", "email_password"}
    data = {k: str(v) for k, v in body.items() if k in allowed}
    _validate_settings(data)
    return _mask(db.set_settings(data))


# 站点 logo：上传/读取/删除。文件存在数据目录（site_logo.*），
# 文件名存 settings.site_logo；读取是公开的（<img> 标签不带鉴权头）。
LOGO_EXTS = {".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".svg": "image/svg+xml",
             ".ico": "image/x-icon"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


def _logo_path() -> Any:
    name = (db.get_settings().get("site_logo") or "").strip()
    if not name:
        return None
    p = DATA_DIR / name
    return p if p.is_file() else None


# 默认 logo：仓库自带的 logo/cdt-logo.svg。用户没上传 logo 时，
# 读取接口与各处 site_logo 标记都回落到它，新部署不用手动传就有 logo。
DEFAULT_LOGO = Path(__file__).resolve().parent.parent / "logo" / "cdt-logo.svg"


def _default_logo_path() -> Any:
    return DEFAULT_LOGO if DEFAULT_LOGO.is_file() else None


def _display_logo_path() -> Any:
    """展示用的 logo：已上传的优先，否则用仓库默认 logo，都没有返回 None。"""
    return _logo_path() or _default_logo_path()


@router.post("/settings/logo")
async def upload_site_logo(file: UploadFile = File(...),
                           _: bool = Depends(_admin_required)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in LOGO_EXTS:
        raise HTTPException(400, "只支持 png/jpg/gif/webp/svg/ico 图片")
    data = await file.read()
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(400, "logo 不能超过 2 MB")
    if not data:
        raise HTTPException(400, "空文件")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 旧 logo 换后缀时先删掉，避免残留多个文件
    for old in DATA_DIR.glob("site_logo.*"):
        try:
            old.unlink()
        except OSError:
            pass
    name = f"site_logo{ext}"
    (DATA_DIR / name).write_bytes(data)
    db.set_settings({"site_logo": name})
    return {"ok": True, "logo": name}


@router.get("/settings/logo")
def get_site_logo():
    p = _display_logo_path()
    if p is None:
        raise HTTPException(404, "未设置 logo")
    return FileResponse(p, media_type=LOGO_EXTS.get(
        p.suffix.lower(), "application/octet-stream"))


@router.delete("/settings/logo")
def delete_site_logo(_: bool = Depends(_admin_required)):
    p = _logo_path()
    if p is not None:
        try:
            p.unlink()
        except OSError:
            pass
    db.set_settings({"site_logo": ""})
    return {"ok": True}


def _validate_settings(data: dict) -> None:
    """保存前校验几个关键设置，避免非法值让调度器崩溃。"""
    import re
    for key in ("api_refresh_minutes", "check_interval_minutes"):
        if key in data:
            try:
                v = int(float(data[key]))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} 必须是分钟数")
            # 0=不刷新，相当于关闭调度；1–1440=按分钟数调度。
            if not 0 <= v <= 1440:
                raise HTTPException(400, f"{key} 必须在 0–1440 分钟之间（0=不刷新）")
    if ("threshold_recovery_action" in data
            and data["threshold_recovery_action"] not in ("start", "keep_stopped")):
        raise HTTPException(400, "threshold_recovery_action 只能是 start/keep_stopped")
    if "exclusion_action" in data:
        # 别名归一化：evict_ddns 等价于 none（踢出DDNS），只存一档的值
        if data["exclusion_action"] == "evict_ddns":
            data["exclusion_action"] = "none"
        if data["exclusion_action"] not in ("none", "stop", "stop_charging"):
            raise HTTPException(400, "exclusion_action 只能是 none/stop/stop_charging")
    if "daily_report_time" in data and not re.fullmatch(
            r"([01]\d|2[0-3]):[0-5]\d", str(data["daily_report_time"])):
        raise HTTPException(400, "daily_report_time 必须是 HH:MM 格式")
    if "cleanup_time" in data:
        # 非法值不报 400，直接归一化回 03:00（见 db.norm_cleanup_time）
        data["cleanup_time"] = db.norm_cleanup_time(data["cleanup_time"])
    if "timezone" in data:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(str(data["timezone"]))
        except Exception:
            raise HTTPException(400, "timezone 必须是合法的 IANA 时区名，如 Asia/Shanghai")
    if "settlement_currency" in data and data["settlement_currency"] not in ("USD", "CNY"):
        raise HTTPException(400, "settlement_currency 只能是 USD/CNY")
    if "exchange_rate_usd_cny" in data:
        try:
            r = float(data["exchange_rate_usd_cny"])
        except (TypeError, ValueError):
            raise HTTPException(400, "exchange_rate_usd_cny 必须是大于 0 的数字")
        if not r > 0:
            raise HTTPException(400, "exchange_rate_usd_cny 必须是大于 0 的数字")


def _mask(s: dict) -> dict:
    out = dict(s)
    out["cloudflare_configured"] = bool(s.get("cloudflare_token"))
    out["email_configured"] = bool(s.get("email_password"))
    out["telegram_configured"] = bool(s.get("telegram_token"))
    out["google_oauth_configured"] = _oauth_configured(s, "google")
    out["github_oauth_configured"] = _oauth_configured(s, "github")
    out["google_oauth_bound"] = bool(_oauth_bound("google"))
    out["github_oauth_bound"] = bool(_oauth_bound("github"))
    out["webdav_configured"] = bool(s.get("webdav_password"))
    out.pop("cloudflare_token", None)
    out.pop("telegram_token", None)
    out.pop("email_password", None)
    out.pop("google_oauth_client_secret", None)
    out.pop("github_oauth_client_secret", None)
    out.pop("webdav_password", None)
    return out


@router.get("/notify")
def get_notify(_: bool = Depends(_admin_required)):
    s = db.get_settings()
    return {"proxy": s.get("notify_proxy"), "telegram_enabled": s.get("telegram_enabled"),
            "telegram_chat_id": s.get("telegram_chat_id"),
            "email_enabled": s.get("email_enabled"),
            "events": {k: s.get(v) == "1" for k, v in
                       {"instance_status": "notify_instance_status",
                        "threshold_alarm": "notify_threshold_alarm",
                        "group_change": "notify_group_change",
                        "update_available": "notify_update_available"}.items()}}


@router.post("/notify/test")
def test_notify(body: dict, _: bool = Depends(_admin_required)):
    n = Notifier(db.get_settings())
    sent = n.send(body.get("event") or "error", "测试通知",
                  "alicloud_ECS_CDT 测试通知：配置已保存，调度暂停时也可发送。",
                  channel=body.get("channel") or None, force=True)
    return {"sent": sent, "errors": n.last_errors}


@router.get("/notify/telegram-check")
def telegram_check(_: bool = Depends(_admin_required)):
    """Telegram 连通性自检：分步检查配置 / 代理 / Token / Chat ID，
    返回每步的结论与可读原因（供设置页“检测连接”用）。"""
    from .notify import check_telegram
    return check_telegram(db.get_settings())


def _daily_report_accounts() -> list[dict]:
    """财报账户列表（带新状态点）。无真实账户时用虚构演示数据，仅用于预览/测试。"""
    raw = db.list_accounts()
    if raw:
        settings = db.get_settings()
        evicted_ids = db.list_evicted_account_ids()
        serving_map, queue_ranks = _status_context(raw, settings, evicted_ids)
        return [_public_account(a, serving_map, queue_ranks, settings=settings,
                                evicted_ids=evicted_ids) for a in raw]
    # 演示数据（虚构：203.0.113.x 测试网段、example.xyz），不含真实凭据/域名/IP
    demo = [
        {"id": 1, "name": "Demo-HK-1", "quota_gb": 200, "traffic_used_gb": 37.77,
         "instance_status": "Running", "public_ip": "203.0.113.10",
         "instance_spec": "2C0.5G", "billing_month_cost": 0.47,
         "billing_currency": "USD", "billing_balance": 0.0,
         "protection_threshold_pct": None, "exclusion_action": None,
         "switch_diff_mode": None, "switch_diff_gb": None, "switch_diff_pct": None,
         "access_key_secret_enc": "", "group_id": 1},
        {"id": 2, "name": "Demo-HK-2", "quota_gb": 200, "traffic_used_gb": 192.0,
         "instance_status": "Running", "public_ip": "203.0.113.11",
         "instance_spec": "", "billing_month_cost": 0.36,
         "billing_currency": "USD", "billing_balance": 0.0,
         "protection_threshold_pct": None, "exclusion_action": None,
         "switch_diff_mode": None, "switch_diff_gb": None, "switch_diff_pct": None,
         "access_key_secret_enc": "", "group_id": 1},
    ]
    return [_public_account(a, serving_map={1: "backup.example.xyz"},
                            queue_ranks={}) for a in demo]


@router.get("/notify/daily-preview")
def daily_preview(_: bool = Depends(_admin_required)):
    """每日财报预览（不发送）：返回第一条消息的 html/text，供通知设置页展示。
    全部账户跟随全局开关，与“发送测试财报”保持一致。"""
    from .notify import build_daily_report, filter_daily_report_accounts
    today = scheduler._now().strftime("%Y-%m-%d")
    settings = db.get_settings()
    accounts = filter_daily_report_accounts(
        _daily_report_accounts(), settings, force=True)
    chunks = build_daily_report(accounts, today, settings.get("site_name") or "")
    first = chunks[0] if chunks else {"html": "", "text": "", "email_html": ""}
    return {"date": today, "messages": len(chunks), "html": first["html"],
            "text": first["text"], "email_html": first["email_html"],
            "accounts": len(accounts)}


@router.post("/notify/test-daily")
def test_daily(body: dict | None = None, _: bool = Depends(_admin_required)):
    """发送测试财报：用当前账户（无账户时用演示数据）立即发一条，
    跳过每日开关与推送时间，但仍只走已启用的渠道；
    全部账户跟随全局开关。body.channel 指定时只发该渠道。"""
    from .notify import build_daily_report, filter_daily_report_accounts
    today = scheduler._now().strftime("%Y-%m-%d")
    settings = db.get_settings()
    accounts = filter_daily_report_accounts(
        _daily_report_accounts(), settings, force=True)
    chunks = build_daily_report(accounts, today, settings.get("site_name") or "")
    n = Notifier(settings)
    sent = n.send_daily_report(accounts, today, force=True,
                               channel=(body or {}).get("channel") or None)
    return {"sent": sent, "errors": n.last_errors, "messages": len(chunks),
            "preview": chunks[0] if chunks else None,
            "accounts": len(accounts)}


@router.post("/notify/send-daily")
def send_daily(body: dict | None = None, _: bool = Depends(_admin_required)):
    """手动推送日报：正式手动推送，随时可推，按已启用渠道真发。

    与定时推送的区别：不读也不写定时推送的旗标
    （daily_report_sent:<日期>:<时间>:<渠道>）——
    手动推送不占用/不消耗当天的定时一次（手动推完，定时时间到了照常再推；
    定时已推过，当天仍可手动再推）。全部账户跟随全局开关，
    记事件“手动推送日报”。"""
    from .notify import build_daily_report, filter_daily_report_accounts
    today = scheduler._now().strftime("%Y-%m-%d")
    settings = db.get_settings()
    accounts = filter_daily_report_accounts(
        _daily_report_accounts(), settings)
    chunks = build_daily_report(accounts, today, settings.get("site_name") or "")
    n = Notifier(settings)
    sent = n.send_daily_report(accounts, today, skip_global_switch=True,
                               channel=(body or {}).get("channel") or None)
    db.add_event("daily_report", "手动推送日报",
                 f"{today} 手动推送，共 {len(accounts)} 个账户，"
                 f"渠道：{','.join(sent) if sent else '未启用任何通知渠道'}")
    return {"sent": sent, "messages": len(chunks),
            "preview": chunks[0] if chunks else None,
            "accounts": len(accounts)}


@router.get("/backup/export")
def backup_export(_: bool = Depends(_admin_required)):
    """下载备份：整库 JSON（含加密后的 AK Secret 与主密钥，见 app/backup.py）。"""
    from fastapi.responses import JSONResponse

    from . import backup
    data = backup.build_backup()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    return JSONResponse(
        content=data,
        headers={"Content-Disposition":
                 f'attachment; filename="cdt-backup-{stamp}.json"'})


@router.post("/backup/restore")
def backup_restore(body: dict, _: bool = Depends(_admin_required)):
    """恢复备份：校验后整体替换当前数据。备份里的管理员密码也会恢复，
    当前登录会话不受影响。"""
    from . import backup
    try:
        counts = backup.restore_backup(body or {})
    except ValueError as exc:
        raise HTTPException(400, f"备份无效：{exc}")
    db.add_event("info", "恢复备份",
                 "已从备份恢复："
                 + "、".join(f"{k} {v} 条" for k, v in counts.items()))
    return {"ok": True, "counts": counts}


@router.get("/backup/webdav/status")
def webdav_status(_: bool = Depends(_admin_required)):
    """云端备份状态：是否配置、云端文件是否存在及大小/修改时间。"""
    from . import webdav
    settings = db.get_settings()
    cfg = webdav.get_config(settings)
    if not cfg["url"]:
        return {"configured": False, "enabled": False, "url": "",
                "exists": False, "size": None, "modified": None}
    try:
        info = webdav.client_from_settings(settings).propfind()
    except webdav.WebDAVError as exc:
        raise HTTPException(400, str(exc))
    return {"configured": True, "enabled": cfg["enabled"],
            "url": webdav.file_url(cfg["url"], cfg["backup_dir"]),
            **info}


@router.post("/backup/webdav/test")
def webdav_test(_: bool = Depends(_admin_required)):
    """测试 WebDAV 连接：服务器通 + 认证通过即成功；文件不存在也算成功
    （只是还没传过备份）。"""
    from . import webdav
    try:
        info = webdav.client_from_settings(db.get_settings()).propfind()
    except webdav.WebDAVError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **info}


@router.post("/backup/webdav/upload")
def webdav_upload(_: bool = Depends(_admin_required)):
    """把当前整库备份上传到 WebDAV。"""
    from . import webdav
    try:
        info = webdav.upload_backup(db.get_settings())
    except webdav.WebDAVError as exc:
        raise HTTPException(400, str(exc))
    db.add_event("info", "云端备份",
                 f"已上传备份到 WebDAV（{info['size']} 字节）")
    return {"ok": True, **info}


@router.post("/backup/webdav/download")
def webdav_download(_: bool = Depends(_admin_required)):
    """从 WebDAV 下载备份并整体恢复（与本地恢复一样替换全部数据）。"""
    from . import webdav
    try:
        counts = webdav.download_backup(db.get_settings())
    except webdav.WebDAVError as exc:
        raise HTTPException(400, str(exc))
    db.add_event("info", "云端恢复",
                 "已从 WebDAV 恢复备份："
                 + "、".join(f"{k} {v} 条" for k, v in counts.items()))
    return {"ok": True, "counts": counts}


@router.post("/schedule/run")
def schedule_run(body: dict | None = None, _: bool = Depends(_admin_required)):
    return scheduler.run_once(dry_run=False, refresh=True)


@router.post("/schedule/dry-run")
def schedule_dry_run(_: bool = Depends(_admin_required)):
    return scheduler.run_once(dry_run=True, refresh=False)


@router.post("/schedule/pause")
def schedule_pause(body: dict, _: bool = Depends(_admin_required)):
    db.set_settings({"scheduler_paused": "1" if body.get("paused") else "0"})
    if body.get("paused"):
        scheduler.stop()
    else:
        scheduler.start()
    return {"paused": bool(body.get("paused"))}


@router.post("/maintenance/cleanup")
def manual_cleanup(_: bool = Depends(_admin_required)):
    """手动触发一次数据清理（与每天固定时间的自动清理内容相同）。
    手动清理也记当天已清理，避免定时清理一小时内重复跑。"""
    result = db.run_cleanup(ran_at=scheduler._now().strftime("%Y-%m-%d %H:%M"))
    db.set_flag("cleanup_done", scheduler._now().strftime("%Y-%m-%d"))
    return {"ok": True, **result}


@router.get("/update/status")
def update_status(_: bool = Depends(_admin_required)):
    """面板更新状态：当前版本、GitHub 最新版本、是否有更新、检查是否出错。"""
    return updater.status()


@router.post("/update/check")
def update_check(_: bool = Depends(_admin_required)):
    """强制刷新一次 GitHub 最新版本（跳过 1 小时缓存）。"""
    updater.check_latest(force=True)
    return updater.status()


@router.post("/update/run")
def update_run(_: bool = Depends(_admin_required)):
    """启动一次在线更新（后台线程，立即返回；容器将重启）。
    没有 Docker socket 时返回手动命令，面板不假装更新成功。"""
    return updater.start_update()


@router.get("/logs")
def logs(limit: int = 100, _: bool = Depends(_admin_required)):
    return db.list_events(limit=min(limit, 500))


@router.post("/cloud/status")
def cloud_status(_: bool = Depends(_admin_required)):
    settings = db.get_settings()
    accounts = scheduler.refresh_cloud_status(settings)
    evicted_ids = db.list_evicted_account_ids()
    serving_map, queue_ranks = _status_context(accounts, settings, evicted_ids)
    return [_public_account(a, serving_map, queue_ranks, settings=settings,
                            evicted_ids=evicted_ids) for a in accounts]


# ---------------------------------------------------------------------------
# 阿里云区域发现（区域可选，来源必须是阿里云接口，见 README“区域与站点”）
# ---------------------------------------------------------------------------

def _aliyun_client_for_regions(site: str | None = None,
                               account_id: int | None = None,
                               access_key_id: str | None = None,
                               access_key_secret: str | None = None):
    """解析区域查询用的 AliyunClient。

    优先用已保存账户的 AK（account_id）；无账户/未填 AK 时允许传临时
    access_key_id + access_key_secret 走一次查询，不保存。
    site 决定账号归属与 BSS 币种（global=国际站 USD，cn=国内站 CNY）。
    """
    from .aliyun import AliyunClient
    if site is not None and site not in ("global", "cn"):
        raise HTTPException(400, "site 只能是 global（国际站）或 cn（国内站）")
    if account_id is not None:
        row = db.get_account(int(account_id))
        if not row:
            raise HTTPException(404, "账户不存在")
        secret = decrypt_secret(row.get("access_key_secret_enc") or "")
        if not row.get("access_key_id") or not secret:
            raise HTTPException(400, "该账户未配置 AccessKey，无法查询区域")
        account = dict(row)
        account["site"] = site or row.get("site") or "global"
        return AliyunClient(account, secret)
    if access_key_id and access_key_secret:
        return AliyunClient({"access_key_id": access_key_id,
                             "region": "cn-hongkong",
                             "site": site or "global"}, access_key_secret)
    raise HTTPException(400, "请先选择账户，或填写 AccessKey ID 与 Secret 再查询")


def _regions_error(exc: Exception):
    """区域接口失败：返回明确错误 + 内置常用区域列表，前端回退到内置列表。"""
    from fastapi.responses import JSONResponse

    from .aliyun import BUILTIN_REGIONS
    return JSONResponse(status_code=502, content={
        "detail": f"阿里云区域查询失败：{exc}",
        "fallback_regions": BUILTIN_REGIONS})


@router.post("/aliyun/regions")
def aliyun_regions(body: dict | None = None,
                   _: bool = Depends(_admin_required)):
    """该站点下的可用区域列表（ECS DescribeRegions），按 local_name 排序。
    POST + JSON body：临时 AK 不再走 URL 查询参数（密钥会进访问日志）。
    body: {"site": ..., "account_id": ..., "access_key_id": ...,
           "access_key_secret": ...}（临时查询，不保存）。"""
    from .aliyun import AliyunError
    body = body or {}
    try:
        client = _aliyun_client_for_regions(body.get("site"), body.get("account_id"),
                                            body.get("access_key_id"),
                                            body.get("access_key_secret"))
        regions = client.describe_regions()
    except HTTPException:
        raise
    except AliyunError as exc:
        return _regions_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _regions_error(exc)
    return {"site": body.get("site") or client.account.get("site") or "global",
            "regions": regions, "source": "aliyun"}


@router.post("/aliyun/discover-regions")
def aliyun_discover_regions(body: dict, _: bool = Depends(_admin_required)):
    """现有服务器位置发现：先 DescribeInstances（分页）汇总该账号下已有实例
    实际所在的区域（去重，标注“已有实例所在区域”），再合并 DescribeRegions
    的全量列表（标注“全部可用区域”）。前端下拉分两组显示。"""
    from .aliyun import AliyunError
    try:
        client = _aliyun_client_for_regions(body.get("site"), body.get("account_id"),
                                            body.get("access_key_id"),
                                            body.get("access_key_secret"))
        all_regions = client.describe_regions()
        owned_ids = set(client.discover_instance_regions(all_regions))
    except HTTPException:
        raise
    except AliyunError as exc:
        return _regions_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _regions_error(exc)
    by_id = {r["region_id"]: r for r in all_regions}
    owned = [dict(by_id[rid], owned=True) for rid in sorted(owned_ids)
             if rid in by_id]
    owned += [{"region_id": rid, "local_name": rid, "endpoint": "", "owned": True}
              for rid in sorted(owned_ids) if rid not in by_id]
    return {"owned": owned, "all": all_regions, "source": "aliyun"}


@router.post("/aliyun/find-instance-region")
def aliyun_find_instance_region(body: dict, _: bool = Depends(_admin_required)):
    """自动发现：按实例 ID 调 DescribeInstances 查出该实例真实所在区域并返回，
    前端自动填入区域下拉。查不到时报错（不要静默填错）。"""
    from .aliyun import AliyunError
    instance_id = (body.get("instance_id") or "").strip()
    if not instance_id:
        raise HTTPException(400, "请先填写实例 ID")
    try:
        client = _aliyun_client_for_regions(body.get("site"), body.get("account_id"),
                                            body.get("access_key_id"),
                                            body.get("access_key_secret"))
        region_id = client.find_instance_region(instance_id)
        regions = client.describe_regions()
    except HTTPException:
        raise
    except AliyunError as exc:
        return _regions_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _regions_error(exc)
    local = next((r["local_name"] for r in regions if r["region_id"] == region_id),
                 region_id)
    return {"instance_id": instance_id, "region_id": region_id,
            "local_name": local, "source": "aliyun"}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, _: bool = Depends(_admin_required)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job
