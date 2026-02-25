from pydantic import BaseModel, Field
from typing import Optional

class UsuarioCreateSchema(BaseModel):
    nombre: str
    apellido: str
    email: str
    password: str
    rol: str
    
class UsuarioUpdateSchema(BaseModel):
    nombre: Optional[str]
    apellido: Optional[str]
    email: Optional[str]
    password: Optional[str]
    rol: Optional[str]
    
class UsuarioSchema(BaseModel):
    id: str
    
    class Config:
        orm_mode = True