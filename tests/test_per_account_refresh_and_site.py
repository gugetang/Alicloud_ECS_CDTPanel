"""单账户 API 刷新时间 + 站点 logo/简介 + 页脚 GitHub 地址。

只用 tmpdb 隔离库 + monkeypatch 假客户端/临时目录，不碰真实阿里云接口。
"""
import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


# 1x1 透明 PNG（68 字节）
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_account_refresh_minutes_persisted_and_normalized(tmpdb):
    # 自定义分钟数持久化；None/0/非法 = 跟随全局（存 NULL）
    a = tmpdb.create_account({"name": "R1", "api_refresh_minutes": 10})
    assert a["api_refresh_minutes"] == 10
    b = tmpdb.create_account({"name": "R2"})
    assert b["api_refresh_minutes"] is None
    c = tmpdb.create_account({"name": "R3", "api_refresh_minutes": 0})
    assert c["api_refresh_minutes"] is None
    d = tmpdb.create_account({"name": "R4", "api_refresh_minutes": "abc"})
    assert d["api_refresh_minutes"] is None
    tmpdb.update_account(a["id"], {"api_refresh_minutes": None})
    assert tmpdb.get_account(a["id"])["api_refresh_minutes"] is None
    tmpdb.update_account(a["id"], {"api_refresh_minutes": "30"})
    assert tmpdb.get_account(a["id"])["api_refresh_minutes"] == 30


def test_refresh_cloud_status_only_due_accounts(tmpdb, monkeypatch):
    import time

    import app.scheduler as sched
    now = int(time.time())
    # 全局默认 5 分钟；A 跟随全局（10 分钟前刷新 -> 到期），
    # B 自定义 60 分钟（10 分钟前刷新 -> 未到期），
    # C 自定义 1 分钟（2 分钟前刷新 -> 到期）
    a = tmpdb.create_account({"name": "A"})
    b = tmpdb.create_account({"name": "B", "api_refresh_minutes": 60})
    c = tmpdb.create_account({"name": "C", "api_refresh_minutes": 1})
    tmpdb.update_account(a["id"], {"last_seen_at": now - 600})
    tmpdb.update_account(b["id"], {"last_seen_at": now - 600})
    tmpdb.update_account(c["id"], {"last_seen_at": now - 120})
    seen = []
    monkeypatch.setattr(sched, "_refresh_one",
                        lambda acc, s: seen.append(acc["id"]))
    sched.refresh_cloud_status(tmpdb.get_settings())
    assert sorted(seen) == sorted([a["id"], c["id"]])


def test_eff_refresh_minutes_fallback(tmpdb):
    import app.scheduler as sched
    settings = tmpdb.get_settings()
    assert sched._eff_refresh_minutes({"api_refresh_minutes": None}, settings) == 5
    assert sched._eff_refresh_minutes({"api_refresh_minutes": 30}, settings) == 30
    assert sched._eff_refresh_minutes({"api_refresh_minutes": "abc"}, settings) == 5
    assert sched._eff_refresh_minutes({"api_refresh_minutes": 0}, settings) == 5
    assert sched._is_refresh_due({"api_refresh_minutes": None, "last_seen_at": 0},
                                 settings, 10 ** 9) is True


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.api as apimod
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    # logo 写进临时目录，不污染真实数据目录
    monkeypatch.setattr(apimod, "DATA_DIR", Path(tmp_path))
    from app.main import create_app
    yield TestClient(create_app())
    dbmod.close()


def test_site_logo_upload_serve_delete(client):
    r = client.post("/api/settings/logo",
                    files={"file": ("logo.png", PNG_1X1, "image/png")})
    assert r.status_code == 200
    assert r.json()["logo"] == "site_logo.png"
    # 读取是公开的（<img> 标签不带鉴权头），内容原样返回
    r = client.get("/api/settings/logo")
    assert r.status_code == 200
    assert r.content == PNG_1X1
    assert "image/png" in r.headers["content-type"]
    # 设置里带 logo 文件名
    assert client.get("/api/settings").json()["site_logo"] == "site_logo.png"
    # 删除后读不到
    r = client.delete("/api/settings/logo")
    assert r.status_code == 200
    assert client.get("/api/settings/logo").status_code == 404
    assert client.get("/api/settings").json()["site_logo"] == ""


def test_site_logo_rejects_non_image_and_oversize(client):
    r = client.post("/api/settings/logo",
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/settings/logo",
                    files={"file": ("x.exe", b"x", "application/octet-stream")})
    assert r.status_code == 400


def test_site_intro_setting(client):
    r = client.put("/api/settings", json={"site_intro": "我的 CDT 面板"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["site_intro"] == "我的 CDT 面板"


def test_frontend_site_and_refresh_and_footer():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i18n = (root / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 实例表单：API 刷新时间（跟随全局 / 固定 / 自定义），保存进 api_refresh_minutes
    assert 't("API 刷新时间")' in tsx
    assert '{ value: "follow", label: t("跟随全局") }' in tsx
    assert "api_refresh_minutes: refresh ===" in tsx
    # 其它设置：站点信息卡片（logo 上传/删除 + 简介）
    assert "<SiteInfoCard" in tsx
    assert 't("站点信息")' in tsx
    assert 't("上传 logo")' in tsx
    assert 't("删除 logo")' in tsx
    assert 't("站点简介")' in tsx
    assert '"/api/settings/logo"' in tsx
    # 页眉：有 logo 时显示在标题左边；简介非空时替代默认副标题
    assert '<img src="/api/settings/logo"' in tsx
    assert "settings?.site_intro" in tsx
    # 页脚：GitHub 项目地址
    assert "https://github.com/gugetang/Alicloud_ECS_CDTPanel" in tsx
    assert "GitHub: gugetang/Alicloud_ECS_CDTPanel" in tsx
    for k in ("站点信息", "站点 logo", "上传 logo", "删除 logo", "站点简介",
              "上传失败", "删除失败"):
        assert k in i18n
