from fastapi import APIRouter

api_router = APIRouter(prefix="/api")

# Rutas
from .Radar import router as radar_router
from .PTZ import router as ptz_router
from .RTSP import router as rtsp_router
# from .TrackPTZ import router as trackptz_router

# Migrar rutas a api_router
api_router.include_router(radar_router)
api_router.include_router(ptz_router)
api_router.include_router(rtsp_router)
# api_router.include_router(trackptz_router)