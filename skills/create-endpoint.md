# Skill: Create REST Endpoint

## Cuándo usar
Cuando se pide crear un nuevo endpoint HTTP, ruta de API, o handler REST.

## Convención del equipo
- Todos los endpoints viven en `src/api/routes/`
- Usar los decoradores del framework (FastAPI / Express)
- Validar el body con Pydantic (Python) o Zod (TS)
- Retornar errores con HTTPException y códigos estándar
- Incluir prueba unitaria en `tests/api/`

## Ejemplo (FastAPI)

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["users"])

class UserCreate(BaseModel):
    name: str
    email: str

@router.post("/", status_code=201)
async def create_user(body: UserCreate):
    # lógica aquí
    return {"id": 1, **body.dict()}
```

## Errores comunes a evitar
- No poner lógica de negocio en el router — delegarla al service layer
- No olvidar incluir el router en `main.py` / `app.py`
