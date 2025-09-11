from pyproj import Geod
from typing import Optional, List
from fastapi import APIRouter
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv
import math
import requests
import time
import json
import asyncio
import websockets
import os
from .PTZ import absolute_move_camera

load_dotenv()

# --- 1. CONFIGURACIÓN Y CALIBRACIÓN DE LA CÁMARA ---
CAM_LAT = float(os.getenv("RADAR_LAT"))  # Latitud de la cámara
CAM_LON = float(os.getenv("RADAR_LON"))  # Longitud de la cámara
CAM_ALT = float(os.getenv("CAM_ALT"))  # Altitud en metros sobre el nivel del mar 
# -- Calibracion punto 0.0 de la camara o hacia donde va a quedar apuntando la camara por defecto
CAM_HEADING_DEGREES = 200.0

# -- Calibracion dependiendo de la camara, para esta en especifico no es necesario cambiar mas
MAX_PAN_DEGREES = 180.0
MIN_ZOOM_DISTANCE = 20.0
MAX_ZOOM_DISTANCE = 1000.0

# --- CONFIGURACIÓN DE SERVICIOS EXTERNOS ---
# URL de tu API de control de cámara Absolute_move
# URL del WebSocket que provee los datos del radar Cambiar a API que traiga la informacion de la zona restringida 
RADAR_WEBSOCKET_URL = "ws://10.30.7.14:8000/api/solo_punto"

# --- Inicialización de la API ---
router = APIRouter()


# --- Modelos Pydantic para la Validación de Datos ---
class Punto(BaseModel):
    latitud: float
    longitud: float
    azimut: Optional[float] = None
    distancia: Optional[float] = None
    id: Optional[int] = None
    type: Optional[int] = None


class TrackData(BaseModel):
    puntos: List[Punto]
    radar: Optional[dict] = None
    poligono: Optional[dict] = None


# --- Lógica de Cálculo y Control ---
def calculate_ptz_for_gps_target(
    target_lat: float,
    target_lon: float,
    target_alt: Optional[float] = None,
    target_azimuth: Optional[float] = None,
):
    """Calcula los valores normalizados de Pan, Tilt y Zoom para un objetivo."""
    if target_alt is None:
        target_alt = CAM_ALT

    geod = Geod(ellps="WGS84")

    if target_azimuth is not None:
        fwd_azimuth = target_azimuth
        _, _, distance_2d = geod.inv(
            lons1=CAM_LON, lats1=CAM_LAT, lons2=target_lon, lats2=target_lat
        )
    else:
        fwd_azimuth, _, distance_2d = geod.inv(
            lons1=CAM_LON, lats1=CAM_LAT, lons2=target_lon, lats2=target_lat
        )

    delta_altitude = target_alt - CAM_ALT
    elevation_angle_deg = math.degrees(math.atan2(delta_altitude, distance_2d))
    pan_angle_final = fwd_azimuth - CAM_HEADING_DEGREES

    if pan_angle_final > 180:
        pan_angle_final -= 360
    elif pan_angle_final < -180:
        pan_angle_final += 360

    normalized_pan_raw = pan_angle_final / MAX_PAN_DEGREES
    physical_tilt_angle = max(-90.0, min(0.0, elevation_angle_deg))
    normalized_tilt_raw = (physical_tilt_angle + 90.0) / 90.0
    safe_limit = 0.9999
    normalized_pan = max(-safe_limit, min(safe_limit, normalized_pan_raw))
    normalized_tilt = max(0.0, min(safe_limit, normalized_tilt_raw))
    distance_3d = math.sqrt(distance_2d**2 + delta_altitude**2)

    if distance_3d <= MIN_ZOOM_DISTANCE:
        normalized_zoom = 0.0
    elif distance_3d >= MAX_ZOOM_DISTANCE:
        normalized_zoom = 1.0
    else:
        normalized_zoom = (distance_3d - MIN_ZOOM_DISTANCE) / (
            MAX_ZOOM_DISTANCE - MIN_ZOOM_DISTANCE
        )

    return {"pan": normalized_pan, "tilt": normalized_tilt, "zoom": normalized_zoom}


async def radar_websocket_client(message):

    camera_id_to_control = "cam"  # Nombre de la camara o id 


    data = json.loads(message)
    track_data = TrackData(**data)  # Validar con Pydantic

    if not track_data.puntos:
        print("   - El mensaje no contenía puntos para procesar.")

    # Procesar cada punto recibido en el mensaje
    for i, point in enumerate(track_data.puntos):
        print(
            f"\n--- Procesando Punto de Track #{i + 1} de la trama ---"
        )

        ptz_commands = calculate_ptz_for_gps_target(
            target_lat=point.latitud,
            target_lon=point.longitud,
            target_alt=None,  # Asumiendo que no viene altitud
            target_azimuth=point.azimut,
        )

        payload = {
            "pan": round(ptz_commands["pan"], 4),
            "tilt": round(ptz_commands["tilt"], 4),
            # "zoom": round(ptz_commands["zoom"], 4) este lo tengo desactivado por 
        }

        print(f"   - Comandos PTZ calculados: {payload}")
        absolute_move_camera(camera_id_to_control, payload)

        # Si hay más de un punto en la misma trama, esperar 10 segundos
        if i < len(track_data.puntos) - 1:
            print(
                f"\n...Esperando 10 segundos para el siguiente punto de la misma trama..."
            )


# --- Eventos de Ciclo de Vida de la API ---
@router.on_event("startup")
async def startup_event():
    """Al iniciar la API, se lanza el cliente WebSocket como una tarea de fondo."""
    print("🚀 Iniciando servicio de seguimiento...")
    asyncio.create_task(radar_websocket_client())
    
    #Zona critica - roja
    #Zona no alerta - gris
    #Zona alerta atencion - amarilla