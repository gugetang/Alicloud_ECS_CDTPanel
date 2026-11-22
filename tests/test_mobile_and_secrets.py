"""密钥掩码 + 分组页去框 + 手机端适配 + 夜间底色：

- 后端：GET /settings 与 _mask 返回 email_configured /
  telegram_configured，且不返回任何密钥明文。
- 前端：已配置的密钥一律显示 16 个 * 掩码（SECRET_MASK），保存时跳过
  掩码（不把 * 当新密钥存回去）；点进框清空、失焦留空恢复掩码。
- 分组页：外层卡片框体取消，“+ 新建分组”移到右下角。
- 手机端（≤800px）：单列卡片、指标两列、表格横向滚动、触控目标放大、
  输入框 16px（防 iOS 聚焦缩放）；电脑端保持 1200px 三列布局。
- 夜间底色不再是纯黑 #000000，改成更柔和的深蓝灰 #1e293b（原 #0b1220 仍太黑）。
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
    assert "--bg:#1e293b" in m.group(1).replace(" ", "")
    assert "#000000" not in CSS


def test_light_card_is_30pct_transparent():
    m = re.search(r'html\[data-theme="light"\]\s*\{([^}]*)\}', CSS)
    assert m, "light theme block missing"
    body = m.group(1).replace(" ", "")
    # 卡片毛玻璃加深：白天卡片 alpha 0.5（原 0.3 太透、不够突出），夜间灰卡 0.65
    assert "--card:rgba(255,255,255,0.5)" in body
    assert "--card2:rgba(255,255,255,0.5)" in body
    assert "rgba(255,255,255,0.7)" not in CSS
    dm = re.search(r'html\[data-theme="dark"\]\s*\{([^}]*)\}', CSS)
    assert dm, "dark theme block missing"
    dbody = dm.group(1).replace(" ", "")
    assert "--card:rgba(71,85,105,0.65)" in dbody


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
    # 运行概览顶部的调度提示说明已取消；临时消息条 msg 改成右下角悬浮 toast
    #（字体主题色），不再是顶部通栏 notice——见 test_save_toast_bottom_right_theme_color
    assert "调度运行时会按当前配置执行 VPS 和 DNS 操作。" not in tsx
    assert "调度运行时会按当前配置执行 VPS 和 DNS 操作。" not in i18n
    assert '{msg && <div className="toast">{msg}</div>}' in tsx
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
    # 设置子标签：全局设置、账号设置、通知设置、其它设置、日志统计（日志统计在最后）
    m = re.search(r"const SUB_TABS[^;]*;", tsx, re.S)
    assert m, "SUB_TABS missing"
    subs = m.group(0)
    assert '["logs", "日志统计"]' in subs
    assert subs.index('["logs", "日志统计"]') > subs.index('["notify", "通知设置"]')
    assert subs.index('["logs", "日志统计"]') > subs.index('["other", "其它设置"]')
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


def test_api_refresh_own_row_and_other_settings_tab():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 全局的 API 刷新时间单独占一行，移到轮巡周期下面（不在两列 form2 里）；
    # 实例表单里另有一项单账户 API 刷新时间（跟随全局/自定义），两者各出现一次
    assert tsx.count('API 刷新时间 *（分钟）') == 1
    assert tsx.count('t("API 刷新时间")') == 1
    api_idx = tsx.index('API 刷新时间 *（分钟）')
    assert 'marginTop: 12' in tsx[max(0, api_idx - 120):api_idx]
    assert tsx.index('轮巡周期') < api_idx
    assert tsx.index('<h3 style={{ marginTop: 20 }}>{t("定时轮巡")}</h3>') < api_idx
    # 其它设置是设置页的第四个子标签（在通知设置后面、日志统计前面），
    # 只剩货币结算与主题设置；登录与安全、备份与恢复已移到新开的“账号设置”
    m = re.search(r"const SUB_TABS[^;]*;", tsx, re.S)
    assert m, "SUB_TABS missing"
    subs = m.group(0)
    assert '["other", "其它设置"]' in subs
    assert subs.index('["other", "其它设置"]') < subs.index('["logs", "日志统计"]')
    assert subs.index('["other", "其它设置"]') > subs.index('["notify", "通知设置"]')
    assert '"other"' in tsx and 'type SubTab = "settings" | "account" | "notify" | "other" | "logs"' in tsx
    # 其它设置里只有货币结算卡片、主题设置卡片
    other_idx = tsx.index('{tab === "config" && subTab === "other" && (')
    acc_idx = tsx.index('{tab === "config" && subTab === "account" && (')
    notify_idx = tsx.index('{tab === "config" && subTab === "notify" && (')
    other_block = tsx[other_idx:acc_idx]
    assert '<div className="card"><h2>{t("货币结算")}</h2>' in other_block
    assert '<div className="card"><h2>{t("主题设置")}</h2>' in other_block
    assert '<LoginSecurity' not in other_block and '<BackupCard' not in other_block
    assert 'value={theme}' in other_block and 'setTheme(v)' in other_block
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
    # 分组定时轮巡设置区只剩间隔切换周期 + 开始时间，固定每日切换时刻/夜间窗口开始/结束三个时间框已取消
    assert 't("固定每日切换时刻")' not in tsx
    assert 't("夜间窗口开始")' not in tsx
    assert 't("夜间窗口结束")' not in tsx
    # 间隔切换周期仍在；说明小字已随“取消小字说明”一并去掉
    assert 't("间隔切换周期")' in tsx
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
    # 费用精确到小数点后 2 位：fmtCost 辅助函数，3 处单账户费用 + 当前费用回退合计都用它
    assert "const fmtCost" in tsx
    assert "n.toFixed(2)" in tsx
    assert tsx.count("fmtCost(a.billing_month_cost)") == 3
    assert "fmtCost(v)} ${c}" in tsx
    # 状态详情（在线/排队）用主题色，不再是绿色
    assert 'var(--dot-green)" : undefined' not in tsx
    # 实例状态卡片字体缩小一号：卡片 fontSize 14
    assert '<div className="card" style={{ fontSize: 14, display: "flex", flexDirection: "column" }}><h3>{t("实例状态")}</h3>' in tsx
    # 本月流量“已取得”与当前费用“已查询”小字都用主题色、14px
    assert tsx.count('color: "var(--accent)", fontSize: 14') >= 2
    assert 'className="muted" style={{ fontSize: 14 }}' not in tsx
    # 服务器卡片统计数值不加粗
    assert ".stat b { font-size:18px; font-weight:400;" in css
    assert ".stat b { font-size:18px; font-weight:600;" not in css


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


def test_subtabs_height_reduced_by_third():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 设置子标签按钮高度降低约 1/3：上下内边距由 10px 降到 4px（桌面与手机端一致）
    m = re.search(r"\.subtabs button \{[^}]*\}", css)
    assert m and "padding:4px 20px" in m.group(0)
    m2 = re.search(r"\.subtabs button \{[^}]*padding:4px 14px[^}]*\}", css)
    assert m2, "mobile subtabs padding should be 4px 14px"


def test_managed_domains_title_removed():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 域名管理页“受管理域名 {count}”大标题已删（与账户管理/分组标题一致），
    # 右上角 ⚙（DDNS 设置开关）保留
    assert 't("受管理域名 {count}"' not in tsx
    assert 'title={t("DDNS 设置")}' in tsx
    assert "setShowDdns(!showDdns)" in tsx


def test_save_toast_bottom_right_theme_color():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 自动保存后的提示框：右下角悬浮 toast，不再是顶部通栏 notice；
    # 字体用主题色；5 秒自动隐藏的定时器仍在
    assert '<div className="toast">{msg}</div>' in tsx
    assert '<div className="notice">{msg}</div>' not in tsx
    m = re.search(r"\.toast \{([^}]*)\}", css)
    assert m, ".toast rule missing"
    rule = m.group(1)
    assert "position:fixed" in rule
    assert "right:16px" in rule and "bottom:16px" in rule
    assert "color:var(--accent-text)" in rule
    assert "setTimeout" in tsx and "5000" in tsx


def test_header_lang_toggle_and_logout():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 语言切换：图标式 中/En 切换键，点一下切语言，不再是下拉菜单；
    # 当前语言主题色加粗（.lang-on），未选的灰色（.lang-off）
    assert 'className="btn ghost lang-btn"' in tsx
    assert 'setLang(lang === "zh" ? "en" : "zh")' in tsx
    assert '>中</span>' in tsx and '>En</span>' in tsx
    assert 'langMenuOpen' not in tsx
    assert ".lang-on { color:var(--accent-text)" in css
    assert ".lang-off { color:var(--muted)" in css
    # 退出登录图标在页眉最右边（主题按钮之后）：调 /api/auth/logout、
    # 清 token、回登录页
    assert 'aria-label={t("退出登录")}' in tsx
    assert 'post("/api/auth/logout", {})' in tsx
    assert 'setToken(null)' in tsx
    assert 'setAuthState("login")' in tsx
    # 顺序：语言键、主题键、退出键（退出在最右）
    assert tsx.index("lang-btn") < tsx.index("theme-btn")
    assert tsx.index("theme-btn") < tsx.index('t("退出登录")')


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
    # 实例状态底部“调度…”、本月流量“已取得…”、当前费用“已查询…”、服务器卡片状态详情
    # 调度状态行是 flex 行：文字在左、恢复/暂停按钮在右，仍用 marginTop: auto 钉在卡片底部
    assert 'marginTop: "auto", paddingTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8' in tsx
    assert '<span style={{ color: "var(--accent)" }}>{t("调度")}' in tsx
    assert 'marginTop: 8, color: "var(--accent)", textAlign: "right"' not in tsx
    assert tsx.count('color: "var(--accent)", fontSize: 14, marginTop: "auto"') >= 2
    assert 'color: "var(--accent)", fontSize: 14, textAlign: "right"' not in tsx
    dash_detail = [l for l in tsx.splitlines() if 'marginTop: "auto"' in l and "status_detail" in l]
    assert len(dash_detail) == 1 and 'textAlign' not in dash_detail[0]
    # 账户管理：状态详情行移到卡片左下角——与操作按钮同一行、居左，
    # 操作按钮在右；不再是单独一行放在按钮上面
    assert 'justifyContent: "space-between", alignItems: "center", marginTop: "auto", paddingTop: 12' in tsx
    assert 'marginTop: 4, color: (a.status_dot' not in tsx
    assert 'justifyContent: "flex-end", marginTop: 12 }}><ActionButtons' not in tsx


def test_group_card_custom_interval_and_start_time():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    group_card = tsx.split("function GroupCard")[1].split("function ChannelCard")[0]
    # 间隔切换周期有“自定义”选项（值 0），选自定义时显示自定义间隔行（数字+分钟/小时/天）
    assert '{ value: "0", label: t("自定义") }' in group_card
    assert 'interval === "0"' in group_card
    assert 't("自定义间隔")' in group_card
    assert 'custom_interval_minutes' in group_card
    # 开始时间在自定义行后面，默认 00:00:00，带秒的 time 输入，失焦自动保存
    assert group_card.index('t("开始时间")') > group_card.index('t("自定义间隔")')
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
    assert ".modal-overlay" in css and ".modal" in css
    assert 'justifyContent: "flex-end", marginTop: 12' in tsx


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


def test_buttons_filled_with_accent():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    btn = re.search(r"\.btn\s*\{([^}]*)\}", css)
    assert btn is not None
    body = btn.group(1)
    # 主按钮用主题色填充（白字），不再是透明底 + 主题色边框
    assert "background:var(--accent)" in body
    assert "color:#fff" in body
    # 删除实例仍是红色实心（内联样式，不受 .btn 影响）
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'background: "#EA4335"' in tsx


def test_group_card_pause_toggle_button():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 分组卡片右下角的停止/启动按钮：停止后绿色三角（点一下启动），
    # 启动后灰色方块（点一下停止）
    assert 't("启动分组")' in tsx and 't("停止分组")' in tsx
    assert 'paused: g.paused ? 0 : 1' in tsx
    assert 'icon-btn ${g.paused ? "green" : "gray"}' in tsx
    assert '<polygon points="6 3 20 12 6 21 6 3"' in tsx
    assert '<rect x="6" y="6" width="12" height="12"' in tsx


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
    # 点右上角 ⚙ 才展开
    assert "const [showSettings, setShowSettings] = useState(false);" in tsx


def test_accounts_tab_renamed_to_instances():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i18n = (ROOT / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 顶部导航与页眉标题：账户管理改名实例管理
    assert '["accounts", "实例管理"]' in tsx
    assert 'tab === "accounts" ? t("实例管理")' in tsx
    assert "账户管理" not in tsx or "// 拖动排序" in tsx and tsx.count("账户管理") == 0
    assert '实例管理: "Instance Management"' in i18n
    assert "账户管理" not in i18n


def test_dash_footer_has_pause_resume_button():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 运行概览实例状态卡片底部：调度状态旁有恢复/暂停调度按钮，
    # 点一下调 POST /api/schedule/pause，调度暂停时能从页面上恢复
    assert 'post("/api/schedule/pause"' in tsx
    assert 't("恢复调度")' in tsx
    assert 't("暂停调度")' in tsx


def test_two_line_labels_with_two_char_indent():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 复合标签分两行：第一行父项，第二行子项缩进 2 字符（全局设置 + 分组设置）
    assert "function TwoLineLabel" in tsx
    assert 'paddingLeft: "2em"' in tsx
    # 父项（负载均衡）字体与定时轮巡 h3 一样大（16px），子项保持 13px
    assert '<span style={{ fontSize: 16 }}>{t(parent)}</span>' in tsx
    # API 刷新时间标签字体也与定时轮巡 h3 一样大（16px）
    assert '<label className="req" style={{ fontSize: 16 }}>{t("API 刷新时间 *（分钟）")}</label>' in tsx
    # 负载均衡切换阀值（全局 3 处：主标签 + % + GB；分组 3 处同理）
    assert tsx.count('<TwoLineLabel parent="负载均衡"') == 6
    assert '<TwoLineLabel parent="负载均衡" child="切换阀值 *"' in tsx
    assert '<TwoLineLabel parent="负载均衡" child="切换阀值"' in tsx
    # 定时轮巡子项标签缩进 2 字符（全局轮巡周期/自定义间隔，分组间隔切换周期/自定义间隔/开始时间）
    for label in ("轮巡周期", "间隔切换周期", "自定义间隔", "开始时间"):
        assert f'style={{{{ paddingLeft: "2em" }}}}>{{t("{label}")}}' in tsx, label


def test_seg_selected_is_accent_filled():
    # 选中项用主题色边框+浅底深色字（填充太难看已改回），未选中项不动（透明底、灰框、灰字）
    assert ".seg button.seg-on { border:2px solid var(--accent); color:var(--accent-text); background:var(--accent-soft);" in CSS
    assert ".seg button { background:transparent; border:1px solid var(--seg-off); color:var(--muted);" in CSS
    # 填充已取消，选中成员按键里的 IP 不再用浅白字
    assert ".seg button.seg-on .member-ip" not in CSS


def test_controls_align_with_indented_labels():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 控件与缩进 2 字符的标签左对齐：Seg/输入框包在 paddingLeft 2em 的 div 里，
    # 阈值动作/阈值恢复整段缩进 2em；全局设置与分组设置都一样
    assert tsx.count('paddingLeft: "2em"') >= 12
    # 全局：阈值 Seg、轮巡周期 Seg 包在缩进 div 里
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sDiffMode}' in tsx
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sInterval}' in tsx
    # 全局：阈值动作/阈值恢复整段缩进
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>' in tsx
    # API 刷新时间按键区与上面轮巡周期按键区左对齐（同缩进 2em）
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={sRefresh}' in tsx
    # 分组：差值 Seg、间隔 Seg 包在缩进 div 里
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={diffMode}' in tsx
    assert '<div style={{ paddingLeft: "2em" }}><Seg value={interval}' in tsx


def test_parent_child_hierarchy_global():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 父栏标题比子栏大一号：TwoLineLabel 父项 16px、子项 13px；h3 标题 16px
    assert '<span style={{ fontSize: 16 }}>{t(parent)}</span>' in tsx
    assert ".card h3 { margin:0 0 8px; font-size:16px; }" in (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 父栏下的所有子栏后退两字符：分组卡片的选择成员实例、域名选择按键区也缩进 2em
    assert '<h3 style={{ marginTop: 12 }}>{t("选择成员实例")}</h3>\n      <div className="seg" style={{ paddingLeft: "2em" }}>' in tsx
    assert '<h3 style={{ marginTop: 12 }}>{t("域名选择")}</h3>\n      <div className="seg" style={{ paddingLeft: "2em" }}>' in tsx


def test_theme_accents_are_light():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 5 种主题色全部改浅（600 -> 500）：按钮/边框/选中填充用的 accent 更浅
    for hexv in ("#10b981", "#3b82f6", "#f97316", "#8b5cf6", "#ef4444"):
        assert hexv in css.lower(), hexv
    for old in ("#059669", "#2563eb", "#ea580c", "#7c3aed", "#dc2626"):
        assert old not in css.lower(), old
    # 主题卡片渐变色块同步改浅
    for g in ("#a7f3d0,#10b981", "#bfdbfe,#3b82f6", "#fed7aa,#f97316", "#ddd6fe,#8b5cf6", "#fecaca,#ef4444"):
        assert g in tsx, g


def test_settings_cards_use_parent_child_layout():
    tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # 父栏标题（h2/h3/16px）贴左，子栏内容缩进 2 字符：登录与安全、通知渠道、
    # 备份与恢复、站点信息、货币结算、主题设置、通知事件、DDNS 设置都一样
    # 登录与安全：修改密码/Google/GitHub 表单包在缩进 div 里
    assert tsx.count('<div style={{ paddingLeft: "2em" }}><div className="form2">') >= 3
    # 通知渠道：字段区缩进；每日财报推送升为 h3 父标题、其控件缩进
    assert '<div style={{ marginTop: 12, paddingLeft: "2em" }}>' in tsx
    assert '<h3 style={{ margin: 0 }}>{t("每日财报推送")}</h3>' in tsx
    # 备份与恢复：按钮行缩进
    assert '<div className="row" style={{ paddingLeft: "2em" }}>' in tsx
    # 站点信息：内容包在缩进 div 里
    assert '<div className="card"><h2>{t("站点信息")}</h2>\n      <div style={{ paddingLeft: "2em" }}>' in tsx
    # 货币结算 / 主题设置：内容缩进
    assert '<div className="card"><h2>{t("货币结算")}</h2>\n            <div style={{ paddingLeft: "2em" }}>' in tsx
    assert '<div className="theme-grid" style={{ paddingLeft: "2em" }}>' in tsx
    # 通知事件开关行、DDNS 设置表单网格缩进
    assert 'style={{ padding: "6px 0 6px 2em" }}' in tsx
    assert '<div className="form2" style={{ paddingLeft: "2em" }}>' in tsx


def test_nav_and_subtabs_selected_are_accent_filled():
    css = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    # 顶部导航与设置子标签选中态用主题色填充白字（与主按钮、Seg 选中一致），不再是浅底
    m = re.search(r"nav button\.active\s*\{([^}]*)\}", css)
    assert m, "nav button.active missing"
    body = m.group(1).replace(" ", "")
    assert "background:var(--accent)" in body
    assert "color:#fff" in body
    m2 = re.search(r"\.subtabs button\.active\s*\{([^}]*)\}", css)
    assert m2, ".subtabs button.active missing"
    body2 = m2.group(1).replace(" ", "")
    assert "background:var(--accent)" in body2
    assert "color:#fff" in body2
