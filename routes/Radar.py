from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv, set_key, find_dotenv
from pymongo import MongoClient
from pydantic import BaseModel
from typing import List, Optional
from .TrackPTZ import radar_websocket_client
import websockets
import asyncio
import os
import json
import re
import math

router = APIRouter()
load_dotenv() 

# URL del endpoint del radar
RADAR_WEBSOCKET_URL = os.getenv("RADAR_WEBSOCKET_URL")

DBMONGO_URI = MongoClient(os.getenv("BDMONGO_URI"))
ASTRADAR_BD = DBMONGO_URI["astradar"]
DATA_COLLECTION = ASTRADAR_BD["data"]
CONFIGURACION_DATA_COLLECTION = ASTRADAR_BD["configuracion_radar"]
ZONAS_COLLECTION = ASTRADAR_BD["zonas"]

# Posición del radar
RADAR_LAT = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"].get("latitud") #float(os.getenv("RADAR_LAT"))
RADAR_LON = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"].get("longitud")#float(os.getenv("RADAR_LON"))
RADAR_RADIO_M = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"].get("radar_radio_m")#float(os.getenv("RADAR_RADIO_M")) 
METROS_POR_GRADO_LATITUD = float(os.getenv("METROS_POR_GRADO_LATITUD"))
ANGULO_ROTACION = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"].get("angulo_rotacion") #float(os.getenv("ANGULO_ROTACION"))
GRADO_INCLINACION = 40

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Si falla, el cliente probablemente se desconectó
                pass

manager = ConnectionManager()

# Funcion encargada de convertir los puntos cardinales en latitud y longitud
# Los datos transformados dependen totalmente de la latidud y longitud del radar
def convertir_cartesiano_a_geografico(x_meters: float, y_meters: float) -> tuple:
    delta_lat = y_meters / METROS_POR_GRADO_LATITUD
    
    lat_rad = math.radians(RADAR_LAT)
    meters_per_lon_degree = METROS_POR_GRADO_LATITUD * math.cos(lat_rad)
    delta_lon = x_meters / meters_per_lon_degree
    
    new_lat = RADAR_LAT + delta_lat
    new_lon = RADAR_LON + delta_lon
    
    return (new_lat, new_lon)

def convertir_cartesiano_a_geografico_configuracion(x_meters: float, y_meters: float, posiciones: dict) -> tuple:
    delta_lat = y_meters / METROS_POR_GRADO_LATITUD
    
    lat_rad = math.radians(posiciones["radar_lat"])
    meters_per_lon_degree = METROS_POR_GRADO_LATITUD * math.cos(lat_rad)
    delta_lon = x_meters / meters_per_lon_degree
    
    new_lat = posiciones["radar_lat"] + delta_lat
    new_lon = posiciones["radar_lon"] + delta_lon
    
    return (new_lat, new_lon)

# Mueve la función de rotación a un lugar reutilizable
def rotate_point(x: float, y: float, angle_degrees: float) -> tuple:
    """Aplica la rotación a un punto (x, y)."""
    angle_rad = math.radians(angle_degrees)
    cos_theta = math.cos(angle_rad)
    sin_theta = math.sin(angle_rad)
    x_rotated = x * cos_theta - y * sin_theta
    y_rotated = x * sin_theta + y * cos_theta
    return (x_rotated, y_rotated)

# Calcula los vértices del polígono de detección del radar.
def calcular_vertices_poligono(
    RADAR_RADIO_M, 
    ANGULO_ROTACION, 
    ANGULO_APERTURA, 
    posiciones: dict
) -> list:
    """
    Calcula los tres vértices del polígono de detección del radar en forma de cono.
    """
    # Convierte el alcance del radar a un nombre más claro
    alcance_radar = RADAR_RADIO_M
    
    # El primer vértice es el centro del radar (0,0) en coordenadas cartesianas
    vertices = [(0, 0)]

    # Calcula los ángulos de inicio y fin del cono.
    # El ángulo de rotación se aplica al centro del cono.
    angulo_inicio = ANGULO_ROTACION - ANGULO_APERTURA / 2
    angulo_fin = ANGULO_ROTACION + ANGULO_APERTURA / 2

    # Calcula las coordenadas cartesianas del segundo y tercer vértice.
    # Vértice 2: punto en el límite del alcance con el ángulo de inicio.
    x2 = alcance_radar * math.cos(math.radians(90 - angulo_inicio))
    y2 = alcance_radar * math.sin(math.radians(90 - angulo_inicio))
    vertices.append((x2, y2))
    
    # Vértice 3: punto en el límite del alcance con el ángulo de fin.
    x3 = alcance_radar * math.cos(math.radians(90 - angulo_fin))
    y3 = alcance_radar * math.sin(math.radians(90 - angulo_fin))
    vertices.append((x3, y3))
    
    rotated_vertices_geographic = []
    
    for x, y in vertices:
        # Convierte el vértice cartesiano a coordenadas geográficas
        if posiciones:
            lat, lon = convertir_cartesiano_a_geografico_configuracion(x, y, posiciones)
        else:
            lat, lon = convertir_cartesiano_a_geografico(x, y)
        
        rotated_vertices_geographic.append([lat, lon])

    return rotated_vertices_geographic



