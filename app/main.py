"""入口：FastAPI + 内嵌前端静态资源 + 后台调度线程。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, scheduler, settings
from .api import router

BASE = Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"


def _host_without_port(host: str) -> str:
    """Host 头去端口：example.com:443 → example.com，[::1]:1999 → ::1。"""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if ":" in host else host


class _HostGuardMiddleware:
    """应用层防线（部署安全第二道）：只认配置的域名 Host。

    配了 CDT_PUBLIC_DOMAIN 才生效；本机回环（健康检查、容器内自访）与
    localhost 始终放行；反代透传的 X-Forwarded-Proto 是 http 时也拒绝
    （正常流量经 Caddy 只会是 https）。其它 Host 一律 403，不提供面板。
    纯 ASGI 中间件，不走 BaseHTTPMiddleware。
    """

    def __init__(self, app):
        self.app = app
        self.loopback = {"127.0.0.1", "localhost", "::1"}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # 每次请求都读 settings.PUBLIC_DOMAIN（进程启动时从环境变量
            # CDT_PUBLIC_DOMAIN 读入；不配则为空，中间件不生效）。
            allowed_host = settings.PUBLIC_DOMAIN
            if allowed_host:
                # 注意：scope 里的 header 名是 bytes，查之前要先 decode 成 str。
                headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                           for k, v in scope.get("headers", [])}
                host = _host_without_port(headers.get("host", ""))
                if host not in self.loopback and host != allowed_host:
                    resp = JSONResponse(
                        {"detail": "仅允许通过配置的域名访问本站点"}, status_code=403)
                    await resp(scope, receive, send)
                    return
                proto = headers.get("x-forwarded-proto", "").lower()
                if proto and proto != "https":
                    resp = JSONResponse(
                        {"detail": "本站点只提供 HTTPS 访问"}, status_code=403)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启停钩子（lifespan，替代已废弃的 @app.on_event）：启动调度线程，
    关闭时停掉。CDT_NO_SCHEDULER=1 时不启动调度（测试/单次任务用）。"""
    if os.environ.get("CDT_NO_SCHEDULER") != "1":
        scheduler.start()
    yield
    # P0-01：停机时等调度线程退出（带超时），避免打断正在执行的 Plan。
    scheduler.stop()


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="alicloud_ECS_CDT", version="0.1.0", lifespan=lifespan)
    # 部署安全：应用层 Host 白名单中间件一直挂着，但只有配了
    # CDT_PUBLIC_DOMAIN 时才生效（第一道是 Caddy 只给该域名做 HTTPS，
    # 1999 端口不对外发布）。不配则不限制（本地开发、旧部署保持原样）。
    # 中间件每次请求都读 settings.PUBLIC_DOMAIN，所以测试里改
    # settings.PUBLIC_DOMAIN 就能开关，不依赖导入顺序。
    app.add_middleware(_HostGuardMiddleware)
    # P1-04：Gzip 压缩文本响应（JS/CSS/JSON）。
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.include_router(router)

    if STATIC_DIR.exists():
        # P1-04：哈希命名的 /assets/* 资源长期缓存（文件名变了 URL 就变，
        # 不会拿到旧版本）；index.html 与其它 SPA 文件不缓存，始终取最新。
        # 子类覆写响应头（老版 Starlette 的 StaticFiles 不接受 headers 参数）。
        class _CachedAssets(StaticFiles):
            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                resp.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable")
                return resp

        app.mount("/assets", _CachedAssets(directory=STATIC_DIR / "assets"),
                  name="assets")

        @app.get("/")
        def index():
            return FileResponse(STATIC_DIR / "index.html",
                                headers={"Cache-Control": "no-cache"})

        @app.get("/{path:path}")
        def spa(path: str):
            # 非 /api 路径一律回前端 SPA；/api 未命中由 FastAPI 返回 404
            if path.startswith("api/"):
                from fastapi import HTTPException
                raise HTTPException(404, "接口不存在")
            candidate = STATIC_DIR / path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html",
                                headers={"Cache-Control": "no-cache"})

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.LISTEN_HOST,
                port=settings.LISTEN_PORT, log_level="info")
