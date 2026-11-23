"""云端备份：把整库备份 JSON 上传到 WebDAV 服务器，或从 WebDAV 下载恢复。

WebDAV 是标准 HTTP 扩展（Nextcloud、ownCloud、坚果云、群晖等都支持）：
用 PUT 上传、GET 下载、PROPFIND 查文件是否存在及大小/修改时间。
认证用 HTTP Basic Auth（用户名 + 密码）。

配置存在 settings 表里（webdav_url / webdav_backup_dir / webdav_username /
webdav_password / webdav_enabled），密码与邮箱密码一样只存库里，API 读设置
时不回显（见 api._mask）。备份文件本身含全部凭据（含主密钥），请只传到
自己信任的 WebDAV 服务器。

地址与目录：webdav_url 是 WebDAV 服务器地址（如 Alist 的
https://oplist.alicloud.ru/dav），webdav_backup_dir 是备份目录
（如 /GoogleDrive/Backup/1panel），备份文件名固定为 cdt-backup.json，
完整地址 = 地址 + 目录 + 文件名。备份目录留空时，webdav_url 按旧方式
直接当作完整文件地址，兼容旧配置。
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth

TIMEOUT = 30
DAV_NS = "{DAV:}"
BACKUP_FILENAME = "cdt-backup.json"


class WebDAVError(Exception):
    """面向用户的 WebDAV 错误（文案可直接展示）。"""


def get_config(settings: dict) -> dict:
    """从 settings 字典读出 WebDAV 配置。"""
    return {
        "enabled": str(settings.get("webdav_enabled", "0")) == "1",
        "url": str(settings.get("webdav_url", "") or "").strip(),
        "backup_dir": str(settings.get("webdav_backup_dir", "") or "").strip(),
        "username": str(settings.get("webdav_username", "") or "").strip(),
        "password": str(settings.get("webdav_password", "") or ""),
    }


def file_url(url: str, backup_dir: str) -> str:
    """拼备份文件的完整地址。

    备份目录非空时：地址 + 目录 + 文件名（cdt-backup.json）。
    备份目录为空时：地址本身就是完整文件地址（旧配置，兼容）。
    """
    url = (url or "").strip()
    backup_dir = (backup_dir or "").strip()
    if not backup_dir:
        return url
    base = url.rstrip("/")
    d = "/" + backup_dir.strip("/")
    return f"{base}{d}/{BACKUP_FILENAME}"


def _check_url(url: str) -> str:
    if not url:
        raise WebDAVError("未配置 WebDAV 地址")
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise WebDAVError("WebDAV 地址必须是 http(s):// 开头的完整地址")
    return url


class WebDAVClient:
    """最小 WebDAV 客户端：PROPFIND / PUT / GET。"""

    def __init__(self, url: str, username: str = "", password: str = ""):
        self.url = _check_url(url)
        self.auth = HTTPBasicAuth(username, password) if username else None

    def _request(self, method: str, data: bytes | None = None):
        headers = {"Depth": "0"} if method == "PROPFIND" else {}
        if method == "PUT":
            headers["Content-Type"] = "application/json"
        try:
            return requests.request(
                method, self.url, auth=self.auth,
                headers=headers, data=data, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise WebDAVError(f"连接 WebDAV 失败：{exc}") from exc

    @staticmethod
    def _auth_error(resp) -> WebDAVError:
        if resp.status_code in (401, 403):
            return WebDAVError("WebDAV 认证失败：用户名或密码不对")
        return WebDAVError(f"WebDAV 返回 HTTP {resp.status_code}")

    def propfind(self) -> dict:
        """查云端备份文件是否存在及大小/修改时间。

        返回 {"exists": bool, "size": int|None, "modified": str|None}。
        404 = 服务器通、认证通过，只是文件还没传过。
        """
        resp = self._request("PROPFIND")
        if resp.status_code == 404:
            return {"exists": False, "size": None, "modified": None}
        if resp.status_code not in (200, 207):
            raise self._auth_error(resp)
        size, modified = None, None
        try:
            root = ET.fromstring(resp.content)
            for el in root.iter():
                if el.tag == f"{DAV_NS}getcontentlength" and el.text:
                    size = int(el.text)
                elif el.tag == f"{DAV_NS}getlastmodified" and el.text:
                    modified = el.text.strip()
        except (ET.ParseError, ValueError):
            pass
        return {"exists": True, "size": size, "modified": modified}

    def put(self, data: bytes) -> int:
        """上传备份，返回字节数。"""
        resp = self._request("PUT", data=data)
        if resp.status_code not in (200, 201, 204):
            raise self._auth_error(resp)
        return len(data)

    def get(self) -> bytes:
        """下载备份文件内容。"""
        resp = self._request("GET")
        if resp.status_code == 404:
            raise WebDAVError("云端没有备份文件")
        if resp.status_code != 200:
            raise self._auth_error(resp)
        return resp.content


def client_from_settings(settings: dict) -> WebDAVClient:
    """按 settings 建客户端；未配置时报可读错误。"""
    cfg = get_config(settings)
    url = file_url(cfg["url"], cfg["backup_dir"])
    if not url:
        raise WebDAVError("未配置 WebDAV 地址，请先在备份设置里填写")
    return WebDAVClient(url, cfg["username"], cfg["password"])


def build_backup_bytes() -> tuple[bytes, str]:
    """生成当前整库备份的 JSON 字节串与导出时间。"""
    from . import backup
    data = backup.build_backup()
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return raw, data.get("exported_at", "")


def upload_backup(settings: dict) -> dict:
    """把当前备份上传到 WebDAV，返回大小与导出时间。"""
    client = client_from_settings(settings)
    raw, exported_at = build_backup_bytes()
    size = client.put(raw)
    return {"size": size, "exported_at": exported_at}


def download_backup(settings: dict) -> dict:
    """从 WebDAV 下载备份并整体恢复，返回各表恢复行数。"""
    from . import backup
    client = client_from_settings(settings)
    raw = client.get()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebDAVError("云端备份文件不是合法的 JSON") from exc
    try:
        counts = backup.restore_backup(data)
    except ValueError as exc:
        raise WebDAVError(f"云端备份无效：{exc}") from exc
    return counts
