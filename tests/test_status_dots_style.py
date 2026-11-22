"""状态点样式回归（轻量断言，不启动浏览器）：

- 颜色为 Google 品牌色：绿 #34A853 / 黄 #FBBC04 / 红 #EA4335 / 灰 #9AA0A6，
  在 styles.css 里用 --dot-* 变量集中定义。
- 尺寸：直径约 2mm，.dot 均为 8px 正圆，垂直居中于字体高度。
- styles.css 里不含旧的默认色值（#22c55e / #eab308 / #4ade80 /
  #94a3b8）：确认过旧值只在 :root 的 --green/--red 与 .pill 里出现过，
  已随本轮统一换成 Google 色；#ef4444 不再禁（2026-11-22 23:38 主题色
  全部改浅后它是绯红月主题的 accent，不作状态点色）；App.tsx 里仅剩一处 #f87171 是表单报错
  文字色，不是状态点，故不在此断言。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "web" / "src" / "styles.css").read_text()
TSX = (ROOT / "web" / "src" / "App.tsx").read_text()

GOOGLE = ("#34A853", "#FBBC04", "#EA4335", "#9AA0A6")
OLD = ("#22c55e", "#eab308", "#4ade80", "#94a3b8")


def test_styles_css_has_google_dot_colors_as_variables():
    lower = CSS.lower()
    for hexv in ("#34a853", "#fbbc04", "#ea4335", "#9aa0a6"):
        assert hexv in lower
    for var, hexv in (("--dot-green", "#34a853"), ("--dot-yellow", "#fbbc04"),
                      ("--dot-red", "#ea4335"), ("--dot-gray", "#9aa0a6")):
        assert re.search(rf"{var}\s*:\s*{hexv}", CSS, re.I), var
    assert "background:var(--dot-green)" in CSS.replace(" ", "")


def test_styles_css_has_no_old_default_colors():
    lower = CSS.lower()
    for hexv in OLD:
        assert hexv.lower() not in lower, hexv


def test_dot_size_is_8px():
    m = re.search(r"\.dot\s*\{([^}]*)\}", CSS)
    assert m, ".dot rule missing"
    assert re.search(r"width:\s*8px", m.group(1))
    assert re.search(r"height:\s*8px", m.group(1))
    assert re.search(r"border-radius:\s*50%", m.group(1))
    assert re.search(r"vertical-align:\s*middle", m.group(1))
    # 图例已删除（2026-11-22）：不再有 .legend .dot 规则
    assert ".legend" not in CSS
    # 闪烁光晕按小尺寸缩小：扩散约 2–3px，不用旧的 7px 大光圈
    assert "0 0 0 7px" not in CSS
    assert "0 0 0 3px" in CSS


def test_app_tsx_dot_colors_are_google():
    for hexv in GOOGLE:
        assert hexv in TSX, hexv
    dot_line = next(ln for ln in TSX.splitlines()
                    if '"#34A853"' in ln and "?" in ln
                    and "green_pulse" in ln)
    for hexv in ("#22c55e", "#eab308", "#ef4444", "#4ade80", "#94a3b8"):
        assert hexv not in dot_line, hexv
    assert "width: 8, height: 8" in TSX