def punto_en_poligono(point: tuple, polygon: list) -> bool:
    """
    Verifica si un punto (lat, lon) está dentro de un polígono.
    Implementa el algoritmo de cruce de rayos (Ray Casting).
    """
    x, y = point
    n = len(polygon)
    inside = False
    
    p1x, p1y = polygon[0]
    for i in range(n + 1):
        p2x, p2y = polygon[i % n]
        
        # Verifica si el punto está en el borde del polígono
        if (x == p1x and y == p1y) or (x == p2x and y == p2y):
            return True
            
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
        
    return inside

PRIORIDAD_ZONAS = {
    "exterior": 1,
    "atencion": 2,
    "interior": 3,
    "modulo": 4, 
}

async def radar_listener_task():
    while True:
        try:
            # Tip: Mueve la carga de config fuera del 'while True' interno 
            # para no saturar la base de datos en cada punto recibido.
            radar_config = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"]
            ANGULO_ROTACION = float(radar_config.get("angulo_rotacion", 0))
            ZONAS_DE_DETECCION = list(ZONAS_COLLECTION.find({}, {"_id": 0}))

            async with websockets.connect(RADAR_WEBSOCKET_URL, ping_interval=30, ping_timeout=60) as radar_ws:
                print("Conectado al radar (Conexión Única)")
                while True:
                    radar_data_raw = await radar_ws.recv()
                    
                    # --- CRÍTICO: Limpiar el string antes de convertir a JSON ---
                    processed_str = re.sub(r'(\w+):', r'"\1":', radar_data_raw)
                    try:
                        radar_data_json = json.loads(processed_str)
                        # Pasar el JSON ya convertido a la lógica
                        processed_data = await process_radar_logic(radar_data_json, ANGULO_ROTACION, ZONAS_DE_DETECCION)
                        
                        if processed_data:
                            await manager.broadcast(processed_data)
                    except websockets.ConnectionClosed:
                        print("Conexión con el radar cerrada. Reintentando...")
                        break
                    
        except Exception as e:
            print(f"Error en conexión radar: {e}. Reintentando en 5s...")
            await asyncio.sleep(5)

async def process_radar_logic(radar_data_json, ANGULO_ROTACION, ZONAS_DE_DETECCION):
        if "data" not in radar_data_json or not isinstance(radar_data_json["data"], list):
            return None
        
        processed_points = []
        
        for point_data in radar_data_json["data"]:
            x_meters = float(point_data.get("x", 0))
            y_meters = float(point_data.get("y", 0))
            
            x_adjusted = -x_meters
            y_adjusted = -y_meters
            anguloTotalRotacion = (ANGULO_ROTACION + GRADO_INCLINACION) % 360
            x_rotated, y_rotated = rotate_point(x_adjusted, y_adjusted, anguloTotalRotacion)
            latitud, longitud = convertir_cartesiano_a_geografico(x_rotated, y_rotated)
            
            zona_detectada = None
            prioridad_actual = 0
            
            for zona in ZONAS_DE_DETECCION:
                zona_poligono = [tuple(c) for c in zona.get("coordinates", [])]
                
                if punto_en_poligono((latitud, longitud), zona_poligono):
                    categoria_zona = zona.get("category")
                    prioridad_zona = PRIORIDAD_ZONAS.get(categoria_zona, 0)
                    
                    if prioridad_zona > prioridad_actual:
                        prioridad_actual = prioridad_zona
                        zona_detectada = zona
            
            puntos_a_enviar = {
                "id": point_data.get("id"),
                "type": point_data.get("type"),
                "latitud": latitud,
                "longitud": longitud,
                "azimut": float(point_data.get("a")),
                "distancia": float(point_data.get("d"))
            }
            
            if zona_detectada:
                puntos_a_enviar["zona_alerta"] = {
                    "id": zona_detectada.get("id"),
                    "name": zona_detectada.get("name"),
                    "color": zona_detectada.get("color"),
                    "category": zona_detectada.get("category")
                }
                
                # Llama al cliente de websocket para mover la cámara
                # La lógica aquí es que si se detectó una zona (la más prioritaria), se activa la cámara
                mensaje_para_camara = {
                    "puntos": puntos_a_enviar
                }
                mensaje_json_str = json.dumps(mensaje_para_camara)
                await radar_websocket_client(mensaje_json_str)

            processed_points.append(puntos_a_enviar)
        
        processed_data = {
            "puntos": processed_points
        }
        
        return processed_data
    
