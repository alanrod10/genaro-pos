"""Inyección de dependencias para la API local de GenaroPOS."""
import os
from pathlib import Path
import sqlite3
from typing import Any, Generator, Optional
from fastapi import Depends, Header, HTTPException, status
from api.auth import AuthenticatedUser, decode_access_token


def get_db_connection() -> Generator[Optional[sqlite3.Connection], None, None]:
    """Provee la conexión de base de datos a los endpoints.

    Comportamiento:
    - Si COMMERCIAL_DB_PATH o GENARO_COMMERCIAL_DB_PATH están definidos, abre y entrega
      una conexión SQLite local asegurando aislamiento estricto y PRAGMA foreign_keys = ON.
    - Si no está definido, entrega None.
    - En suites de prueba se sobreescribe mediante:
      app.dependency_overrides[get_db_connection] = lambda: test_conn.
    """
    db_path = os.getenv("COMMERCIAL_DB_PATH") or os.getenv("GENARO_COMMERCIAL_DB_PATH")
    if db_path:
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()
    else:
        yield None


def get_tesoreria_connection() -> Generator[Optional[sqlite3.Connection], None, None]:
    """Provee la conexión de base de datos de Tesorería a los endpoints."""
    db_path = os.getenv("TESORERIA_DB_PATH") or os.getenv("GENARO_TESORERIA_DB_PATH")
    if db_path:
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()
    else:
        yield None


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthenticatedUser:
    """Verifica el token de autorización Bearer y retorna el usuario autenticado.

    Rechaza solicitudes anónimas, con tokens malformados, manipulados o expirados.
    En suites de pruebas se puede sobreescribir mediante:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado. Se requiere cabecera 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de autorización inválido. Debe ser 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    return decode_access_token(token)


def require_admin_or_socio(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Verifica que el usuario autenticado posea rol de Administrador o Socio.

    Rechaza usuarios con rol 'cajero' o sin privilegios de gestión técnica y auditoría.
    """
    rol_normalizado = str(getattr(current_user, "role", "administrador")).strip().lower()
    if rol_normalizado not in ("administrador", "admin", "socio"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Se requieren permisos de Administrador o Socio.",
        )
    return current_user
