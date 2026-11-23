"""测试隔离：每个测试前清空 BSS 账单进程内缓存（P1-01），否则同一
AK/站点/周期在不同测试间会互相命中缓存，计数类断言失效。"""
import pytest


@pytest.fixture(autouse=True)
def _clear_billing_cache():
    from app.aliyun import clear_billing_cache
    clear_billing_cache()
    yield
    clear_billing_cache()
