import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.capabilities import router as capabilities_router
from .api.routes import router
from .api.workflow import router as workflow_router
from .application import EventBus, JobManager
from .config import load_settings
from .domain.schema import SCHEMA_VERSION, SchemaVersionError
from .logging_utils import configure_logging
from .storage import FileSystemProjectStore


def create_app(data_dir=None):
    config_path = os.getenv("VOICETRANSL_CONFIG")
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "config" / "app.yaml"
    settings = load_settings(config_path)
    if data_dir is not None:
        settings.data_dir = Path(data_dir)
    # An injected data root is used by embedded callers and isolated tests;
    # avoid binding a process-global log handler to that shorter-lived root.
    configure_logging(settings.log_level, settings.data_dir if data_dir is None else None)
    store = FileSystemProjectStore(settings.data_dir)
    events = EventBus()
    jobs = JobManager(
        store,
        events,
        settings.mock_delay_ms,
        settings.max_concurrent,
        False,
    )

    @asynccontextmanager
    async def lifespan(_app):
        # Scan only when the server actually starts. Constructing an app for
        # tests or tooling must not mark a live job from another server.
        store.mark_interrupted()
        store.mark_workflow_runs_interrupted()
        try:
            yield
        finally:
            await jobs.shutdown()

    app = FastAPI(title="VoiceTransl API", version="0.1.0", lifespan=lifespan)
    logger = logging.getLogger("voicetransl.api")

    def schema_version_response() -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "UNSUPPORTED_SCHEMA_VERSION",
                    "message": (f"不支持该数据版本，当前仅支持 schema_version {SCHEMA_VERSION!r}"),
                    "details": None,
                }
            },
        )

    @app.exception_handler(SchemaVersionError)
    async def schema_version_error(_request: Request, exc: SchemaVersionError):
        logger.warning("Unsupported schema version for %s: %r", exc.document, exc.version)
        return schema_version_response()

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        # Keep the documented error contract at the response root instead of
        # exposing FastAPI's internal ``detail`` wrapper.
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": str(detail), "details": None}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        for error in exc.errors():
            context = error.get("ctx")
            if isinstance(context, dict) and isinstance(context.get("error"), SchemaVersionError):
                return schema_version_response()
        details = [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "输入无效"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "VALIDATION_ERROR", "message": "请求参数无效", "details": details}
            },
        )

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        # Keep implementation details and tracebacks in server logs.  The API
        # contract always exposes a stable, user-facing error object.
        logger.exception("Unhandled backend exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None}
            },
        )

    app.state.settings = settings
    app.state.store = store
    app.state.events = events
    app.state.jobs = jobs
    app.include_router(router)
    app.include_router(capabilities_router)
    app.include_router(workflow_router)
    return app


app = create_app()
