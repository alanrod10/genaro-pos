"""Punto de entrada de la API local de GenaroPOS."""
import os
from pathlib import Path

# Carga de variables de entorno desde .env antes de inicializar routers y dependencias.
# Se respeta cualquier variable ya definida en el proceso del sistema operativo.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_PATH = PROJECT_ROOT / ".env"


def cargar_env_local(ruta_env: Path = ENV_FILE_PATH) -> bool:
    """Carga variables desde un archivo .env si existe, sin sobreescribir las ya definidas."""
    if not ruta_env.exists():
        return False
    for line in ruta_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        k = key.strip()
        if k and k not in os.environ:
            os.environ[k] = val.strip().strip("\"'")
    return True


cargar_env_local()

from contextlib import asynccontextmanager
from typing import Optional
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from api.dependencies import get_current_user
from api.routers import (
    auth,
    catalog,
    commercial_bridge,
    expenses,
    health,
    historical,
    preventistas,
    reports,
    sales,
    services,
    system,
    tesoreria,
)
from db.readiness import ErrorEsquemaNoPreparado
from tesoreria.commercial_bridge_worker import (
    CommercialBridgeBackgroundWorker,
    register_worker_trigger_hook,
)
from api.routers.commercial_bridge import (
    _obtener_conn_comercial_activa,
    _obtener_conn_tesoreria_activa,
)

_global_bridge_worker: Optional[CommercialBridgeBackgroundWorker] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _global_bridge_worker
    enabled = os.getenv("GENARO_BRIDGE_WORKER_ENABLED", "true").strip().lower() in ("true", "1", "yes")
    interval = float(os.getenv("GENARO_BRIDGE_INTERVAL_SECONDS", "5.0"))
    if enabled and not os.getenv("PYTEST_CURRENT_TEST"):
        try:
            _global_bridge_worker = CommercialBridgeBackgroundWorker(
                comm_conn_factory=_obtener_conn_comercial_activa,
                tes_conn_factory=_obtener_conn_tesoreria_activa,
                interval_seconds=interval,
                close_connections_on_drain=True,
            )
            register_worker_trigger_hook(_global_bridge_worker.trigger_immediate_drain)
            _global_bridge_worker.start()
        except Exception:
            pass
    yield
    if _global_bridge_worker:
        _global_bridge_worker.stop()


_ES_PRODUCCION = os.getenv("GENARO_ENV", "").strip().lower() == "production"

app = FastAPI(
    title="GenaroPOS Local API",
    description="API REST local para desacoplamiento progresivo de GenaroPOS.",
    version="0.1.0",
    # En producción no se exponen la documentación interactiva ni el esquema OpenAPI.
    docs_url=None if _ES_PRODUCCION else "/docs",
    openapi_url=None if _ES_PRODUCCION else "/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)

# Seguridad: orígenes explícitos en loopback. No wildcard (*).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8501",
        "http://localhost:8501",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://172.20.233.143:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "Accept",
        "X-Requested-With",
    ],
)


def is_api_read_only() -> bool:
    """Determina si el backend de GenaroPOS está configurado en modo de solo lectura.

    Precedencia de configuración:
    1. GENARO_API_READ_ONLY: Variable propia del backend.
       - Si está definida con valores de verdad ('true', '1', 'yes', 'on'): Activa el modo.
       - Si está definida con cualquier otro valor (ej. 'false', '0', 'no'): Desactiva explícitamente el modo.
    2. VITE_READ_ONLY_CONNECTED_SMOKE: Respaldo para compatibilidad con el entorno de inspección actual.
       - Solo se evalúa si GENARO_API_READ_ONLY no está definida en el entorno.
       - Si es 'true', '1', 'yes', 'on', activa el modo de solo lectura en el backend.
    3. Si ninguna variable está definida, retorna False (modo operativo normal).
    """
    val = os.getenv("GENARO_API_READ_ONLY")
    if val is not None:
        return val.strip().lower() in ("true", "1", "yes", "on")
    smoke = os.getenv("VITE_READ_ONLY_CONNECTED_SMOKE")
    if smoke is not None:
        return smoke.strip().lower() in ("true", "1", "yes", "on")
    return False


@app.middleware("http")
async def read_only_backend_middleware(request: Request, call_next):
    """Bloquea operaciones mutadoras en el backend cuando está activo el modo de solo lectura.

    Garantías de seguridad:
    - Permite métodos idempotentes y de solo lectura: GET, HEAD, OPTIONS (necesarios para
      consultas de datos y preflights de CORS).
    - Permite exclusivamente POST /api/auth/login para emitir tokens JWT válidos durante sesiones
      de inspección técnica sin escrituras.
    - Rechaza cualquier otra petición mutadora (POST, PUT, PATCH, DELETE) con HTTP 423 Locked
      antes de alcanzar los routers comerciales, servicios de dominio o conexiones a base de datos.
    - Mensaje genérico para no revelar detalles de implementación ni rutas internas.
    """
    if is_api_read_only():
        method = request.method.upper()
        if method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        path = request.url.path.rstrip("/")
        if method == "POST" and path == "/api/auth/login":
            return await call_next(request)

        return JSONResponse(
            status_code=status.HTTP_423_LOCKED,
            content={
                "error_code": "API_READ_ONLY_LOCKED",
                "detail": "El backend de GenaroPOS se encuentra en modo de solo lectura. Operaciones de escritura comercial bloqueadas.",
            },
        )

    return await call_next(request)

# Manejo seguro de inconsistencia o falta de preparación del esquema
@app.exception_handler(ErrorEsquemaNoPreparado)
async def esquema_no_preparado_exception_handler(request: Request, exc: ErrorEsquemaNoPreparado):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error_code": "SCHEMA_NOT_READY",
            "detail": exc.mensaje,
            "mensaje": exc.mensaje,
            "estado": exc.estado,
            "detalles": exc.detalles,
        },
    )


# Endpoints públicos exentos
app.include_router(health.router, tags=["Salud"])
app.include_router(auth.router, prefix="/api/auth", tags=["Autenticación"])

# Endpoints operativos protegidos (Acceso exclusivo autenticado para Alan y Yilda)
app.include_router(
    system.router,
    prefix="/api/system",
    tags=["Sistema"],
    dependencies=[Depends(get_current_user)],
)

# Endpoints operativos protegidos (Acceso exclusivo autenticado para Alan y Yilda)
app.include_router(
    catalog.router,
    prefix="/api/catalog",
    tags=["Catálogo"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    sales.router,
    prefix="/api/sales",
    tags=["Ventas"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    services.router,
    prefix="/api/services",
    tags=["Servicios"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    expenses.router,
    prefix="/api/expenses",
    tags=["Gastos y Arqueo"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    preventistas.router,
    prefix="/api/preventistas",
    tags=["Preventistas y Pedidos"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    reports.router,
    prefix="/api/reports",
    tags=["Reportes y Dashboard"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    historical.router,
    prefix="/api/historical",
    tags=["Histórico"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    tesoreria.router,
    prefix="/api/tesoreria",
    tags=["Tesorería Lab"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    commercial_bridge.router,
    prefix="/api/tesoreria/commercial-bridge",
    tags=["Commercial Bridge Monitor"],
    dependencies=[Depends(get_current_user)],
)



if __name__ == "__main__":
    import uvicorn

    # Escucha estrictamente en loopback 127.0.0.1 (no expuesto a red local ni internet)
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)
