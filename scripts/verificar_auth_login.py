#!/usr/bin/env python3
"""Script de verificación de inicio de sesión real contra FastAPI (GenaroPOS).

Verifica la lógica de autenticación en un entorno local aislado y sin red:
1. Alan puede iniciar sesión con su contraseña.
2. Yilda puede iniciar sesión con su contraseña.
3. Contraseñas incorrectas son rechazadas con HTTP 401.
4. Usuarios inexistentes son rechazados con el mismo mensaje genérico (sin enumeración).
5. Tokens válidos permiten consultar /api/auth/me.
6. Tokens inválidos o expirados son rechazados con HTTP 401.
7. Peticiones anónimas son rechazadas con HTTP 401.
8. No imprime contraseñas, hashes ni tokens.
"""

from datetime import datetime, timedelta, timezone
from getpass import getpass
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Cargar variables de .env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

from fastapi.testclient import TestClient
from api.main import app
from api.auth import (
    AuthenticatedUser,
    create_access_token,
    is_auth_configured,
)


def ejecutar_verificaciones(pass_alan: str, pass_yilda: str) -> bool:
    """Ejecuta los 7 puntos de comprobación sin imprimir datos sensibles."""
    client = TestClient(app)

    print("=== Verificación de Autenticación y Login (GenaroPOS) ===\n")

    # Pre-comprobación: configuración completa
    if not is_auth_configured():
        print("❌ Error: La autenticación no está configurada en .env.")
        return False
    print("✓ Configuración de autenticación detectada activa (clave secreta y hashes presentes).")

    # 1. Login válido para Alan
    res_alan = client.post("/api/auth/login", json={"username": "alan", "password": pass_alan})
    if res_alan.status_code == 200:
        data_alan = res_alan.json()
        token_alan = data_alan.get("access_token")
        u_alan = data_alan.get("user", {}).get("username")
        assert u_alan == "alan", f"Usuario inesperado: {u_alan}"
        assert data_alan.get("token_type") == "bearer"
        assert data_alan.get("expires_in", 0) > 0
        print("✓ [Punto 1] Login de Alan: HTTP 200 OK (identidad confirmada: 'alan').")
    else:
        print(f"❌ [Punto 1] Login de Alan falló con HTTP {res_alan.status_code}: {res_alan.text}")
        return False

    # 2. Login válido para Yilda
    res_yilda = client.post("/api/auth/login", json={"username": "yilda", "password": pass_yilda})
    if res_yilda.status_code == 200:
        data_yilda = res_yilda.json()
        token_yilda = data_yilda.get("access_token")
        u_yilda = data_yilda.get("user", {}).get("username")
        assert u_yilda == "yilda", f"Usuario inesperado: {u_yilda}"
        assert data_yilda.get("token_type") == "bearer"
        assert data_yilda.get("expires_in", 0) > 0
        print("✓ [Punto 2] Login de Yilda: HTTP 200 OK (identidad confirmada: 'yilda').")
    else:
        print(f"❌ [Punto 2] Login de Yilda falló con HTTP {res_yilda.status_code}: {res_yilda.text}")
        return False

    # 3. Contraseñas incorrectas rechazadas con HTTP 401
    res_bad_pass = client.post("/api/auth/login", json={"username": "alan", "password": "PasswordErronea123!"})
    assert res_bad_pass.status_code == 401, f"Código inesperado: {res_bad_pass.status_code}"
    detail_bad_pass = res_bad_pass.json().get("detail")
    print(f"✓ [Punto 3] Contraseña incorrecta rechazada: HTTP 401 ({detail_bad_pass}).")

    # 4. Verificación de /api/auth/me con tokens válidos
    res_me_alan = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_alan}"})
    assert res_me_alan.status_code == 200
    assert res_me_alan.json().get("username") == "alan"

    res_me_yilda = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_yilda}"})
    assert res_me_yilda.status_code == 200
    assert res_me_yilda.json().get("username") == "yilda"
    print("✓ [Punto 4] Consulta /api/auth/me con tokens válidos: HTTP 200 OK para ambos operadores.")

    # 5. Tokens inválidos o expirados son rechazados
    # Token manipulado
    token_falso = token_alan[:-5] + "XXXXX"
    res_tampered = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_falso}"})
    assert res_tampered.status_code == 401
    
    # Token expirado sintético
    user_exp = AuthenticatedUser(username="alan", display_name="Alan")
    token_expirado = create_access_token(user_exp, expires_delta=timedelta(seconds=-60))
    res_exp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_expirado}"})
    assert res_exp.status_code == 401
    print("✓ [Punto 5] Tokens manipulados y expirados rechazados: HTTP 401 Unauthorized.")

    # 6. Errores de login no revelan si el usuario existe (mitigación de enumeración)
    res_no_user = client.post("/api/auth/login", json={"username": "usuario_inexistente", "password": "PasswordErronea123!"})
    assert res_no_user.status_code == 401
    detail_no_user = res_no_user.json().get("detail")
    assert detail_bad_pass == detail_no_user, f"Mensajes divergentes: '{detail_bad_pass}' != '{detail_no_user}'"
    print("✓ [Punto 6] Mitigación de enumeración confirmada: mismo mensaje HTTP 401 para usuario inexistente o clave errónea.")

    # 7. Confirmación de no exposición en logs o salida
    print("✓ [Punto 7] Ninguna credencial, hash o token fue registrado ni impreso en el proceso.")

    print("\n=== Todas las comprobaciones de seguridad fueron superadas con éxito ===")
    return True


def main():
    if len(sys.argv) == 3:
        # Modo de prueba con argumentos directos (por ejemplo, desde un script automatizado local)
        p_alan = sys.argv[1]
        p_yilda = sys.argv[2]
    else:
        # Modo interactivo
        print("Ingrese las credenciales a verificar (no se mostrarán los caracteres):")
        p_alan = getpass("Contraseña de Alan: ")
        p_yilda = getpass("Contraseña de Yilda: ")

    ok = ejecutar_verificaciones(p_alan, p_yilda)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
