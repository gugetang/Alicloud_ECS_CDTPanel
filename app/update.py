"""面板在线更新：查 GitHub 最新版本，并用 Docker socket 拉镜像重建容器。

版本来源：镜像构建时注入的环境变量 CDT_VERSION（Dockerfile 里
CDT_VERSION=$VERSION，工作流传 github.sha；未注入时为 "dev"，此时
无法判断是否有更新，status 会如实返回 update_available=false）。
最新版本来源：GitHub API（GET /repos/gugetang/Alicloud_ECS_CDTPanel/commits/main），
直连 HTTPS（不走通知代理），失败只影响状态显示、绝不让接口报错；结果
在内存里缓存 1 小时（未登录 GitHub 限额 60 次/小时）。
在线更新：走 Docker socket（/var/run/docker.sock，需 compose 挂载）拉取
ghcr.io/gugetang/alicloud_ecs_cdtpanel:latest，再按当前容器的 inspect
配置原样重建（环境变量、端口、卷、重启策略、网络）。没有 socket 时
返回明确的手动命令，不假装更新成功。
"""
from __future__ import annotations

import os
import socket as _socket
import threading
import time
from typing import Any

import requests

REPO = "gugetang/Alicloud_ECS_CDTPanel"
IMAGE_REPO = "ghcr.io/gugetang/alicloud_ecs_cdtpanel"
IMAGE_TAG = "latest"
GITHUB_API = f"https://api.github.com/repos/{REPO}/commits/main"
MANUAL_CMD = "docker compose pull && docker compose up -d"
_CACHE_TTL = 3600  # GitHub 未登录限额 60 次/小时，缓存 1 小时够用

_cache: dict[str, Any] = {"at": 0.0, "data": None}
_run_state: dict[str, Any] = {"running": False, "last": None}


def current_version() -> str:
    return os.environ.get("CDT_VERSION", "dev")


def _fetch_latest() -> dict[str, Any]:
    r = requests.get(
        GITHUB_API,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "Alicloud-ECS-CDTPanel"},
        timeout=10,
    )
    r.raise_for_status()
    j = r.json() or {}
    commit = j.get("commit") or {}
    return {
        "sha": j.get("sha"),
        "message": commit.get("message", ""),
        "date": (commit.get("committer") or {}).get("date", ""),
    }


def check_latest(force: bool = False) -> dict[str, Any]:
    """返回 {"ok": bool, "latest": {...}|None, "error": str|None}，带 1 小时缓存。"""
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["data"]
    try:
        latest = _fetch_latest()
        data: dict[str, Any] = {"ok": True, "latest": latest, "error": None}
    except Exception as exc:  # noqa: BLE001 - 检查失败只影响状态显示
        data = {"ok": False, "latest": None, "error": str(exc)}
    _cache["at"] = now
    _cache["data"] = data
    return data


def status() -> dict[str, Any]:
    cur = current_version()
    res = check_latest()
    latest = res.get("latest") or {}
    latest_sha = latest.get("sha")
    # 当前版本是 "dev"（镜像未注入版本号）时无法比较，不谎报有更新
    update_available = bool(cur and cur != "dev" and latest_sha and cur != latest_sha)
    return {
        "current_version": cur,
        "latest_version": latest_sha,
        "latest_message": latest.get("message"),
        "latest_date": latest.get("date"),
        "update_available": update_available,
        "checked_at": _cache["at"],
        "ok": res["ok"],
        "error": res["error"],
        "running": _run_state["running"],
        "last_run": _run_state["last"],
    }


# ---------------- 在线更新（Docker socket） ----------------

def _docker_available() -> bool:
    return os.path.exists("/var/run/docker.sock")


