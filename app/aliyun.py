"""阿里云 ECS / CDT / BSS 客户端（官方 SDK）。

- ECS 状态/开关机：aliyun-python-sdk-ecs，DescribeInstances / StartInstance / StopInstance
- 轻量应用服务器（SAS）：SWAS-OPEN（2020-06-01）ListInstances / StartInstance /
  StopInstance / RebootInstance。ECS DescribeInstances 永远查不到轻量服务器
  （它是独立产品，实例 ID 为 UUID 形如 3eeeb2fb…，不是 i-…），所以读状态、
  找地域、开关机都要先 ECS、再 SWAS 兜底。SWAS 必须用地域端点
  swas.<region>.aliyuncs.com（中心端点 swas.aliyuncs.com 不解析），
  且 AK 须有 SWAS 权限（AliyunSWASOpenFullAccess / AliyunSWASFullAccess），
  否则报 403 NoPermission——此时记日志并当作“SWAS 查不到”，不掩盖 ECS 结果。
- CDT 流量：CommonRequest，cdt.aliyuncs.com，2021-08-13，ListCdtInternetTraffic，
  累加 TrafficDetails[].Traffic（字节）→ GiB。与参考项目同口径。
- BSS 费用：DescribeInstanceBill（失败回退 QueryBillOverview）+ QueryAccountBalance，
  端点按站点先国际站 business.ap-southeast-1.aliyuncs.com / 国内站
  business.aliyuncs.com，失败换另一站；失败只返回 None，
  绝不影响启停流程（BSS 权限经常没给够）。
- 保守原则：任何查询失败 → 该账户流量/状态记为未知，上层绝不据此停机或切换。
  SDK 未安装时所有调用抛 AliyunError（不是静默假数据）。
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from zoneinfo import ZoneInfo

log = logging.getLogger("cdt.aliyun")

try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
    # 注意：aliyunsdkecs 的请求类在各自的子模块里（import 包名得到的是模块，
    # 不是类）；必须从子模块 import 类，否则 DescribeInstancesRequest() 会报
    # TypeError: 'module' object is not callable。
    from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import (
        DescribeInstancesRequest)
    from aliyunsdkecs.request.v20140526.StartInstanceRequest import (
        StartInstanceRequest)
    from aliyunsdkecs.request.v20140526.StopInstanceRequest import (
        StopInstanceRequest)
    from aliyunsdkecs.request.v20140526.RebootInstanceRequest import (
        RebootInstanceRequest)
    _SDK_READY = True
except Exception:  # pragma: no cover
    _SDK_READY = False
    AcsClient = None  # type: ignore
    CommonRequest = None  # type: ignore

CDT_DOMAIN = "cdt.aliyuncs.com"
CDT_REGION = "cn-hongkong"
CDT_VERSION = "2021-08-13"
BSS_DOMAIN = "business.aliyuncs.com"
BSS_DOMAIN_INTL = "business.ap-southeast-1.aliyuncs.com"
BSS_VERSION = "2017-12-14"
ECS_DOMAIN = "ecs.aliyuncs.com"
ECS_VERSION = "2014-05-26"
# 轻量应用服务器（SWAS-OPEN）：必须用地域端点，中心端点 swas.aliyuncs.com 不解析。
SWAS_VERSION = "2020-06-01"


def _swas_domain(region: str) -> str:
    return f"swas.{region}.aliyuncs.com"


# 轻量服务器实例 ID 是 UUID（32 位十六进制，可带连字符），ECS 实例 ID 是 i-…。
_SWAS_ID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)

# BSS 账单缓存（P1-01）：账单按月结算，5 分钟刷一次纯属浪费还易限流。
# 进程内按（AK、站点、结算周期）缓存 6 小时；ECS 状态与 CDT 流量仍按
# 刷新间隔刷，不受影响。失败不缓存，下次刷新重试。缓存只存费用/余额
# 数值，不存任何密钥。
BILLING_CACHE_TTL_SECONDS = 6 * 3600
_BILLING_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}


def clear_billing_cache() -> None:
    """清空 BSS 账单缓存（测试用；费用卡片手动刷新时也可调用）。"""
    _BILLING_CACHE.clear()
BUILTIN_REGIONS = [
    {"region_id": "cn-hongkong", "local_name": "中国香港", "endpoint": "ecs.cn-hongkong.aliyuncs.com"},
    {"region_id": "ap-southeast-1", "local_name": "新加坡", "endpoint": "ecs.ap-southeast-1.aliyuncs.com"},
    {"region_id": "ap-northeast-1", "local_name": "东京", "endpoint": "ecs.ap-northeast-1.aliyuncs.com"},
    {"region_id": "us-west-1", "local_name": "硅谷", "endpoint": "ecs.us-west-1.aliyuncs.com"},
    {"region_id": "eu-central-1", "local_name": "法兰克福", "endpoint": "ecs.eu-central-1.aliyuncs.com"},
    {"region_id": "cn-beijing", "local_name": "北京", "endpoint": "ecs.cn-beijing.aliyuncs.com"},
    {"region_id": "cn-shanghai", "local_name": "上海", "endpoint": "ecs.cn-shanghai.aliyuncs.com"},
    {"region_id": "cn-shenzhen", "local_name": "深圳", "endpoint": "ecs.cn-shenzhen.aliyuncs.com"},
    {"region_id": "cn-hangzhou", "local_name": "杭州", "endpoint": "ecs.cn-hangzhou.aliyuncs.com"},
]

_AUTH_HINTS = ("signaturedoesnotmatch", "invalidaccesskey", "authfailure",
               "forbidden", "unauthorized", "accessdenied")
_THROTTLE_HINTS = ("throttling", "requestlimitexceeded", "serviceunavailable")
_STOCK_HINTS = ("insufficientinventory", "nostock")
_NET_HINTS = ("network is unreachable", "max retries exceeded", "connection refused",
              "connection reset", "name or service not known", "temporary failure")


class AliyunError(RuntimeError):
    def __init__(self, message: str, *, kind: str = ""):
        super().__init__(message)
        self.kind = kind or classify_error(message)


def classify_error(exc) -> str:
    low = str(exc).lower()
    if any(h in low for h in _AUTH_HINTS):
        return "auth"
    if any(h in low for h in _THROTTLE_HINTS):
        return "throttle"
    if any(h in low for h in _STOCK_HINTS):
        return "stock"
    if any(h in low for h in _NET_HINTS) or "timed out" in low or "timeout" in low:
        return "transient"
    return "other"


_SECRET_RE = re.compile(r"(?i)\b(Signature|AccessKeyId|AccessKeySecret)=([^&\s'\"]+)")


def brief_error(exc) -> str:
    text = _SECRET_RE.sub(r"\1=***", str(exc))
    return text[:300].strip()


def _is_not_found(exc) -> bool:
    """ECS 报实例不存在（InvalidInstanceId.NotFound 等）：说明这台不是 ECS，
    开关机应改走 SWAS。只认“实例不存在”类错误，其它错误（权限、限流）原样抛出。"""
    low = str(exc).lower()
    return "notfound" in low and "instance" in low


def _require_sdk() -> None:
    if not _SDK_READY:
        raise AliyunError("阿里云 SDK 未安装（aliyun-python-sdk-core/ecs）", kind="other")


class AliyunClient:
    """一个账号的客户端。account 为 db 行 dict，secret 已解密。"""

    def __init__(self, account: dict, secret: str):
        _require_sdk()
        self.account = account
        self.secret = secret
        self.region = account.get("region") or "cn-hongkong"
        # 本次调用里 SWAS 是否报过 403（AK 无 SWAS 权限），用于最终报错提示。
        self._swas_denied = False

    def _client(self, region: str | None = None):
        return AcsClient(self.account["access_key_id"], self.secret,
                         region or self.region)

    def _common(self, domain: str, version: str, action: str, region: str,
                params: dict | None = None):
        """CommonRequest 调用（POST + JSON）。params 有值时逐个加为查询参数。
        注意：部分接口有必填参数，不传直接 400（DescribeInstanceBill 不传
        BillingCycle 就报 MissingBillingCycle，费用永远读不到），
        调用方必须传齐。"""
        req = CommonRequest()
        req.set_domain(domain)
        req.set_version(version)
        req.set_action_name(action)
        req.set_method("POST")
        req.set_accept_format("json")
        for k, v in (params or {}).items():
            req.add_query_param(k, v)
        return self._client(region).do_action_with_exception(req)

    def _swas_list_instances(self, region: str, instance_id: str) -> dict | None:
        """SWAS ListInstances 查一台轻量服务器，查到返回实例 dict，查不到返回 None。

        轻量服务器（SAS）不在 ECS 里，ECS DescribeInstances 永远返回空。
        SWAS 的 ListInstances 用 InstanceIds（JSON 数组字符串）过滤。
        AK 无 SWAS 权限时报 403 NoPermission：记一条 warning 并返回 None，
        让调用方继续走“查不到”流程，不把权限问题伪装成其它错误。
        （调用方在最终报错里会提示去加 AliyunSWASOpenFullAccess。）
        """
        import json
        try:
            raw = self._common(_swas_domain(region), SWAS_VERSION,
                               "ListInstances", region,
                               {"RegionId": region,
                                "InstanceIds": json.dumps([instance_id])})
        except Exception as exc:
            if "nopermission" in str(exc).lower() or "forbidden" in str(exc).lower():
                log.warning("SWAS 权限不足（%s）：AK 需 AliyunSWASOpenFullAccess，"
                            "轻量服务器查不到", region)
                self._swas_denied = True
                return None
            raise
        data = json.loads(raw)
        items = data.get("Instances") or []
        return items[0] if items else None

    def _swas_action(self, action: str, region: str) -> None:
        """SWAS 开关机/重启（StartInstance / StopInstance / RebootInstance）。
        轻量服务器是包月订阅，StopInstance 没有 ECS 的 StoppedMode（节省停机）概念。"""
        self._common(_swas_domain(region), SWAS_VERSION, action, region,
                     {"RegionId": region,
                      "InstanceId": self.account["instance_id"]})

    def _describe_swas(self, region: str | None = None) -> dict | None:
        """SWAS 读一台轻量服务器，统一成 describe_instance 的返回格式。"""
        inst = self._swas_list_instances(region or self.region,
                                         self.account["instance_id"])
        if not inst:
            return None
        # SWAS 的 PublicIpAddress 是字符串（ECS 是 {IpAddress: [...]}），
        # 套餐用 PlanName（无则 PlanId）当 instance_type。
        return {"status": inst.get("Status", "") or "",
                "public_ip": inst.get("PublicIpAddress", "") or "",
                "instance_type": (inst.get("PlanName", "") or ""
                                  or inst.get("PlanId", "") or "")}

    def describe_instance(self) -> dict:
        """返回 {"status": "Running"|"Stopped"|..., "public_ip": str,
        "instance_type": str}。instance_type 为 ECS 原始规格（如 ecs.t6-c1m1.large），
        财报里只在有值时显示，绝不凭空编造 2C0.5G 这类友好规格。
        轻量服务器（SAS）不在 ECS 里：ECS 查不到时再查 SWAS，仍查不到才报错。"""
        import json
        req = DescribeInstancesRequest()
        req.set_InstanceIds([self.account["instance_id"]])
        raw = self._client().do_action_with_exception(req)
        data = json.loads(raw)
        items = (data.get("Instances") or {}).get("Instance") or []
        if items:
            inst = items[0]
            # 公网 IP 取法（对齐参考项目 10000ge10000/aliyun_monitor）：
            # 实例绑了弹性 IP 时，DescribeInstances 把 IP 放在 EipAddress 里，
            # PublicIpAddress 是空的——只读后者会永远读不到（2026-11-22 线上证实：
            # 两台实例的 IP 全是弹性 IP，刷新成功但 public_ip 恒为空）。
            # 顺序：EipAddress 优先，PublicIpAddress[0] 兜底。
            eip = (inst.get("EipAddress") or {}).get("IpAddress") or ""
            ips = (inst.get("PublicIpAddress") or {}).get("IpAddress") or []
            return {"status": inst.get("Status", ""), "public_ip": eip or (ips[0] if ips else ""),
                    "instance_type": inst.get("InstanceType", "") or ""}
        # ECS 查不到：可能是轻量服务器（SAS），再查 SWAS。
        swas = self._describe_swas()
        if swas:
            return swas
        if self._swas_denied and _SWAS_ID_RE.match(self.account["instance_id"] or ""):
            raise AliyunError(
                f"实例 {self.account['instance_id']} 查不到：它是轻量服务器，"
                "但 AK 无 SWAS 权限（403），请给 AK 加 AliyunSWASOpenFullAccess",
                kind="auth")
        raise AliyunError(f"实例 {self.account['instance_id']} 查不到", kind="other")

    def start_instance(self) -> None:
        try:
            req = StartInstanceRequest()
            req.set_InstanceId(self.account["instance_id"])
            self._client().do_action_with_exception(req)
        except Exception as exc:
            if _is_not_found(exc):
                self._swas_action("StartInstance", self.region)
                return
            raise

    def stop_instance(self, stop_charging: bool = False) -> None:
        try:
            req = StopInstanceRequest()
            req.set_InstanceId(self.account["instance_id"])
            # 节省停机：StoppedMode=StopCharging；普通停机：KeepCharging
            req.set_StoppedMode("StopCharging" if stop_charging else "KeepCharging")
            req.set_Force(True)
            self._client().do_action_with_exception(req)
        except Exception as exc:
            if _is_not_found(exc):
                # 轻量服务器是包月订阅，没有 ECS 的节省停机概念，直接停机。
                self._swas_action("StopInstance", self.region)
                return
            raise

    def reboot_instance(self) -> None:
        try:
            req = RebootInstanceRequest()
            req.set_InstanceId(self.account["instance_id"])
            req.set_Force(True)
            self._client().do_action_with_exception(req)
        except Exception as exc:
            if _is_not_found(exc):
                self._swas_action("RebootInstance", self.region)
                return
            raise

    def describe_regions(self) -> list[dict]:
        """该站点下的可用地域列表（ECS DescribeRegions）。

        用中心端点 ecs.aliyuncs.com（DescribeRegions 是地域无关接口）。
        国际站/国内站用同一 ECS 端点；站点决定的是账号归属与 BSS 币种
        （USD/CNY），不是 ECS 地域端点（见 README“区域与站点”）。
        失败抛 AliyunError，调用方回退内置列表。
        """
        import json
        try:
            raw = self._common(ECS_DOMAIN, ECS_VERSION,
                               "DescribeRegions", "cn-hangzhou")
            data = json.loads(raw)
        except Exception as exc:
            raise AliyunError(f"地域查询失败：{brief_error(exc)}") from exc
        return parse_regions(data)

    def _list_instance_regions_in(self, region: str) -> list[str]:
        """某地域下已有实例所在的地域 id（通常就是该地域本身）。"""
        import json
        req = DescribeInstancesRequest()
        req.set_PageSize(50)
        raw = self._client(region).do_action_with_exception(req)
        data = json.loads(raw)
        return parse_instance_regions(data)

    def discover_instance_regions(self, regions: list[dict] | None = None) -> list[str]:
        """已有实例实际所在的地域 id（去重、排序）。

        逐地域调 DescribeInstances（每地域最多查 50 台）汇总；
        regions 未给时先调 describe_regions 拿全量列表。
        单个地域失败只记日志跳过，不影响其它地域。
        """
        if regions is None:
            regions = self.describe_regions()
        owned: set[str] = set()
        for r in regions:
            rid = r.get("region_id") or ""
            if not rid:
                continue
            try:
                if self._list_instance_regions_in(rid):
                    owned.add(rid)
            except Exception as exc:
                log.warning("地域 %s 实例发现失败（跳过）: %s", rid, brief_error(exc))
        return sorted(owned)

    def find_instance_region(self, instance_id: str) -> str:
        """按实例 ID 查它真实所在的地域：先查账号已保存地域，再逐地域查。

        DescribeInstances 在错误地域返回空列表而非报错，所以逐地域试；
        查不到抛 AliyunError（前端提示，不要静默填错）。
        ECS 全地域查不到时再逐地域查 SWAS（轻量服务器不在 ECS 里）。"""
        import json
        regions = [self.region] + [r["region_id"] for r in self.describe_regions()
                                   if r["region_id"] != self.region]
        for rid in regions:
            req = DescribeInstancesRequest()
            req.set_InstanceIds([instance_id])
            try:
                raw = self._client(rid).do_action_with_exception(req)
            except Exception as exc:
                # AK 错/无权限会在首个地域就报错；直接抛，不要逐地域重复报错。
                if rid == regions[0]:
                    raise AliyunError(f"实例 {instance_id} 查询失败：{brief_error(exc)}") from exc
                continue
            data = json.loads(raw)
            items = (data.get("Instances") or {}).get("Instance") or []
            if items:
                return items[0].get("RegionId") or rid
        # ECS 全地域都没有：可能是轻量服务器，逐地域查 SWAS。
        for rid in regions:
            try:
                if self._swas_list_instances(rid, instance_id):
                    return rid
            except Exception as exc:
                # 首个地域就报错（如 AK 错）直接抛；其它地域失败只跳过。
                if rid == regions[0]:
                    raise AliyunError(f"实例 {instance_id} 查询失败：{brief_error(exc)}") from exc
                continue
        if self._swas_denied and _SWAS_ID_RE.match(instance_id or ""):
            raise AliyunError(
                f"实例 {instance_id} 在该账号下查不到：它是轻量服务器，"
                "但 AK 无 SWAS 权限（403），请给 AK 加 AliyunSWASOpenFullAccess",
                kind="auth")
        raise AliyunError(f"实例 {instance_id} 在该账号下查不到", kind="other")

    def get_traffic_gb(self) -> float:
        """当月 CDT 出方向流量 GiB。失败抛 AliyunError，调用方记为未知。"""
        import json
        try:
            raw = self._common(CDT_DOMAIN, CDT_VERSION,
                               "ListCdtInternetTraffic", CDT_REGION)
            data = json.loads(raw)
        except Exception as exc:
            raise AliyunError(f"CDT 流量查询失败：{brief_error(exc)}") from exc
        details = data.get("TrafficDetails") or []
        total_bytes = sum(float(d.get("Traffic", 0) or 0) for d in details)
        return total_bytes / (1024 ** 3)

    def get_billing(self, timezone: str = "Asia/Shanghai",
                    force_refresh: bool = False) -> dict | None:
        """返回 {"cost": float, "currency": "USD"|"CNY", "cycle": "YYYY-MM",
        "balance": float|None, "balance_currency": str}，失败返回 None。
        余额单独调 QueryAccountBalance，查不到时 balance=None（财报显示 —，不造假）。
        取法对齐参考项目 10000ge10000/aliyun_monitor：先 DescribeInstanceBill，
        全失败再回退 QueryBillOverview；端点按站点排序（国际站先
        business.ap-southeast-1.aliyuncs.com，国内站先 business.aliyuncs.com），
        失败换另一站。DescribeInstanceBill 要求必填 BillingCycle（YYYY-MM，
        不传直接 400 MissingBillingCycle，费用永远读不到——旧 bug，
        2026-11-22 线上日志证实），按面板时区取当月。
        P1-01：结果按（AK、站点、周期）缓存 6 小时，缓存期内不再调 BSS；
        失败不缓存。B-08：周期按传入的 timezone 算，不再硬编码 Asia/Shanghai
        （非法时区回退 Asia/Shanghai）。"""
        import json
        try:
            tz = ZoneInfo((timezone or "Asia/Shanghai").strip())
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
        cycle = dt.datetime.now(tz).strftime("%Y-%m")
        currency = "CNY" if (self.account.get("site") == "cn") else "USD"
        cache_key = (str(self.account.get("access_key_id") or ""),
                     currency, cycle)
        if not force_refresh:
            hit = _BILLING_CACHE.get(cache_key)
            if hit and hit[0] > time.time():
                return dict(hit[1])
        domains = ([BSS_DOMAIN_INTL, BSS_DOMAIN] if currency == "USD"
                   else [BSS_DOMAIN, BSS_DOMAIN_INTL])
        cost = None
        for action in ("DescribeInstanceBill", "QueryBillOverview"):
            for domain in domains:
                try:
                    raw = self._common(domain, BSS_VERSION, action,
                                       CDT_REGION, {"BillingCycle": cycle})
                    data = json.loads(raw)
                except Exception as exc:
                    log.warning("BSS %s 查询失败（%s，不影响调度）: %s",
                                action, domain, brief_error(exc))
                    continue
                # 无 Data 的响应是接口级报错体（Success:false），不认作成功，
                # 继续试下一站/下一动作；有 Data 即使条目为空也认（费用为 0）。
                if not isinstance(data.get("Data"), dict):
                    continue
                items = _bill_items(data)
                cost = sum(float(i.get("PretaxAmount", 0) or 0) for i in items)
                cycle = (data.get("Data") or {}).get("BillingCycle", "") or cycle
                break
            if cost is not None:
                break
        if cost is None:
            log.warning("BSS 查询失败（不影响调度）: DescribeInstanceBill 与 "
                        "QueryBillOverview 在两站端点均失败")
            return None
        result: dict = {"cost": round(cost, 6), "currency": currency,
                        "cycle": cycle, "balance": None,
                        "balance_currency": currency}
        for domain in domains:
            try:
                raw = self._common(domain, BSS_VERSION,
                                   "QueryAccountBalance", CDT_REGION)
                data = json.loads(raw)
                bal = data.get("Data") or {}
                avail = bal.get("AvailableAmount")
                if avail is None:
                    continue
                # 金额可能带千分位逗号，如 "1,234.56"。
                result["balance"] = round(float(str(avail).replace(",", "")), 2)
                result["balance_currency"] = bal.get("Currency") or currency
                break
            except Exception as exc:
                log.warning("余额查询失败（%s，财报余额显示 —）: %s",
                            domain, brief_error(exc))
        _BILLING_CACHE[cache_key] = (
            time.time() + BILLING_CACHE_TTL_SECONDS, dict(result))
        return result


def _bill_items(data: dict) -> list:
    """BSS 账单条目：兼容 Items 直接是列表，和 Items.Item 是列表两种形状
    （参考项目两处写法不一致；以 Items.Item 为主）。"""
    items = (data.get("Data") or {}).get("Items")
    if isinstance(items, dict):
        items = items.get("Item") or []
    return items or []


def parse_regions(data: dict) -> list[dict]:
    """解析 DescribeRegions 返回：[{region_id, local_name, endpoint}]，按 local_name 排序。"""
    items = (data.get("Regions") or {}).get("Region") or []
    out = []
    for r in items:
        rid = r.get("RegionId") or ""
        if not rid:
            continue
        out.append({"region_id": rid,
                    "local_name": r.get("LocalName") or rid,
                    "endpoint": r.get("RegionEndpoint") or ""})
    out.sort(key=lambda x: x["local_name"])
    return out


def parse_instance_regions(data: dict) -> list[str]:
    """解析 DescribeInstances 返回：实例所在的地域 id（去重、排序）。"""
    items = (data.get("Instances") or {}).get("Instance") or []
    return sorted({i.get("RegionId") for i in items if i.get("RegionId")})


def wait_status(client: AliyunClient, want: str, timeout: int = 180,
                interval: int = 10) -> bool:
    """轮询实例状态直到 want 或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.describe_instance()["status"] == want:
                return True
        except AliyunError:
            pass
        time.sleep(interval)
    return False
