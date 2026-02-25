from pymongo import MongoClient
from pyproj import Geod
from typing import Optional, List
from pydantic import BaseModel
from .PTZ import absolute_move_camera
from . import Estado as state
import math
import json
import os

# Datos Base de datos
DBMONGO_URI = MongoClient(os.getenv("BDMONGO_URI"))
ASTRADAR_BD = DBMONGO_URI["astradar"]
CONFIGURACION_DATA_COLLECTION = ASTRADAR_BD["configuracion_radar"]

# --- 1. CONFIGURACIÓN Y CALIBRACIÓN DE LA CÁMARA ---
CAM_LAT = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"].get("latitud") # Latitud de la cámara
CAM_LON = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"].get("longitud") # Longitud de la cámara
CAM_ALT = 60.0  # Altitud en metros sobre el nivel del mar (MSL)
CAM_HEADING_DEGREES = 200.0
MAX_PAN_DEGREES = 180.0
MIN_ZOOM_DISTANCE = 20.0
MAX_ZOOM_DISTANCE = 1000.0

# --- 2. CALIBRACIÓN AVANZADA DE MONTAJE ---
ENABLE_LEAN_CORRECTION = True
LEAN_ANGLE_DEGREES = 0.0
LEAN_DIRECTION_DEGREES = 0.0
ZOOM_TILT_OFFSET_DEGREES = 0.0

# --- 3. ESTIMACIÓN DE ALTITUD (OPCIONAL) ---
# Activa el cálculo de la altitud del objetivo a partir de la distancia del radar.
# La suposición es que el objetivo siempre estará POR DEBAJO de la cámara.
ESTIMATE_ALT_FROM_DISTANCE = True


# --- Modelos Pydantic ---
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


# --- FUNCIÓN DE NORMALIZACIÓN DE TILT CON NUEVO MAPEO ---
def normalize_tilt_new_mapping(physical_angle_deg: float) -> float:
    physical_angle_deg = max(-90.0, min(90.0, physical_angle_deg))
    if physical_angle_deg >= 0:
        normalized_value = 1.0 - (physical_angle_deg / 180.0)
    else:
        normalized_value = 1.0 + (physical_angle_deg / 60.0)
    return max(-0.5, min(1.0, normalized_value))