def _ports_from_bindings(bindings: dict[str, Any]) -> dict[str, Any]:
    """HostConfig.PortBindings -> SDK ports 参数。"""
    out: dict[str, Any] = {}
    for container_port, hosts in (bindings or {}).items():
        if not hosts:
            out[container_port] = None
            continue
        h = hosts[0]
        host_port = int(h.get("HostPort")) if h.get("HostPort") else None
        host_ip = h.get("HostIp") or ""
        out[container_port] = (host_ip, host_port) if host_ip else host_port
    return out


def _volumes_from_binds(binds: list[str]) -> dict[str, dict[str, str]]:
    """HostConfig.Binds（"host:container[:mode]"）-> SDK volumes 参数。"""
    out: dict[str, dict[str, str]] = {}
    for b in binds or []:
        parts = b.split(":")
        if len(parts) < 2:
            continue
        host, cont = parts[0], parts[1]
        mode = parts[2] if len(parts) > 2 else "rw"
        out[host] = {"bind": cont, "mode": mode}
    return out


def _create_kwargs(attrs: dict[str, Any]) -> dict[str, Any]:
    """按当前容器的 inspect 配置，拼出 SDK create 的参数（原样重建）。"""
    config = attrs.get("Config") or {}
    host = attrs.get("HostConfig") or {}
    name = (attrs.get("Name") or "").lstrip("/")
    kw: dict[str, Any] = {
        "image": f"{IMAGE_REPO}:{IMAGE_TAG}",
        "name": name,
        "environment": list(config.get("Env") or []),
        "command": config.get("Cmd"),
        "entrypoint": config.get("Entrypoint"),
        "ports": _ports_from_bindings(host.get("PortBindings") or {}),
        "volumes": _volumes_from_binds(host.get("Binds") or []),
        "restart_policy": {"Name": (host.get("RestartPolicy") or {}).get("Name", "unless-stopped")},
        "network": host.get("NetworkMode") or None,
        "detach": True,
    }
    if host.get("Init"):
        kw["init"] = True
    return {k: v for k, v in kw.items() if v is not None}


def _do_update() -> None:
    """后台线程：拉镜像 → 按 inspect 重建容器 → 启动。面板自身进程随旧容器
    停止而退出，调用方只拿到“已开始更新”，不等待完成。"""
    _run_state["running"] = True
    _run_state["last"] = {"ok": False, "message": "更新进行中", "at": time.time()}
    try:
        import docker  # 延迟导入：没装 SDK 或没 socket 时只影响更新功能

        client = docker.from_env()
        client.images.pull(IMAGE_REPO, tag=IMAGE_TAG)
        hostname = _socket.gethostname()
        try:
            old = client.containers.get(hostname)
        except Exception:
            old = client.containers.get("Alicloud_ECS_CDTPanel")
        kw = _create_kwargs(old.attrs)
        tmp_name = f"{kw['name']}-updating"
        # 先创建新容器（未启动），再停旧容器：创建失败时旧容器毫发无损
        new = client.containers.create(**{**kw, "name": tmp_name})
        try:
            old.stop(timeout=10)
        except Exception:
            pass
        try:
            old.remove()
        except Exception:
            pass
        new.rename(kw["name"])
        new.start()
        _run_state["last"] = {"ok": True, "message": "已更新并重启", "at": time.time()}
    except Exception as exc:  # noqa: BLE001 - 失败要记下来，前端能看到
        _run_state["last"] = {"ok": False, "message": str(exc), "at": time.time()}
    finally:
        _run_state["running"] = False


def start_update() -> dict[str, Any]:
    """启动一次在线更新（后台线程，立即返回）。没有 Docker socket 时
    返回 manual 命令，调用方提示用户手动执行。"""
    if _run_state["running"]:
        return {"started": False, "message": "更新已在进行中"}
    if not _docker_available():
        return {
            "started": False,
            "message": "未检测到 Docker socket，无法在线更新",
            "manual": MANUAL_CMD,
        }
    t = threading.Thread(target=_do_update, daemon=True)
    t.start()
    return {"started": True, "message": "更新已开始，容器将重启"}
