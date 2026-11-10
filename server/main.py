"""平台入口：FastAPI 应用装配。

- 启动时初始化数据库、清理僵尸 running 记录、收集一次用例清单；
- 静态资源目录存在时托管前端构建产物（镜像内 /app/static）。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server import db
from server.api import accuracy, auth, cases, contracts, health, runs, users
from server.api import settings as settings_api
from server.config import settings
from server.core import collector

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("server.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()

    interrupted = db.mark_interrupted_runs()
    if interrupted:
        logger.warning("%s 条执行记录因服务重启被标记为中断", interrupted)

    collector.get_cases(force=True)  # 启动时刷新清单，页面可直接展示
    if settings.auth_enabled and not settings.auth_password:
        logger.warning("AUTH_ENABLED=true 但未配置 AUTH_PASSWORD，登录鉴权未生效")
    logger.info("平台启动完成，data_dir=%s，base_url=%s", settings.data_dir, settings.base_url or "(未配置)")
    yield


app = FastAPI(title="江淮智能合同审核 · 自动化接口回归测试平台", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def no_cache_html(request, call_next):
    """HTML 入口不缓存，避免前端发版后浏览器继续使用旧 JS。"""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def require_login(request, call_next):
    """除登录、探活外的 /api 请求都需要有效会话。"""
    path = request.url.path
    if (
        auth.auth_active()
        and path.startswith("/api/")
        and request.method != "OPTIONS"
        and path not in {"/api/auth/login", "/api/auth/me", "/api/health"}
        and not auth.current_user(request)
    ):
        return JSONResponse(status_code=401, content={"detail": "未登录"})
    return await call_next(request)


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(cases.router, prefix="/api")
app.include_router(contracts.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(accuracy.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(users.router, prefix="/api")

# 报告静态目录：本地裸跑（不经 nginx）时也可直接打开报告；容器内由 nginx 直接托管该路径
settings.reports_dir.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(settings.reports_dir)), name="reports")

# 前端构建产物（镜像内存在时才挂载；本地开发走 vite dev server）
if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