# --- Lógica de Cálculo y Control ---
def calculate_ptz_for_gps_target(
    target_lat: float,
    target_lon: float,
    target_alt: Optional[float] = None,
    target_azimuth: Optional[float] = None,
    target_slant_distance: Optional[float] = None,
):
    geod = Geod(ellps="WGS84")
    fwd_azimuth, _, distance_2d = geod.inv(
        lons1=CAM_LON, lats1=CAM_LAT, lons2=target_lon, lats2=target_lat
    )
    if target_azimuth is not None:
        fwd_azimuth = target_azimuth

    # --- ✨ INICIO: LÓGICA DE ALTITUD CORREGIDA ✨ ---
    delta_altitude = 0.0
    if target_alt is not None:
        delta_altitude = target_alt - CAM_ALT
    elif ESTIMATE_ALT_FROM_DISTANCE and target_slant_distance is not None:
        # Se verifica si la distancia inclinada es menor que la horizontal (imposible/error).
        # Si esto ocurre, se asume diferencia de altura cero para evitar un error matemático.
        if target_slant_distance < distance_2d:
            delta_altitude = 0.0
            # Descomenta la siguiente línea si quieres ver una advertencia en la consola
            # print(f"⚠️ ADVERTENCIA: Distancia inclinada ({target_slant_distance:.2f}m) < horizontal ({distance_2d:.2f}m). Se asume altitud 0.")
        else:
            # Si la distancia es válida (>=), se procede con Pitágoras.
            delta_alt_squared = target_slant_distance**2 - distance_2d**2
            delta_altitude_magnitude = math.sqrt(delta_alt_squared)
            delta_altitude = -delta_altitude_magnitude
    # --- ✨ FIN: LÓGICA DE ALTITUD CORREGIDA ✨ ---

    elevation_angle_deg = math.degrees(math.atan2(delta_altitude, distance_2d))
    pan_angle_final = fwd_azimuth - CAM_HEADING_DEGREES

    # --- Corrección de inclinación (Lean Correction) ---
    corrected_pan_angle = pan_angle_final
    corrected_elevation_angle = elevation_angle_deg
    if ENABLE_LEAN_CORRECTION and LEAN_ANGLE_DEGREES != 0.0:
        pan_rad, tilt_rad = (
            math.radians(pan_angle_final),
            math.radians(elevation_angle_deg),
        )
        lean_angle_rad, lean_direction_rad = (
            math.radians(LEAN_ANGLE_DEGREES),
            math.radians(LEAN_DIRECTION_DEGREES),
        )

        sin_new_tilt = math.sin(tilt_rad) * math.cos(lean_angle_rad) - math.cos(
            tilt_rad
        ) * math.sin(lean_angle_rad) * math.cos(pan_rad - lean_direction_rad)
        sin_new_tilt = max(-1.0, min(1.0, sin_new_tilt))
        new_tilt_rad = math.asin(sin_new_tilt)

        y = math.sin(pan_rad - lean_direction_rad) * math.cos(tilt_rad)
        x = math.cos(pan_rad - lean_direction_rad) * math.cos(tilt_rad) * math.cos(
            lean_angle_rad
        ) + math.sin(tilt_rad) * math.sin(lean_angle_rad)
        new_pan_rad = math.atan2(y, x) + lean_direction_rad

        corrected_pan_angle, corrected_elevation_angle = (
            math.degrees(new_pan_rad),
            math.degrees(new_tilt_rad),
        )

    # --- Ajuste de Pan, Zoom y Tilt Final ---
    if corrected_pan_angle > 180:
        corrected_pan_angle -= 360
    elif corrected_pan_angle < -180:
        corrected_pan_angle += 360

    safe_limit_pan = 0.9999
    normalized_pan = max(
        -safe_limit_pan, min(safe_limit_pan, corrected_pan_angle / MAX_PAN_DEGREES)
    )

    distance_3d = math.sqrt(distance_2d**2 + delta_altitude**2)
    if distance_3d <= MIN_ZOOM_DISTANCE:
        normalized_zoom = 0.0
    elif distance_3d >= MAX_ZOOM_DISTANCE:
        normalized_zoom = 1.0
    else:
        normalized_zoom = (distance_3d - MIN_ZOOM_DISTANCE) / (
            MAX_ZOOM_DISTANCE - MIN_ZOOM_DISTANCE
        )

    final_elevation_angle = corrected_elevation_angle + (
        normalized_zoom * ZOOM_TILT_OFFSET_DEGREES
    )

    normalized_tilt = normalize_tilt_new_mapping(final_elevation_angle)

    return {"pan": normalized_pan, "tilt": normalized_tilt, "zoom": normalized_zoom}

class AbsoluteMoveRequest(BaseModel):
    pan: Optional[float] = None
    tilt: Optional[float] = None
    zoom: Optional[float] = None

async def radar_websocket_client(message):
    if state.manual_override:
        print("⏸️ Control manual activo. Se ignorará el comando automático.")
        return

    try:
        data = json.loads(message)
        # Asegura que 'data["puntos"]' sea una lista, incluso si es un solo diccionario.
        if "puntos" in data:
            # Si 'puntos' es un diccionario, lo envuelve en una lista
            if isinstance(data["puntos"], dict):
                data["puntos"] = [data["puntos"]]
            # Si 'puntos' es una lista de listas, toma la primera
            elif isinstance(data["puntos"], list) and data["puntos"] and isinstance(data["puntos"][0], list):
                data["puntos"] = data["puntos"][0]

        try:
            # Esto asumirá que TrackData puede manejar una lista o un solo objeto
            track_data = TrackData(**data)
            
            if not track_data.puntos:
                print(" - El mensaje no contenía puntos para procesar.")
                return 

            for i, point in enumerate(track_data.puntos):

                # Tu lógica de cálculo y movimiento de la cámara aquí
                ptz_commands = calculate_ptz_for_gps_target(
                    target_lat=point.latitud,
                    target_lon=point.longitud,
                    target_azimuth=point.azimut,
                    target_slant_distance=point.distancia,
                )

                payload = {
                    "pan": round(ptz_commands["pan"], 4),
                    "tilt": round(ptz_commands["tilt"], 4),
                }
                
                
                move_request = AbsoluteMoveRequest(**payload)


                absolute_move_camera("camara_principal",move_request)

        except Exception as e:
            print(f"Error inesperado durante el procesamiento de datos: {e}")
    except Exception as e:
        print(f"Error inesperado durante el procesamiento de datos: {e}")




