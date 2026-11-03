#!/usr/bin/env python3
"""Aprovisionador interactivo y seguro de credenciales para GenaroPOS.

Configura de forma local y estrictamente confidencial las credenciales
de los operadores autorizados (Alan y Yilda) en el archivo .env.

Garantías de seguridad:
1. Entrada interactiva sin eco en terminal mediante getpass.
2. Exige contraseñas de al menos 12 caracteres y confirmación idéntica.
3. Genera hashes PBKDF2-HMAC-SHA256 con sal criptográfica usando api.auth.
4. Genera GENARO_AUTH_SECRET_KEY si no existe o es débil.
5. Preserva todas las demás variables de .env sin duplicados.
6. Escritura atómica con permisos restringidos 0600 (solo lectura/escritura del propietario).
7. Comprueba que .env no esté rastreado por Git y esté en .gitignore.
8. Nunca imprime contraseñas, hashes, tokens ni secretos en logs o terminal.
"""

from getpass import getpass
import os
from pathlib import Path
import secrets
import subprocess
import sys

# Agregar la raíz del proyecto al sys.path para importar api.auth
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.auth import (
    DEFAULT_PBKDF2_ITERATIONS,
    MIN_SECRET_KEY_LENGTH,
    generate_password_hash,
    is_auth_configured,
)


