"""入口：FastAPI + 内嵌前端静态资源 + 后台调度线程。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, scheduler
from .api import router

BASE = Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启停钩子（lifespan，替代已废弃的 @app.on_event）：启动调度线程，
    关闭时停掉。CDT_NO_SCHEDULER=1 时不启动调度（测试/单次任务用）。"""
    if os.environ.get("CDT_NO_SCHEDULER") != "1":
        scheduler.start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="alicloud_ECS_CDT", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{path:path}")
        def spa(path: str):
            # 非 /api 路径一律回前端 SPA；/api 未命中由 FastAPI 返回 404
            if path.startswith("api/"):
                from fastapi import HTTPException
                raise HTTPException(404, "接口不存在")
            candidate = STATIC_DIR / path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    from . import settings
    uvicorn.run("app.main:app", host=settings.LISTEN_HOST,
                port=settings.LISTEN_PORT, log_level="info")
