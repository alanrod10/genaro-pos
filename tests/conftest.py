"""Configuración compartida de pytest para GenaroPOS.

Barrera de seguridad: captura, al inicio de la sesión, el hash SHA-256 de las bases
persistentes de Producción y de Staging, y verifica al finalizar que ninguna prueba
las haya modificado. Si alguna cambió, la sesión se marca como fallida.

Las pruebas deben operar exclusivamente sobre bases temporales (tmp_path / memoria).
"""

import hashlib
from pathlib import Path
from typing import Dict, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BASES_PROTEGIDAS = {
    "produccion_comercial": Path("/home/alanrod10/production/data/genaro-pos.db"),
    "produccion_tesoreria": Path("/home/alanrod10/production/data/tesoreria.db"),
    "staging_comercial": PROJECT_ROOT / "staging" / "genaro_pos_staging.db",
    "staging_tesoreria": PROJECT_ROOT / "staging" / "tesoreria_staging.db",
}


def _sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _capturar_hashes() -> Dict[str, Optional[str]]:
    return {nombre: _sha256(ruta) for nombre, ruta in BASES_PROTEGIDAS.items()}


# Captura en tiempo de importación del conftest (antes de ejecutar cualquier prueba).
HASHES_INICIALES = _capturar_hashes()


@pytest.fixture(scope="session")
def hashes_bases_protegidas_iniciales() -> Dict[str, Optional[str]]:
    """Hashes de las bases persistentes protegidas al inicio de la sesión."""
    return dict(HASHES_INICIALES)


def pytest_sessionfinish(session, exitstatus):
    finales = _capturar_hashes()
    alteradas = [n for n in BASES_PROTEGIDAS if HASHES_INICIALES[n] != finales[n]]
    if alteradas:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = (
            "VIOLACIÓN DE AISLAMIENTO: la sesión de pruebas modificó bases persistentes protegidas: "
            + ", ".join(f"{n} ({BASES_PROTEGIDAS[n]})" for n in alteradas)
        )
        if reporter:
            reporter.write_line(msg, red=True, bold=True)
        else:
            print(msg)
        session.exitstatus = 1
