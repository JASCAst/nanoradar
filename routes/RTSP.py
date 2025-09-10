from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import cv2
import asyncio


router = APIRouter()

# --- Configuración de las Cámaras ---
# Usamos un diccionario para gestionar múltiples cámaras fácilmente
CAMERA_URLS = {
    "1": "rtsp://admin:admin888@10.30.7.230/Stream/Live/101?transportmode=unicast&profile=ONFProfileToken_101",
    "2": "rtsp://admin:admin888@10.30.7.230/Stream/Live/201?transportmode=unicast&profile=ONFProfileToken_201", 
    # Puedes añadir más cámaras aquí: "3": "rtsp://..."
}

# Diccionario para mantener los objetos de captura de video
cameras = {id: cv2.VideoCapture(url) for id, url in CAMERA_URLS.items()}


# --- Lógica de Streaming ---
async def generate_frames(camera_id: str):
    camera = cameras.get(camera_id)
    url = CAMERA_URLS.get(camera_id)

    if not camera or not camera.isOpened():
        print(f"Error: No se pudo abrir la cámara {camera_id}.")
        return

    while True:
        success, frame = camera.read()
        if not success:
            print(
                f"Cámara {camera_id}: No se pudo leer el frame, reintentando conexión..."
            )
            camera.release()
            camera.open(url)
            await asyncio.sleep(2)  # Espera un poco más antes de reintentar
            continue
        else:
            ret, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
            )  # Comprime un poco la imagen
            if not ret:
                continue

            frame_bytes = buffer.tobytes()
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )

        # Ajusta el sleep para controlar el framerate y reducir la carga de CPU
        await asyncio.sleep(0.01)

# --- Rutas de la API (Endpoints) ---
@router.get("/video_feed/{camera_id}")
async def video_feed(camera_id: str):
    if camera_id not in cameras:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    return StreamingResponse(
        generate_frames(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/")
def read_root():
    available_feeds = [f"/video_feed/{id}" for id in cameras.keys()]
    return {
        "message": "Servidor de streaming funcionando.",
        "feeds_disponibles": available_feeds,
    }