@router.websocket("/radar")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Mantener la conexión abierta esperando mensajes del cliente (si los hay)
            # o simplemente bloqueado hasta que se cierre.
            await websocket.receive_text() 
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# @router.websocket("/radar")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
    
#     # Obtener configuración y zonas una sola vez al inicio
#     try:
#         radar_config = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})["radar"]
#         ANGULO_ROTACION = float(radar_config.get("angulo_rotacion", 0))
#     except (TypeError, KeyError, ValueError):
#         print("Error: No se pudo obtener la configuración del radar o el ángulo de rotación no es numérico.")
#         await websocket.close()
#         return
    
#     ZONAS_DE_DETECCION = list(ZONAS_COLLECTION.find({}, {"_id": 0}))

#     while True:
#         try:
#             async with websockets.connect(
#                 RADAR_WEBSOCKET_URL,
#                 ping_interval=30,
#                 ping_timeout=60
#             ) as radar_ws:
#                 print("Conectado al radar. Iniciando retransmisión de datos...")
#                 while True:
#                     radar_data_raw = await radar_ws.recv()
#                     processed_data = re.sub(r'(\w+):', r'"\1":', radar_data_raw)
                    
#                     try:
#                         radar_data_json = json.loads(processed_data)
                        
#                         if "data" in radar_data_json and isinstance(radar_data_json["data"], list):
#                             processed_points = []
                            
#                             for point_data in radar_data_json["data"]:
#                                 x_meters = float(point_data.get("x", 0))
#                                 y_meters = float(point_data.get("y", 0))
                                
#                                 x_adjusted = -x_meters
#                                 y_adjusted = -y_meters
#                                 x_rotated, y_rotated = rotate_point(x_adjusted, y_adjusted, ANGULO_ROTACION + GRADO_INCLINACION)
#                                 latitud, longitud = convertir_cartesiano_a_geografico(x_rotated, y_rotated)
                                
#                                 zona_detectada = None
#                                 prioridad_actual = 0
                                
#                                 for zona in ZONAS_DE_DETECCION:
#                                     zona_poligono = [tuple(c) for c in zona.get("coordinates", [])]
                                    
#                                     if punto_en_poligono((latitud, longitud), zona_poligono):
#                                         categoria_zona = zona.get("category")
#                                         prioridad_zona = PRIORIDAD_ZONAS.get(categoria_zona, 0)
                                        
#                                         if prioridad_zona > prioridad_actual:
#                                             prioridad_actual = prioridad_zona
#                                             zona_detectada = zona
                                
#                                 puntos_a_enviar = {
#                                     "id": point_data.get("id"),
#                                     "type": point_data.get("type"),
#                                     "latitud": latitud,
#                                     "longitud": longitud,
#                                     "azimut": float(point_data.get("a")),
#                                     "distancia": float(point_data.get("d"))
#                                 }
                                
#                                 if zona_detectada:
#                                     puntos_a_enviar["zona_alerta"] = {
#                                         "id": zona_detectada.get("id"),
#                                         "name": zona_detectada.get("name"),
#                                         "color": zona_detectada.get("color"),
#                                         "category": zona_detectada.get("category")
#                                     }
                                    
#                                     # Llama al cliente de websocket para mover la cámara
#                                     # La lógica aquí es que si se detectó una zona (la más prioritaria), se activa la cámara
#                                     mensaje_para_camara = {
#                                         "puntos": puntos_a_enviar
#                                     }
#                                     mensaje_json_str = json.dumps(mensaje_para_camara)
#                                     await radar_websocket_client(mensaje_json_str)

