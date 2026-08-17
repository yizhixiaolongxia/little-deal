"""FastAPI 应用入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import scheduler
from .routers import watchlist, fund, stock, market, sim, nav, benchmark, macro


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 净值同步挂在后端进程里：launchd 起的后台进程读不了 ~/Documents（TCC 拦），
    # 详见 scheduler.py 的模块说明。代价是后端不开就不同步
    scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="FundScope API", version="1.0.0", lifespan=lifespan)

# CORS 中间件 - 允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(watchlist.router)
app.include_router(fund.router)
app.include_router(stock.router)
app.include_router(market.router)
app.include_router(sim.router)
app.include_router(nav.router)
app.include_router(benchmark.router)
app.include_router(macro.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
