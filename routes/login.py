from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase

from auth.auth import verify_password, create_access_token
from database import get_db_mongo

router = APIRouter()

class LoginRequest(BaseModel):
    username: str
    password: str
    
@router.post("/login")
async def login_json(data: LoginRequest, db: AsyncIOMotorDatabase = Depends(get_db_mongo)):
    # Buscar al usuario por email en la colección 'usuarios'
    usuario = await db.usuarios.find_one({"email": data.username})

    if not usuario or not verify_password(data.password, usuario["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # El _id de MongoDB es un ObjectId. Para JWT, es mejor usarlo como string.
    access_token = create_access_token(data={"sub": str(usuario["_id"])})
    return {"access_token": access_token, "token_type": "bearer"}