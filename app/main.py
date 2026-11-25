"""入口：FastAPI + 内嵌前端静态资源 + 后台调度线程。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import bot, db, scheduler, settings
from .api import router

BASE = Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"

# 反代转发标识头：标准反代（Nginx / Caddy / Cloudflare）都会带上其中至少一个。
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "cf-connecting-ip")


class _ProxyOnlyMiddleware:
    """部署安全：只接受经过反代的请求。

    反代不一定在本机，所以不能把 1999 只绑 127.0.0.1（远端反代连不上）。
    改为端口正常发布、应用层拦截直访：请求必须带反代转发标识头
    （X-Forwarded-For / X-Forwarded-Proto / CF-Connecting-IP 之一），
    否则直接 403——浏览器直接打开 IP:端口打不开。
    本机回环（容器健康检查、install.sh 轮询）始终放行。
    只在 CDT_PROXY_ONLY=1 时生效（compose 默认开；本地直接跑、测试默认不开）。
    纯 ASGI 中间件，不走 BaseHTTPMiddleware。
    """

    def __init__(self, app):
        self.app = app
        self.loopback = {"127.0.0.1", "localhost", "::1"}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and settings.PROXY_ONLY:
            # 注意：scope 里的 header 名是 bytes，查之前要先 decode 成 str。
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            client = scope.get("client")
            client_ip = (client[0] if client else "").lower()
            if client_ip not in self.loopback and not any(
                    h in headers for h in _PROXY_HEADERS):
                resp = JSONResponse(
                    {"detail": "请通过反代访问本站点"}, status_code=403)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启停钩子（lifespan，替代已废弃的 @app.on_event）：启动调度线程，
    关闭时停掉。CDT_NO_SCHEDULER=1 时不启动调度（测试/单次任务用）。"""
    if os.environ.get("CDT_NO_SCHEDULER") != "1":
        scheduler.start()
        # 机器人控制线程随调度一起启停；是否轮询由 bot_enabled 开关决定
        bot.start()
    yield
    # P0-01：停机时等调度线程退出（带超时），避免打断正在执行的 Plan。
    bot.stop()
    scheduler.stop()


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="alicloud_ECS_CDT", version="0.1.0", lifespan=lifespan)
    # 部署安全：只接受经过反代的请求（CDT_PROXY_ONLY=1 时生效，
    # compose 默认开；直接用 IP:端口打开会被 403）。
    # 中间件每次请求都读 settings.PROXY_ONLY，所以测试里改
    # settings.PROXY_ONLY 就能开关，不依赖导入顺序。
    app.add_middleware(_ProxyOnlyMiddleware)
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
