"""Pruebas exhaustivas de autenticación, control de acceso y seguridad para GenaroPOS.

Verifica:
1. Login válido para Alan y para Yilda con credenciales de prueba.
2. Rechazo de contraseñas incorrectas y usuarios desconocidos (con errores genéricos).
3. Rechazo de peticiones operativas sin autenticación (HTTP 401).
4. Rechazo de tokens inválidos, corruptos, con firma falsa y expirados.
5. Acceso correcto a endpoints protegidos con una sesión válida.
6. Que el endpoint de salud no divulgue información sensible.
7. Que una configuración de autenticación incompleta no habilite acceso anónimo.
8. Que ninguna ruta operativa quede fuera de la protección de seguridad.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth import (
    AuthenticatedUser,
    create_access_token,
    decode_access_token,
    generate_password_hash,
)
from api.dependencies import get_current_user, get_db_connection
from api.main import app
from api.rate_limiter import login_rate_limiter

# Claves y contraseñas de prueba estrictamente ficticias para la suite
TEST_SECRET_KEY = "clave_secreta_de_prueba_con_mas_de_32_caracteres_12345"
TEST_ALAN_PASS = "clave-segura-alan-prueba"
TEST_YILDA_PASS = "clave-segura-yilda-prueba"


@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    """Configura variables de entorno ficticias para Alan y Yilda en cada prueba."""
    login_rate_limiter.limpiar()
    # Hashes con 1.000 iteraciones en tests para agilidad de ejecución
    alan_hash = generate_password_hash(TEST_ALAN_PASS, iterations=1_000)
    yilda_hash = generate_password_hash(TEST_YILDA_PASS, iterations=1_000)

    monkeypatch.setenv("GENARO_AUTH_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("GENARO_USER_ALAN_HASH", alan_hash)
    monkeypatch.setenv("GENARO_USER_YILDA_HASH", yilda_hash)
    monkeypatch.setenv("GENARO_AUTH_TOKEN_EXPIRE_MINUTES", "60")


@pytest.fixture
def auth_client():
    """TestClient estándar sin bypass de autenticación."""
    with TestClient(app) as client:
        yield client


# ==============================================================================
# 1. LOGIN VÁLIDO PARA ALAN Y PARA YILDA
# ==============================================================================


def test_login_valido_alan(auth_client):
    """Verifica inicio de sesión exitoso para Alan y emisión de token JWT."""
    resp = auth_client.post(
        "/api/auth/login",
        json={"username": "alan", "password": TEST_ALAN_PASS},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["username"] == "alan"
    assert data["user"]["display_name"] == "Alan"
    assert data["expires_in"] == 3600


def test_login_valido_yilda_con_normalizacion(auth_client):
    """Verifica inicio de sesión para Yilda soportando mayúsculas y espacios."""
    resp = auth_client.post(
        "/api/auth/login",
        json={"username": "  Yilda  ", "password": TEST_YILDA_PASS},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["username"] == "yilda"
    assert data["user"]["display_name"] == "Yilda"


# ==============================================================================
# 2. RECHAZO DE CONTRASEÑAS INCORRECTAS Y USUARIOS DESCONOCIDOS
# ==============================================================================


def test_login_contrasena_incorrecta(auth_client):
    """Rechaza contraseña errónea con mensaje genérico."""
    resp = auth_client.post(
        "/api/auth/login",
        json={"username": "alan", "password": "contrasena-totalmente-erronea"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Usuario o contraseña incorrectos."


def test_login_usuario_desconocido(auth_client):
    """Rechaza usuario inexistente con el mismo mensaje genérico para no filtrar existencia."""
    resp = auth_client.post(
        "/api/auth/login",
        json={"username": "carlos_admin", "password": "cualquier_password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Usuario o contraseña incorrectos."


def test_login_rechaza_registro_publico(auth_client):
    """Comprueba que no existan rutas públicas de registro o alta de usuarios."""
    resp = auth_client.post("/api/auth/register", json={"username": "nuevo"})
    assert resp.status_code in (404, 405)


# ==============================================================================
# 3. RECHAZO DE PETICIONES OPERATIVAS SIN AUTENTICACIÓN
# ==============================================================================


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/catalog"),
        ("post", "/api/sales"),
        ("get", "/api/services/resumen"),
        ("get", "/api/expenses/resumen"),
        ("get", "/api/preventistas/proveedores"),
        ("get", "/api/auth/me"),
    ],
)
def test_peticiones_operativas_sin_autenticacion_retornan_401(auth_client, method, path):
    """Garantiza que cualquier petición anónima a rutas de negocio retorne HTTP 401."""
    fn = getattr(auth_client, method)
    resp = fn(path)
    assert resp.status_code == 401
    assert "No autenticado" in resp.json()["detail"]


# ==============================================================================
# 4. RECHAZO DE TOKENS INVÁLIDOS Y EXPIRADOS
# ==============================================================================


def test_rechazo_token_malformado(auth_client):
    """Rechaza tokens que no posean la estructura estándar de 3 partes."""
    headers = {"Authorization": "Bearer esto_no_es_un_jwt_valido"}
    resp = auth_client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert "Estructura de token inválida" in resp.json()["detail"]


def test_rechazo_token_firma_falsificada(auth_client):
    """Rechaza un token firmado con una clave diferente a la del servidor."""
    user = AuthenticatedUser(username="alan", display_name="Alan")
    token_falso = create_access_token(
        user,
        secret_key="otra_clave_secreta_completamente_distinta_12345",
    )
    headers = {"Authorization": f"Bearer {token_falso}"}
    resp = auth_client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert "Firma de token inválida" in resp.json()["detail"]


def test_rechazo_token_expirado(auth_client):
    """Rechaza un token cuya fecha de expiración ya transcurrió."""
    user = AuthenticatedUser(username="alan", display_name="Alan")
    token_expirado = create_access_token(
        user,
        expires_delta=timedelta(seconds=-10),  # Expiró hace 10 segundos
    )
    headers = {"Authorization": f"Bearer {token_expirado}"}
    resp = auth_client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert "La sesión ha expirado" in resp.json()["detail"]


def test_rechazo_cabecera_con_esquema_no_bearer(auth_client):
    """Rechaza cabeceras que no utilicen el esquema Bearer."""
    headers = {"Authorization": "Basic dXNlcjpwYXNz"}
    resp = auth_client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert "Formato de autorización inválido" in resp.json()["detail"]


# ==============================================================================
# 5. ACCESO CORRECTO A ENDPOINTS PROTEGIDOS CON SESIÓN VÁLIDA
# ==============================================================================


def test_acceso_endpoint_protegido_con_token_valido(auth_client):
    """Verifica que un token emitido en el login permita consultar /api/auth/me."""
    login_resp = auth_client.post(
        "/api/auth/login",
        json={"username": "alan", "password": TEST_ALAN_PASS},
    )
    token = login_resp.json()["access_token"]

    resp = auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "alan"
    assert data["display_name"] == "Alan"
    # No expone contraseñas, hashes ni información interna
    assert "password" not in data
    assert "hash" not in data


# ==============================================================================
# 6. QUE EL ENDPOINT DE SALUD NO DIVULGUE INFORMACIÓN SENSIBLE
# ==============================================================================


def test_salud_endpoint_no_divulga_secretos(auth_client):
    """Comprueba que /health responde públicamente sin exponer configuraciones ni secretos."""
    resp = auth_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "GENARO_AUTH_SECRET_KEY" not in str(data)
    assert TEST_SECRET_KEY not in str(data)
    assert TEST_ALAN_PASS not in str(data)
    assert "password" not in data
    assert "token" not in data


# ==============================================================================
# 7. CONFIGURACIÓN INCOMPLETA NO HABILITA ACCESO ANÓNIMO
# ==============================================================================


def test_configuracion_incompleta_falla_seguro(auth_client, monkeypatch):
    """Si falta la clave secreta o es débil, el sistema falla de manera segura."""
    monkeypatch.setenv("GENARO_AUTH_SECRET_KEY", "clave_corta")  # Menos de 32 chars

    # Intento de login falla con 503
    resp_login = auth_client.post(
        "/api/auth/login",
        json={"username": "alan", "password": TEST_ALAN_PASS},
    )
    assert resp_login.status_code == 503

    # Petición operativa anónima sigue siendo rechazada (401)
    resp_op = auth_client.get("/api/catalog")
    assert resp_op.status_code == 401

    # Petición con token cualquiera tampoco permite acceso (500 de seguridad)
    resp_token = auth_client.get(
        "/api/catalog",
        headers={"Authorization": "Bearer any.token.here"},
    )
    assert resp_token.status_code in (401, 500)


# ==============================================================================
# 8. NINGUNA RUTA OPERATIVA QUEDA FUERA DE PROTECCIÓN
# ==============================================================================


def test_cobertura_total_de_rutas_operativas(auth_client):
    """Inspecciona dinámicamente app.openapi()['paths'] y verifica que todas las rutas operativas exijan auth."""
    # Rutas intencionalmente públicas
    rutas_publicas_permitidas = {
        "/health",
        "/api/auth/login",
    }

    openapi = app.openapi()
    rutas_probadas = 0

    for path, path_item in openapi["paths"].items():
        if path in rutas_publicas_permitidas:
            continue

        # Reemplazar parámetros de ruta {param} con un identificador de prueba
        test_path = (
            path.replace("{id_producto}", "1")
            .replace("{ticket_id}", "T-TEST-1")
            .replace("{item_id}", "1")
            .replace("{carga_id}", "1")
            .replace("{gasto_id}", "1")
            .replace("{pedido_id}", "PED-1")
            .replace("{fecha}", "2026-10-24")
        )

        for method in path_item.keys():
            if method.lower() in ("head", "options"):
                continue

            rutas_probadas += 1
            fn = getattr(auth_client, method.lower())
            resp = fn(test_path)
            assert resp.status_code == 401, (
                f"FALLA DE SEGURIDAD: La ruta operativa {method.upper()} {test_path} ({path}) "
                f"no exigió autenticación (respondió {resp.status_code} en vez de 401)."
            )

    assert rutas_probadas >= 20, f"Se esperaban probar múltiples rutas operativas, se probaron {rutas_probadas}"


# ==============================================================================
# 9. CARGA AUTOMÁTICA DE VARIABLES .ENV AL INICIAR API
# ==============================================================================


def test_cargar_env_local_sin_variables_previas(tmp_path):
    """Comprueba que cargar_env_local cargue variables desde un archivo .env sin sobreescribir las existentes."""
    from api.main import cargar_env_local

    dummy_env = tmp_path / ".env"
    dummy_env.write_text(
        "# Archivo de prueba\n"
        "VARIABLE_NUEVA_TEST=valor_cargado_123\n"
        "VARIABLE_EXISTENTE_TEST=valor_que_no_debe_cambiar\n"
        "   \n",
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"VARIABLE_EXISTENTE_TEST": "valor_original_del_proceso"}, clear=False):
        cargado = cargar_env_local(dummy_env)
        assert cargado is True
        assert os.environ.get("VARIABLE_NUEVA_TEST") == "valor_cargado_123"
        # Verifica que no sobreescribe la variable existente
        assert os.environ.get("VARIABLE_EXISTENTE_TEST") == "valor_original_del_proceso"


def test_arranque_proceso_sin_env_exportado_resuelve_autenticacion():
    """Verifica en un subproceso aislado que api.main cargue .env automáticamente
    y que /api/auth/login no devuelva 503 sin divulgar secretos."""
    import subprocess
    import sys

    code = """
