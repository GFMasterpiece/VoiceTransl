from pathlib import Path
import os
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .config import load_settings
from .storage import FileSystemProjectStore
from .application import EventBus, JobManager
from .api.routes import router

def create_app(data_dir=None):
    config_path = os.getenv("VOICETRANSL_CONFIG")
    if config_path is None:
        candidate = Path(__file__).resolve().parents[2] / "config" / "app.yaml"
        config_path = candidate if candidate.exists() else None
    settings=load_settings(config_path)
    if data_dir is not None: settings.data_dir=Path(data_dir)
    app=FastAPI(title="VoiceTransl API",version="0.1.0"); store=FileSystemProjectStore(settings.data_dir); store.mark_interrupted(); events=EventBus()
    logger = logging.getLogger("voicetransl.api")
    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        # Keep the documented error contract at the response root instead of
        # exposing FastAPI's internal ``detail`` wrapper.
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": str(detail), "details": None}})
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg", "输入无效"), "type": error.get("type", "value_error")}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "请求参数无效", "details": details}},
        )
    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        # Keep implementation details and tracebacks in server logs.  The API
        # contract always exposes a stable, user-facing error object.
        logger.exception("Unhandled backend exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None}},
        )
    app.state.settings=settings; app.state.store=store; app.state.events=events; app.state.jobs=JobManager(store,events,settings.mock_delay_ms,settings.max_concurrent); app.include_router(router); return app
try:
    app=create_app(os.getenv("VOICETRANSL_DATA_DIR", "./data"))
except OSError:
    app=create_app("/tmp/voicetransl-data")
