from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from routes.Radar import radar_listener_task
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Iniciando servidor: Conectando con la tarea del radar...")
    
    radar_task = asyncio.create_task(radar_listener_task())
    
    yield
    
    print("Apagando servidor: Cancelando tarea del radar...")
    radar_task.cancel()
    try:
        await radar_task
    except asyncio.CancelledError:
        print("Tarea del radar cancelada correctamente.")
        
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cargar rutas a app
from routes import api_router
app.include_router(api_router)