from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from passlib.context import CryptContext

from database import get_db_mongo  # Usa la nueva dependencia para MongoDB
from auth.auth import get_current_user
from schemas.Usuario import UsuarioCreateSchema, UsuarioUpdateSchema, UsuarioSchema

router = APIRouter()
proteccion_user = Depends(get_current_user)

# Configuración del hash de contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

# GET
@router.get("/usuarios", response_model=List[UsuarioSchema])
async def get_usuarios(
    db: AsyncIOMotorDatabase = Depends(get_db_mongo),
    # current_user: dict = proteccion_user  # Descomenta para proteger esta ruta
):
    usuarios_cursor = db.usuarios.find()
    usuarios = await usuarios_cursor.to_list(1000)

    for usuario in usuarios:
        usuario["id"] = str(usuario["_id"])  # Conversión clave
    
    return usuarios

# POST
@router.post("/usuarios", status_code=status.HTTP_201_CREATED)
async def create_usuario(
    usuario: UsuarioCreateSchema, 
    db: AsyncIOMotorDatabase = Depends(get_db_mongo),
    #current_user: dict = proteccion_user
):
    usuario_dict = usuario.dict()
    usuario_dict["password"] = hash_password(usuario_dict["password"])
    
    # Insertar el documento. No necesitas el 'result' si solo quieres el último.
    await db.usuarios.insert_one(usuario_dict)
    
    # Obtener el último usuario insertado de la colección
    new_usuario = await db.usuarios.find_one(sort=[("_id", -1)])
    
    # Convertir el ObjectId a str para evitar el error de serialización
    if new_usuario and "_id" in new_usuario:
        new_usuario["_id"] = str(new_usuario["_id"])
    
    return {
        "data": new_usuario,
        "res": True,
        "msg": "Usuario creado correctamente"
    }
    
# PUT
@router.put("/usuarios/{id}", response_model=UsuarioSchema)
async def update_usuario(
    usuario_data: UsuarioUpdateSchema, 
    id: str, 
    db: AsyncIOMotorDatabase = Depends(get_db_mongo)
):
    try:
        object_id = ObjectId(id)
    except:
        raise HTTPException(status_code=400, detail="ID de usuario inválido")

    update_data = usuario_data.dict(exclude_unset=True)
    if "password" in update_data:
        update_data["password"] = hash_password(update_data["password"])

    result = await db.usuarios.update_one(
        {"_id": object_id},
        {"$set": update_data}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    updated_usuario = await db.usuarios.find_one({"_id": object_id})
    updated_usuario["id"] = str(updated_usuario["_id"]) # Conversión clave
    return updated_usuario

# DELETE
@router.delete("/usuarios/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_usuario(id: str, db: AsyncIOMotorDatabase = Depends(get_db_mongo)):
    try:
        object_id = ObjectId(id)
    except:
        raise HTTPException(status_code=400, detail="ID de usuario inválido")

    # Elimina el documento
    result = await db.usuarios.delete_one({"_id": object_id})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    return {"message": "Usuario eliminado correctamente"}

