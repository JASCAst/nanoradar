from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import os
from dotenv import load_dotenv
from pathlib import Path

# Cargar variables de entorno
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# URL de conexión a MongoDB desde .env
MONGODB_URI = os.getenv("BDMONGO_URI")  # Asegúrate de que esta variable existe en tu .env

# Cliente de MongoDB
client = AsyncIOMotorClient(MONGODB_URI)

# Accede a la base de datos "astradar"
db = client.astradar

async def get_db_mongo() -> AsyncIOMotorDatabase:
    """
    Provee una conexión asíncrona a la base de datos de MongoDB.
    """
    return db