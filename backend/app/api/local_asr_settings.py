from fastapi import APIRouter, Depends, Request

from ..application.local_asr_settings import (
    LocalASRPaths,
    LocalASRUpdate,
    error,
    require_local_request,
)

router = APIRouter(
    prefix="/api/v1/settings/local-asr", dependencies=[Depends(require_local_request)]
)


@router.get("")
async def get_local_asr(request: Request):
    return request.app.state.local_asr_settings.get()


@router.put("")
async def update_local_asr(request: Request, values: LocalASRUpdate):
    return await request.app.state.local_asr_settings.update(values)


@router.post("/check")
async def check_local_asr(request: Request, values: LocalASRPaths):
    return await request.app.state.local_asr_settings.check(values)


@router.get("/models")
async def get_local_asr_models(request: Request):
    if (
        request.query_params
        or request.headers.get("content-length", "0") != "0"
        or "transfer-encoding" in request.headers
    ):
        raise error(
            422,
            "MODEL_DISCOVERY_ARGUMENTS_NOT_ALLOWED",
            "模型发现仅使用当前后端配置，不接受路径或其他查询参数",
        )
    return await request.app.state.local_asr_models.get()
