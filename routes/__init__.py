from fastapi import APIRouter

api_router = APIRouter(prefix="/api")

# Rutas
from .Radar import router as radar_router
#from .PTZ import router as ptz_router
from .Usuario import router as usuario_router
from .login import router as login_router
# from .RTSP import router as rtsp_router
# from .TrackPTZ import router as trackptz_router

# Migrar rutas a api_router
api_router.include_router(radar_router)
#api_router.include_router(ptz_router)
api_router.include_router(usuario_router)
api_router.include_router(login_router)
# api_router.include_router(rtsp_router)
# api_router.include_router(trackptz_router)
