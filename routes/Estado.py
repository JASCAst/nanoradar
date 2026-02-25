# state.py
import asyncio

# --- ESTADO GLOBAL PARA EL CONTROL MANUAL ---
manual_override = False
manual_override_task = None
TIMEOUT_MANUAL_CONTROL = 15  # 5 minutos en segundos 300

async def reset_manual_override():
    """Restablece el control manual después de un tiempo."""
    global manual_override
    try:
        await asyncio.sleep(TIMEOUT_MANUAL_CONTROL)
        print("⏰ Tiempo de inactividad. Reactivando el control automático del radar.")
        manual_override = False
    except asyncio.CancelledError:
        print("❌ Temporizador de anulación manual cancelado.")
        pass

async def set_manual_override():
    """Establece el estado de control manual y reinicia el temporizador."""
    global manual_override, manual_override_task
    manual_override = True
    print("🖐️ Control manual activado. Desactivando el movimiento automático.")
    
    # Cancela la tarea anterior si existe
    if manual_override_task and not manual_override_task.done():
        manual_override_task.cancel()
    
    # Inicia una nueva tarea de temporizador
    manual_override_task = asyncio.create_task(reset_manual_override())