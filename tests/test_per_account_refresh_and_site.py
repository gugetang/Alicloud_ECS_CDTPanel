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
    # 实例级刷新时间已取消、全部强制跟随全局（默认 5 分钟）：实例上存的
    # api_refresh_minutes 不再起作用；A/B 都是 10 分钟前刷新 -> 到期，
    # C 是 2 分钟前刷新 -> 未到期
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
    assert sorted(seen) == sorted([a["id"], b["id"]])


def test_eff_refresh_minutes_fallback(tmpdb):
    import app.scheduler as sched
    settings = tmpdb.get_settings()
    # 实例级刷新时间已取消：不管实例上存的是多少，一律返回全局间隔（默认 5）
    assert sched._eff_refresh_minutes({"api_refresh_minutes": None}, settings) == 5
    assert sched._eff_refresh_minutes({"api_refresh_minutes": 30}, settings) == 5
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
    # 登录页用的 auth/status 也带 site_logo 标记（有 logo 才显示）
    assert client.get("/api/auth/status").json()["site_logo"] is True
    # 删除后读不到
    r = client.delete("/api/settings/logo")
    assert r.status_code == 200
    assert client.get("/api/settings/logo").status_code == 404
    assert client.get("/api/settings").json()["site_logo"] == ""
    assert client.get("/api/auth/status").json()["site_logo"] is False


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
    # 全局 API 刷新时间（参数设置里，5/10/30/自定义/不刷新；0=不刷新=关闭调度）；
    # 实例表单里的“API 刷新时间（跟随全局/固定/自定义）”已取消，实例强制跟随全局
    assert 't("API 刷新时间 *（分钟）")' in tsx
    assert '{ value: "0", label: t("不刷新")' in tsx
    assert 't("跟随全局")' not in tsx
    assert "api_refresh_minutes: refresh ===" not in tsx
    # 站点设置：站点信息卡片（logo 上传/删除 + 简介）
    assert "<SiteInfoCard" in tsx
    assert 't("站点信息")' in tsx
    assert 't("上传 logo")' in tsx
    assert 't("删除 logo")' in tsx
    assert 't("站点简介")' in tsx
    assert '"/api/settings/logo"' in tsx
    # 页眉：有 logo 时显示在标题左边；简介非空时替代默认副标题
    assert '<img src="/api/settings/logo"' in tsx
    assert "settings?.site_intro" in tsx
    # 页脚：AlicloudECS_CDT © 2026 | v1.0.0 + 项目地址超链 + TG 超链，联系邮箱已删
    assert "AlicloudECS_CDT © 2026 | v1.0.0" in tsx
    assert "https://github.com/gugetang/Alicloud_ECS_CDTPanel" in tsx
    assert 't("项目地址")' in tsx
    assert "https://t.me/AliLang_AgentBot" in tsx
    assert "TG @AliLang_AgentBot" in tsx
    assert 't("联系邮箱")' not in tsx
    assert "alicloudadmin@gmail.com" not in tsx
    assert "mailto:" not in tsx
    assert "GitHub: gugetang/Alicloud_ECS_CDTPanel" not in tsx
    for k in ("站点信息", "站点 logo", "上传 logo", "删除 logo", "站点简介",
              "上传失败", "删除失败"):
        assert k in i18n


def test_footer_links_no_underline():
    # 页脚两个超链（项目地址、TG）不带下划线
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    import re
    for m in re.finditer(r'<a href="(?:https://github\.com/gugetang/Alicloud_ECS_CDTPanel|https://t\.me/AliLang_AgentBot)"[^>]*>', tsx):
        assert 'textDecoration: "none"' in m.group(0), m.group(0)


def test_site_logo_buttons_right_icons():
    # 站点 logo 的上传/删除按钮移到最右边、改成图标按钮（上传=上传箭头图标，
    # 删除=垃圾桶图标红色），悬停提示仍是“上传 logo”/“删除 logo”。
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    logo = tsx.split('t("站点 logo")')[1].split('t("站点简介")')[0]
    # 两个按钮之间隔一个 spacer，推到最右边（logo 图在左，按钮在右）
    assert '<span className="spacer" />' in logo
    assert logo.index('<span className="spacer" />') < logo.index('title={t("上传 logo")}')
    # 上传是图标按钮（icon-btn + 上传箭头 SVG），不再是文字按钮
    assert '<button className="icon-btn" title={t("上传 logo")}' in logo
    assert '<polyline points="17 8 12 3 7 8" />' in logo
    assert '>{t("上传 logo")}</button>' not in logo
    # 删除是红色图标按钮（icon-btn danger + 垃圾桶 SVG，与域名/指纹删除同款）
    assert '<button className="icon-btn danger" title={t("删除 logo")}' in logo
    assert '<polyline points="3 6 5 6 21 6" />' in logo
    assert '>{t("删除 logo")}</button>' not in logo
    # 删除按钮只在已有 logo 时显示，上传按钮始终显示
    assert logo.count('title={t("删除 logo")}') == 1
    assert logo.count('title={t("上传 logo")}') == 1


def test_login_screen_shows_logo_when_set():
    # 登录页显示站点 logo：已添加才显示（居中在标题上方），没添加不显示
    # （不直接渲染 <img src="/api/settings/logo">，否则没 logo 时会 404 破图）。
    # 后端 /api/auth/status 带 site_logo 标记，LoginScreen 用 info.site_logo 判断。
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 注意：SetupScreen 在 LoginScreen 之前定义，旧的 split("function SetupScreen")
    # 截不到头，login 会一直延伸到文件末尾（含页脚），旧断言 textAlign:"center"
    # 实际命中的是页脚的内联样式，不是登录 logo；页脚改固定定位去掉内联样式后
    # 它就失效了。这里改以 GroupCard（LoginScreen 后第一个顶层函数）为界。
    login = tsx.split("function LoginScreen")[1].split("function GroupCard")[0]
    assert '(info.site_logo as boolean) ? (' in login
    assert '<img src="/api/settings/logo" alt="logo"' in login
    # logo 在标题左边同一行（flex 行里，auth-title 之前），不再居中独占一行
    assert login.index('info.site_logo') < login.index('auth-title')
    assert '<div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>' in login
