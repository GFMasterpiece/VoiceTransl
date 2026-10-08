from fastapi import APIRouter, Depends, Request

from ..application.local_asr_settings import (
    LocalASRPaths,
    LocalASRUpdate,
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
