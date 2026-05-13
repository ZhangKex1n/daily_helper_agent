from __future__ import annotations

import os


def _sanitize_ssl_bundle_env() -> None:
    """若 SSL 证书环境变量指向不存在的路径，则移除，避免 httpx/LangChain 初始化时报 FileNotFoundError。

    Windows 上 Conda/代理工具偶发把 SSL_CERT_FILE 设成无效路径；删除后由系统默认 CA 校验。
    """
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(key)
        if path and not os.path.isfile(path):
            os.environ.pop(key, None)


_sanitize_ssl_bundle_env()

from contextlib import asynccontextmanager

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_server.api.chat import router as chat_router
from api_server.api.compress import router as compress_router
from api_server.api.config_api import router as config_router
from api_server.api.files import router as files_router
from api_server.api.sessions import router as sessions_router
from api_server.api.tokens import router as tokens_router
from config import get_settings
from api_server.graph.agent import agent_manager
from api_server.graph.checkpointer import init_checkpointer_async
from api_server.service.memory_indexer import memory_indexer
from api_server.tools.skills_scanner import refresh_snapshot


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Ensure app logs (and Langfuse diagnostics) are visible under uvicorn.
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    settings = get_settings()
    await init_checkpointer_async()
    # data_dir 指向 miniOpenClaw/data/，包含 workspace/、skills/、knowledge/、memory_module_v1/ 等运行时数据
    refresh_snapshot(settings.data_dir) 

    memory_indexer.configure(settings.data_dir)
    memory_indexer.rebuild_index()
    yield


app = FastAPI(
    title="Mini-OpenClaw API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(files_router, prefix="/api", tags=["files"])
app.include_router(tokens_router, prefix="/api", tags=["tokens"])
app.include_router(compress_router, prefix="/api", tags=["compress"])
app.include_router(config_router, prefix="/api", tags=["config"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
