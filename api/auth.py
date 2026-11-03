"""Módulo de autenticación y seguridad para GenaroPOS.

Implementa control de acceso restringido exclusivamente para Alan y Yilda.
Utiliza PBKDF2-HMAC-SHA256 con sal criptográfica y tokens firmados HMAC-SHA256 (JWT estándar RFC 7519).
Las credenciales y la clave secreta deben configurarse únicamente mediante variables de entorno.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import os
import secrets
from typing import Dict, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger("genaro_pos.auth")

# Usuarios autorizados normalizados (únicos usuarios admitidos en el sistema)
ALLOWED_USERS: Dict[str, str] = {
    "alan": "Alan",
    "yilda": "Yilda",
}

# Parámetros criptográficos de referencia
DEFAULT_PBKDF2_ITERATIONS = 600_000
MIN_SECRET_KEY_LENGTH = 32
DEFAULT_TOKEN_EXPIRE_MINUTES = 480  # 8 horas

# Constantes fijas para verificación dummy (mitigación de ataques de temporización)
DUMMY_SALT = "0" * 32
DUMMY_HASH = "0" * 64


class AuthenticatedUser(BaseModel):
    """Identidad verificada del usuario autenticado."""

    username: str
    display_name: str
    role: str = "administrador"


def get_auth_secret_key() -> str:
    """Retorna la clave secreta configurada en el entorno para firma de tokens."""
    return os.getenv("GENARO_AUTH_SECRET_KEY", "").strip()


def get_token_expire_minutes() -> int:
    """Retorna la expiración de token en minutos configurada en el entorno."""
    val = os.getenv("GENARO_AUTH_TOKEN_EXPIRE_MINUTES")
    if not val:
        return DEFAULT_TOKEN_EXPIRE_MINUTES
    try:
        parsed = int(val)
        return max(1, min(parsed, 10080))  # Entre 1 minuto y 7 días
    except (ValueError, TypeError):
        return DEFAULT_TOKEN_EXPIRE_MINUTES


def get_user_password_hash(normalized_username: str) -> Optional[str]:
    """Obtiene el hash configurado en variables de entorno para un usuario admitido."""
    if normalized_username == "alan":
        return os.getenv("GENARO_USER_ALAN_HASH", "").strip() or None
    elif normalized_username == "yilda":
        return os.getenv("GENARO_USER_YILDA_HASH", "").strip() or None
    return None


def is_auth_configured() -> bool:
    """Verifica si el entorno cuenta con los secretos mínimos indispensables.

    Requiere una clave secreta robusta (>= 32 caracteres) y al menos un hash de usuario.
    """
    secret = get_auth_secret_key()
    if len(secret) < MIN_SECRET_KEY_LENGTH:
        return False
    has_alan = bool(get_user_password_hash("alan"))
    has_yilda = bool(get_user_password_hash("yilda"))
    return has_alan or has_yilda


def generate_password_hash(password: str, iterations: int = DEFAULT_PBKDF2_ITERATIONS) -> str:
    """Genera un hash con sal aleatoria criptográfica utilizando PBKDF2-HMAC-SHA256.

    Formato: pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>
    """
    if not password:
        raise ValueError("La contraseña no puede estar vacía.")
    salt_bytes = secrets.token_bytes(16)
    salt_hex = salt_bytes.hex()
    hash_bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt_hex}${hash_bytes.hex()}"


def verify_password(password: str, stored_hash: Optional[str]) -> bool:
    """Comprueba la contraseña contra el hash almacenado en tiempo constante.

    Si el hash es None o inválido, ejecuta un cómputo ficticio para evitar
    ataques de temporización que revelen la existencia del usuario.
    """
    if not stored_hash or not stored_hash.startswith("pbkdf2_sha256$"):
        # Cómputo ficticio de tiempo constante
        dummy_salt = bytes.fromhex(DUMMY_SALT)
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), dummy_salt, 100_000)
        return False

    parts = stored_hash.split("$")
    if len(parts) != 4:
        dummy_salt = bytes.fromhex(DUMMY_SALT)
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), dummy_salt, 100_000)
        return False

    _, it_str, salt_hex, expected_hex = parts
    try:
        iterations = int(it_str)
        salt_bytes = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False

    computed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        iterations,
    ).hex()

    return hmac.compare_digest(computed, expected_hex)


def authenticate_user(username: str, password: str) -> Optional[AuthenticatedUser]:
    """Autentica a Alan o Yilda verificando sus credenciales de manera segura.

    No registra contraseñas en logs y retorna None ante cualquier error o usuario desconocido.
    """
    normalized = username.strip().lower()
    if normalized not in ALLOWED_USERS:
        # Mitigación de timing attack con usuario no existente
        verify_password("dummy_password", None)
        logger.warning("Intento de login con usuario no reconocido: %s", normalized)
        return None

    stored_hash = get_user_password_hash(normalized)
    if not stored_hash:
        verify_password("dummy_password", None)
        logger.warning("Usuario reconocido sin hash configurado en entorno: %s", normalized)
        return None

    if not verify_password(password, stored_hash):
        logger.warning("Contraseña incorrecta para usuario: %s", normalized)
        return None

    logger.info("Autenticación exitosa para usuario: %s", normalized)
    user_role = "socio" if normalized == "alan" else "administrador"
    return AuthenticatedUser(
        username=normalized,
        display_name=ALLOWED_USERS[normalized],
        role=user_role,
    )


# ==============================================================================
# MANEJO DE TOKENS JWT (RFC 7519 con HMAC-SHA256)
# ==============================================================================


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def create_access_token(
    user: AuthenticatedUser,
    expires_delta: Optional[timedelta] = None,
    secret_key: Optional[str] = None,
) -> str:
    """Genera un token JWT firmado mediante HMAC-SHA256 para una sesión de usuario."""
    key = secret_key or get_auth_secret_key()
    if len(key) < MIN_SECRET_KEY_LENGTH:
        raise ValueError(
            f"GENARO_AUTH_SECRET_KEY no configurada o menor a {MIN_SECRET_KEY_LENGTH} caracteres."
        )

    delta = expires_delta or timedelta(minutes=get_token_expire_minutes())
    now = datetime.now(timezone.utc)
    exp = now + delta

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user.username,
        "name": user.display_name,
        "role": getattr(user, "role", "administrador"),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": "genaro-pos",
    }

    header_json = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    h_b64 = _base64url_encode(header_json)
    p_b64 = _base64url_encode(payload_json)

    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    signature = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _base64url_encode(signature)

    return f"{h_b64}.{p_b64}.{sig_b64}"


def decode_access_token(
    token: str,
    secret_key: Optional[str] = None,
) -> AuthenticatedUser:
    """Valida la firma, algoritmo, expiración y claims de un token JWT.

    Lanza HTTPException con status 401 si el token es inválido o expiró,
    o status 500 si la clave secreta del servidor está ausente.
    """
    key = secret_key or get_auth_secret_key()
    if len(key) < MIN_SECRET_KEY_LENGTH:
        logger.error("Error de configuración de seguridad: GENARO_AUTH_SECRET_KEY ausente o débil.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Servicio de autenticación no configurado en el servidor.",
        )

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Estructura de token inválida.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    h_b64, p_b64, sig_b64 = parts
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected_sig = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()

    try:
        actual_sig = _base64url_decode(sig_b64)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma de token malformada.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma de token inválida o adulterada.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        header_data = json.loads(_base64url_decode(h_b64).decode("utf-8"))
        payload_data = json.loads(_base64url_decode(p_b64).decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cuerpo del token ilegible.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if header_data.get("alg") != "HS256" or header_data.get("typ") != "JWT":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Algoritmo de firma no soportado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload_data.get("iss") != "genaro-pos":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Emisor del token no autorizado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    exp = payload_data.get("exp")
    if not exp or not isinstance(exp, (int, float)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expiración de token ausente o corrupta.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if now_ts >= exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión ha expirado. Por favor, iniciá sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = str(payload_data.get("sub", "")).strip().lower()
    if sub not in ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identidad de usuario desconocida o no autorizada.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    role = payload_data.get("role") or ("socio" if sub == "alan" else "administrador")
    return AuthenticatedUser(
        username=sub,
        display_name=ALLOWED_USERS[sub],
        role=role,
    )
