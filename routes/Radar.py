from fastapi import APIRouter, WebSocket
from dotenv import load_dotenv, set_key, find_dotenv
from pymongo import MongoClient
from pydantic import BaseModel
from typing import List, Optional
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

# Mueve la función de rotación a un lugar reutilizable
def rotate_point(x: float, y: float, angle_degrees: float) -> tuple:
    """Aplica la rotación a un punto (x, y)."""
    angle_rad = math.radians(angle_degrees)
    cos_theta = math.cos(angle_rad)
    sin_theta = math.sin(angle_rad)
    x_rotated = x * cos_theta - y * sin_theta
    y_rotated = x * sin_theta + y * cos_theta
    return (x_rotated, y_rotated)

# Calcula los cuatro vértices del polígono de detección del radar.
def calcular_vertices_poligono(RADAR_RADIO_M, ANGULO_ROTACION) -> list:
    # Define los límites del polígono sin rotación
    x_max = RADAR_RADIO_M  # 652 metros al este
    x_min = RADAR_RADIO_M * -1  # -652 metros al oeste
    y_max = RADAR_RADIO_M  # 652 metros al norte
    y_min = 0    # 0 metros al sur

    # Vértices del polígono sin rotación
    vertices = [
        (x_max, y_max), # Superior Derecho
        (x_min, y_max), # Superior Izquierdo
        (x_min, y_min), # Inferior Izquierdo
        (x_max, y_min)  # Inferior Derecho
    ]

    # Convierte el ángulo a radianes para los cálculos
    angle_rad = math.radians(ANGULO_ROTACION)
    cos_theta = math.cos(angle_rad)
    sin_theta = math.sin(angle_rad)

    rotated_vertices_geographic = []
    
    for x, y in vertices:
        # Aplica la fórmula de rotación
        x_rotated = x * cos_theta - y * sin_theta
        y_rotated = x * sin_theta + y * cos_theta
        
        # Convierte el vértice rotado a coordenadas geográficas
        lat, lon = convertir_cartesiano_a_geografico(x_rotated, y_rotated)
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

@router.websocket("/radar")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ZONAS_DE_DETECCION = list(ZONAS_COLLECTION.find({}, {"_id": 0}))
    while True:
        try:
            async with websockets.connect(
                RADAR_WEBSOCKET_URL,
                ping_interval=30,
                ping_timeout=60
            ) as radar_ws:
                print("Conectado al radar. Iniciando retransmisión de datos...")
                while True:
                    radar_data_raw = await radar_ws.recv()
                    processed_data = re.sub(r'(\w+):', r'"\1":', radar_data_raw)
                    
                    try:
                        # Asume que los datos son un JSON string
                        radar_data_json = json.loads(processed_data)
                        
                        if "data" in radar_data_json and isinstance(radar_data_json["data"], list):
                            processed_points = []
                            for point_data in radar_data_json["data"]:
                                # Extrae las coordenadas cartesianas
                                x_meters = point_data.get("x", 0)
                                y_meters = point_data.get("y", 0)

                                # Aplica la rotación a cada punto
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

                                # Estructura el JSON de salida con las coordenadas geográficas
                                # y el polígono pre-calculado
                                puntos_a_enviar = {
                                    "id": point_data.get("id"),
                                    "type": point_data.get("type"),
                                    "latitud": latitud,
                                    "longitud": longitud,
                                    "azimut": point_data.get("a"),
                                    "distancia": point_data.get("d")
                                }
                                
                                # Si se detectó una zona, agrégala al objeto del punto
                                if zona_detectada:
                                    puntos_a_enviar["zona_alerta"] = {
                                        "id": zona_detectada.get("id"),
                                        "name": zona_detectada.get("name"),
                                        "color": zona_detectada.get("color"),
                                        "category": zona_detectada.get("category")
                                    }
                                    
                                processed_points.append(puntos_a_enviar)
                                
                        processed_data = {
                                "puntos": processed_points
                            }
                                                
                        await websocket.send_json(processed_data)
                    
                    except json.JSONDecodeError:
                        # Maneja el caso en que los datos no sean un JSON válido
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


@router.websocket("/solo_punto")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ZONAS_DE_DETECCION = list(ZONAS_COLLECTION.find({}, {"_id": 0}))
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
                        "vertices": calcular_vertices_poligono(float(config.radar_radio_m), float(config.angulo_rotacion))
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

    next_id_doc = ZONAS_COLLECTION.find_one_and_update(
        {"_id": "zonas_id"},
        {"$inc": {"sequence_value": 1}},
        return_document=True,
        upsert=True  # Crea el documento si no existe
    )
    
    nuevo_id = next_id_doc["sequence_value"]

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
    # 6. Retornar la nueva zona creada
    return nueva_zona_con_id


@router.delete("/zonas/{zona_id}")
async def eliminar_zona(zona_id):
        
    ZONAS_COLLECTION.delete_one({"id": int(zona_id)})
    
    return {
        "msg": "Zona eliminada de JSON y MongoDB."
        }