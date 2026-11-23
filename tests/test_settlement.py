"""货币结算选项 USD/CNY + 汇率换算：

- 默认值：settlement_currency=USD、exchange_rate_usd_cny=6.6719
  （1 USD = 6.6719 CNY，2026-11-22 市价，用户可在设置里改）。
- PUT /api/settings 接受这两个键（DEFAULT_SETTINGS 里有，allowed 自动包含）。
- _validate_settings：结算货币只能是 USD/CNY；汇率必须是大于 0 的数字。
- 换算本身在前端做（status.total_cost 按币种分 + 设置里的货币/汇率），
  后端只负责存取与校验，这里覆盖后端行为。
"""
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def test_settlement_defaults(tmpdb):
    s = tmpdb.get_settings()
    assert s["settlement_currency"] == "USD"
    assert s["exchange_rate_usd_cny"] == "6.6719"


def test_validate_settlement_accepts_valid(tmpdb):
    from app.api import _validate_settings
    _validate_settings({"settlement_currency": "USD"})
    _validate_settings({"settlement_currency": "CNY"})
    _validate_settings({"exchange_rate_usd_cny": "6.6719"})
    _validate_settings({"exchange_rate_usd_cny": "7.25"})
    _validate_settings({"settlement_currency": "CNY",
                        "exchange_rate_usd_cny": "7"})


def test_validate_settlement_rejects_bad_currency(tmpdb):
    from app.api import _validate_settings
    for bad in ("EUR", "usd", "cny", "", "USDD"):
        with pytest.raises(HTTPException) as ei:
            _validate_settings({"settlement_currency": bad})
        assert ei.value.status_code == 400


def test_validate_settlement_rejects_bad_rate(tmpdb):
    from app.api import _validate_settings
    for bad in ("0", "-1", "abc", "", "NaN"):
        with pytest.raises(HTTPException) as ei:
            _validate_settings({"exchange_rate_usd_cny": bad})
        assert ei.value.status_code == 400


def test_put_settings_accepts_settlement(tmpdb):
    """走真实 handler：allowed 包含新键（来自 DEFAULT_SETTINGS），
    校验通过后落库，非法值 400。"""
    from app.api import put_settings
    out = put_settings({"settlement_currency": "CNY",
                        "exchange_rate_usd_cny": "7.25"}, True)
    assert out["settlement_currency"] == "CNY"
    assert out["exchange_rate_usd_cny"] == "7.25"
    assert tmpdb.get_settings()["settlement_currency"] == "CNY"
    assert tmpdb.get_settings()["exchange_rate_usd_cny"] == "7.25"
    with pytest.raises(HTTPException) as ei:
        put_settings({"settlement_currency": "EUR"}, True)
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        put_settings({"exchange_rate_usd_cny": "0"}, True)
    assert ei.value.status_code == 400
    # 非法值没写进去
    assert tmpdb.get_settings()["settlement_currency"] == "CNY"


def test_frontend_settlement_save_does_not_throw_off_global_tab():
    """结算货币在其它设置子标签里改，saveParamSettings 不能抛错：

    全局设置的 s-quota / s-prot 输入框只在 subTab === "settings" 时才渲染，
    其它设置子标签里没有这两个 DOM 节点。以前 saveParamSettings 直接读
    getElementById("s-quota").value，节点不存在就抛 TypeError，PUT 发不出去，
    结算货币选了也存不上，当前费用卡片（按 settings.settlement_currency 换算）
    就不生效。现在读不到时回退已存值（与 s-diff / s-diffgb 同款写法）。
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # s-quota / s-prot 用 ?. 读，取不到回退已存值
    assert '(document.getElementById("s-quota") as HTMLInputElement)?.value || String(settings.quota_gb ?? 200)' in tsx
    assert '(document.getElementById("s-prot") as HTMLInputElement)?.value || String(settings.protection_threshold_pct ?? 95)' in tsx
    assert '(document.getElementById("s-quota") as HTMLInputElement).value' not in tsx
    assert '(document.getElementById("s-prot") as HTMLInputElement).value' not in tsx
    # 结算货币选中即刻更新 settings（当前费用卡片按它换算），不用等后端返回
    assert 'setSettings((s) => ({ ...s, settlement_currency: v }))' in tsx
    # 结算货币 Seg 仍在其它设置卡片里，选中即保存
    assert 'saveParamSettings({ settlement_currency: v })' in tsx