import os, sys
# Limpiar explícitamente cualquier variable de auth en el entorno
for k in ['GENARO_AUTH_SECRET_KEY', 'GENARO_USER_ALAN_HASH', 'GENARO_USER_YILDA_HASH']:
    os.environ.pop(k, None)

# Importar api.main dispara cargar_env_local()
from api.main import app
from api.auth import is_auth_configured
from fastapi.testclient import TestClient

assert is_auth_configured() is True, "Autenticación no quedó configurada al importar api.main"

client = TestClient(app)
# Probar con contraseña incorrecta deliberada: debe responder 401 (no 503)
res = client.post('/api/auth/login', json={'username': 'alan', 'password': 'clave_incorrecta_deliberada'})
assert res.status_code == 401, f"Se esperaba HTTP 401, recibido {res.status_code}"
assert "Usuario o contraseña incorrectos" in res.json().get("detail", "")
print("SUBPROCESO_OK")
"""

    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd="/home/alanrod10/workspace/genaro-pos-turso-lab",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Fallo en subproceso: {res.stderr}\\n{res.stdout}"
    assert "SUBPROCESO_OK" in res.stdout


# ==============================================================================
# PRUEBAS DE RATE LIMITING EN INICIO DE SESIÓN
# ==============================================================================


def test_rate_limiter_bloquea_al_sexto_intento_fallido():
    """Valida que tras 5 intentos fallidos consecutivos desde la misma IP, el 6to sea HTTP 429."""
    with TestClient(app, client=("192.168.10.50", 12345)) as client:
        for i in range(5):
            res = client.post(
                "/api/auth/login",
                json={"username": "alan", "password": "password_erronea_123"},
            )
            assert res.status_code == 401, f"Intento {i+1} debió ser 401"

        res_bloqueado = client.post(
            "/api/auth/login",
            json={"username": "alan", "password": "password_erronea_123"},
        )
        assert res_bloqueado.status_code == 429
        assert "Demasiados intentos fallidos" in res_bloqueado.json().get("detail", "")
        assert res_bloqueado.headers.get("retry-after") is not None
        assert int(res_bloqueado.headers.get("retry-after")) > 0


def test_rate_limiter_aislamiento_entre_ips():
    """Valida que el bloqueo por fuerza bruta de una IP no afecte a otra IP legítima."""
    with TestClient(app, client=("192.168.10.101", 12345)) as client_atacante:
        for _ in range(5):
            client_atacante.post(
                "/api/auth/login",
                json={"username": "alan", "password": "password_erronea"},
            )
        res_atacante = client_atacante.post(
            "/api/auth/login",
            json={"username": "alan", "password": "password_erronea"},
        )
        assert res_atacante.status_code == 429

    with TestClient(app, client=("192.168.10.202", 12345)) as client_legitimo:
        res_legitimo = client_legitimo.post(
            "/api/auth/login",
            json={"username": "alan", "password": TEST_ALAN_PASS},
        )
        assert res_legitimo.status_code == 200
        assert "access_token" in res_legitimo.json()


def test_rate_limiter_reseteo_tras_login_exitoso():
    """Valida que un login exitoso resetee los intentos fallidos acumulados."""
    with TestClient(app, client=("192.168.10.77", 12345)) as client:
        for _ in range(4):
            res = client.post(
                "/api/auth/login",
                json={"username": "alan", "password": "password_incorrecta"},
            )
            assert res.status_code == 401

        res_ok = client.post(
            "/api/auth/login",
            json={"username": "alan", "password": TEST_ALAN_PASS},
        )
        assert res_ok.status_code == 200

        for _ in range(4):
            res = client.post(
                "/api/auth/login",
                json={"username": "alan", "password": "password_incorrecta"},
            )
            assert res.status_code == 401


def test_rate_limiter_no_confia_en_x_forwarded_for():
    """Valida que un atacante no pueda evadir el límite enviando encabezados X-Forwarded-For falsificados."""
    with TestClient(app, client=("192.168.10.88", 12345)) as client:
        for i in range(5):
            res = client.post(
                "/api/auth/login",
                json={"username": "alan", "password": "bad_password"},
                headers={"X-Forwarded-For": f"203.0.113.{i+1}"},
            )
            assert res.status_code == 401

        res_bloqueado = client.post(
            "/api/auth/login",
            json={"username": "alan", "password": "bad_password"},
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        assert res_bloqueado.status_code == 429


def test_rate_limiter_aislamiento_entre_usuarios_detras_de_mismo_proxy():
    """P2/P3-02: Verifica que cuando las peticiones llegan desde la misma IP (127.0.0.1 via proxy),
    5 intentos fallidos sobre 'alan' bloqueen a 'alan' con HTTP 429 pero NO bloqueen a 'yilda'."""
    with TestClient(app, client=("127.0.0.1", 5173)) as client:
        for _ in range(5):
            r = client.post(
                "/api/auth/login",
                json={"username": "alan", "password": "wrong_password"},
            )
            assert r.status_code == 401

        # Alan queda bloqueado desde 127.0.0.1
        r_alan_blocked = client.post(
            "/api/auth/login",
            json={"username": "alan", "password": TEST_ALAN_PASS},
        )
        assert r_alan_blocked.status_code == 429

        # Yilda desde la misma IP 127.0.0.1 puede iniciar sesión normalmente
        r_yilda_ok = client.post(
            "/api/auth/login",
            json={"username": "yilda", "password": TEST_YILDA_PASS},
        )
        assert r_yilda_ok.status_code == 200
        assert r_yilda_ok.json()["user"]["username"] == "yilda"


def test_rate_limiter_reloj_inyectable_expira_ventana_sin_sleep():
    """P2/P3-02: Verifica el desbloqueo determinista al avanzar el reloj inyectable sin usar sleep."""
    from api.rate_limiter import InMemoryLoginRateLimiter
    from starlette.requests import Request

    tiempo_actual = [1000.0]
    limiter = InMemoryLoginRateLimiter(
        max_fallos=3,
        ventana_segundos=60,
        clock=lambda: tiempo_actual[0],
    )
    scope = {"type": "http", "client": ("127.0.0.1", 50000), "headers": []}
    req = Request(scope)

    for _ in range(3):
        limiter.verificar_limite(req, "alan")
        limiter.registrar_fallo(req, "alan")

    with pytest.raises( Exception ) as exc_info:
        limiter.verificar_limite(req, "alan")
    assert getattr(exc_info.value, "status_code", None) == 429

    # Otra cuenta en la misma IP no está bloqueada
    limiter.verificar_limite(req, "yilda")

    # Avanzar el reloj 61 segundos desbloquea a 'alan'
    tiempo_actual[0] += 61.0
    limiter.verificar_limite(req, "alan")


def test_cors_preflight_permite_put_y_headers_explictos_en_loopback():
    """Verifica que el preflight CORS autorice PUT, Authorization e Idempotency-Key desde origen permitido y rechace orígenes externos."""
    with TestClient(app) as client:
        res_ok = client.options(
            "/api/sales/T-1/items/1",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Authorization, Content-Type, Idempotency-Key",
            },
        )
        assert res_ok.status_code == 200
        assert res_ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "PUT" in res_ok.headers.get("access-control-allow-methods", "")
        allowed_headers = res_ok.headers.get("access-control-allow-headers", "")
        assert "Authorization" in allowed_headers
        assert "Idempotency-Key" in allowed_headers

        # Origen externo no autorizado no debe recibir Access-Control-Allow-Origin
        res_bad = client.options(
            "/api/sales/T-1/items/1",
            headers={
                "Origin": "https://sitio-malicioso.example.com",
                "Access-Control-Request-Method": "PUT",
            },
        )
        assert res_bad.headers.get("access-control-allow-origin") is None
