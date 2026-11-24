"""密钥掩码 + 分组页去框 + 手机端适配 + 夜间底色：

- 后端：GET /settings 与 _mask 返回 email_configured /
  telegram_configured，且不返回任何密钥明文。
- 前端：已配置的密钥一律显示 16 个 * 掩码（SECRET_MASK），保存时跳过
  掩码（不把 * 当新密钥存回去）；点进框清空、失焦留空恢复掩码。
- 分组页：外层卡片框体取消，“+ 新建分组”移到右下角。
- 手机端（≤800px）：单列卡片、指标两列、表格横向滚动、触控目标放大、
  输入框 16px（防 iOS 聚焦缩放）；电脑端保持 1200px 三列布局。
- 夜间底色：用户新代码回到 #0b1220（原 #1e293b 已改回）；白天卡片磨砂回到
  rgba(255,255,255,0.3)，夜间灰卡 0.5；列表 table 字体回到 14px。
- 按钮/选中态：.btn 回到透明底 + 主题色边框（不再主题色填充）；导航与子标签
  选中回到主题色边框样式（不再填充）；Seg 选中仍是主题色边框（无填充、无写死
  蓝字）；主题 accent 回到 600 档（:root #2563eb），主题风格渐变色块仍用 500 档。
- 保存提示框 .toast 已按用户 2026-11-23 19:26 要求恢复：右下角悬浮定位
  （position:fixed; right:20px; bottom:20px），字体用主题色 var(--accent)，
  磨砂底 + 细边框 + 阴影；5 秒自动隐藏的定时器仍在 App.tsx 里。
  分组成员按键的 .member-btn/.member-ip 已按用户 2026-11-23 19:01 要求恢复：
  圆点 + 两行文字（名称/IP），IP 单独一行居中，圆点垂直居中在两行中心。
  语言按钮按用户 2026-11-23 19:58 要求改成下拉菜单：按钮只显示当前语言
  （中 或 En，.lang-on 主题色高亮），不再同时显示两种语言；点按钮开菜单
  选简体中文/English；.lang-off/.lang-sep 已删。按钮仍与主题/退出图标
  按钮同大（36×36 圆角正方形）。
- 主题风格 CSS 已恢复：html[data-accent] 五条规则（500 档 --accent）与
  .theme-grid/.theme-card/.theme-swatch/.theme-check/.theme-name/.theme-en
  卡片样式都在 styles.css 里；:root 的 --accent:#2563eb 只作 fallback。
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
    # M-05（已批准）：手机端指标改 1 列（以前 2 列太挤）
    assert ".stats { grid-template-columns:1fr" in block
    # 桌面端卡片内文字不换行（.stat b/span 与 .status-detail 的 nowrap），
    # 手机端卡片窄，恢复换行
    assert ".stat b, .stat span, .status-detail { white-space:normal; }" in block
    assert "input, select, textarea { font-size:16px" in block
    # 手机端表格横向滚动由 .table-wrap 提供（基础规则，全尺寸适用），
    # 表格本身不再 display:block（否则列宽只按内容算、右边留空）
    assert ".table-wrap { overflow-x:auto; }" in CSS
    assert "table { display:block; overflow-x:auto" not in CSS
    assert ".btn { padding:10px 16px" in block
    assert ".icon-btn { width:36px; height:36px" in block
    assert ".auth-wrap { margin:24px auto 0" in block
    assert ".app { padding:16px 12px 60px" in block
    # 电脑端保持 1200px 三列
    assert ".app { max-width:1200px" in CSS
    assert ".grid3 { display:grid; grid-template-columns:repeat(3,1fr)" in CSS


def test_mobile_server_card_fixed_header_footer():
    # 手机端服务器卡片：高度降为原来的 2/3（自然高度约 390px → 260px），
    # 名称行固定顶部、操作按钮固定底部，中间内容在卡片内上下滑动；桌面端不动。
    m = re.search(r"@media\s*\(max-width:\s*800px\)\s*\{(.*)\}\s*$", CSS, re.S)
    assert m, "mobile media query missing"
    block = m.group(1)
    assert ".server-card { height:260px; overflow:hidden; }" in block
    assert ".server-card-head { flex:none; }" in block
    assert ".server-card-body { flex:1 1 auto; min-height:0; overflow-y:auto;" in block
    assert ".server-card-foot { flex:none; }" in block
    # 固定高度只在手机端媒体查询里，桌面端卡片高度不变
    assert ".server-card" not in CSS.split("@media (max-width: 800px)")[0]
    # 运行概览与实例管理两处服务器卡片：名称行 + 可滚动中间区
    assert TSX.count('className="card server-card"') == 2
    assert TSX.count('className="row server-card-head"') == 2
    assert TSX.count('<div className="server-card-body">') == 2
    # 底部固定按钮行只在实例管理卡片（运行概览卡片本来就没有操作按钮）
    assert TSX.count('className="row server-card-foot"') == 1
    # 状态说明在可滚动中间区里，不在底部按钮行里
    body = TSX.split('<div className="server-card-body">')[1]
    assert 'className="muted status-detail"' in body


def test_dark_bg_is_not_pure_black():
    m = re.search(r'html\[data-theme="dark"\]\s*\{([^}]*)\}', CSS)
    assert m, "dark theme block missing"
    # 用户新代码回到 #0b1220（#1e293b 已改回）；仍不是纯黑 #000000
    assert "--bg:#0b1220" in m.group(1).replace(" ", "")
    assert "#000000" not in CSS


def test_light_card_is_30pct_transparent():
    m = re.search(r'html\[data-theme="light"\]\s*\{([^}]*)\}', CSS)
    assert m, "light theme block missing"
    body = m.group(1).replace(" ", "")
    # 用户新代码回到白天卡片 alpha 0.3（加深到 0.5 的版本已改回），夜间灰卡 0.5
    assert "--card:rgba(255,255,255,0.3)" in body
    assert "--card2:rgba(255,255,255,0.3)" in body
    assert "rgba(255,255,255,0.7)" not in CSS
    dm = re.search(r'html\[data-theme="dark"\]\s*\{([^}]*)\}', CSS)
    assert dm, "dark theme block missing"
    dbody = dm.group(1).replace(" ", "")
    assert "--card:rgba(71,85,105,0.5)" in dbody


def test_google_sans_font_self_hosted():
    # M-09（已批准）：内嵌约 48KB base64 字体已移除，改用系统字体栈；
    # 不走 Google Fonts CDN，国内也能加载
    assert "data:font/woff2;base64," not in CSS
    assert "@font-face" not in CSS
    assert "font-family:system-ui" in CSS
    assert "/fonts/" not in CSS
    assert not (ROOT / "web" / "public" / "fonts").exists(), "no separate font file"

def test_list_font_and_dash_notice_removed():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 列表字体：用户新代码回到表格 14px、表头 13px（缩小到 12px 的版本已改回）
    assert re.search(r"table\s*\{[^}]*font-size:\s*14px", CSS)
    assert re.search(r"th\s*\{[^}]*font-size:\s*13px", CSS)
    assert "table { width:100%; border-collapse:collapse; font-size:12px; }" not in CSS
    # 运行概览顶部的调度提示说明已取消；临时消息条 msg 改成右下角悬浮 toast
    #（字体主题色），不再是顶部通栏 notice——见 test_save_toast_bottom_right_theme_color
    assert "调度运行时会按当前配置执行 VPS 和 DNS 操作。" not in tsx
    assert "调度运行时会按当前配置执行 VPS 和 DNS 操作。" not in i18n
    # M-10（已批准）：Toast 带关闭按钮、错误时加 toast-error 类
    assert 'className={`toast${msgError ? " toast-error" : ""}`}' in tsx
    assert 'className="toast-close"' in tsx
    # 在线/排队状态详情（当前在线…/池内排队待命…）显示主题色字体，
    # 仅 status_dot 为 green/yellow 时用主题色，其它状态仍是 muted 灰色
    # （2026-11-22 用户要求：以前的绿色字体统一改成主题字体）
    assert tsx.count('(a.status_dot === "green" || a.status_dot === "yellow") ? "var(--accent)"') == 2
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
    # 设置子标签：全局设置、账号设置、站点设置、通知设置、日志统计（日志统计在最后）
    m = re.search(r"const SUB_TABS[^;]*;", tsx, re.S)
    assert m, "SUB_TABS missing"
    subs = m.group(0)
    assert '["logs", "日志统计"]' in subs
    assert subs.index('["logs", "日志统计"]') > subs.index('["notify", "通知设置"]')
    assert subs.index('["logs", "日志统计"]') > subs.index('["other", "站点设置"]')
    # 站点设置在通知设置前面（原“其它设置”更名并前移）
    assert subs.index('["other", "站点设置"]') < subs.index('["notify", "通知设置"]')
    assert subs.index('["other", "站点设置"]') > subs.index('["account", "账号设置"]')
    assert '["account", "账号设置"]' in subs
    assert subs.index('["account", "账号设置"]') > subs.index('["settings", "全局设置"]')
    assert subs.index('["account", "账号设置"]') < subs.index('["notify", "通知设置"]')
    assert '["domains", "域名管理"]' not in subs
    # 类型与渲染条件同步：域名管理是顶栏 tab，日志是设置子标签
    assert 'type Tab = "dash" | "accounts" | "groups" | "domains" | "config"' in tsx
    assert 'type SubTab = "settings" | "account" | "notify" | "other" | "logs"' in tsx
    assert '{tab === "domains" && (' in tsx
    assert '{tab === "config" && subTab === "logs" && (' in tsx
    assert 'tab === "logs"' not in tsx and 'subTab === "domains"' not in tsx
    # 日志在进入设置→日志子标签时才加载
    assert 'tab === "config" && subTab === "logs"' in tsx


def test_mobile_nav_horizontal_scroll():
    # 手机端主导航与子导航一行横向滑动、不换行：按钮不收缩、不换行，
    # 滚动条隐藏、触摸滑动顺滑；当前选中的标签自动滑到可见位置；桌面端不动。
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert 'nav { margin-bottom:14px; flex-wrap:nowrap; overflow-x:auto; scrollbar-width:none; -webkit-overflow-scrolling:touch; overscroll-behavior-x:contain;' in css
    assert 'nav::-webkit-scrollbar { display:none; }' in css
    assert 'nav button { padding:10px 14px; flex:none; white-space:nowrap; }' in css
    assert '.subtabs { overflow-x:auto; flex-wrap:nowrap; scrollbar-width:none; overscroll-behavior-x:contain; }' in css
    assert '.subtabs::-webkit-scrollbar { display:none; }' in css
    assert '.subtabs button { flex:none; padding:10px 14px; white-space:nowrap; }' in css
    # 桌面端主导航仍换行（基础规则不变）
    assert 'nav { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; }' in css
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '<nav ref={navRef}>' in tsx
    assert '<div className="subtabs" ref={subtabsRef}>' in tsx
    assert 'scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" })' in tsx


def test_global_settings_rotation_before_threshold_and_no_heading():
    # 全局设置页：轮巡周期（B）在阈值动作/阈值恢复（A）上面，A/B 互换位置；
    # B 上面的“定时轮巡”小标题（C）整行删掉；分组卡片里的同名段落不动。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    g = tsx[tsx.index('{tab === "config" && subTab === "settings" && ('):tsx.index('{tab === "config" && subTab === "other" && (')]
    assert g.index('{t("轮巡周期")}') < g.index('{t("阈值动作")}') < g.index('{t("阈值恢复")}') < g.index('API 刷新时间 *（分钟）')
    assert '<h3 style={{ marginTop: 20 }}>{t("定时轮巡")}</h3>' not in g
    # 显示条件不变：轮巡周期仍只在定时轮巡模式下显示，阈值动作/恢复仍始终显示
    assert 'sRunMode === "time" && (<div className="form2">' in g
    # 选项与保存逻辑不变
    assert 'interval_hours' in g and 'exclusion_action' in g and 'threshold_recovery_action' in g


def test_api_refresh_own_row_and_site_settings_tab():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 全局的 API 刷新时间单独占一行，移到轮巡周期下面（不在两列 form2 里）；
    # 实例表单里的单账户 API 刷新时间已被用户取消（141ca7d，强制跟随全局），
    # 全站只剩全局这一处
    assert tsx.count('API 刷新时间 *（分钟）') == 1
    assert tsx.count('t("API 刷新时间")') == 0
    api_idx = tsx.index('API 刷新时间 *（分钟）')
    assert 'marginTop: 12' in tsx[max(0, api_idx - 120):api_idx]
    assert tsx.index('轮巡周期') < api_idx
    # 站点设置是设置页的第三个子标签（在账号设置后面、通知设置前面、日志统计前面），
    # 只剩站点信息、货币结算与数据清理；主题设置卡片已删除（风格改到右上角
    # 调色板图标下拉里）；登录与安全、备份与恢复已移到新开的“账号设置”
    m = re.search(r"const SUB_TABS[^;]*;", tsx, re.S)
    assert m, "SUB_TABS missing"
    subs = m.group(0)
    assert '["other", "站点设置"]' in subs
    assert '["other", "其它设置"]' not in subs
    assert subs.index('["other", "站点设置"]') < subs.index('["logs", "日志统计"]')
    assert subs.index('["other", "站点设置"]') < subs.index('["notify", "通知设置"]')
    assert subs.index('["other", "站点设置"]') > subs.index('["account", "账号设置"]')
    assert '"other"' in tsx and 'type SubTab = "settings" | "account" | "notify" | "other" | "logs"' in tsx
    # 站点设置里只有站点信息、货币结算、数据清理三张卡片（主题设置卡片已删）
    other_idx = tsx.index('{tab === "config" && subTab === "other" && (')
    acc_idx = tsx.index('{tab === "config" && subTab === "account" && (')
    notify_idx = tsx.index('{tab === "config" && subTab === "notify" && (')
    other_block = tsx[other_idx:acc_idx]
    assert '<div className="card"><h2>{t("货币结算")}</h2>' in other_block
    assert '<div className="card"><h2>{t("数据清理")}</h2>' in other_block
    assert '<h2>{t("主题设置")}</h2>' not in other_block
    assert '<LoginSecurity' not in other_block and '<BackupCard' not in other_block
    # 账号设置（新开，在全局设置后面）里是登录与安全、备份与恢复
    acc_block = tsx[acc_idx:notify_idx]
    assert '<LoginSecurity' in acc_block and '<BackupCard' in acc_block
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
    # 主题风格已恢复：5 条 html[data-accent] 规则，只改 --accent（按钮与边框），
    # 磨砂底色与页面底色不变；:root 的 #2563eb 只作 JS 运行前的 fallback
    for tid, hexv in [("emerald", "#10b981"), ("deepblue", "#3b82f6"), ("warmsun", "#f97316"),
                      ("nebula", "#8b5cf6"), ("crimson", "#ef4444")]:
        m = re.search(rf'html\[data-accent="{tid}"\] \{{([^}}]*)\}}', css)
        assert m, f"data-accent {tid} missing"
        assert m.group(1).replace(" ", "") == f"--accent:{hexv};"
    assert "--accent:#2563eb" in css.replace(" ", "")
    # 主题风格卡片样式：.theme-grid/.theme-card/.theme-swatch/.theme-check/
    # .theme-name/.theme-en 都在 styles.css 里
    for cls in (".theme-grid", ".theme-card", ".theme-swatch", ".theme-check", ".theme-name", ".theme-en"):
        assert cls in css, cls
    for g in ("#a7f3d0,#10b981", "#bfdbfe,#3b82f6", "#fed7aa,#f97316", "#ddd6fe,#8b5cf6", "#fecaca,#ef4444"):
        assert g in tsx, g
    # 选择器在右上角调色板图标下拉里（页眉与登录页各一处，退出按钮前面）：
    # 5 个选项各带渐变色点 + 名称 + 勾选；主题设置卡片已删除
    assert tsx.count('<PaletteIcon size={18} />') == 2
    assert tsx.count('aria-label={t("主题风格")}') == 2
    assert 'ACCENTS.map' in tsx and 'menu-swatch' in tsx
    assert '<h2>{t("主题设置")}</h2>' not in tsx
    assert 'className="theme-grid"' not in tsx


def test_group_member_shows_masked_ip():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 分组成员按键：圆点 + 两行文字，上面名称、下面当前 IP（A.B 显示，C.D 用 * 代替），
    # IP 单独一行居中，圆点用 flex 垂直居中，落在两行文字的中心高度
    assert 'className={(on ? "seg-on " : "") + "member-btn"}' in tsx
    assert '{dotEl(mDot, leftGroup ? t("离队") : (a.status_detail || a.status_label || ""))}<span className="member-text">' in tsx
    # 未选中的成员不在本组运行：全局在线（绿点）、启动中（闪烁绿）或排队中
    # （黄点）时都显示离队（灰点），不显示在线/启动中/排队；已停机（红点）
    # 是实例本身的状态，仍显示已停机，其它灰点状态不动
    assert 'const { dot: mDot, leftGroup } = groupMemberDot(on, a.status_dot);' in tsx
    assert 'const mLabel = leftGroup ? t("离队")' in tsx
    member_dot = (ROOT / "web" / "src" / "memberDot.ts").read_text(encoding="utf-8")
    assert 'statusDot === "green"' in member_dot
    assert 'statusDot === "green_pulse"' in member_dot
    assert 'statusDot === "yellow"' in member_dot
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert "离队" in i18n
    assert '<span className="member-name">{a.name as string}</span>' in tsx
    assert '<span className="member-ip">{maskIp(a.public_ip)}</span>' in tsx
    assert ".member-btn { display:inline-flex; align-items:center;" in css
    assert ".member-text { display:inline-flex; flex-direction:column; align-items:center; }" in css
    assert ".member-ip { display:block;" in css


def test_group_settings_stay_open_after_save():
    # 分组卡片设置区的展开状态放父级（按分组 id 记）：GroupCard 的 key 里含可变
    # 设置字段，保存后 key 变了卡片会重挂载，展开状态放卡片本地会在每次保存后
    # 被重置（设置自动收起）；放父级则保存后仍保持展开
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 父级按分组 id 记展开状态
    assert "const [openGroupSettings, setOpenGroupSettings] = useState<Record<number, boolean>>({});" in tsx
    # GroupCard 不再有本地 showSettings 状态，改由 props 传入
    assert "const [showSettings, setShowSettings] = useState(false);" not in group_card
    assert "showSettings: boolean; onToggleSettings: () => void;" in group_card
    assert "onClick={onToggleSettings}" in group_card
    # 渲染时传入父级的展开状态与切换函数
    assert "showSettings={!!openGroupSettings[g.id as number]}" in tsx
    assert "onToggleSettings={() => setOpenGroupSettings((m) => ({ ...m, [g.id as number]: !m[g.id as number] }))}" in tsx


def test_group_time_section_only_interval():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 分组定时轮巡设置区只剩轮巡周期 + 开始时间，固定每日切换时刻/夜间窗口开始/结束三个时间框已取消
    assert 't("固定每日切换时刻")' not in tsx
    assert 't("夜间窗口开始")' not in tsx
    assert 't("夜间窗口结束")' not in tsx
    # 轮巡周期仍在（原来叫“间隔切换周期”，与全局设置统一改名）；说明小字已随“取消小字说明”一并去掉
    assert 't("轮巡周期")' in tsx
    assert 't("间隔切换周期")' not in tsx
    assert 't("间隔按上次切换锚点每 N 小时切一次。")' not in tsx
    # 开始时间是分组里唯一的 time 输入（默认 00:00:00，带秒）
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    assert 't("开始时间")' in group_card
    assert 'type="time" step="1"' in group_card
    assert group_card.count('type="time"') == 1


def test_cards_not_draggable_when_settings_open():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 点开设置时卡片不可拖动：设置展开时不挂拖拽属性、光标变默认；收起时恢复可拖动
    # 账户卡片：cardSettingsId === a.id 时不展开 dragProps
    assert 'cursor: cardSettingsId === a.id ? "default" : "grab"' in tsx
    assert '{...(cardSettingsId === a.id ? {} : dragProps("account", a.id as number))}' in tsx
    # 分组卡片：showSettings 为真时不展开 gDragProps
    assert 'cursor: showSettings ? "default" : "grab"' in tsx
    assert '{...(showSettings ? {} : (gDragProps || {}))}' in tsx


def test_fee_two_decimals_and_theme_font_and_stat_weight():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 费用精确到小数点后 2 位：fmtCost 辅助函数，4 处单账户费用 + 当前费用回退合计都用它
    #（列表视图新加 1 处，原 3 处变 4 处）
    assert "const fmtCost" in tsx
    assert "n.toFixed(2)" in tsx
    assert tsx.count("fmtCost(a.billing_month_cost)") == 4
    assert "fmtCost(v)} ${c}" in tsx
    # 状态详情（在线/排队）用主题色，不再是绿色
    assert 'var(--dot-green)" : undefined' not in tsx
    # 实例状态卡片字体缩小一号：卡片 fontSize 14
    assert '<div className="card" style={{ fontSize: 14, display: "flex", flexDirection: "column" }}><h3>{t("实例状态")}</h3>' in tsx
    # 本月流量“已取得”与当前费用“已查询”小字都用主题色、14px
    assert tsx.count('color: "var(--accent)", fontSize: 14') >= 2
    assert 'className="muted" style={{ fontSize: 14 }}' not in tsx
    # 服务器卡片统计数值比标签大一号（值 14px、标签 12px）、只加粗
    assert ".stat b { font-size:14px; font-weight:700;" in css
    assert ".stat b { font-size:14px; font-weight:600;" not in css
    # 指标值颜色统一：灰色 muted 只给 .stat 的直接子元素（标签），
    # <b> 里的 IpView/IdView 内层 span 不再被染灰，实例 ID、IP 与流量值同深
    assert ".stat > span { display:block; font-size:12px; color:var(--muted);" in css
    assert ".stat span { display:block;" not in css


def test_small_notes_removed():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 取消小字说明：全站解释性小字已删（表单/分组/DDNS/通知/登录/备份/域名表/账户卡片配置摘要）
    for frag in [
        't("点一下选中 / 取消，被选中的实例参与 DDNS 域名分配；名称前的点是当前状态。")',
        't("跟随全局：按全局每日财报开关；单独开启：即使全局关了也推这台；单独关闭：即使全局开了也不推这台。")',
        't("同名存在多条 A / AAAA / CNAME 记录时报告冲突，由用户整理。")',
        't("当前：{tz}。夜间窗口、固定时刻、间隔锚点、每日开关机、日报推送时间都按此时区执行；保存后立即生效，下次调度按新时区走，不用重启。容器 TZ 环境变量只作后备默认值。',
        't("本月费用账期 {month}"',
        't("Token 留空保存时保持不变，只有填了新值才更新。")',
        't("每天这个时间推送一次，只推已启用的渠道。改时间后自动保存；若改到的时间当天已过，等明天再推，不会当天补推。")',
        't("下载备份：导出全部数据为一个 JSON 文件',
        't("恢复备份：整体替换当前数据，管理员密码也会回到备份时的那一个；当前登录会话不受影响，恢复后建议检查一遍再调度。")',
        't("在 Google Cloud Console 创建 OAuth 应用',
        't("在 GitHub Settings 创建 OAuth 应用',
    ]:
        assert frag not in tsx, frag
    # 账户卡片配置摘要行（阈值动作/恢复/每日定时/日报）已删
    assert 'exclLabel(a.exclusion_action_effective' not in tsx


def test_exchange_rate_readonly_no_input():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 汇率不可修改：输入框已取消，只显示只读文本；保存时不再读 DOM 输入框
    assert 'id="s-fx-rate"' not in tsx
    assert '1 USD = {String(settings.exchange_rate_usd_cny' in tsx
    assert '(document.getElementById("s-fx-rate")' not in tsx


def test_subtabs_button_padding():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 设置子标签按钮：用户新代码回到上下内边距 10px（降到 4px 的版本已改回）
    m = re.search(r"\.subtabs button \{[^}]*\}", css)
    assert m and "padding:10px 20px" in m.group(0)
    m2 = re.search(r"\.subtabs button \{[^}]*padding:10px 14px[^}]*\}", css)
    assert m2, "mobile subtabs padding should be 10px 14px"


def test_managed_domains_title_removed():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 域名管理页“受管理域名 {count}”大标题已删（与账户管理/分组标题一致），
    # 右上角 ⚙（DDNS 设置开关）保留
    assert 't("受管理域名 {count}"' not in tsx
    assert 'title={t("DDNS 设置")}' in tsx
    assert "setShowDdns(!showDdns)" in tsx


def test_save_toast_renders_and_autohides():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 自动保存后的提示框：{msg} 仍渲染为 toast 元素，不再是顶部通栏 notice；
    # 成功类 5 秒自动隐藏的定时器仍在（M-10：错误类不自动消失、带关闭按钮）
    assert 'className={`toast${msgError ? " toast-error" : ""}`}' in tsx
    assert '<div className="notice">{msg}</div>' not in tsx
    assert "setTimeout" in tsx and "5000" in tsx


def test_save_toast_bottom_right_theme_color():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 保存提示框：右下角悬浮（fixed + right/bottom），字体用主题色 var(--accent）
    m = re.search(r"\.toast\s*\{[^}]*\}", css)
    assert m, ".toast missing"
    rule = m.group(0)
    assert "position:fixed" in rule
    assert "right:20px" in rule and "bottom:20px" in rule
    assert "color:var(--accent)" in rule


def test_logs_title_same_size_bold_and_detail_numbers_accent():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 日志行标题（如“调度完成”）与后面详情同大（详情 .muted 是 13px）并加粗
    assert '<span style={{ fontSize: 13, fontWeight: 700 }}>{e.title as string}</span>' in tsx
    # 详情里的数字用主题色：accentNums 按数字分段，数字段包主题色 span
    assert "const accentNums" in tsx
    assert '.split(/(\\d+)/g)' in tsx
    assert 'style={{ color: "var(--accent)" }}' in tsx
    assert "{accentNums(e.detail as string)}" in tsx


def test_dash_card_stats_site_before_traffic_traffic_cost_same_row():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 运行概览卡片指标顺序：区域、IP、站点、流量、费用（三列网格，第一行
    # 区域/IP/站点，第二行流量/费用——站点排在流量前面，流量和费用同一行）
    m = re.search(r'<div className="stats">\s*(.*?)\s*</div>\s*\{a\.status_detail', tsx, re.S)
    assert m, "dash card .stats missing"
    block = m.group(1)
    labels = ['t("区域")', "IP", 't("站点")', 't("流量")', 't("费用")']
    idx = [block.index(x) for x in labels]
    assert idx == sorted(idx), idx
    # 三列网格里第 4、5 项（流量、费用）落在同一行（第二行）
    assert block.count('<div className="stat') == 5


def test_modal_frosted_50_percent():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 对话框磨砂玻璃 50%：亮色白底 0.5、暗色灰底 0.5，不再用 --card（亮色只有 0.3，
    # 太透明，后面的页面内容会透出来）；磨砂模糊 blur(18px) 保留
    m = re.search(r"\.modal\s*\{[^}]*\}", css)
    assert m, ".modal missing"
    assert "background:rgba(255,255,255,0.5)" in m.group(0)
    assert "backdrop-filter:blur(18px)" in m.group(0)
    assert 'html[data-theme="dark"] .modal { background:rgba(71,85,105,0.5); }' in css


def test_webdav_dialog_opaque_drag_anywhere_click_outside_saves():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 云端备份设置对话框：90% 不透明（只作用于本对话框，其它对话框仍是 50% 磨砂）
    assert ".modal.modal-wd { background:rgba(255,255,255,0.9); }" in css
    assert 'html[data-theme="dark"] .modal.modal-wd { background:rgba(71,85,105,0.9); }' in css
    assert 'className="modal modal-wd"' in tsx
    # 对话框任意位置按住都可拖动，拖动绑在 .modal 上（不再只在标题上）；
    # 点输入框/按钮等控件时不拖动，让控件正常聚焦/点击
    assert 'className="modal modal-wd" onClick={(e) => e.stopPropagation()} onMouseDown={onWdDragStart}' in tsx
    assert 'if ((e.target as HTMLElement).closest("input, button, select, textarea, a, [contenteditable]")) return;' in tsx
    assert '<h3 style={{ cursor: "grab", userSelect: "none" }}>{t("云端备份设置")}</h3>' in tsx
    # 点遮罩（对话框外）先自动保存再退出；其它对话框（动态自定义、添加域名）
    # 不受影响，点遮罩只是取消/关闭，不保存
    assert "await saveWd(); setShowWdDialog(false);" in tsx
    assert 'onClick={() => setShowDynCustom(false)}' in tsx
    assert 'onClick={() => setShowAddDom(false)}' in tsx


def test_group_follow_system_hides_param_sections():
    # 运行模式选“跟随系统”（switch_mode 为 NULL）时，下面的参数框体都不显示：
    # 定时轮巡段、负载均衡段、阈值动作、阈值恢复；设置区只留分组名称、运行模式
    # 与删除分组。切回具体模式时参数框体重新出现（卡片按 switch_mode 重挂载，
    # 本地状态重新初始化）。分组名称、运行模式、删除分组不受影响。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 参数段都包在“未选跟随系统”的条件里（wrapper 在运行模式 Seg 之后打开）
    i = group_card.index('{g.switch_mode !== null && (<>')
    assert group_card.index('t("运行模式")') < i
    # 时间段、负载均衡段、阈值动作、阈值恢复都在 wrapper 打开之后、删除分组之前
    k = group_card.index('t("删除分组")', i)
    assert 'effMode === "time" || effMode === "both"' in group_card[i:k]
    assert 'effMode !== "time"' in group_card[i:k]
    assert 't("阈值动作")' in group_card[i:k]
    assert 't("阈值恢复")' in group_card[i:k]


def test_add_domain_button_at_bottom_right_of_domains_card():
    # 域名管理页的“+ 添加域名”按钮移到域名卡片右下角（表格下方右对齐一行），
    # 顶行只留 DDNS 设置 ⚙；只留一个添加按钮
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert tsx.count('>{t("+ 添加域名")}</button>') == 1
    # 右下角一行：表格之后、弹窗之前
    assert '<div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>\n            <button className="btn" onClick={() => { setAddDomName(""); setShowAddDom(true); }}>{t("+ 添加域名")}</button>\n          </div>' in tsx
    # 按钮在域名表格之后（不在顶行）
    dom = tsx.index('<table className="domains-table">')
    tbl_end = tsx.index("</table>", dom)
    btn = tsx.index('>{t("+ 添加域名")}</button>')
    assert btn > tbl_end


def test_header_lang_dropdown_and_logout():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 语言切换：按钮只显示当前语言（中 或 En，主题色高亮），点按钮开下拉菜单
    # 选简体中文/English，不再点一下直接切换、也不再同时显示两种语言；
    # 语言键与主题/退出图标按钮同大（36×36 圆角正方形，内容居中）；
    # 语言与主题两个下拉菜单互斥（开一个关另一个）
    assert 'className="btn ghost lang-btn"' in tsx
    assert 'setLang(lang === "zh" ? "en" : "zh")' not in tsx
    assert tsx.count('const [langMenuOpen, setLangMenuOpen] = useState(false);') == 2  # 页眉与登录页各一处
    assert tsx.count('<span className="lang-on">{lang === "zh" ? "中" : "En"}</span>') == 2
    assert 'setLang("zh")' in tsx and 'setLang("en")' in tsx
    # 语言选项显示各自的母语名，不走 t() 翻译（否则英文界面下“简体中文”会被
    # 翻成 Simplified Chinese）；页眉与登录页各一处
    assert 't("简体中文")' not in tsx
    assert tsx.count('<span>简体中文</span>') == 2
    assert tsx.count('<span>English</span>') == 2
    assert ".lang-off" not in css and ".lang-sep" not in css
    assert "lang-off" not in tsx and "lang-sep" not in tsx
    # 双类选择器 .btn.lang-btn：单类会被后写的 .btn（7px 14px、14px 字）覆盖，
    # 中/En 按钮实际比图标按钮大；双类优先级更高才真正同大。
    # 三个按钮都是 36×36 圆角正方形（宽高相等、圆角 10px、内容居中）；
    # 页眉顶栏（站点名称、介绍、三个图标）sticky 固定在页面顶部，导航仍随页面滚动。
    assert ".btn.lang-btn { width:36px; height:36px; min-height:36px; padding:0; font-size:14px; line-height:1; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; }" in css
    assert ".lang-on { color:var(--accent); }" in css
    assert "header { position:-webkit-sticky; position:sticky; top:0; z-index:55; background:var(--bg); padding-top:16px; }" in css
    assert "nav { position:sticky" not in css and "nav { position:fixed" not in css
    # 主题键图标与退出图标按 36px 按钮等比放大（主题 14→18px，页眉与登录页
    # 各一处；退出 9→16px，同样两处）；语言字 9→14px，不挤压；
    # .btn.theme-btn 同样是 36×36 圆角正方形；点主题键仍弹出亮色/暗色/跟随系统菜单
    # 调色板图标（主题风格）同样 18px、两处，与主题键同款按钮
    assert tsx.count("<ThemeIcon theme={theme} size={18} />") == 2
    assert tsx.count("<PaletteIcon size={18} />") == 2
    assert tsx.count("size={14}") == 0
    assert tsx.count('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"') == 2  # 退出图标 16px，两处
    assert tsx.count('<svg width="9" height="9" viewBox="0 0 24 24"') == 0
    assert ".btn.theme-btn { width:36px; height:36px; min-height:36px; padding:0; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; }" in css
    assert 't(v === "light" ? "亮色" : v === "dark" ? "暗色" : "跟随系统")' in tsx
    # 登录页右上角同样是这排图标按钮（语言键 + 主题图标 + 退出图标，右对齐一行）
    assert '<div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>' in tsx
    assert 'aria-label={t("退出")}' in tsx
    # 退出登录图标在页眉最右边（主题按钮之后）：调 /api/auth/logout、
    # 清 token、回登录页
    assert 'aria-label={t("退出登录")}' in tsx
    assert 'post("/api/auth/logout", {})' in tsx
    assert 'setToken(null)' in tsx
    assert 'setAuthState("login")' in tsx
    # 顺序：语言键、主题键（模式）、调色板键（风格）、退出键（退出在最右）
    assert tsx.index("lang-btn") < tsx.index("theme-btn")
    assert tsx.index("theme-btn") < tsx.index('t("退出登录")')
    # 调色板在退出按钮前面（页眉与登录页各一处），点开是 5 种风格下拉
    assert tsx.count('aria-label={t("主题风格")}') == 2
    hdr = tsx[tsx.index("function App"):]
    assert hdr.index('t("主题风格")') < hdr.index('t("退出登录")')


def test_traffic_obtained_line_uses_theme_color():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 本月流量卡片的“已取得 X / Y 个账户用量”用主题色（与“已查询 X / Y 个账户账单”一致），
    # 钉在卡片左下角（卡片 flex 列布局 + marginTop: auto，左对齐默认）
    line = [l for l in tsx.splitlines() if "已取得 {a} / {b} 个账户用量" in l]
    assert len(line) == 1
    assert 'color: "var(--accent)"' in line[0]
    assert 'marginTop: "auto"' in line[0]
    assert 'textAlign' not in line[0]
    assert 'className="muted"' not in line[0]


def test_theme_lines_alignment_dash_left_accounts_left():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 运行概览：主题字体的小字行都钉在卡片左下角（卡片 flex 列布局 + marginTop: auto，左对齐默认）——
    # 实例状态底部“调度…”、本月流量“已取得…”、当前费用“已查询…”；服务器卡片的状态详情
    # 不再钉底，改在卡片内上下滑动的中间区里（手机端名称固定顶部、按钮固定底部）。
    assert 'marginTop: "auto", paddingTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8' in tsx
    assert '<span style={{ color: "var(--accent)" }}>{t("调度")}' in tsx
    assert 'marginTop: 8, color: "var(--accent)", textAlign: "right"' not in tsx
    assert tsx.count('color: "var(--accent)", fontSize: 14, marginTop: "auto"') >= 2
    assert 'color: "var(--accent)", fontSize: 14, textAlign: "right"' not in tsx
    # 服务器卡片状态详情在可滚动中间区（server-card-body）里，不再用 marginTop: auto 钉底
    assert 'marginTop: "auto", paddingTop: 6' not in tsx
    # 账户管理：状态详情行移到可滚动中间区里（指标下面），操作按钮单独一行固定在卡片底部靠右；
    # 不再是状态与按钮同一行
    assert 'className="row server-card-foot" style={{ justifyContent: "flex-end", paddingTop: 12 }}>' in tsx
    assert 'marginTop: 4, color: (a.status_dot' not in tsx
    assert 'justifyContent: "flex-end", marginTop: 12 }}><ActionButtons' not in tsx


def test_group_card_custom_interval_and_start_time():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 轮巡周期有“自定义”选项（值 0），选自定义时数值输入直接跟在选项下面
    # （数字 + 分钟/小时/天，可编辑），不再另起“自定义间隔”行；
    # 自定义按钮带 sub 第二行数值（双行显示），所以选项行以 sub 结尾，不再是 label 后直接闭合
    assert '{ value: "0", label: t("自定义"), sub: interval === "0"' in group_card
    assert 'interval === "0"' in group_card
    assert 't("自定义间隔")' not in group_card
    assert 'custom_interval_minutes' in group_card
    # 自定义的数值输入在轮巡周期 Seg 之后、开始时间之前（同一缩进 div 内）
    assert group_card.index('<Seg value={interval}') < group_card.index('value={custVal}') < group_card.index('t("开始时间")')
    # 开始时间默认 00:00:00，带秒的 time 输入，失焦自动保存
    assert 'interval_start_time: startTime || "00:00:00"' in group_card
    # 切回固定时长时清掉自定义分钟数（否则自定义优先，固定时长不生效）
    assert "custom_interval_minutes: 0" in group_card


def test_group_card_domain_single_select_and_multi_group_members():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # “绑定域名（最多 1 个）”改名“域名选择”，改成列表单选：全部域名列成按键，点一个绑到本组
    assert 't("绑定域名（最多 1 个）")' not in tsx
    assert 't("域名选择")' in group_card
    assert "/api/groups/${g.id}/bind-domain" in group_card
    # “成员实例（参与 DDNS，可多选）”改名“选择成员实例”；同一台可同时选进多个分组
    assert 't("成员实例（参与 DDNS，可多选）")' not in tsx
    assert 't("选择成员实例")' in group_card
    assert "group_ids" in group_card
    assert "a.group_ids" in group_card


def test_add_domain_dialog():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 添加域名改对话框形式：点“+ 添加域名”弹出对话框，填完确认才添加；
    # 内联的域名输入 + 分组下拉已取消，改成对话框里的受控输入
    assert 'id="dom-name"' not in tsx
    assert 'id="dom-group"' not in tsx
    assert "showAddDom" in tsx
    assert 'className="modal-overlay"' in tsx
    assert 'className="modal"' in tsx
    # 按钮放右下：触发按钮在卡片右下，对话框里的取消/添加也在右下
    assert 'justifyContent: "flex-end", marginTop: 12' in tsx
    # 云端备份对话框也用同一套 modal 样式，CSS 已恢复：遮罩固定全屏、
    # 弹框磨砂、点遮罩关闭（添加域名对话框同步恢复正常显示）
    assert ".modal-overlay" in css and ".modal" in css
    assert "position:fixed" in css and "z-index:200" in css


def test_add_domain_no_group_select_and_new_group_domain_select():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 添加域名对话框不再选分组：没有分组下拉，也不再校验“请选择分组”
    assert 'id="dom-group"' not in tsx
    assert "addDomGroup" not in tsx
    assert 't("请选择分组")' not in tsx
    # 新建分组表单里选一个已添加的域名（可选；已被选取的不可选）
    assert "newGroupDomain" in tsx
    assert 't("选择域名")' in tsx
    assert 't("暂不选择域名")' in tsx
    assert "（已被 {group} 选取）" in tsx
    assert "domain_id: Number(newGroupDomain)" in tsx


def test_accounts_page_no_outer_card():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 账户管理页取消最外面的大框体：卡片直接排在页面上（与分组管理页一致）
    accounts = tsx.split('{tab === "accounts"')[1].split('{tab === "domains"')[0]
    assert '<div className="card">' not in accounts.split("accounts.map")[0]
    assert "accounts.map((a) => (" in accounts


def test_buttons_accent_border_not_filled():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    btn = re.search(r"\.btn\s*\{([^}]*)\}", css)
    assert btn is not None
    body = btn.group(1)
    # 主按钮全局灰色：透明底 + 灰边框 + 正常文字（主题色边框 + 蓝字的版本已改回）
    assert "background:transparent" in body
    assert "border:2px solid var(--border)" in body
    assert "color:var(--text)" in body
    assert "background:var(--accent)" not in body
    assert "var(--accent)" not in body
    # 删除实例仍是红色实心（内联样式，不受 .btn 影响）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'background: "#EA4335"' in tsx


def test_group_card_ddns_toggle_only():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 分组卡片与服务器卡片同一布局：右上只有 ⚙（与服务器卡片一样的 icon-btn
    # 齿轮 SVG，不再是 btn ghost + ⚙️ emoji）；DDNS 开关移到卡片右上角
    # （标题行，⚙ 左边）；删除分组是卡片右下角的红色垃圾桶图标（不再是
    # 设置区底部的红色实心文字按钮）；刷新/重启按钮已取消，启动/停机合并
    # 成 DDNS 这一个开关
    assert '<button className="icon-btn" title={t("分组设置")}' in group_card
    assert "⚙️" not in group_card
    assert 'className="icon-btn danger" title={t("删除")}' not in group_card
    # 删除是红色垃圾桶图标（右下角），不是红色实心文字按钮
    assert '<button className="icon-btn danger" title={t("删除分组")}' in group_card
    assert 'style={{ background: "#EA4335", borderColor: "#EA4335", color: "#fff" }}' not in group_card
    assert '{t("删除分组")}' in group_card
    # DDNS 开关在标题行（⚙ 左边），带悬停状态提示
    assert '<span title={ddnsOn ? t("DDNS 已开启") : t("DDNS 已关闭")}' in group_card
    # 只有 DDNS 开关：用现成的 Toggle 开关组件，状态一眼可辨（开=主题色，
    # 关=灰色）；开=取消暂停（paused: 0），关=暂停（paused: 1），域名指向
    # 0.0.0.0、不分配实例（后端 engine.py 已是这个逻辑）；关时先弹确认
    assert 'gbtn(' not in group_card
    assert 'groupAction' not in group_card
    assert '<Toggle checked={ddnsOn} onChange={(v) => void toggleDdns(v)}' in group_card
    assert 'const ddnsOn = !g.paused;' in group_card
    assert 'paused: on ? 0 : 1' in group_card
    assert 't("关闭分组 {name} 的 DDNS？域名将指向 0.0.0.0"' in group_card
    assert 't("DDNS 已关闭，域名指向 0.0.0.0")' in group_card
    assert 't("DDNS 已开启")' in group_card
    # 刷新/重启的逐台操作已不在分组卡片上（服务器卡片的 ActionButtons 不动）
    assert 'for (const m of members) await post(`/api/accounts/${m.id}/refresh`, {})' not in group_card
    assert 'for (const m of members.filter((x) => x.instance_status === "Running")) await post(`/api/accounts/${m.id}/restart`, {})' not in group_card
    assert 't("重启分组 {name} 内全部实例？"' not in group_card
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    for key in ("DDNS 已开启", "DDNS 已关闭", "DDNS 已关闭，域名指向 0.0.0.0", "关闭分组 {name} 的 DDNS？域名将指向 0.0.0.0"):
        assert key in i18n, key


def test_ddns_settings_no_account_id():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # Account ID 已取消：DDNS 设置卡片只有域名 + 账户邮箱 + API Token
    assert 'id="cf-account-id"' not in tsx
    assert "cloudflare_account_id" not in tsx
    assert 'id="cf-zone"' in tsx
    assert 'id="cf-email"' in tsx
    assert 'id="cf-token"' in tsx


def test_ddns_check_interval_hardcoded_no_setting():
    src = (ROOT / "app" / "scheduler.py").read_text(encoding="utf-8")
    # DDNS 检查固定每分钟一次，无后端设置；每分钟的检查会刷新到期的账户
    # （按各账户生效的 API 刷新时间：自定义或跟随全局），再重算分配并同步 DNS
    assert "DDNS_CHECK_INTERVAL_SECONDS = 60" in src
    assert "def ddns_check" in src
    assert "run_once(refresh=True, quiet=True)" in src
    assert "cloudflare" not in src.split("DDNS_CHECK_INTERVAL_SECONDS")[0].split("def ddns_check")[0][-200:] or True
    # 设置里没有 ddns 检查间隔这一项
    settings = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
    assert "ddns_check" not in settings and "ddns_interval" not in settings


def test_ddns_settings_domain_email_token_form():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # DDNS 设置按截图的表单：域名 + 账户邮箱 + API Token（账户邮箱保留）
    assert 'id="cf-zone"' in tsx
    assert 'id="cf-email"' in tsx
    assert 'id="cf-token"' in tsx
    assert "patch.cloudflare_zone" in tsx
    assert "patch.cloudflare_email" in tsx
    # 新增/编辑域名时优先用 DDNS 设置里填的 Zone 名，不再只按后两段推算
    assert '(settings.cloudflare_zone as string) || name.split(".").slice(-2)' in tsx
    assert '(settings.cloudflare_zone as string) || nm.split(".").slice(-2)' in tsx


def test_required_fields_have_star_marker():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 必填字段的 * 号标识全局应用：DDNS 的 API Token、分组名称、添加域名
    # 的域名都要带 *（req 样式）；账户邮箱是选填，不带 *
    assert '{t("Cloudflare API Token *")}' in tsx
    assert '{t("分组名称 *")}' in tsx
    assert '{t("域名 *")}' in tsx
    assert '<label className="req">{t("Cloudflare API Token *")}</label>' in tsx


def test_group_card_settings_collapsed_by_default():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 点开分组管理后，每个分组卡片默认是不被编辑状态（设置区收起），
    # 点右上角 ⚙ 才展开；展开状态在父级按分组 id 记，初始为空（全部收起）
    assert "const [openGroupSettings, setOpenGroupSettings] = useState<Record<number, boolean>>({});" in tsx
    assert "showSettings={!!openGroupSettings[g.id as number]}" in tsx


def test_accounts_tab_renamed_to_instances():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 顶部导航：账户管理改名实例管理（页眉标题已按用户 2026-11-23 20:00
    # 要求改成固定站点名称，不再按标签页切换，见 test_header_shows_site_name）
    assert '["accounts", "实例管理"]' in tsx
    assert "账户管理" not in tsx or "// 拖动排序" in tsx and tsx.count("账户管理") == 0
    assert '实例管理: "Instance Management"' in i18n
    assert "账户管理" not in i18n


def test_dash_footer_no_pause_button_refresh_zero_off():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 暂停调度按钮已被用户取消：运行概览不再有恢复/暂停调度按钮，
    # 也不再调 POST /api/schedule/pause；关闭调度改用 API 刷新时间选“不刷新”（0），
    # 选分钟数即恢复运行，后端 scheduler_paused 跟该选择同步
    assert 'post("/api/schedule/pause"' not in tsx
    assert 't("恢复调度")' not in tsx
    assert 't("暂停调度")' not in tsx
    assert '{ value: "0", label: t("不刷新")' in tsx
    assert '不刷新=暂停调度，选分钟数=恢复运行' in tsx
    assert 'scheduler_paused: sRefresh === "0" ? "1" : "0"' in tsx


def test_parent_labels_16px_children_14px_and_no_two_line_label():
    # 父项字体统一 16px，子项字体统一 14px，父项比子项大一号（应用全局）。
    # “负载均衡”父项字已取消：全局切换模式不再用 TwoLineLabel，只留单行缩进标签
    # （与分组卡片同款）；TwoLineLabel 组件已删除。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert "function TwoLineLabel" not in tsx
    assert "TwoLineLabel" not in tsx
    assert 'paddingLeft: "2em"' in tsx
    # 子项字体统一 14px：全局 label 规则
    assert "label { display:block; font-size:14px;" in css
    # 父项字体统一 16px：API 刷新时间标签与全局/分组的父标签
    assert '<label className="req" style={{ fontSize: 16 }}>{t("API 刷新时间 *（分钟）")}</label>' in tsx
    assert tsx.count('<label className="lbl" style={{ fontSize: 16 }}>{t("运行模式")}</label>') == 1
    assert tsx.count('<label className="lbl">{t("运行模式")}</label>') == 1
    assert tsx.count('<label className="lbl" style={{ fontSize: 16 }}>{t("阈值动作")}</label>') == 0
    assert tsx.count('<label className="lbl" style={{ fontSize: 16 }}>{t("阈值恢复")}</label>') == 0
    assert tsx.count('style={{ paddingLeft: "2em", fontSize: 16 }}>{t("动态切换间隔")}</label>') == 0
    assert tsx.count('style={{ paddingLeft: "2em", fontSize: 16 }}>{t("轮巡周期")}</label>') == 0
    # 子项标签（14px，走全局 label 规则）：全局与分组各一处
    assert tsx.count('<label className="lbl">{t("阈值动作")}</label>') == 2
    assert tsx.count('<label className="lbl">{t("阈值恢复")}</label>') == 2
    assert tsx.count('style={{ paddingLeft: "2em" }}>{t("动态切换间隔")}</label>') == 2
    assert tsx.count('style={{ paddingLeft: "2em" }}>{t("轮巡周期")}</label>') == 2
    # 全局切换模式是单行缩进标签（子项，14px 走全局 label 规则），不再有父项“负载均衡”行
    assert '<label className="req"><span style={{ paddingLeft: "2em" }}>{t("切换模式")}</span></label>' in tsx
    # 全局切换阀值在切换模式下面（不再左右两列），单行缩进标签
    assert tsx.index('<Seg value={sMode}') < tsx.index('t("切换阀值 *（%）")')
    assert '<label className="req"><span style={{ paddingLeft: "2em" }}>{t("切换阀值 *（%）")}</span></label>' in tsx
    assert '<label className="lbl"><span style={{ paddingLeft: "2em" }}>{t("切换阀值（GB）")}</span></label>' in tsx
    # 分组侧同样只留子标签：切换模式与切换阀值（%）/（GB）都是单行缩进标签
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    assert '<label className="lbl"><span style={{ paddingLeft: "2em" }}>{t("切换模式")}</span></label>' in group_card


def test_subsection_h3_indent_two_chars_under_card_title():
    # 子项 h3（登录与安全下的修改密码/指纹登录/Google 登录/GitHub 登录，
    # 分组卡片的选择成员实例/域名选择，通知的每日推送）对应父项（卡片 h2
    # 标题）留 2 字符空位：h3 缩进 2em，与其下已缩进的内容对齐。应用全局。
    # 概览顶卡（实例状态/本月流量/当前费用）与弹窗标题是卡片主标题，不缩进。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    for h3 in ['<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("修改密码")}</h3>',
                '<h3 style={{ marginTop: 20, paddingLeft: "2em", fontSize: 14 }}>{t("指纹登录")}</h3>',
                '<h3 style={{ marginTop: 20, paddingLeft: "2em", fontSize: 14 }}>{t("Google 登录")}</h3>',
                '<h3 style={{ marginTop: 20, paddingLeft: "2em", fontSize: 14 }}>{t("GitHub 登录")}</h3>',
                '<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("选择成员实例")}</h3>',
                '<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("域名选择")}</h3>',
                '<h3 style={{ margin: 0, paddingLeft: "2em", fontSize: 14 }}>{t("每日推送")}</h3>']:
        assert h3 in tsx, h3
    # 概览顶卡标题不缩进
    assert '<h3>{t("实例状态")}</h3>' in tsx
    assert '<h3>{t("本月流量")}</h3>' in tsx


def test_buttons_hover_zoom_global():
    # 按钮悬停放大特效，应用全局：主按钮 / Seg / 图标按钮（设置 ⚙、删除 🗑、
    # 操作图标）/ 导航 / 子标签 / OAuth / 主题卡片，悬停时整体放大 1.08 倍，
    # 过渡 0.15s；禁用按钮不放大。transform 只影响视觉，不推开其它元素。
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert ".btn, .seg button, .icon-btn, nav button, .subtabs button, .oauth-btn, .theme-card { transition:transform .15s ease; }" in css
    assert "transform:scale(1.08);" in css
    for sel in (".btn:hover:not(:disabled)", ".seg button:hover:not(:disabled)",
                ".icon-btn:hover:not(:disabled)", "nav button:hover:not(:disabled)",
                ".subtabs button:hover:not(:disabled)", ".oauth-btn:hover:not(:disabled)",
                ".theme-card:hover:not(:disabled)"):
        assert sel in css, sel


def test_header_taller_and_footer_fixed():
    # 页眉占用高度太低不协调：增高页眉——logo 48px（桌面端，与登录页同高；
    # 手机端保持 36px）、标题 28px→32px、页眉上边距 8px→16px、副标题下边距 20px→24px。
    # 页脚固定在视口底部，不跟随页面滚动：position:fixed + 底色 + 上边框；
    # .app 下边距留出空间（桌面 60px、手机 60px），不让固定页脚盖住内容。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert '<img src="/api/settings/logo" alt="logo" className="site-logo" />' in tsx
    assert ".site-logo { height:48px; max-width:140px; object-fit:contain; }" in css
    assert ".site-logo { height:36px; }" in css
    assert "header h1 { font-size:32px; margin:0 0 4px; }" in css
    assert "header p { color:var(--muted); margin:0 0 24px; }" in css
    assert "padding-top:16px;" in css
    assert "footer { position:fixed; left:0; right:0; bottom:0; z-index:50; background:var(--bg); border-top:1px solid var(--border); padding:12px 0; text-align:center; }" in css
    assert "<footer>" in tsx
    assert '<footer style=' not in tsx
    assert ".app { max-width:1200px; margin:0 auto; padding:24px 20px 60px; }" in css
    assert ".app { padding:16px 12px 60px; }" in css

def test_seg_selected_is_accent_border():
    # 选中项用主题色边框 + 加粗，不用填充色、不写死蓝字（填充与写死色都已取消），
    # 未选中项不动（透明底、灰框、灰字）
    assert ".seg button.seg-on { border:2px solid var(--accent); padding:7px 15px; font-weight:600; }" in CSS
    assert ".seg button { background:transparent; border:1px solid var(--seg-off); color:var(--muted);" in CSS
    # 填充已取消，选中成员按键里的 IP 不再用浅白字
    assert ".seg button.seg-on .member-ip" not in CSS


def test_controls_align_with_indented_labels():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 控件与缩进 2 字符的标签左对齐：Seg/输入框包在 paddingLeft 2em 的 div 里，
    # 阈值动作/阈值恢复整段缩进 2em；全局设置与分组设置都一样
    assert tsx.count('paddingLeft: "2em"') >= 12
    # 全局：切换模式三选一 Seg（sMode，百分比/固定流量/动态模式）、轮巡周期 Seg
    # 包在缩进 div 里
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sMode}' in tsx
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sInterval}' in tsx
    # 全局：阈值动作/阈值恢复整段缩进
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>' in tsx
    # API 刷新时间按键区与上面轮巡周期按键区左对齐（同缩进 2em）
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sRefresh}' in tsx
    # 分组：负载均衡四选一 Seg（跟随系统时值为 "follow"，动态模式时为
    # "dynamic"，否则跟 diffMode）、间隔 Seg 包在缩进 div 里
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={(lbFollowGlobal ? "follow" : g.switch_mode === "dynamic" ? "dynamic" : diffMode) as string}' in tsx
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={interval}' in tsx


def test_ip_copy_only_no_full_reveal():
    # IP 显示：只掩码（A.B.*.*），不提供点按显示完整 IP；实例管理页的卡片
    # 与列表点 ⧉ 复制完整 IP；运行概览的卡片与列表只显示掩码 IP，没有复制按钮
    # （IpView 的 copy 参数，默认 true，运行概览传 copy={false}）；
    # 复制用 navigator.clipboard，明文 HTTP 下回退 textarea + execCommand
    # （clipboard 在非安全上下文不可用）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "showFullIp" not in tsx and "toggleFullIp" not in tsx
    # 复制逻辑抽成 copyText（IP / 实例 ID 共用），copyIp 只是包一层
    assert "const copyText = async (text: string, okMsg: string) => {" in tsx
    assert "navigator.clipboard.writeText(text)" in tsx
    assert 'const copyIp = async (ip: string) => { await copyText(ip, t("IP 已复制")); };' in tsx
    assert "const IpView = ({ a, copy = true }: { a: Any; copy?: boolean }) => {" in tsx
    assert "{copy && ip ? <button" in tsx
    # 实例管理页卡片与列表用 IpView（带复制），运行概览卡片与列表用 copy={false}
    assert tsx.count("<IpView a={a} />") == 2
    assert tsx.count("<IpView a={a} copy={false} />") == 2
    assert 'document.execCommand("copy")' in tsx
    assert '{maskIp(a.public_ip)}' in tsx
    assert 'title={t("复制 IP")}' in tsx
    # 提示语由 copyIp 传给 copyText（okMsg），copyText 里统一 setMsg(okMsg)
    assert 'setMsg(okMsg)' in tsx
    # 复制按钮紧跟 IP 后方同一行：IpView 外层是 inline-flex（一行不换行，
    # 文字与按钮之间 gap 4px），运行概览与实例管理的卡片/列表四处共用
    assert 'display: "inline-flex", alignItems: "center", gap: 4, whiteSpace: "nowrap"' in tsx
    # 分组成员按键的 IP 仍掩码（悬停提示里有完整 IP），不动
    assert '<span className="member-ip">{maskIp(a.public_ip)}</span>' in tsx
    # 点按显示/隐藏的文案已删（t() 0 缺失由 i18n 测试覆盖，这里只点名）
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    for key in ("IP 已复制", "复制 IP"):
        assert key in i18n, key
    for key in ("点击显示完整 IP", "点击隐藏 IP"):
        assert key not in i18n, key


def test_group_mode_seg_no_dynamic_option():
    # 分组卡片顶层“运行模式”三项：定时轮巡 / 负载均衡 / 跟随系统。动态模式不在顶层，
    # 在下面的负载均衡切换方式（百分比/固定流量/动态模式/跟随系统）里选；
    # 动态分组在顶栏显示负载均衡（动态是负载均衡的子模式），下面才显示动态模式。
    # 跟随系统即清空 switch_mode（存 NULL，继承全局切换模式）。
    # 后端与引擎仍支持 dynamic（test_optimize 的 test_dynamic_switch_*）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 顶层 Seg 的值：未填（跟随系统）显示跟随系统，time 显示定时轮巡，其它（含 dynamic）都显示负载均衡
    assert '<Seg value={(g.switch_mode == null ? "follow" : g.switch_mode === "time" ? "time" : "load_balance")}' in group_card
    # 顶层标签已由“切换模式”改名“运行模式”（只改分组卡片顶层这一处；新建分组表单
    # 与负载均衡切换方式的“切换模式”标签不动）
    assert '<label className="lbl">{t("运行模式")}</label>' in group_card
    assert '<label className="lbl">{t("切换模式")}</label>' not in tsx
    assert '{ value: "time", label: t("定时轮巡") }' in group_card
    assert '{ value: "load_balance", label: t("负载均衡") }' in group_card
    assert '{ value: "follow", label: t("跟随系统"), title: t("运行模式继承全局设置") }' in group_card
    # 选跟随系统即把 switch_mode 清空（存 NULL，继承全局）
    assert '{ switch_mode: m === "follow" ? null : m }' in group_card
    # 动态模式选项还在下面（负载均衡切换方式四选一里）、新建分组表单与全局设置里，
    # 共 3 处；顶层已无动态模式选项
    assert tsx.count('{ value: "dynamic", label: t("动态模式")') == 3
    # 域名表格里的分组模式标签：跟随分组卡片顶栏（顶层模式，time→定时轮巡，
    # 其余→负载均衡；动态是负载均衡的子模式，显示负载均衡，不显示“动态模式”），
    # 颜色也与分组卡片顶栏的绿 pill 一致（className="pill"）。
    # （实例列表没有分组列；服务器卡片名后面的 accountModeLabels 仍显示具体模式，
    # 见 test_server_card_groups_modes_and_masked_instance_id）
    assert 'groupMode(d.group_id) === "dynamic" ? t("动态模式")' not in tsx
    assert '<span className="pill">{groupMode(d.group_id) === "time" ? t("定时轮巡") : t("负载均衡")}</span>' in tsx


def test_group_mode_follow_global_effective_mode():
    # 顶层“跟随系统”：分组 switch_mode 为 NULL 时继承全局切换模式；
    # 有效模式决定显示哪段参数：time 只显示定时轮巡段，load_balance/dynamic
    # 只显示负载均衡段，both（全局默认）两段都显示
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 有效模式：分组未填时取全局（非法值回退 both）
    assert 'const effMode = (g.switch_mode as string | null) ?? (["time", "load_balance", "dynamic", "both"].includes(_gsm) ? _gsm : "both");' in group_card
    assert '{(effMode === "time" || effMode === "both") && (' in group_card
    assert '{effMode !== "time" && (' in group_card
    # 动态切换间隔段按有效模式显示（跟随系统且全局是 dynamic 时也要显示）
    assert '{effMode === "dynamic" && (' in group_card
    # 模式标签：跟随系统时显示全局的顶层运行模式（不是“跟随系统”），
    # 与顶层 Seg 同款映射（time→定时轮巡，其余→负载均衡；动态是负载均衡
    # 的子模式，顶栏显示负载均衡）。服务器卡片与域名表格的模式标签仍显示
    # 具体模式（动态模式/百分比/固定流量），见 groupModeLabel。
    assert '{effMode === "time" ? t("定时轮巡") : t("负载均衡")}' in group_card
    assert 'g.switch_mode == null ? t("跟随系统")' not in group_card
    # 切模式会重挂载卡片（key 含 switch_mode），新分支的本地状态重新初始化；
    # 设置展开状态在父级，不受重挂载影响（仍保持展开）
    assert '${g.switch_mode ?? "follow"}' in tsx
    # 实例/域名列表的模式标签：跟随系统时用全局模式
    assert '(settings?.switch_mode as string) || "load_balance"' in tsx


def test_parent_child_hierarchy_global():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 父栏标题比子栏大一号：父项字体统一 16px（内联 fontSize:16），子项字体
    # 统一 14px（全局 label 规则）；分组卡片的选择成员实例/域名选择 h3 是子项，
    # 内联 fontSize:14（其余 h3 仍是 .card h3 的 16px；TwoLineLabel 已删）
    assert '<label className="lbl" style={{ fontSize: 16 }}>{t("运行模式")}</label>' in tsx
    assert "label { display:block; font-size:14px;" in (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert ".card h3 { margin:0 0 8px; font-size:16px; }" in (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 父栏下的所有子栏后退两字符：分组卡片的选择成员实例、域名选择按键区也缩进 2em
    # （PR #8 后两区改成两列 member-grid 网格，class 变了，缩进 2em 的规则没变）
    assert '<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("选择成员实例")}</h3>\n      <div className="seg member-grid" style={{ paddingLeft: "2em" }}>' in tsx
    assert '<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("域名选择")}</h3>\n      <div className="seg member-grid" style={{ paddingLeft: "2em" }}>' in tsx
    # 两列布局本身：手机/平板（≤1024px）下 member-grid 是两列网格，
    # 桌面端保持原来的自适应横排不变
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    grid_rule = ".member-grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr));"
    assert grid_rule in css
    assert css.index("@media (max-width:1024px)") < css.index(grid_rule)


def test_theme_accents_are_light():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 5 种主题色全部改浅（600 -> 500）：html[data-accent] 规则里的 --accent 用 500 档
    for hexv in ("#10b981", "#3b82f6", "#f97316", "#8b5cf6", "#ef4444"):
        assert hexv in css.lower(), hexv
    # 其余旧 600 档色值已不在 CSS 里；#2563eb 只剩 :root 的 fallback（JS 运行前）
    # 与 .icon-btn.blue 的写死蓝，不再是主题 accent
    for old in ("#059669", "#ea580c", "#7c3aed", "#dc2626"):
        assert old not in css.lower(), old
    assert "--accent:#2563eb" in css.replace(" ", "")
    # 主题卡片渐变色块同步改浅
    for g in ("#a7f3d0,#10b981", "#bfdbfe,#3b82f6", "#fed7aa,#f97316", "#ddd6fe,#8b5cf6", "#fecaca,#ef4444"):
        assert g in tsx, g


def test_settings_cards_use_parent_child_layout():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 父栏标题（h2/h3/16px）贴左，子栏内容缩进 2 字符：登录与安全、通知渠道、
    # 备份与恢复、站点信息、货币结算、通知事件、DDNS 设置都一样（主题设置卡片已删）
    # 登录与安全：修改密码/Google/GitHub 表单包在缩进 div 里
    assert tsx.count('<div style={{ paddingLeft: "2em" }}><div className="form2">') >= 3
    # 通知渠道：字段区缩进；每日推送是 h3 子标题（14px）、其控件缩进
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>' in tsx
    assert '<h3 style={{ margin: 0, paddingLeft: "2em", fontSize: 14 }}>{t("每日推送")}</h3>' in tsx
    # 备份与恢复：按钮行缩进
    assert '<div className="row" style={{ paddingLeft: "2em" }}>' in tsx
    # 站点信息：内容包在缩进 div 里
    assert '<div className="card"><h2>{t("站点信息")}</h2>\n      <div style={{ paddingLeft: "2em" }}>' in tsx
    # 货币结算：内容缩进（主题设置卡片已删，风格改到右上角调色板下拉）
    assert '<div className="card"><h2>{t("货币结算")}</h2>\n            <div style={{ paddingLeft: "2em" }}>' in tsx
    assert '<div className="theme-grid" style={{ paddingLeft: "2em" }}>' not in tsx
    # 通知事件开关行、DDNS 设置表单网格缩进
    assert 'style={{ padding: "6px 0 6px 2em" }}' in tsx
    assert '<div className="form2" style={{ paddingLeft: "2em" }}>' in tsx


def test_nav_and_subtabs_selected_are_accent_border():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 顶部导航与设置子标签选中态回到主题色边框样式（主题色填充已改回），
    # 不用填充色、不写死白字；选中仍加粗。子标签与导航一样用 2px 主题色边框
    # （边框加 1px 则内边距各减 1px，框体高度与按钮高度一致），选中时突出
    m = re.search(r"nav button\.active\s*\{([^}]*)\}", css)
    assert m, "nav button.active missing"
    body = m.group(1).replace(" ", "")
    assert "border:2pxsolidvar(--accent)" in body
    assert "background:var(--accent)" not in body
    assert "font-weight:600" in body
    m2 = re.search(r"\.subtabs button\.active\s*\{([^}]*)\}", css)
    assert m2, ".subtabs button.active missing"
    body2 = m2.group(1).replace(" ", "")
    assert "border:2pxsolidvar(--accent)" in body2
    assert "padding:9px19px" in body2
    assert "background:var(--accent)" not in body2
    assert "font-weight:600" in body2
    # 手机端同样补偿内边距
    assert ".subtabs button.active { padding:9px 13px; }" in css
    # 旧的浏览器标签页样式（上圆角、底部开口、margin-bottom:-1px）已删
    assert "border-radius:10px 10px 0 0" not in css
    assert "margin-bottom:-1px" not in css


def test_backup_card_cloud_button_local_button_and_dialog():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 云端备份是按钮（不是滑动开关），放在本地备份前面；选中（已启用）时
    # 用 seg-on 样式（主题色边框 + 加粗），点一下切换 webdav_enabled；
    # 悬停说明是功能描述（选中开启云端备份功能）
    assert 'title={t("选中开启云端备份功能")}' in tsx
    assert 'className={wdEnabled ? "seg-on" : ""}' in tsx
    assert 'saveSettings({ webdav_enabled: wdEnabled ? "0" : "1" })' in tsx
    # 云端备份按钮在本地备份按钮前面
    assert tsx.index('t("云端备份")') < tsx.index('t("本地备份")')
    # 下载备份改名本地备份，点一下下载；悬停说明是点击下载到本地
    assert 'title={t("点击下载到本地")}' in tsx
    assert 'className="btn" onClick={download}' in tsx
    assert '本地备份: "Local backup"' in i18n
    # 卡片右上角有云端备份设置按钮（⚙），点一下弹出云端备份对话框
    assert 'title={t("云端备份设置")}' in tsx
    assert "showWdDialog" in tsx
    assert 'className="modal-overlay"' in tsx
    # 对话框里是 WebDAV 配置（地址/用户名/密码/备份目录）+ 测试/上传/从云端恢复 + 状态
    assert 't("云端备份设置")' in tsx
    assert 'id="wd-url"' in tsx and 'id="wd-user"' in tsx and 'id="wd-pass"' in tsx and 'id="wd-dir"' in tsx
    # 旧的 inline 开关与 inline 表单已取消：卡片里不再有启用云端备份的 Toggle
    assert 'label={t("启用云端备份")}' not in tsx
    # modal 样式存在（云端备份对话框与添加域名对话框共用）
    assert ".modal-overlay" in css and ".modal" in css


def test_card_settings_gear_same_size_as_action_icons():
    # 服务器卡片右上角的设置齿轮与下面 4 个操作图标（刷新/启动/重启/停机）一样大：
    # 都是 icon-btn（30×30 框）+ Ico 16×16 SVG，不再是 btn ghost + ⚙️ emoji；
    # 设置图标只在实例管理：实例管理卡片、实例管理列表行各一处，共 2 处
    # （运行概览卡片/列表不设设置按钮，见 test_overview_no_settings_button）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    import re
    gears = re.findall(r'<button className="([^"]*)" title=\{t\("卡片设置"\)\}.*?</button>', tsx)
    assert len(gears) == 2, f"expected 2 card-settings gear buttons, got {len(gears)}"
    assert all(c == "icon-btn" for c in gears), gears
    # 两处齿轮按钮里都是 Ico 齿轮 SVG（circle + path），不是 emoji
    for m in re.finditer(r'title=\{t\("卡片设置"\)\}.*?</button>', tsx):
        assert '<Ico label={t("卡片设置")}>' in m.group(0)
        assert '⚙️' not in m.group(0)
    # 实例管理卡片点齿轮打开卡片设置（单行形式）；实例管理列表行是多行形式
    # {cardSettingsId === a.id && (，另算；运行概览卡片不再有设置区
    assert tsx.count('{cardSettingsId === a.id && <CardSettings a={a} />}') == 1
    assert '{cardSettingsId === a.id && (' in tsx


def test_overview_no_settings_button():
    # 运行概览卡片和列表不设设置按钮：dash 区域里没有任何“卡片设置”齿轮
    # 和 CardSettings 渲染；实例管理卡片/列表的设置按钮保留（各一处，共 2 处）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    dash = tsx[tsx.index('{tab === "dash" && ('):tsx.index('{tab === "accounts" && (')]
    assert "卡片设置" not in dash
    assert "CardSettings" not in dash
    assert "setCardSettingsId" not in dash
    # 运行概览列表（overview-table）没有操作列/设置按钮
    assert "overview-table" in dash
    assert tsx.count('title={t("卡片设置")}') == 2


def test_card_settings_gear_top_right_no_wrap():
    # 服务器卡片顶行：名称再长，设置齿轮也贴在卡片右上角，不换到第二行单独占一行。
    # 根因：.row 默认 flex-wrap:wrap，手机上名称 + 状态 pill 占满一行后齿轮被挤到
    # 第二行左对齐。修法：顶行不换行（flexWrap nowrap）+ 顶对齐（flex-start），
    # 名称区可收缩（minWidth 0，pill 在名称区内换行），齿轮容器不收缩（flex none）。
    # 运行概览与实例管理卡片顶行各一处，共 2 处；齿轮容器只剩实例管理卡片一处
    # （运行概览卡片不设设置按钮）；分组卡片的设置齿轮不在此列。
    # 顶行同时是手机端固定头（server-card-head），名称固定在卡片顶部。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert tsx.count('<div className="row server-card-head" style={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "nowrap" }}>') == 2
    assert tsx.count('<div style={{ minWidth: 0 }}>{dotEl(a.status_dot') == 2
    assert tsx.count('<div className="row" style={{ gap: 6, flex: "none" }}>') == 1


def test_dynamic_mode_heading_not_bold():
    # 全局设置里“动态切换间隔”紧跟在切换阀值三选一（百分比/固定流量/动态模式）
    # 下面，只在选了动态模式时显示；底部的“动态模式”小节标题已取消。
    # “不适用定时轮巡。”提示已删除（用户 2026-11-24 要求删除该文字），
    # 全局与分组两处都不再出现。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '<h3 style={{ marginTop: 20, fontWeight: 400 }}>{t("动态模式")}</h3>' not in tsx
    assert "不适用定时轮巡" not in tsx
    # 全局三选一 Seg（value={sMode}）后就是动态间隔区（条件 sMode === "dynamic"），
    # 在“阈值动作”前面
    seg_idx = tsx.index('<Seg value={sMode} options={[')
    dyn_idx = tsx.index('{sMode === "dynamic" && (', seg_idx)
    act_idx = tsx.index('t("阈值动作")', seg_idx)
    assert dyn_idx < act_idx
    assert 't("动态切换间隔")' in tsx[seg_idx:act_idx]
    assert tsx.index('t("动态切换间隔")', seg_idx) < act_idx


def test_lang_button_same_size_as_icon_buttons_and_current_lang_accent():
    # 语言按钮与主题/退出图标按钮同大（36×36 圆角正方形，内容居中），
    # 按钮只显示当前语言（中 或 En），用主题色高亮（.lang-on）；
    # 必须是双类选择器 .btn.lang-btn/.btn.theme-btn，单类会被后写的 .btn 覆盖
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert ".btn.theme-btn { width:36px; height:36px; min-height:36px; padding:0; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; }" in css
    assert ".btn.lang-btn { width:36px; height:36px; min-height:36px; padding:0; font-size:14px; line-height:1; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; }" in css
    assert ".lang-on { color:var(--accent); }" in css
    assert ".lang-off" not in css and ".lang-sep" not in css
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert tsx.count('<span className="lang-on">{lang === "zh" ? "中" : "En"}</span>') == 2
    # 点按钮开下拉菜单（不是直接切换）：页眉与登录页各一处
    assert tsx.count("langMenuOpen") >= 2


def test_group_settings_follow_global_layout_indent():
    # 分组卡片设置区跟随全局设置（参数设置）的父子栏布局：分组名称/运行模式/
    # 阈值动作/阈值恢复都缩进 2 字符（与全局设置的阈值动作/阈值恢复一致），
    # 切换参数本来就是缩进的
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 分组名称
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>\n        <label className="req">{t("分组名称 *")}</label>' in tsx
    # 运行模式（顶层标签已由“切换模式”改名）
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>\n        <label className="lbl">{t("运行模式")}</label>' in tsx
    # 阈值动作 / 阈值恢复（与全局设置一样，整个 label+Seg 区块缩进 2em；两处都是子项 14px）
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>\n        <label className="lbl">{t("阈值动作")}</label>' in tsx
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>\n        <label className="lbl">{t("阈值恢复")}</label>' in tsx
    # 全局设置侧的阈值动作/阈值恢复同样缩进 2em（对照，同样是子项 14px）
    assert tsx.count('<div style={{ marginTop: 12, paddingLeft: "2em" }}>\n              <label className="lbl">{t("阈值动作")}</label>') == 1


def test_backup_cloud_and_local_buttons_same_height():
    # 备份与恢复卡片里云端备份（.seg 按钮）与本地备份（.btn）同一行等高：
    # .seg 自带 margin-top:6px 会把云端备份顶下去 6px，与旁边的 .btn 不对齐，
    # 所以这行的 .seg 要把 margin-top 归零
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '<div className="seg" style={{ marginTop: 0 }}>' in tsx
    assert 'className={wdEnabled ? "seg-on" : ""}' in tsx


def test_cloud_backup_dialog_draggable_and_alist_layout():
    # 云端备份对话框：标题可拖动（onMouseDown 拖拽 + transform 位移 + 松手防误关），
    # 字段按参考图重排：* 地址 / * 用户名 / * 密码 / * 备份目录，
    # 地址下有 Alist 官方文档快速跳转，云端备份按钮悬停说明是功能描述
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 可拖动
    assert "onWdDragStart" in tsx
    assert 'onMouseDown={onWdDragStart}' in tsx
    assert "translate(${wdPos.x}px, ${wdPos.y}px)" in tsx
    assert "wdDragEndAt.current" in tsx
    # 字段按参考图
    assert 't("* 地址")' in tsx
    assert 't("* 用户名")' in tsx
    assert 't("* 密码")' in tsx
    assert 't("* 备份目录")' in tsx
    assert 'id="wd-dir"' in tsx
    # 地址/备份目录输入框里的示例文字已删（用户 2026-11-23 19:37 要求）：
    # 框里不再显示 https://oplist.alicloud.ru/dav 与 /GoogleDrive/Backup/1panel
    assert 'placeholder="/GoogleDrive/Backup/1panel"' not in tsx
    assert 'placeholder="https://oplist.alicloud.ru/dav"' not in tsx
    assert 't("WebDAV 连接 Alist 可参考官方文档")' in tsx
    assert "https://alist.nn.ci/zh/guide/webdav.html" in tsx
    assert 't("快速跳转")' in tsx
    # 悬停说明
    assert 'title={t("选中开启云端备份功能")}' in tsx


def test_buttons_gray_globally():
    # 全站主按钮默认灰色：.btn 用灰边框 + 正常文字（不再是主题色边框 + 蓝字），
    # 本地备份/恢复备份/修改密码/注册指纹都用普通 .btn 即灰色
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert ".btn { background:transparent; color:var(--text); border:2px solid var(--border);" in css
    assert "btn gray" not in tsx and ".btn.gray" not in css
    assert 'className="btn" onClick={download}' in tsx
    assert 'className="btn" onClick={restore}' in tsx
    assert 'className="btn" onClick={changePw}' in tsx
    assert 'className="btn" onClick={registerFingerprint}' in tsx
    # 本地备份悬停说明是点击下载到本地
    assert 'title={t("点击下载到本地")}' in tsx


def test_button_font_not_bold_by_default_bold_on_select_or_hover():
    # 按钮全局规则：默认框体默认色、字不加粗；悬停或选中时字加粗、框体变主题色，
    # 字本身不换主题色，默认文字色
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    btn = re.search(r"\.btn\s*\{([^}]*)\}", css)
    assert btn is not None and "font-weight:400" in btn.group(1)
    assert "color:var(--text)" in btn.group(1)
    assert "var(--accent)" not in btn.group(1)
    # 悬停加粗 + 框体主题色，不换字色
    assert ".btn:hover { font-weight:600; border-color:var(--accent); }" in css
    # 选择器默认不加粗，选中/悬停加粗，不换字色，框体主题色
    seg = re.search(r"\.seg button\s*\{([^}]*)\}", css)
    assert seg is not None and "font-weight:400" in seg.group(1)
    assert ".seg button:hover { font-weight:600; border-color:var(--accent); }" in css
    seg_on = re.search(r"\.seg button\.seg-on\s*\{([^}]*)\}", css)
    assert seg_on is not None and "font-weight:600" in seg_on.group(1)
    assert "color:" not in seg_on.group(1)
    assert "border:2px solid var(--accent)" in seg_on.group(1)
    # 导航/子标签：默认不加粗，选中加粗不换色（框体主题色），悬停加粗 + 框体主题色
    assert "nav button:hover { font-weight:600; border-color:var(--accent); }" in css
    assert ".subtabs button:hover { color:var(--text); font-weight:600; border-color:var(--accent); }" in css
    # 主题风格卡片与 OAuth 登录按钮：悬停框体同样变主题色
    assert ".theme-card:hover { border-color:var(--accent); }" in css
    assert ".oauth-btn:hover { font-weight:600; border-color:var(--accent); }" in css


def test_server_cards_one_per_row_no_wrap():
    # 服务器卡片一行一个（运行概览与实例管理），卡片内文字不换行：
    # .grid2 已改成一列（类名未改，方便改回两列）；顶上三张总览卡片仍是 grid3 三列，不动
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert ".grid2 { display:grid; grid-template-columns:1fr; gap:16px; }" in css
    assert ".grid2 { display:grid; grid-template-columns:repeat(2,1fr)" not in css
    assert '<div className="grid2" style={{ marginTop: 12 }}>' in tsx  # 运行概览服务器卡片
    assert '<div className="grid2">\n          {accounts.map((a) => (' in tsx  # 实例管理卡片包一层 grid2
    assert '<div className="grid3">' in tsx  # 顶上总览卡片仍三列
    assert ".grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }" in css
    # 指标三列等宽 1fr 撑满卡片：卡片一行一个后每列约 370px，流量值
    # （179–195px）与实例 ID（207px）放得下；之前 auto + 两端对齐在宽卡片
    # 上把三列推到两端、中间留大片空白显空（用户 2026-11-24 截图反馈）。
    assert ".stats { display:grid; grid-template-columns:repeat(3,1fr); gap:10px 16px; margin-top:12px; }" in css
    # 实例管理卡片原来的五项一行（.stats.five）同样放不下，已改成三列
    assert ".stats.five" not in css and "stats five" not in tsx
    # 卡片内文字不换行：指标标签/数值、状态详情行、服务器名（手机端恢复换行）
    # 标签规则是 .stat > span（直接子元素），<b> 里的 IpView/IdView 内层 span 不被染灰
    assert ".stat > span { display:block; font-size:12px; color:var(--muted); white-space:nowrap; }" in css
    assert ".stat b { font-size:14px; font-weight:700; overflow-wrap:anywhere; white-space:nowrap; }" in css
    assert ".status-detail { white-space:nowrap; }" in css
    assert tsx.count('className="muted status-detail"') == 2
    assert tsx.count('<b style={{ fontSize: 16, whiteSpace: "nowrap" }}>{a.name as string}</b>') == 2


def test_list_values_are_bold():
    # 列表值加粗，与卡片指标值（.stat b，700）一致：运行概览列表与实例管理
    # 列表的值列（ID、区域、IP、站点、流量、费用、阈值）都是 700；表头 th
    # （13px/500 灰色）不动，拖动列（⠿）和操作列不是值，跨列展开行也不是
    # 数据列，都不加粗
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '<table className="overview-table"' in tsx
    assert ".overview-table td, .accounts-table td { font-weight:700; }" in css
    assert ".accounts-table td:first-child, .accounts-table td:last-child, table td[colspan] { font-weight:400; }" in css
    assert "th { color:var(--muted); font-weight:500; font-size:13px; }" in css


def test_header_shows_site_name():
    # 页眉 logo 后面固定显示站点名称，不再显示当前标签页名（运行概览/实例管理/
    # 站点设置……）；站点名称在站点设置→站点信息里改，当前标签仍由导航高亮表示
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '<h1 style={{ margin: 0 }}>{(settings?.site_name as string) || "CDT 面板"}</h1>' in tsx
    assert '{tab === "dash" ? t("运行概览")' not in tsx


def test_group_dynamic_interval_shows_when_dynamic():
    # 动态模式时切换间隔设置一直显示：原来整个负载均衡参数区只在“自定义”时
    # 出现，动态模式 + 跟随系统时切换参数区一片空白，看不到间隔设置。
    # 现在间隔块由有效模式 effMode === "dynamic" 直接控制，不在跟随系统开关里；
    # 跟随系统时显示全局值（_gdm 取分组值或全局值），改值后自动变成自定义
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i = tsx.index('t("动态切换间隔")')
    # 动态间隔块直接由有效模式 effMode === "dynamic" 控制（跟随系统且全局是
    # dynamic 时也显示）
    j = tsx.rindex('{effMode === "dynamic" && (', 0, i)
    assert 0 < i - j < 1500, "dynamic interval block must sit inside the dynamic-mode branch"
    # 前面是负载均衡四选一（四选一不再藏在跟随系统开关里），后面才是间隔块
    assert 'lbFollowGlobal ? "follow"' in tsx[:j]
    assert 'g.dynamic_interval_minutes ?? (settings as Any)?.dynamic_interval_minutes ?? 0' in tsx
    # 三选一里的动态选项与说明文案仍在
    assert '{ value: "dynamic", label: t("动态模式"), title: t("每到间隔切到剩余流量最多的服务器，不看流量差值") }' in tsx
    assert 't("每到间隔切到剩余流量最多的服务器，不看流量差值。")' in tsx
    # 动态间隔两选一：跟随系统 / 自定义；选自定义弹对话框改分钟数（最少 30），
    # 确认后保存，取消不改；30/60/90 预设已取消；选跟随系统即清空动态间隔参数（继承全局）
    assert '{ value: "follow", label: t("跟随系统"), title: t("动态切换间隔继承全局设置") }' in tsx
    assert 'g.dynamic_interval_minutes == null ? "follow"' in tsx
    assert 'savePatch({ dynamic_interval_minutes: null })' in tsx
    # 分组卡片里 30/60/90 预设已取消，自定义是对话框（modal-overlay），确认后才保存
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    assert '{ value: "30", label: t("30 分钟") }' not in group_card
    assert '{ value: "60", label: t("60 分钟") }' not in group_card
    assert '{ value: "90", label: t("90 分钟") }' not in group_card
    assert 't("自定义动态切换间隔")' in group_card
    assert 'showDynCustom' in group_card
    assert 'savePatch({ dynamic_interval_minutes: v })' in group_card
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert "动态切换间隔继承全局设置" in i18n
    assert "自定义动态切换间隔" in i18n
    assert "间隔（分钟，最少 30）" in i18n
    assert "确定" in i18n


def test_group_lb_four_way_options():
    # 分组里选负载均衡时，切换方式四选一一直显示：百分比（%）/ 固定流量（GB）/
    # 动态模式 / 跟随系统（原来三选一藏在“切换参数=自定义”后面，跟随系统时
    # 一片空白）。跟随系统时选中“跟随系统”；选它清空负载均衡参数（继承全局），
    # 选百分比/固定流量时从跟随系统切到自定义（填入全局差值），已是自定义时
    # 只改模式、保留已填差值
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '<Seg value={(lbFollowGlobal ? "follow" : g.switch_mode === "dynamic" ? "dynamic" : diffMode) as string}' in tsx
    assert '{ value: "percent", label: t("百分比（%）")' in tsx
    assert '{ value: "gb", label: t("固定流量（GB）")' in tsx
    assert '{ value: "follow", label: t("跟随系统"), title: t("负载均衡参数继承全局设置") }' in tsx
    assert 'savePatch({ switch_diff_mode: null, switch_diff_pct: null, switch_diff_gb: null, dynamic_interval_minutes: null })' in tsx
    assert 'if (lbFollowGlobal) { patch.switch_diff_pct = Number(settings.switch_diff_pct ?? 10) || 10; patch.switch_diff_gb = Number(settings.switch_diff_gb ?? 10) || 10; }' in tsx
    # 差值输入只在负载均衡不跟随系统、且不是动态模式时显示（不再看共用的
    # switchFollowGlobal：定时轮巡自定义时差值输入不应冒出来）
    assert '{!lbFollowGlobal && g.switch_mode !== "dynamic" && (' in tsx
    assert '{!switchFollowGlobal && g.switch_mode !== "dynamic" && (' not in tsx
    # “流量模式”旧标签已改成“固定流量（GB）”（与全局设置一致），英文表里旧键已删；
    # “百分比模式”旧标签已改成“百分比（%）”（与全局设置一致），英文表里旧键已删
    assert 't("流量模式")' not in tsx
    assert 't("百分比模式")' not in tsx
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert "流量模式" not in i18n
    assert "百分比模式" not in i18n
    assert "负载均衡参数继承全局设置" in i18n


def test_group_time_follow_custom():
    # 分组里选定时轮巡时，轮巡周期五选一一直显示：跟随系统 / 3/6/12 小时 /
    # 自定义（与全局设置的轮巡周期同一排版，只多一个跟随系统；原来是跟随系统/
    # 自定义两选一 + 下面藏着的间隔 Seg）。选跟随系统即清空三项时间参数
    # （继承全局），选其它即切到自定义；从跟随系统切出来时把全局的时间值填入
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 框体外的“定时轮巡”父级字已取消，只留单行缩进的“轮巡周期”标签
    assert '<label className="lbl" style={{ paddingLeft: "2em" }}>{t("轮巡周期")}</label>' in tsx
    assert '<Seg value={interval} options={[' in tsx
    assert '{ value: "follow", label: t("跟随系统"), title: t("定时轮巡参数继承全局设置") }' in tsx
    assert 'savePatch({ interval_hours: null, custom_interval_minutes: null, interval_start_time: null })' in tsx
    # 时间参数只在不跟随系统时显示（统一的“切换参数”开关已取消，跟随系统改由
    # 各段自己的 Seg 承载；原来共用的 switchFollowGlobal 已删）
    assert '{interval !== "follow" && (<>' in tsx
    assert '{!timeFollowGlobal && (<>}' not in tsx
    assert '{!switchFollowGlobal && (<>}' not in tsx
    assert 'switchFollowGlobal' not in tsx and 'setSwitchFollowGlobal' not in tsx
    # 从跟随系统切出来时把全局的时间值填入（间隔、单位、开始时间）
    assert 'const wasFollow = interval === "follow"' in tsx
    assert 'interval_start_time: effStart' in tsx
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert "定时轮巡参数继承全局设置" in i18n

def test_seg_custom_two_line_sub():
    # 自定义按钮双行显示：编辑完成后按钮第一行“自定义”，第二行同时显示数值
    # （如“5 分钟”）；Seg 选项支持 sub 第二行小字，只在该选项被选中且有值时传
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert "sub?: string" in tsx
    assert '<span className="seg-sub">{o.sub}</span>' in tsx
    assert ".seg-sub { display:block; font-size:12px; color:var(--muted); font-weight:400; }" in css
    # 五处自定义 Seg 都有 sub：全局轮巡周期/动态间隔/API 刷新，分组轮巡周期/动态间隔
    assert 'sub: sInterval === "0"' in tsx
    assert 'sub: sDynInterval === "0"' in tsx
    assert 'sub: sRefresh === "custom"' in tsx
    assert 'sub: interval === "0"' in tsx
    assert 'sub: dynInterval === "0"' in tsx
    # 数值带单位（轮巡周期按分钟/小时/天，动态间隔与 API 刷新按分钟）
    assert '`${sCustVal} ${sCustUnit === "day" ? t("天") : sCustUnit === "hour" ? t("小时") : t("分钟")}`' in tsx
    assert '`${sDynCustVal} ${t("分钟")}`' in tsx
    assert '`${sRefreshCustVal} ${t("分钟")}`' in tsx


def test_custom_values_inline_except_group_dynamic_dialog():
    # 自定义数值都在内联输入框里改，不用 prompt()；唯一例外是分组的动态切换
    # 间隔：自定义弹对话框改值（用户要求）。其它自定义入口（区域自定义输入、
    # 分组/全局自定义间隔、全局动态间隔、API 刷新）都是页面里的 input，失焦即保存
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "prompt(" not in tsx and "window.prompt" not in tsx
    assert 'placeholder={t("区域 ID，如 cn-hongkong")}' in tsx
    assert 't("自定义间隔")' in tsx and 't("自定义间隔（分钟，最少 30）")' in tsx
    assert 't("自定义刷新时间（分钟）")' in tsx
    # 分组动态间隔的自定义是对话框（modal-overlay），分组卡片里不再有内联的
    # 自定义间隔输入行；全局的动态间隔自定义仍是内联输入
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    assert 't("自定义动态切换间隔")' in group_card
    assert 't("自定义间隔（分钟，最少 30）")' not in group_card


def test_server_card_groups_modes_and_masked_instance_id():
    # 服务器卡片（实例管理页）：模式显示在服务器名后面（蓝色，多个分组的模式
    # 都显示，去重），分组名（Premium）不再显示在卡片上；区域与站点是单独的
    # 指标（标签在上、数值在下），顺序是实例、区域、IP、站点、流量、费用、
    # 阈值；实例 ID 只显示前 6 位（ECS 是 i- + 6 位），后面用 *** 代替，
    # 并有复制按钮（复制完整 ID，和 IP 复制一样）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 多分组的模式：用 accountGroupIds（group_ids 多对多，回退单 group_id），
    # accountModeLabels 去重后显示在状态标签（在线）后面，蓝色 pill 标签
    # （动态模式后面可能还有其它模式，都排在状态标签后面）
    assert 'const accountGroupIds = (a: Any): number[] => {' in tsx
    assert 'const accountModeLabels = (a: Any): string[] => {' in tsx
    assert 'if (!labels.includes(l)) labels.push(l);' in tsx
    assert 'accountModeLabels(a).map((m) => (' in tsx
    assert '<span key={m} className="pill blue" style={{ marginLeft: 4 }}>{m}</span>' in tsx
    # 状态标签（在线）排在模式标签前面：卡片名称行里 statusPillClass
    # 先出现，accountModeLabels 的 map 在后面
    name_line = [l for l in tsx.splitlines() if 'accountModeLabels(a).map((m) => (' in l][0]
    assert name_line.index('statusPillClass(a.status_dot)') < name_line.index('accountModeLabels(a).map((m) => (')
    assert '（{m}）' not in tsx
    # 名称下面不再有分组行（分组名不显示在卡片上）
    assert ' · {regionAbbr(a.region)} · {groupName(a.group_id)}' not in tsx
    assert '<span style={{ color: "var(--accent)" }}>{groupName(gid)}</span>' not in tsx
    assert '<span style={{ color: "#34A853" }}>（{groupModeLabel(gid)}）</span>' not in tsx
    # 模式标签：time→定时轮巡，dynamic→动态模式，load_balance/both→差值模式
    assert 'if (m === "time") return t("定时轮巡");' in tsx
    assert 'if (m === "dynamic") return t("动态模式");' in tsx
    assert 'return dm === "gb" ? t("固定流量") : t("百分比");' in tsx
    # 实例 ID 脱敏 + 复制按钮（复制完整 ID）。ID 的脱敏与复制都包在
    # IdView 里（卡片与列表共用，和 IpView 同款）
    assert 'return id.startsWith("i-") ? `${id.slice(0, 8)}***` : `${id.slice(0, 6)}***`;' in tsx
    assert 'const IdView = ({ a }: { a: Any }) => {' in tsx
    assert '<span>{maskInstanceId(id)}</span>' in tsx
    assert 'title={t("复制实例 ID")} onClick={() => void copyInstanceId(id)}>⧉</button>' in tsx
    assert 'const copyInstanceId = async (id: string) => { await copyText(id, t("实例 ID 已复制")); };' in tsx
    # 区域与站点是单独的指标（标签在上、数值在下，和运行概览卡片同款），
    # 不再是 ID / IP 后面的小字后缀；卡片指标顺序与列表一致：
    # 实例、区域、IP、站点、流量、费用、阈值
    assert '<b><IdView a={a} /></b>' in tsx
    assert '· {regionAbbr(a.region)}</span></b>' not in tsx
    assert '· {a.site === "cn" ? t("中国站 cn") : t("国际站 global")}</span></b>' not in tsx
    i = tsx.index('<b><IdView a={a} /></b>')
    i_stats = tsx.rfind('<div className="stats">', 0, i)
    stats = tsx[i_stats:i_stats + 1400]
    assert '<div className="stat"><span>{t("区域")}</span><b>{regionAbbr(a.region)}</b></div>' in stats
    assert '<div className="stat stat-detail"><span>{t("站点")}</span><b>{a.site === "cn" ? t("中国站") : t("国际站")}</b></div>' in stats
    assert stats.index('<span>{t("实例")}</span>') < stats.index('<span>{t("区域")}</span>')
    assert stats.index('<span>{t("区域")}</span>') < stats.index('<span>IP</span>')
    assert stats.index('<span>IP</span>') < stats.index('<span>{t("站点")}</span>')
    assert stats.index('<span>{t("站点")}</span>') < stats.index('<span>{t("流量")}</span>')
    assert stats.index('<span>{t("流量")}</span>') < stats.index('<span>{t("费用")}</span>')
    assert stats.index('<span>{t("费用")}</span>') < stats.index('<span>{t("阈值")}</span>')
    # 复制配置摘要仍给完整 ID（与 IP 复制给完整 IP 一致），编辑表单也不脱敏
    assert '${t("实例 ID")}：${a.instance_id}' in tsx
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert '百分比: "Percent"' in i18n
    assert '固定流量: "Fixed traffic"' in i18n
    assert '"实例 ID 已复制": "Instance ID copied"' in i18n
    assert '"复制实例 ID": "Copy instance ID"' in i18n
    assert '名称: "Name"' in i18n


def test_accounts_list_column_order():
    # 实例管理页列表：列顺序与卡片一致——名称、ID、区域、IP、站点、流量、
    # 费用、阈值（操作在最后）。ID 列显示脱敏 ID + 复制按钮（IdView），
    # 区域与站点是单独的列（卡片里它们是 ID / IP 后面的小字后缀）。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i = tsx.index('<th></th><th>{t("名称")}</th><th>ID</th>')
    header = tsx[i:i + 400]
    assert header.index('<th>ID</th>') < header.index('<th>{t("区域")}</th>')
    assert header.index('<th>{t("区域")}</th>') < header.index('<th>IP</th>')
    assert header.index('<th>IP</th>') < header.index('<th>{t("站点")}</th>')
    assert header.index('<th>{t("站点")}</th>') < header.index('<th>{t("流量")}</th>')
    assert header.index('<th>{t("流量")}</th>') < header.index('<th>{t("费用")}</th>')
    assert header.index('<th>{t("费用")}</th>') < header.index('<th>{t("阈值")}</th>')
    assert header.index('<th>{t("阈值")}</th>') < header.index('<th>{t("操作")}</th>')
    # 行里的单元格顺序与表头一致：名称、IdView、区域、IpView、站点
    j = tsx.index('<td><IdView a={a} /></td>', i)
    assert tsx.index('<td>{regionAbbr(a.region)}</td>', j) < tsx.index('<td><IpView a={a} /></td>', j)
    assert tsx.index('<td><IpView a={a} /></td>', j) < tsx.index('<td>{a.site === "cn" ? t("中国站") : t("国际站")}</td>', j)


def test_dash_list_column_order():
    # 运行概览列表：列顺序也是服务器、区域、IP、站点、流量、费用（站点移到
    # IP 后面，与实例管理页列表一致；没有 ID 与阈值列，也没有复制按钮）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i = tsx.index('<th>{t("服务器")}</th><th>{t("区域")}</th><th>IP</th><th>{t("站点")}</th>')
    header = tsx[i:i + 400]
    assert header.index('<th>{t("区域")}</th>') < header.index('<th>IP</th>')
    assert header.index('<th>IP</th>') < header.index('<th>{t("站点")}</th>')
    assert header.index('<th>{t("站点")}</th>') < header.index('<th>{t("已用 / 月度流量额度")}</th>')
    assert header.index('<th>{t("已用 / 月度流量额度")}</th>') < header.index('<th>{t("实时费用")}</th>')
    # 行里的单元格顺序与表头一致：名称、区域、IpView（无复制）、站点
    j = tsx.index('<td><IpView a={a} copy={false} /></td>', i)
    assert tsx.index('<td>{regionAbbr(a.region)}</td>', i) < j
    assert j < tsx.index('<td>{a.site === "cn" ? t("中国站") : t("国际站")}</td>', j)


def test_global_settings_run_mode_above_lb():
    # 全局设置（参数设置卡片）：负载均衡上面新增运行模式按键（定时轮巡 /
    # 负载均衡两项，动态模式仍在下面的负载均衡切换方式里选）；顶层只分
    # 两项，动态/both 都归负载均衡（与分组卡片顶层同款映射）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 运行模式在月度流量额度/流量保护阈值之后、负载均衡切换模式标签之前
    # （“负载均衡”父项字已取消，TwoLineLabel 已删，只留单行缩进的切换模式标签）
    i_run = tsx.index('<label className="lbl" style={{ fontSize: 16 }}>{t("运行模式")}</label>', tsx.index('id="s-prot"'))
    i_lb = tsx.index('<label className="req"><span style={{ paddingLeft: "2em" }}>{t("切换模式")}</span></label>', i_run)
    assert i_run < i_lb
    run_seg = tsx[i_run:i_lb]
    assert '<Seg value={sRunMode} options={' in run_seg
    assert '{ value: "time", label: t("定时轮巡") }' in run_seg
    assert '{ value: "load_balance", label: t("负载均衡") }' in run_seg
    assert "动态模式" not in run_seg
    assert 'saveParamSettings({ switch_mode: v })' in run_seg
    # 运行模式 Seg 缩进 2 字符（2026-11-24 用户截图红框要求：标签仍是父项
    # 贴左，只有 Seg 整体缩进 2em，与下面的切换模式 Seg 对齐）
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sRunMode} options={' in run_seg
    # 值映射：time→定时轮巡，其余→负载均衡；改完即存 switch_mode
    assert 'setSRunMode(settings.switch_mode === "time" ? "time" : "load_balance");' in tsx
    # 负载均衡切换方式里仍有动态模式（未动）
    assert '{ value: "dynamic", label: t("动态模式")' in tsx
    # 顶部两列（月度流量额度/流量保护阈值）是子项，整体缩进 2 字符，
    # 标签与输入框都对齐在子项位置（2026-11-24 用户截图要求）
    assert '<div className="form2" style={{ paddingLeft: "2em" }}>' in tsx


def test_stat_values_uniform_weight():
    # 卡片与列表的数值统一粗细：等宽字体是错的改法（等宽字体笔画细，与 Google Sans
    # 的流量/费用放一起还是有的粗有的细，已撤掉）；真正粗细不一的根因是列表流量
    # 已用数包了 <b>（浏览器默认 700 粗体），而区域/IP/ID/费用/阈值都是常规粗细。
    # 改法：列表流量已用数去掉 <b>，卡片与列表全部数值都用同一字体同一粗细。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 等宽字体已撤掉：.mono 类与用法都不存在
    assert ".mono" not in css
    assert 'className="mono"' not in tsx
    assert '<span className="mono">{maskIp(a.public_ip)}</span>' not in tsx
    assert '<span className="mono">{maskInstanceId(id)}</span>' not in tsx
    # 列表流量已用数：两张列表（运行概览与实例管理）都按用户新要求（2026-11-24）
    # 把“/”前的已用量加粗并整格不换行，覆盖了早期的统一粗细规则；
    # plain 格式已不存在，两处都是 nowrap + <b> 格式
    assert tsx.count('<td>{fmtGb(a.traffic_used_gb)} / {a.quota_gb ?? 200} GiB{trafficPct(a)}</td>') == 0
    assert tsx.count('<td style={{ whiteSpace: "nowrap" }}><b>{fmtGb(a.traffic_used_gb)}</b> / {a.quota_gb ?? 200} GiB{trafficPct(a)}</td>') == 2
    # 卡片数值仍在 .stat b 里，比标签大一号（值 14px、标签 12px）、只加粗（700），流量/费用/阈值与区域/IP/实例 ID 同款
    assert '.stat b { font-size:14px; font-weight:700;' in css
    assert tsx.count('<div className="stat"><span>{t("流量")}</span><b>{fmtGb(a.traffic_used_gb)} / {a.quota_gb ?? 200} GiB{trafficPct(a)}</b></div>') == 2


def test_status_pill_follows_dot_and_mode_label_no_parens():
    # 状态标签文字颜色跟随状态点颜色（在线绿 / 排队黄 / 停机红 / 灰），
    # 亮暗主题一致；服务器卡片名称后面的模式标签不要括号。
    # 三处状态标签（运行概览卡片、实例管理卡片、实例管理列表）都用同一映射。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 状态标签 class 按 status_dot 选，与 dotColor 同款映射
    assert 'dot === "yellow" ? "pill st-yellow" : dot === "gray" ? "pill st-gray" : dot === "red" || dot === "red_pulse" ? "pill st-red" : "pill st-green"' in tsx
    assert tsx.count('<span className={statusPillClass(a.status_dot)}>') == 3
    assert '<span className="pill">{a.status_label' not in tsx
    # st-* 的文字颜色就是状态点颜色
    assert ".pill.st-green { color:#34A853;" in css
    assert ".pill.st-yellow { color:#FBBC04;" in css
    assert ".pill.st-red { color:#EA4335;" in css
    assert ".pill.st-gray { color:#9AA0A6;" in css
    # 模式标签不要括号：蓝色 pill 标签，排在状态标签（在线）后面
    assert '<span key={m} className="pill blue" style={{ marginLeft: 4 }}>{m}</span>' in tsx
    assert '（{m}）' not in tsx


def test_group_name_and_section_labels_font_size_swapped():
    # 分组卡片：组名称是唯一的父项（16px）；“选择成员实例”“域名选择”两处
    # h3 是子项（内联 fontSize:14），不再是与组名称同大的 16px；
    # 两处 h3 对应父项（组名称）缩进 2 字符；子项标签走全局 label 规则 14px。
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (root / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 组名称是 16px（父项，统一）
    assert '<b style={{ fontSize: 16 }}>{g.name as string}</b>' in tsx
    # 两处标题仍是 h3，字号 16px（.card h3），与组名称同大（父项统一）
    assert '<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("选择成员实例")}</h3>' in tsx
    assert '<h3 style={{ marginTop: 12, paddingLeft: "2em", fontSize: 14 }}>{t("域名选择")}</h3>' in tsx
    assert ".card h3 { margin:0 0 8px; font-size:16px; }" in css

def test_login_logo_left_admin_account_remember():
    # 登录页：logo 在标题左边同一行（不再居中独占一行）、管理员账号输入框
    # 在密码框上面、密码框下方登录按钮上方有“记住账号密码”勾选（默认勾选）。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    api = (ROOT / "web" / "src" / "api.ts").read_text(encoding="utf-8")
    # logo 与标题同一行：logo 高 36，标题在同一 flex 行里
    assert '<div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>' in tsx
    assert '<img src="/api/settings/logo" alt="logo" style={{ height: 36, maxWidth: 120, objectFit: "contain", flex: "none" }} />' in tsx
    assert '<div className="auth-title" style={{ margin: 0 }}>{(info.site_name as string) || "CDT 面板"}</div>' in tsx
    assert '{t("登录")} {(info.site_name' not in tsx  # 标题只留站点名，“登录”二字已删
    assert 'textAlign: "center", marginBottom: 12' not in tsx  # 旧的居中独占一行已删
    # 账号框在密码框上面：账号标签/输入在“密码”之前，带 username 自动填充；
    # 输入框内有左侧图标（用户名人像、密码锁），登录按钮与第三方登录之间
    # 有“或使用以下方式登录”分隔线
    login = tsx.index("function LoginScreen")
    assert tsx.index('{t("用户名 *")}', login) < tsx.index('{t("密码 *")}', login)
    assert 'placeholder={t("请输入用户名")}' in tsx
    assert 'placeholder={t("请输入密码")}' in tsx
    assert 'autoComplete="username"' in tsx
    assert '<div className="input-ico">' in tsx
    assert '<div className="auth-divider">{t("或使用以下方式登录")}</div>' in tsx
    assert tsx.index('<div className="auth-divider">', login) > tsx.index('title={t("登录")}>{t("登录")}</button>', login)
    assert 'autoComplete="current-password"' in tsx
    # 登录请求同时发账号和密码
    assert 'post("/api/auth/login", { username: account, password })' in tsx
    # 记住勾选：默认勾选，在密码框下方、登录按钮上方
    assert "const [remember, setRemember] = useState(true);" in tsx
    assert '{t("记住账号密码")}' in tsx
    assert tsx.index('{t("记住账号密码")}') < tsx.index('title={t("登录")}>{t("登录")}</button>')
    # token 存放位置：勾选存 localStorage（跨浏览器会话），不勾选存
    # sessionStorage（关闭浏览器失效）；读 token 时 sessionStorage 优先
    assert "sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY)" in api
    assert "localStorage.setItem(TOKEN_KEY, token);" in api
    assert "sessionStorage.setItem(TOKEN_KEY, token);" in api
    assert "setToken(r.token as string, true);" in tsx
    assert "setToken(r.token as string, false);" in tsx
    # 退出登录清两种存储；只记账号不记密码：密码不进任何本地存储
    assert "localStorage.removeItem(TOKEN_KEY);\n      sessionStorage.removeItem(TOKEN_KEY);" in api
    assert "password" not in api.lower()
    assert "setRememberedUsername(account)" in tsx
    assert "setRememberedUsername(null)" in tsx


def test_list_value_columns_not_bold_and_overview_padding():
    # 列表值加粗（.overview-table/.accounts-table td 700）之后，用户截图指出
    # 区域/站点/流量/阈值这几列的值不该加粗（名称、ID、IP、费用保持加粗）；
    # 例外：两张列表的流量列“/”前的已用量按用户新要求（2026-11-24）单独加粗，
    # 整格不换行——td 本身仍是 400（nth-child 规则不变），只 <b> 里的已用量是 700。
    # 运行概览列表同时行距放宽（默认 10px 8px 太挤）。
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 基础加粗规则仍在：两张列表的 td 默认 700
    assert ".overview-table td, .accounts-table td { font-weight:700; }" in css
    # 区域/站点/流量/阈值列不加粗（nth-child 按表头列序）：
    # 运行概览：区域(2)/站点(4)/流量(5)；实例管理：区域(4)/站点(6)/流量(7)/阈值(9)
    assert ".overview-table td:nth-child(2), .overview-table td:nth-child(4), .overview-table td:nth-child(5)," in css
    assert ".accounts-table td:nth-child(4), .accounts-table td:nth-child(6), .accounts-table td:nth-child(7), .accounts-table td:nth-child(9) { font-weight:400; }" in css
    # 运行概览列表行距放宽：14px 12px（默认 th,td 是 10px 8px）
    assert ".overview-table th, .overview-table td { padding:14px 12px; }" in css

def test_accounts_traffic_no_wrap_and_used_bold():
    # 两张列表的流量列：值不换行显示（27.79 / 200 GiB (13.90%) 一行放下），
    # “/”前面的已用量加粗（<b>），后面“/ 额度 GiB (百分比)”不加粗。
    # 先只改了实例管理列表，用户随后要求运行概览列表也同样改动，两处同款。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    cell = '<td style={{ whiteSpace: "nowrap" }}><b>{fmtGb(a.traffic_used_gb)}</b> / {a.quota_gb ?? 200} GiB{trafficPct(a)}</td>'
    accounts = tsx.split('<table className="accounts-table">')[1]
    assert cell in accounts
    dash = tsx.split('<table className="overview-table"')[1].split("</table>")[0]
    assert cell in dash
    assert tsx.count(cell) == 2



def test_server_card_stats_three_equal_columns():
    # 服务器卡片统计区太空：之前 auto + 两端对齐（space-between）在宽卡片
    # （一行一个）上把三列推到两端、中间留出大片空白；改成三列等宽 1fr 撑满
    # 卡片宽度——每列约 370px，流量值（179–195px）与实例 ID（207px）放得下。
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert ".stats { display:grid; grid-template-columns:repeat(3,1fr); gap:10px 16px; margin-top:12px; }" in css
    # .stats 不再用 space-between（手机端单列 .stats 1fr 不受影响，见媒体查询）
    import re as _re
    m = _re.search(r"\.stats \{[^}]*\}", css)
    assert m is not None and "space-between" not in m.group(0)


def test_group_card_labels_match_group_name_font_size():
    # 分组卡片：顶上的组名称是父项，统一 16px；下面设置区的标签是子项，
    # 走全局 label 规则统一 14px——父项比子项大一号，且父项/子项各自统一大小。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 组名称是 16px（父项），不再有 .group-card 的 14px 覆盖
    assert '<b style={{ fontSize: 16 }}>{g.name as string}</b>' in tsx
    assert ".group-card label" not in css
    assert 'className="card group-card"' not in tsx
    # 全局 label 规则是 14px（子项，统一）
    assert "label { display:block; font-size:14px;" in css


def test_group_card_ddns_switch_top_right_delete_icon_in_settings():
    # 分组卡片：DDNS 开关在卡片右上角（标题行，⚙ 左边）；
    # 删除分组是设置区最下面的红色垃圾桶图标，只在点开 ⚙ 设置时显示，
    # 卡片上不显示删除按钮（2026-11-24 用户要求移回设置里面）。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # DDNS 开关在标题行：组名 <b> 之后、⚙ 之前；悬停提示显示开关状态
    assert '<span title={ddnsOn ? t("DDNS 已开启") : t("DDNS 已关闭")}' in tsx
    assert tsx.count('<Toggle checked={ddnsOn} onChange={(v) => void toggleDdns(v)}') == 1
    # 标题行里 Toggle 在 ⚙ 前面（同一行内顺序）
    hdr = tsx[tsx.index('<b style={{ fontSize: 16 }}>{g.name as string}</b>'):]
    assert hdr.index("<Toggle checked={ddnsOn}") < hdr.index('title={t("分组设置")}')
    # 删除按钮是红色垃圾桶图标按钮（icon-btn danger + Feather trash），带确认
    assert '<button className="icon-btn danger" title={t("删除分组")}' in tsx
    assert '<Ico label={t("删除分组")}><polyline points="3 6 5 6 21 6"' in tsx
    # 删除按钮在设置区里（showSettings 块内、选择成员实例之前），
    # 不在卡片底部：域名选择之后不再有删除按钮
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    del_idx = group_card.index('<button className="icon-btn danger" title={t("删除分组")}')
    assert del_idx > group_card.index("{showSettings && (")
    assert del_idx < group_card.index('{t("选择成员实例")}')
    after_dom = group_card.index('{t("域名选择")}')
    assert '<button className="icon-btn danger" title={t("删除分组")}' not in group_card[after_dom:]
    # 设置区里不再有红色实心“删除分组”文字按钮（删除实例仍是红色实心，不动）
    assert 'style={{ background: "#EA4335", borderColor: "#EA4335", color: "#fff" }}' not in group_card
    assert '{t("删除分组")}\n        </button>' not in group_card


def test_tables_fill_row_width_via_table_wrap():
    # 列表占满整行宽：表格本身是 display:table + width:100%，列宽分满整行；
    # 横向滚动由外层的 .table-wrap 提供。display:block 会让表格列宽只按
    # 内容算、右边留空（2026-11-24 用户截图：运行概览列表右侧留白）。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert tsx.count('<div className="table-wrap">') == 3
    assert ".table-wrap { overflow-x:auto; }" in css
    assert "table { display:block;" not in css
    # 三张表都在 .table-wrap 里（概览 / 实例管理 / 域名）
    for cls in ("overview-table", "accounts-table", "domains-table"):
        assert f'<div className="table-wrap"><table className="{cls}"' in tsx


def test_domains_table_loose_padding():
    # 域名管理表格太紧凑：行距放宽到与运行概览列表同款（14px 12px）。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    assert '<table className="domains-table">' in tsx
    assert ".domains-table th, .domains-table td { padding:14px 12px; }" in css


def test_notify_event_toggle_update_available():
    # 通知事件里有“面板更新”开关（有更新时推 TG/Email，每个版本只推一次）；
    # 后端事件键与默认开关都已接好
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert '"update_available"] as const' in tsx
    assert 'update_available: t("面板更新")' in tsx
    assert 'settings[`notify_${k}`]' in tsx
    settings = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
    assert '"notify_update_available": "1"' in settings
    assert '"update_available": "notify_update_available"' in settings
    sched = (ROOT / "app" / "scheduler.py").read_text(encoding="utf-8")
    assert "def _maybe_notify_update(settings: dict) -> bool:" in sched
    assert 'db.get_flag("update_notified_version")' in sched
    assert 'notifier.send("update_available", title, body)' in sched


def test_header_bell_and_palette_icons():
    # 页眉图标顺序（左→右）：铃铛（有消息才显示）→ 语言 → 主题模式 →
    # 调色板（风格）→ 退出登录。铃铛在中英切换前面，无消息不显示；
    # 调色板在退出按钮前面。
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 铃铛：只有 update_available 时渲染，带红点角标
    assert 'function BellIcon' in tsx
    assert '{upd?.update_available && (' in tsx
    assert '<BellIcon size={18} />' in tsx
    assert '<span className="bell-dot" />' in tsx
    assert ".bell-dot { position:absolute; top:5px; right:5px; width:8px; height:8px; border-radius:50%; background:#EA4335;" in css
    # 铃铛在语言键前面（页眉右行里，铃铛条件块先于 lang-btn）
    hdr = tsx[tsx.index("function App("):]
    assert hdr.index("upd?.update_available") < hdr.index("lang-btn")
    # 铃铛下拉：当前/最新版本（短 SHA）、最新提交信息、立即更新、检查更新
    assert 'aria-label={t("面板更新")}' in tsx
    assert 't("发现新版本")' in tsx and 't("当前版本")' in tsx and 't("最新版本")' in tsx
    assert 't("立即更新")' in tsx and 't("检查更新")' in tsx
    assert 'post("/api/update/run", {})' in tsx
    assert 'post("/api/update/check", {})' in tsx
    assert 'get("/api/update/status")' in tsx
    # 调色板：退出按钮前面，页眉与登录页各一处
    assert tsx.count('<PaletteIcon size={18} />') == 2
    assert tsx.count('aria-label={t("主题风格")}') == 2
    assert hdr.index('t("主题风格")') < hdr.index('t("退出登录")')
    # 登录页没有铃铛（未登录查不到更新状态）
    login = tsx[tsx.index("function LoginScreen"):tsx.index("function App(")]
    assert "BellIcon" not in login and "update/status" not in login
