import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from app.core.config import settings
from app.providers.machinetranslation_provider import MachineTranslationProvider

# --- 终极日志配置 ---
logger.remove()
logger.add(
    sys.stdout,
    level="TRACE",  # 将日志级别调整为 TRACE 以查看所有细节
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
           "<level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    colorize=True,
    serialize=False
)

provider: Optional[MachineTranslationProvider] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global provider
    logger.info(f"应用启动中... {settings.APP_NAME} v{settings.APP_VERSION}")
    provider = MachineTranslationProvider()
    logger.info(f"服务将在 http://localhost:{settings.NGINX_PORT} 上可用")
    yield
    logger.info("应用关闭。")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan
)

async def verify_api_key(authorization: Optional[str] = Header(None)):
    if settings.API_MASTER_KEY and settings.API_MASTER_KEY != "1":
        if not authorization or "bearer" not in authorization.lower():
            raise HTTPException(status_code=401, detail="需要 Bearer Token 认证。")
        token = authorization.split(" ")[-1]
        if token != settings.API_MASTER_KEY:
            raise HTTPException(status_code=403, detail="无效的 API Key。")

@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request):
    """
    核心聊天补全端点。
    为了确保与所有客户端（如 Cherry Studio）的最佳兼容性，
    此端点现在始终以流式（text/event-stream）方式响应。
    """
    request_data = await request.json()
    return StreamingResponse(
        provider.translate_stream(request_data), 
        media_type="text/event-stream"
    )

@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    return await provider.get_models()

@app.get("/", summary="根路径")
def root():
    return {"message": f"欢迎来到 {settings.APP_NAME} v{settings.APP_VERSION}. 服务运行正常。"}
