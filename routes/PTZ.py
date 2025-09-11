from fastapi import HTTPException, APIRouter
from dotenv import load_dotenv
from pydantic import BaseModel
from onvif import ONVIFCamera
from typing import Optional
import os
import sys
import time

load_dotenv()
router = APIRouter()

# configuracion camara
IP_CAMARA = os.getenv("IP_CAMARA")
PUERTO_ONVIF = os.getenv("PUERTO_ONVIF")
USUARIO_ONVIF = os.getenv("USUARIO_ONVIF")
CONTRASENA_ONVIF = os.getenv("CONTRASENA_ONVIF")

CAMERAS = {
    "camara_principal": {
        'id' : 1,
        'name': 'Camara Principal',
        "ip": IP_CAMARA,
        "port": PUERTO_ONVIF,
        "user": USUARIO_ONVIF,
        "password": CONTRASENA_ONVIF
    },
}

# --- Objeto global para la cámara ---
# Se inicializará en None y se conectará al iniciar la app.
camera_services = {
    "ptz": None, 
    "media_token": None
    }

CONNECTED_CAMERAS = {}

@router.on_event("startup")
def startup_event():
    print("🚀 Iniciando conexión con todas las cámaras configuradas...")
    for cam_id, config in CAMERAS.items():
        try:
            print(f"  - Intentando conectar con '{cam_id}' ({config['ip']})...")
            mycam = ONVIFCamera(config['ip'], config['port'], config['user'], config['password'])
            
            ptz_service = mycam.create_ptz_service()
            media_service = mycam.create_media_service()
            profiles = media_service.GetProfiles()
            
            if not profiles:
                raise Exception("No se encontraron perfiles de media.")
                
            media_profile_token = profiles[0].token
            
            # Guardar la conexión exitosa en el diccionario
            CONNECTED_CAMERAS[cam_id] = {
                "ptz": ptz_service,
                "media_token": media_profile_token
            }
            print(f"  ✅ Conexión con '{cam_id}' establecida.")
            
        except Exception as e:
            # Si una cámara falla, se registra el error pero la app no se detiene.
            print(f"  ❌ ERROR al conectar con '{cam_id}': {e}")

    if not CONNECTED_CAMERAS:
        print("FATAL: No se pudo conectar a ninguna cámara. La aplicación no puede continuar.")
        sys.exit("Error crítico: No hay cámaras conectadas.")



# --- Modelos de datos (sin cambios) ---
class MoveRequest(BaseModel):
    pan: float
    tilt: float
    zoom: float

class PresetRequest(BaseModel):
    preset_name: str

class PresetActionRequest(BaseModel):
    preset_token: str
    
camera_id = 1

# --- Función de ayuda (Modificada para buscar por ID) ---
def get_camera_services(camera_id):
    camera = CONNECTED_CAMERAS.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail=f"Cámara '{camera_id}' no encontrada o no conectada.")
    return camera["ptz"], camera["media_token"]

# --- NUEVO Endpoint para listar cámaras conectadas ---
@router.get("/cameras")
def get_connected_cameras():
    """Devuelve una lista de los IDs de las cámaras conectadas exitosamente."""
    return {"cameras": list(CONNECTED_CAMERAS.keys())}

# --- Endpoints (Modificados para usar un ID de cámara) ---

@router.post("/cameras/move")
def move_camera(camera_id, move: MoveRequest):
    ptz, token = get_camera_services(camera_id)
    request = ptz.create_type("ContinuousMove")
    request.ProfileToken = token
    request.Velocity = {
        "PanTilt": {
            "x": move.pan, 
            "y": move.tilt}, 
            "Zoom": {
                "x": move.zoom
                }
            }
    # request.Timeout = "PT1S"
    ptz.ContinuousMove(request)
    return {"status": "Moviendo"}

@router.post("/cameras/stop")
def stop_camera(camera_id):
    ptz, token = get_camera_services(camera_id)
    ptz.Stop({"ProfileToken": token})
    return {"status": "Movimiento detenido"}

@router.post("/cameras/goto_home")
def goto_home_position(camera_id):
    ptz, token = get_camera_services(camera_id)
    ptz.Stop({"ProfileToken": token})
    time.sleep(0.2)
    ptz.GotoHomePosition({'ProfileToken': token})
    return {"status": "Moviendo a Home."}

@router.post("/cameras/set_home")
def set_home_position(camera_id):
    ptz, token = get_camera_services(camera_id)
    ptz.Stop({'ProfileToken': token})
    time.sleep(1)
    ptz.SetHomePosition({'ProfileToken': token})
    return {"status": "Posición actual guardada como Home."}

