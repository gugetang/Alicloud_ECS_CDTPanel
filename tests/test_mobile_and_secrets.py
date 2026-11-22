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