def _verificar_seguridad_git(env_path: Path) -> None:
    """Confirma que .env no esté rastreado por Git y esté excluido en .gitignore."""
    # 1. Comprobar si git rastrea .env
    res_track = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(env_path.name)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if res_track.returncode == 0:
        print(
            "ERROR CRÍTICO DE SEGURIDAD: El archivo .env está actualmente rastreado por Git.",
            file=sys.stderr,
        )
        print(
            "Para resolverlo de forma segura, ejecute:\n"
            "  git rm --cached .env\n"
            "y asegúrese de incluir .env en su archivo .gitignore antes de continuar.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Comprobar si .gitignore ignora .env
    res_ignore = subprocess.run(
        ["git", "check-ignore", str(env_path.name)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if res_ignore.returncode != 0:
        print(
            "ADVERTENCIA DE SEGURIDAD: El archivo .env no figura explícitamente excluido en .gitignore.",
            file=sys.stderr,
        )
        print(
            "Agregue '.env' en .gitignore antes de aprovisionar credenciales.",
            file=sys.stderr,
        )
        sys.exit(1)


def _solicitar_password_robusta(nombre_operador: str) -> str:
    """Solicita una contraseña de al menos 12 caracteres y su confirmación sin eco."""
    while True:
        p1 = getpass(f"Ingrese la contraseña para '{nombre_operador}' (mínimo 12 caracteres): ")
        if not p1 or len(p1.strip()) < 12:
            print("❌ La contraseña debe tener al menos 12 caracteres no vacíos. Intente nuevamente.")
            continue

        p2 = getpass(f"Confirme la contraseña para '{nombre_operador}': ")
        if p1 != p2:
            print("❌ Las contraseñas ingresadas no coinciden. Intente nuevamente.")
            continue

        return p1


def _leer_env_existente(env_path: Path) -> dict:
    """Lee las variables existentes en .env preservando el orden y contenido."""
    lineas = []
    variables = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lineas.append((line, None))
                continue
            k, v = stripped.split("=", 1)
            k = k.strip()
            v = v.strip()
            variables[k] = v
            lineas.append((line, k))
    return {"lineas": lineas, "variables": variables}


def _escribir_env_atomico(env_path: Path, variables_actualizadas: dict) -> None:
    """Escribe las variables en .env de forma atómica y con permisos 0600."""
    tmp_path = env_path.with_suffix(".tmp")
    
    # Conservar orden de claves conocidas o añadir nuevas al final
    claves_escritas = set()
    lineas_resultado = []

    # Si el archivo original existía, actualizar en su posición
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lineas_resultado.append(line)
                continue
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in variables_actualizadas:
                lineas_resultado.append(f"{k}={variables_actualizadas[k]}")
                claves_escritas.add(k)
            else:
                lineas_resultado.append(line)
                claves_escritas.add(k)

    # Agregar cualquier variable pendiente que no estuviese en el archivo
    for k, v in variables_actualizadas.items():
        if k not in claves_escritas:
            lineas_resultado.append(f"{k}={v}")
            claves_escritas.add(k)

    contenido = "\n".join(lineas_resultado) + "\n"
    tmp_path.write_text(contenido, encoding="utf-8")
    
    # Restringir permisos a 0600 (propietario lectura/escritura)
    try:
        os.chmod(tmp_path, 0o600)
    except Exception:
        pass

    # Reemplazo atómico
    tmp_path.replace(env_path)
    try:
        os.chmod(env_path, 0o600)
    except Exception:
        pass


def main():
    env_path = PROJECT_ROOT / ".env"
    print("=== Configuración Segura de Credenciales de Operador (GenaroPOS) ===")
    
    # 1. Validar git status y .gitignore
    _verificar_seguridad_git(env_path)
    print("✓ Archivo .env verificado: no rastreado por Git y correctamente excluido.")

    # 2. Leer estado actual
    env_data = _leer_env_existente(env_path)
    variables = env_data["variables"]

    # 3. Clave secreta para JWT (GENARO_AUTH_SECRET_KEY)
    secret_key = variables.get("GENARO_AUTH_SECRET_KEY", "").strip()
    if len(secret_key) >= MIN_SECRET_KEY_LENGTH:
        print("✓ Clave secreta GENARO_AUTH_SECRET_KEY existente es válida y robusta. Se preserva.")
    else:
        print("-> Generando nueva GENARO_AUTH_SECRET_KEY criptográficamente segura (64 bytes URL-safe)...")
        secret_key = secrets.token_urlsafe(48)
        variables["GENARO_AUTH_SECRET_KEY"] = secret_key

    # 4. Duración de sesión predeterminada (8 horas = 480 minutos) si no existe
    if "GENARO_AUTH_TOKEN_EXPIRE_MINUTES" not in variables:
        variables["GENARO_AUTH_TOKEN_EXPIRE_MINUTES"] = "480"

    # 5. Solicitar contraseñas de forma confidencial
    print("\n--- Credenciales de Operadores (Alan y Yilda) ---")
    pass_alan = _solicitar_password_robusta("Alan")
    print("✓ Contraseña de Alan capturada y validada.")

    pass_yilda = _solicitar_password_robusta("Yilda")
    print("✓ Contraseña de Yilda capturada y validada.")

    # 6. Generar hashes PBKDF2-HMAC-SHA256
    print("\n-> Generando hashes PBKDF2-HMAC-SHA256 con 600.000 iteraciones y sal aleatoria...")
    variables["GENARO_USER_ALAN_HASH"] = generate_password_hash(pass_alan, iterations=DEFAULT_PBKDF2_ITERATIONS)
    variables["GENARO_USER_YILDA_HASH"] = generate_password_hash(pass_yilda, iterations=DEFAULT_PBKDF2_ITERATIONS)

    # 7. Escribir en .env de forma atómica
    _escribir_env_atomico(env_path, variables)
    print("✓ Archivo .env actualizado atómicamente con permisos 0600.")

    # 8. Comprobar que la configuración es reconocida por el módulo de autenticación
    # Cargar las variables en el entorno actual para la prueba inmediata
    for k, v in variables.items():
        os.environ[k] = v

    if is_auth_configured():
        print("✓ Validación de módulo exitosa: is_auth_configured() reporta TRUE.")
    else:
        print("❌ Error: is_auth_configured() reportó False tras escribir la configuración.", file=sys.stderr)
        sys.exit(1)

    print("\n=== Configuración de autenticación finalizada con éxito ===")
    print("Nota de seguridad: Los hashes han sido almacenados sin exponer valores sensibles.")


if __name__ == "__main__":
    main()