#                                 processed_points.append(puntos_a_enviar)
                            
#                             processed_data = {
#                                 "puntos": processed_points
#                             }
#                             await websocket.send_json(processed_data)
                    
#                     except json.JSONDecodeError:
#                         print(f"Datos recibidos no son un JSON válido: {radar_data_raw}")
                    
#         except websockets.exceptions.ConnectionClosed as e:
#             if e.code == 1000:
#                 print("El cliente se ha desconectado o la conexión con el radar se cerró normalmente.")
#                 return
#             else:
#                 print(f"Conexión perdida. Razón: {e}. Reintentando...")
#                 await asyncio.sleep(0.001)

#         except ConnectionRefusedError:
#             print("Conexión con el radar rechazada. Reintentando en 1 segundo...")
#             await asyncio.sleep(1)
        
#         except Exception as e:
#             print(f"Error inesperado: {e}. Cerrando conexión...")
#             break
            
#     await websocket.close()



@router.websocket("/solo_punto")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ZONAS_DE_DETECCION = list(ZONAS_COLLECTION.find({}, {"_id": 0}))
    print(ZONAS_DE_DETECCION)
    while True:
        try:
            async with websockets.connect(
                RADAR_WEBSOCKET_URL,
                ping_interval=30,
                ping_timeout=60
            ) as radar_ws:
                print("Conectado al radar. Iniciando retransmisión de datos...")
                processed_points = []

                while True:
                    radar_data_raw = await radar_ws.recv()
                    # Limpia los datos para que sean un JSON válido
                    processed_data_str = re.sub(r'(\w+):', r'"\1":', radar_data_raw)
                    # Elimina el sufijo de tiempo que no es JSON
                    processed_data_str = re.sub(r'\d{3}\d{1,2}:\d{2}:\d{2}\.\d{3}$', '', processed_data_str)
                    
                    try:
                        radar_data_json = json.loads(processed_data_str)
                        
                        if "data" in radar_data_json and isinstance(radar_data_json["data"], list) and radar_data_json["data"]:
                            
                            # Selecciona el primer objetivo de la lista
                            first_point_data = radar_data_json["data"][0] 
                            
                            # Extrae las coordenadas cartesianas
                            x_meters = first_point_data.get("x", 0)
                            y_meters = first_point_data.get("y", 0)

                            # Aplica la rotación al punto
                            x_rotated, y_rotated = rotate_point(x_meters, y_meters, ANGULO_ROTACION)

                            # Convierte a coordenadas geográficas
                            latitud, longitud = convertir_cartesiano_a_geografico(x_rotated, y_rotated)
                            
                            # Verifica si el punto está dentro de alguna zona de peligro
                            zona_detectada = None
                            # Itera sobre las zonas cargadas
                            for zona in ZONAS_DE_DETECCION:
                                # El polígono debe ser una lista de tuplas (lat, lon)
                                zona_poligono = [tuple(c) for c in zona.get("coordinates", [])]
                                
                                # Usa la función de detección de colisión
                                if punto_en_poligono((latitud, longitud), zona_poligono):
                                    zona_detectada = zona
                                    break  # Sal de este bucle si encuentras una zona

                            # Estructura el JSON de salida con las coordenadas del único punto
                            puntos_a_enviar = {
                                "id": first_point_data.get("id"),
                                "type": first_point_data.get("type"),
                                "latitud": latitud,
                                "longitud": longitud,
                                "azimut": first_point_data.get("a"),
                                "distancia": first_point_data.get("d")
                            }
                            
                            # Si se detectó una zona, agrégala al objeto del punto
                            if zona_detectada:
                                puntos_a_enviar["zona_alerta"] = {
                                    "id": zona_detectada.get("id"),
                                    "name": zona_detectada.get("name"),
                                    "color": zona_detectada.get("color"),
                                    "category" : zona_detectada.get("category")
                                }
                                
                            processed_points.append(puntos_a_enviar)
                            
                            final_data_to_send = {
                                "puntos": processed_points # Coloca el único punto en una lista para mantener el formato
                            }
                                
                            await websocket.send_json(final_data_to_send)
                            
                    except json.JSONDecodeError:
                        print(f"Datos recibidos no son un JSON válido: {radar_data_raw}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            if e.code == 1000:
                print("El cliente se ha desconectado o la conexión con el radar se cerró normalmente.")
                return
            else:
                print(f"Conexión perdida. Razón: {e}. Reintentando...")
                await asyncio.sleep(0.001)

        except ConnectionRefusedError:
            print("Conexión con el radar rechazada. Reintentando en 1 segundo...")
            await asyncio.sleep(1)
        
        except Exception as e:
            print(f"Error inesperado: {e}. Cerrando conexión...")
            break
            
    await websocket.close()

class RadarConfig(BaseModel):
    radar_lat: Optional[str]
    radar_lon: Optional[str]
    radar_radio_m: Optional[str]
    angulo_rotacion: Optional[str] # Nuevo campo para el ángulo de rotación
    
@router.post("/configurar_radar")
async def configurar_radar(config: RadarConfig):
    try:
        # Encuentra la ruta del archivo .env
        dotenv_file = find_dotenv()
        
        # Actualiza las variables en el archivo .env
        set_key(dotenv_file, "RADAR_LAT", str(config.radar_lat))
        set_key(dotenv_file, "RADAR_LON", str(config.radar_lon))
        set_key(dotenv_file, "RADAR_RADIO_M", str(config.radar_radio_m))
        set_key(dotenv_file, "ANGULO_ROTACION", str(config.angulo_rotacion))
        
        posiciones = {
            "radar_lat": float(config.radar_lat),
            "radar_lon": float(config.radar_lon)
        }
        
        # Actualizar lso valores en la base de datos 
        CONFIGURACION_DATA_COLLECTION.update_one(
            {}, 
            {
                "$set": {
                    "radar":{
                        "latitud": float(config.radar_lat),
                        "longitud": float(config.radar_lon),
                        "radar_radio_m": float(config.radar_radio_m),
                        "angulo_rotacion": float(config.angulo_rotacion)
                    },
                    "poligono": {
                        "vertices": calcular_vertices_poligono(float(config.radar_radio_m), float(config.angulo_rotacion), 45, posiciones)
                    }
                }
            },
            upsert=True
        )
        
        # Retorna una respuesta de éxito
        return {"mensaje": "Configuración del radar actualizada con éxito."}
    
    except Exception as e:
        # Maneja cualquier error que pueda ocurrir durante la escritura
        return {"error": f"Ocurrió un error al actualizar la configuración: {e}"}
    

# Zonas de deteccion
class nuevaZona(BaseModel):
    name: str
    category: str
    color : str
    coordinates: List[List[float]]

# Cargar archivo json de zonas
# with open("zonas.json", "r") as f:
#     ZONAS_DE_DETECCION = json.load(f)
    
@router.get("/zonas")
async def obtener_zonas_deteccion():
        
    CONFIGURACION_RADAR = CONFIGURACION_DATA_COLLECTION.find_one({}, {"_id": 0})
    ZONAS_DE_DETECCION = list(ZONAS_COLLECTION.find({}, {"_id": 0}))
    
    return {
        "radar": {
            "latitud": CONFIGURACION_RADAR["radar"].get("latitud"),
            "longitud": CONFIGURACION_RADAR["radar"].get("longitud")
            },
        "poligono": {
            "vertices": CONFIGURACION_RADAR["poligono"].get("vertices")
            },
        "zonas": ZONAS_DE_DETECCION
        }
    
#crear zonas
@router.post("/zonas_deteccion")
async def agregar_zona(zona: nuevaZona): 

    ultimo_documento = ZONAS_COLLECTION.find_one(
            sort=[("_id", -1)]
        )
    
    if ultimo_documento:
        # 3. Convierte el ObjectId a una cadena de texto para que FastAPI lo pueda serializar
        ultimo_documento["_id"] = str(ultimo_documento["_id"])
        
    if ultimo_documento and "id" in ultimo_documento:
            nuevo_id = int(ultimo_documento["id"]) + 1
    else:
        nuevo_id = 1

    # 2. Crear el nuevo diccionario de zona con el ID
    nueva_zona_con_id = {
        "id": nuevo_id,
        "name": zona.name,
        "category": zona.category,
        "color": zona.color,
        "coordinates": zona.coordinates
    }
    
    # 3. Insertar el nuevo documento en la colección de zonas
    ZONAS_COLLECTION.insert_one(nueva_zona_con_id)    
    
    return {
            "msg": "Zona creada con éxito."
            }


@router.delete("/zonas/{zona_id}")
async def eliminar_zona(zona_id):
        
    ZONAS_COLLECTION.delete_one({"id": int(zona_id)})
    
    return {
        "msg": "Zona eliminada de JSON y MongoDB."
        }
