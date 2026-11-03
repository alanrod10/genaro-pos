"""Router de autenticación para GenaroPOS."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status
from api.auth import (
    AuthenticatedUser,
    authenticate_user,
    create_access_token,
    get_token_expire_minutes,
    is_auth_configured,
)
from api.dependencies import get_current_user
from api.rate_limiter import login_rate_limiter
from api.schemas import LoginRequest, TokenResponse, UserInfo

logger = logging.getLogger("genaro_pos.auth.router")

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(creds: LoginRequest, request: Request) -> TokenResponse:
    """Autentica a Alan o Yilda y emite un token JWT de sesión.

    Rechaza usuarios desconocidos, contraseñas erróneas o peticiones sin configuración de seguridad.
    Los mensajes de error son genéricos para evitar enumeración de usuarios.
    Aplica limitación de intentos fallidos por dirección IP (HTTP 429).
    """
    # 1. Comprobación de tasa de intentos fallidos por (IP, usuario)
    login_rate_limiter.verificar_limite(request, creds.username)

    if not is_auth_configured():
        logger.error(
            "Fallo de inicio de sesión: la configuración de autenticación está incompleta en el servidor."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de autenticación no configurado en el servidor.",
        )

    user = authenticate_user(creds.username, creds.password)
    if not user:
        login_rate_limiter.registrar_fallo(request, creds.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Autenticación exitosa: limpiar historial de fallos para (IP, usuario)
    login_rate_limiter.registrar_exito(request, creds.username)

    token = create_access_token(user)
    expires_in_seconds = get_token_expire_minutes() * 60

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in_seconds,
        user=UserInfo(username=user.username, display_name=user.display_name),
    )


@router.get("/me", response_model=UserInfo)
def get_me(current_user: AuthenticatedUser = Depends(get_current_user)) -> UserInfo:
    """Retorna la identidad autenticada mínima necesaria."""
    return UserInfo(
        username=current_user.username,
        display_name=current_user.display_name,
    )
