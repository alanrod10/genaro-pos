"""Módulo de limitación de tasa (rate limiting) para protección de autenticación.

Implementa un limitador en memoria para mitigar ataques de fuerza bruta sobre /api/auth/login.

Garantías y límites técnicos:
- Estado en memoria local del proceso: adecuado para el despliegue mono-proceso de GenaroPOS.
- Si Uvicorn se reinicia o si se ejecutan múltiples workers/procesos independientes, el estado
  no se comparte entre ellos (no es un limitador distribuido como Redis).
- Obtiene la dirección IP observada directamente desde el socket (request.client.host)
  para prevenir la evasión mediante cabeceras manipuladas por el cliente como X-Forwarded-For.
"""

from collections import defaultdict
import logging
import time
from typing import Callable, Dict, List, Optional, Tuple
from fastapi import HTTPException, Request, status

logger = logging.getLogger("genaro_pos.auth.limiter")


class InMemoryLoginRateLimiter:
    """Limitador de intentos fallidos de inicio de sesión por (IP, usuario).

    Permite aislar fallos entre cuentas distintas (ej. Alan vs Yilda) cuando las
    peticiones provienen del mismo proxy local (127.0.0.1), mantendo un techo
    global por IP para prevenir enumeración masiva.
    """

    def __init__(
        self,
        max_fallos: int = 5,
        ventana_segundos: int = 60,
        max_fallos_ip: Optional[int] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.max_fallos = max_fallos
        self.ventana_segundos = ventana_segundos
        self.max_fallos_ip = max_fallos_ip if max_fallos_ip is not None else max(max_fallos * 4, 20)
        self._clock = clock
        # Mapeo de (ip, username_norm) -> lista de timestamps (float) de intentos fallidos
        self._fallos_cuenta: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        # Mapeo de ip -> lista de timestamps (float) de intentos fallidos globales
        self._fallos_ip: Dict[str, List[float]] = defaultdict(list)

    def _obtener_ip_cliente(self, request: Request) -> str:
        """Determina la IP del cliente observada directamente por el servidor."""
        if request.client and request.client.host:
            return request.client.host
        return "127.0.0.1"

    def _normalizar_usuario(self, username: Optional[str]) -> str:
        """Normaliza el nombre de usuario para la clave compuesta del limitador."""
        norm = (username or "").strip().lower()
        return norm if norm else "_anon_"

    def verificar_limite(self, request: Request, username: Optional[str] = None) -> None:
        """Comprueba si la combinación (IP, usuario) o la IP global excedió el límite permitido.

        Lanza HTTP 429 Too Many Requests con mensaje genérico y encabezado Retry-After.
        """
        ip = self._obtener_ip_cliente(request)
        user_key = self._normalizar_usuario(username)
        ahora = self._clock()

        # Purgar timestamps más antiguos que la ventana para la cuenta y para la IP
        clave = (ip, user_key)
        hist_cuenta = [t for t in self._fallos_cuenta[clave] if ahora - t < self.ventana_segundos]
        self._fallos_cuenta[clave] = hist_cuenta

        hist_ip = [t for t in self._fallos_ip[ip] if ahora - t < self.ventana_segundos]
        self._fallos_ip[ip] = hist_ip

        if len(hist_cuenta) >= self.max_fallos:
            segundos_restantes = max(1, int(self.ventana_segundos - (ahora - hist_cuenta[0])) + 1)
            logger.warning(
                "Límite de intentos de autenticación excedido para IP %s / usuario '%s' (%d fallos en %ds)",
                ip,
                user_key,
                len(hist_cuenta),
                self.ventana_segundos,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados intentos fallidos de inicio de sesión. Por favor, intente nuevamente más tarde.",
                headers={"Retry-After": str(segundos_restantes)},
            )

        if len(hist_ip) >= self.max_fallos_ip:
            segundos_restantes = max(1, int(self.ventana_segundos - (ahora - hist_ip[0])) + 1)
            logger.warning(
                "Límite global de intentos de autenticación excedido para IP %s (%d fallos en %ds)",
                ip,
                len(hist_ip),
                self.ventana_segundos,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados intentos fallidos de inicio de sesión. Por favor, intente nuevamente más tarde.",
                headers={"Retry-After": str(segundos_restantes)},
            )

    def registrar_fallo(self, request: Request, username: Optional[str] = None) -> None:
        """Registra un fallo de autenticación para (IP, usuario) y para la IP."""
        ip = self._obtener_ip_cliente(request)
        user_key = self._normalizar_usuario(username)
        ahora = self._clock()
        clave = (ip, user_key)

        self._fallos_cuenta[clave].append(ahora)
        self._fallos_cuenta[clave] = [
            t for t in self._fallos_cuenta[clave] if ahora - t < self.ventana_segundos
        ]

        self._fallos_ip[ip].append(ahora)
        self._fallos_ip[ip] = [
            t for t in self._fallos_ip[ip] if ahora - t < self.ventana_segundos
        ]

    def registrar_exito(self, request: Request, username: Optional[str] = None) -> None:
        """Restablece el contador de fallos para (IP, usuario) al autenticarse exitosamente."""
        ip = self._obtener_ip_cliente(request)
        user_key = self._normalizar_usuario(username)
        self._fallos_cuenta.pop((ip, user_key), None)
        if username is None:
            self._fallos_ip.pop(ip, None)

    def limpiar(self) -> None:
        """Limpia todo el historial en memoria (para uso en pruebas automatizadas)."""
        self._fallos_cuenta.clear()
        self._fallos_ip.clear()


# Instancia global por defecto para /api/auth/login (5 fallos en 60 segundos)
login_rate_limiter = InMemoryLoginRateLimiter(max_fallos=5, ventana_segundos=60)
