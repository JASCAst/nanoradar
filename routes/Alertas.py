from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from database import get_db_mongo
from pymongo import MongoClient
import os

router = APIRouter()

DBMONGO_URI = MongoClient(os.getenv("BDMONGO_URI"))
ASTRADAR_BD = DBMONGO_URI["astradar"]
ALERTAS_COLLECTION = ASTRADAR_BD["alertas"]

@router.get("/alertas")
async def obtener_alertas():
    #mostrar todas las alertas
    return {
        "alertas": ALERTAS_COLLECTION.find()
    }
    