@router.get("/cameras/presets")
def get_presets(camera_id):
    ptz, token = get_camera_services(camera_id)
    presets_data = ptz.GetPresets({'ProfileToken': token})
    return [{"token": p.token, "name": p.Name} for p in presets_data or []]

@router.post("/cameras/set_preset")
def set_preset(camera_id, request: PresetRequest):
    ptz, token = get_camera_services(camera_id)
    ptz.Stop({'ProfileToken': token})
    preset_token = ptz.SetPreset({
        'ProfileToken': token,
        'PresetName': request.preset_name
    })
    return {"status": "Preset guardado", "token": preset_token}

@router.post("/cameras/goto_preset")
def goto_preset(camera_id, request: PresetActionRequest):
    ptz, token = get_camera_services(camera_id)
    ptz.Stop({"ProfileToken": token})
    time.sleep(0.2)
    ptz.GotoPreset({
        'ProfileToken': token,
        'PresetToken': request.preset_token
    })
    return {"status": f"Moviendo al preset {request.preset_token}"}

@router.post("/cameras/remove_preset")
def remove_preset(camera_id, request: PresetActionRequest):
    ptz, token = get_camera_services(camera_id)
    ptz.RemovePreset({
        'ProfileToken': token,
        'PresetToken': request.preset_token
    })
    return {"status": f"Preset {request.preset_token} eliminado"}


# Modelo
class AbsoluteMoveRequest(BaseModel):
    pan: Optional[float] = None
    tilt: Optional[float] = None
    zoom: Optional[float] = None

@router.post("/cameras/absolute_move")
def absolute_move_camera(camera_id, move: AbsoluteMoveRequest):

    ptz, token = get_camera_services(camera_id)

    # Create the request object from the WSDL
    request = ptz.create_type("AbsoluteMove")
    request.ProfileToken = token

    # The ONVIF spec requires a 'Position' object.
    # We build it dynamically based on the user's input.
    position = {}
    if move.pan is not None and move.tilt is not None:
        position["PanTilt"] = {"x": move.pan, "y": move.tilt}

    if move.zoom is not None:
        position["Zoom"] = {"x": move.zoom}

    # If no values were provided, there's nothing to do.
    if not position:
        raise HTTPException(
            status_code=400, detail="You must provide at least pan/tilt or zoom values."
        )

    request.Position = position

    # Send the command to the camera
    ptz.AbsoluteMove(request)

    return {"status": "Moving to absolute position"}


class AuxCommandRequest(BaseModel):
    command: str
    
@router.get("/cameras/aux_commands")
def get_auxiliary_commands(camera_id: str):
    """
    Descubre y devuelve la lista de comandos auxiliares (ej: luces)
    soportados por una cámara específica.
    """
    # ptz aquí es tu servicio PTZ, que es el correcto para esta operación
    ptz, media_token = get_camera_services(camera_id)

    try:
        # LÍNEA CORREGIDA: Llama a GetConfigurations desde el servicio ptz
        ptz_configs = ptz.GetConfigurations()

        if not ptz_configs:
            raise HTTPException(
                status_code=404, detail="No PTZ configurations found for this camera."
            )

        # Asumimos que el perfil usa la primera configuración PTZ disponible
        node_token = ptz_configs[0].NodeToken

        # Usamos el NodeToken para obtener las propiedades del nodo, incluyendo los comandos
        node = ptz.GetNode({"NodeToken": node_token})

        if hasattr(node, "AuxiliaryCommands") and node.AuxiliaryCommands:
            # Extraemos solo los valores de texto de los comandos
            commands = [cmd for cmd in node.AuxiliaryCommands]
            return {"supported_commands": commands}
        else:
            return {
                "supported_commands": [],
                "message": "Esta cámara no reporta comandos auxiliares.",
            }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error al obtener comandos auxiliares: {str(e)}"
        )


# --- NUEVO: Endpoint para enviar un comando auxiliar ---
@router.post("/cameras/send_aux_command")
def send_aux_command(camera_id: str, request: AuxCommandRequest):
    """
    Envía un comando auxiliar específico (ej: para encender/apagar la luz) a la cámara.
    """
    ptz, token = get_camera_services(camera_id)
    try:
        # El comando se envía en el parámetro AuxiliaryData
        response = ptz.SendAuxiliaryCommand(
            {"ProfileToken": token, "AuxiliaryData": request.command}
        )
        return {"status": f"Comando '{request.command}' enviado.", "response": response}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error al enviar el comando auxiliar: {str(e)}"
        ) 