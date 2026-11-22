"""密钥掩码 + 分组页去框 + 手机端适配 + 夜间底色：

- 后端：GET /settings 与 _mask 返回 email_configured /
  telegram_configured，且不返回任何密钥明文。
- 前端：已配置的密钥一律显示 16 个 * 掩码（SECRET_MASK），保存时跳过
  掩码（不把 * 当新密钥存回去）；点进框清空、失焦留空恢复掩码。
- 分组页：外层卡片框体取消，“+ 新建分组”移到右下角。
- 手机端（≤800px）：单列卡片、指标两列、表格横向滚动、触控目标放大、
  输入框 16px（防 iOS 聚焦缩放）；电脑端保持 1200px 三列布局。
- 夜间底色不再是纯黑 #000000，改成深蓝灰 #0b1220。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "web" / "src" / "styles.css").read_text()
TSX = (ROOT / "web" / "src" / "App.tsx").read_text()

from app.api import _mask  # noqa: E402


def test_mask_configured_flags():
    s = {"email_password": "smtp-secret", "telegram_token": "",
         "cloudflare_token": "cf-tok", "google_oauth_client_id": "cid",
         "google_oauth_client_secret": "csec", "github_oauth_client_id": "",
         "github_oauth_client_secret": ""}
    out = _mask(s)
    assert out["email_configured"] is True
    assert out["telegram_configured"] is False
    assert out["cloudflare_configured"] is True
    assert out["google_oauth_configured"] is True
    assert out["github_oauth_configured"] is False
    for k in ("email_password", "telegram_token", "cloudflare_token",
              "google_oauth_client_secret", "github_oauth_client_secret"):
        assert k not in out, k


def test_mask_configured_flags_all_empty():
    out = _mask({"email_password": "", "telegram_token": "", "cloudflare_token": ""})
    assert out["email_configured"] is False
    assert out["telegram_configured"] is False
    assert out["cloudflare_configured"] is False


def test_secret_mask_constant_and_skip_on_save():
    assert 'const SECRET_MASK = "****************"' in TSX
    # AK Secret：有已配置密钥时初始显示掩码
    assert "useState(account?.has_secret ? SECRET_MASK" in TSX
    # 6 处保存都跳过掩码：实例表单、SMTP 密码、Telegram Token、
    # Cloudflare Token、Google/GitHub Secret
    assert TSX.count("!== SECRET_MASK") >= 6
    # 5 个非受控密钥框都有掩码默认值 + 点进清空 + 失焦恢复
    for fid in ("em-pass", "tg-token", "cf-token", "oauth-google-secret",
                "oauth-github-secret"):
        assert f'id="{fid}"' in TSX
        assert TSX.count("onFocus={secretFocus}") >= 5
        assert TSX.count("secretBlurRestore(e, Boolean(settings.") >= 5
    assert "defaultValue={settings.email_configured ? SECRET_MASK" in TSX
    assert "defaultValue={settings.telegram_configured ? SECRET_MASK" in TSX
    assert "defaultValue={settings.cloudflare_configured ? SECRET_MASK" in TSX


def test_groups_page_no_outer_card_and_button_bottom_right():
    start = TSX.index('{tab === "groups" && (')
    end = TSX.index('{tab === "config"', start)
    block = TSX[start:end]
    assert '<div className="card">' not in block  # 外层框体取消
    assert block.strip().startswith('{tab === "groups" && (\n        <>')
    # “+ 新建分组”在分组卡片之后、右下角一行
    assert block.index('groups.map((g)') < block.index('t("+ 新建分组")')
    assert 'justifyContent: "flex-end"' in block


def test_mobile_media_query():
    m = re.search(r"@media\s*\(max-width:\s*800px\)\s*\{(.*)\}\s*$", CSS, re.S)
    assert m, "mobile media query missing"
    block = m.group(1)
    assert ".stats, .stats.five { grid-template-columns:repeat(2,1fr)" in block
    assert "input, select, textarea { font-size:16px" in block
    assert "table { display:block; overflow-x:auto" in block
    assert ".btn { padding:10px 16px" in block
    assert ".icon-btn { width:36px; height:36px" in block
    assert ".auth-wrap { margin:24px auto 0" in block
    assert ".app { padding:16px 12px 40px" in block
    # 电脑端保持 1200px 三列
    assert ".app { max-width:1200px" in CSS
    assert ".grid3 { display:grid; grid-template-columns:repeat(3,1fr)" in CSS


def test_dark_bg_is_not_pure_black():
    m = re.search(r'html\[data-theme="dark"\]\s*\{([^}]*)\}', CSS)
    assert m, "dark theme block missing"
    assert "--bg:#0b1220" in m.group(1).replace(" ", "")
    assert "#000000" not in CSS


def test_light_card_is_30pct_transparent():
    m = re.search(r'html\[data-theme="light"\]\s*\{([^}]*)\}', CSS)
    assert m, "light theme block missing"
    body = m.group(1).replace(" ", "")
    # 白天卡片磨砂更明显：白底透明度 30%（alpha 0.3），夜间灰卡不动
    assert "--card:rgba(255,255,255,0.3)" in body
    assert "--card2:rgba(255,255,255,0.3)" in body
    assert "rgba(255,255,255,0.7)" not in CSS


def test_google_sans_font_self_hosted():
    # 谷歌新闻字体 Google Sans：latin 子集以 base64 内嵌在 CSS 里，不走
    # Google Fonts CDN；内嵌而不是单独 woff2 文件，是因为 GitHub 推送工具
    # 只接受 UTF-8 文本文件，二进制字体推不上去
    assert "data:font/woff2;base64," in CSS
    assert "/fonts/" not in CSS
    assert not (ROOT / "web" / "public" / "fonts").exists(), "no separate font file"
    # @font-face 声明：可变字体 400–700，latin 范围
    assert "@font-face" in CSS and "'Google Sans'" in CSS
    assert "font-weight: 400 700" in CSS
    # body 字体栈：Google Sans 在前，中文回落 Noto Sans SC / 系统字体
    m = re.search(r"body\s*\{([^}]*)\}", CSS)
    assert m, "body rule missing"
    fam = m.group(1)
    assert "'Google Sans'" in fam
    assert fam.index("'Google Sans'") < fam.index("system-ui")
    assert "Noto Sans SC" in fam and "PingFang SC" in fam and "Microsoft YaHei" in fam


def test_list_font_smaller_and_dash_notice_removed():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 列表字体缩小一号：表格 14px → 12px，表头 13px → 12px
    assert re.search(r"table\s*\{[^}]*font-size:\s*12px", CSS)
    assert re.search(r"th\s*\{[^}]*font-size:\s*12px", CSS)
    assert "table { width:100%; border-collapse:collapse; font-size:14px; }" not in CSS
    # 运行概览顶部的调度提示说明已取消（临时消息条 msg 不动）
    assert "调度运行时会按当前配置执行 VPS 和 DNS 操作。" not in tsx
    assert "调度运行时会按当前配置执行 VPS 和 DNS 操作。" not in i18n
    assert '{msg && <div className="notice">{msg}</div>}' in tsx
    # 在线状态详情（当前在线，正在为域名…提供服务）显示绿色字体，
    # 仅 status_dot 为 green 时变绿，其它状态仍是 muted 灰色
    assert tsx.count('a.status_dot === "green" ? "var(--dot-green)"') == 2
    assert "var(--dot-green)" in CSS


def test_nav_restructure_domains_top_level_logs_in_settings():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 顶栏：运行概览、账户管理、分组管理、域名管理、设置（域名管理紧跟分组管理后面）
    m = re.search(r"const TABS[^;]*;", tsx, re.S)
    assert m, "TABS missing"
    tabs = m.group(0)
    assert '["domains", "域名管理"]' in tabs
    assert tabs.index('["domains", "域名管理"]') > tabs.index('["groups", "分组管理"]')
    assert '["logs", "日志"]' not in tabs
    # 设置子标签：全局设置、通知设置、日志（日志在通知设置后面）
    m = re.search(r"const SUB_TABS[^;]*;", tsx, re.S)
    assert m, "SUB_TABS missing"
    subs = m.group(0)
    assert '["logs", "日志"]' in subs
    assert subs.index('["logs", "日志"]') > subs.index('["notify", "通知设置"]')
    assert '["domains", "域名管理"]' not in subs
    # 类型与渲染条件同步：域名管理是顶栏 tab，日志是设置子标签
    assert 'type Tab = "dash" | "accounts" | "groups" | "domains" | "config"' in tsx
    assert 'type SubTab = "settings" | "notify" | "logs" | "other"' in tsx
    assert '{tab === "domains" && (' in tsx
    assert '{tab === "config" && subTab === "logs" && (' in tsx
    assert 'tab === "logs"' not in tsx and 'subTab === "domains"' not in tsx
    # 日志在进入设置→日志子标签时才加载
    assert 'tab === "config" && subTab === "logs"' in tsx


def test_api_refresh_own_row_and_other_settings_tab():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # API 刷新时间单独占一行，移到定时轮巡上面（不在两列 form2 里）
    assert tsx.count('API 刷新时间') == 1
    api_idx = tsx.index('API 刷新时间')
    assert 'marginTop: 12' in tsx[max(0, api_idx - 120):api_idx]
    assert tsx.index('阈值恢复') < api_idx
    assert api_idx < tsx.index('<h3 style={{ marginTop: 20 }}>{t("定时轮巡")}</h3>')
    # 其它设置是设置页的第四个子标签（在日志后面）
    m = re.search(r"const SUB_TABS[^;]*;", tsx, re.S)
    assert m, "SUB_TABS missing"
    subs = m.group(0)
    assert '["other", "其它设置"]' in subs
    assert subs.index('["other", "其它设置"]') > subs.index('["logs", "日志"]')
    assert '"other"' in tsx and 'type SubTab = "settings" | "notify" | "logs" | "other"' in tsx
    # 其它设置里有货币结算卡片、主题设置卡片、登录与安全、备份与恢复
    other_idx = tsx.index('{tab === "config" && subTab === "other" && (')
    notify_idx = tsx.index('{tab === "config" && subTab === "notify" && (')
    other_block = tsx[other_idx:notify_idx]
    assert '<div className="card"><h2>{t("货币结算")}</h2>' in other_block
    assert '<div className="card"><h2>{t("主题设置")}</h2>' in other_block
    assert '<LoginSecurity' in other_block and '<BackupCard' in other_block
    assert 'value={theme}' in other_block and 'setTheme(v)' in other_block
    # 参数设置卡片里不再有货币结算、登录与安全、备份与恢复
    settings_idx = tsx.index('{tab === "config" && subTab === "settings" && (')
    settings_block = tsx[settings_idx:other_idx]
    assert '货币结算' not in settings_block
    assert '<LoginSecurity' not in settings_block and '<BackupCard' not in settings_block


def test_theme_styles_picker():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 5 种主题风格：森之韵/深海谧/暖阳橘/紫微星/绯红月，默认深海谧（当前蓝色）
    for tid, zh, en in [("emerald", "森之韵", "Emerald"), ("deepblue", "深海谧", "Deep Blue"),
                        ("warmsun", "暖阳橘", "Warm Sun"), ("nebula", "紫微星", "Nebula"),
                        ("crimson", "绯红月", "Crimson")]:
        assert f'id: "{tid}"' in tsx and f'zh: "{zh}"' in tsx and f'en: "{en}"' in tsx
    assert 'localStorage.getItem("cdt-accent") || "deepblue"' in tsx
    assert 'document.documentElement.dataset.accent = accent' in tsx
    # CSS：每种风格只定义 --accent/--accent-soft/--accent-text，不改磨砂底色（--card/--card2/--bg）
    for tid in ["emerald", "deepblue", "warmsun", "nebula", "crimson"]:
        m = re.search(rf'html\[data-accent="{tid}"\] \{{[^}}]*\}}', css)
        assert m, f"data-accent {tid} missing"
        assert "--accent:" in m.group(0) and "--accent-soft:" in m.group(0) and "--accent-text:" in m.group(0)
        assert "--card" not in m.group(0) and "--bg" not in m.group(0)
    # 选中按键/按钮的浅底与深色字走变量（随主题换色）：#dbeafe/#1d4ed8 只允许出现在
    # deepblue 风格的变量定义里，不允许写死在任何一条选择器规则里
    css_no_accent = re.sub(r':root \{[^}]*\}', '', css)
    css_no_accent = re.sub(r'html\[data-accent="[^"]+"\] \{[^}]*\}', '', css_no_accent)
    assert "#dbeafe" not in css_no_accent and "#1d4ed8" not in css_no_accent
    # 选择器在主题设置卡片里：标题 + 说明 + 5 张卡片 + 渐变色块 + 勾选
    other_idx = tsx.index('{tab === "config" && subTab === "other" && (')
    notify_idx = tsx.index('{tab === "config" && subTab === "notify" && (')
    other_block = tsx[other_idx:notify_idx]
    assert 't("主题风格")' in other_block
    assert 'className="theme-grid"' in other_block
    assert 'ACCENTS.map' in other_block and 'theme-swatch' in other_block and 'theme-check' in other_block
    assert 'theme-name' in other_block and 'theme-en' in other_block


def test_group_member_shows_masked_ip():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 分组成员按键：两行，上面状态点+名称，下面当前 IP（A.B 显示，C.D 用 * 代替）
    assert 'className={(on ? "seg-on " : "") + "member-btn"}' in tsx
    assert '<span className="member-top">' in tsx
    assert '<span className="member-ip">{maskIp(a.public_ip)}</span>' in tsx
    assert ".seg button.member-btn" in css and ".member-ip" in css
    assert "flex-direction:column" in css


def test_group_time_section_only_interval():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 分组定时轮巡设置区只剩间隔切换周期，固定每日切换时刻/夜间窗口开始/结束三个时间框已取消
    assert 't("固定每日切换时刻")' not in tsx
    assert 't("夜间窗口开始")' not in tsx
    assert 't("夜间窗口结束")' not in tsx
    # 间隔切换周期仍在，说明文字只讲间隔锚点
    assert 't("间隔切换周期")' in tsx
    assert 't("间隔按上次切换锚点每 N 小时切一次。")' in tsx
    assert 'type="time"' not in tsx.split("function GroupCard")[1].split("function ChannelCard")[0]


def test_cards_not_draggable_when_settings_open():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 点开设置时卡片不可拖动：设置展开时不挂拖拽属性、光标变默认；收起时恢复可拖动
    # 账户卡片：cardSettingsId === a.id 时不展开 dragProps
    assert 'cursor: cardSettingsId === a.id ? "default" : "grab"' in tsx
    assert '{...(cardSettingsId === a.id ? {} : dragProps("account", a.id as number))}' in tsx
    # 分组卡片：showSettings 为真时不展开 gDragProps
    assert 'cursor: showSettings ? "default" : "grab"' in tsx
    assert '{...(showSettings ? {} : (gDragProps || {}))}' in tsx
