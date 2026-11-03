import React, { useState } from "react";
import { useTheme } from "../services/theme";

interface ConfigAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
  demoRole: "admin" | "cajero";
  onSelectDemoRole: (role: "admin" | "cajero") => void;
  onRefreshCatalog?: () => void;
}

export const ConfigAdmin: React.FC<ConfigAdminProps> = ({
  onAddToast,
  isDemo,
  demoRole,
  onSelectDemoRole,
  onRefreshCatalog,
}) => {
  const [tabActiva, setTabActiva] = useState<"entorno" | "worker" | "usuarios" | "reglas" | "demo">("entorno");
  const { themeMode, setThemeMode } = useTheme();

  return (
    <div className="config-admin-container p-4 sm:p-6 max-w-7xl mx-auto space-y-6 animate-fadeIn">
      {/* Encabezado Principal */}
      <div className="bg-white dark:bg-gray-900 p-6 rounded-3xl shadow-sm border border-gray-200 dark:border-gray-800 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-2xl">⚙️</span>
            <h1 className="text-xl font-black text-gray-900 dark:text-gray-100">Configuración & Administración</h1>
            <span className="bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 text-xs font-semibold px-2.5 py-0.5 rounded-full">
              GENARO v4.7
            </span>
            <span className="bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 text-xs font-semibold px-2.5 py-0.5 rounded-full">
              {isDemo ? "Demostración Aislada" : "Modo Operativo Local"}
            </span>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Gestión de entorno, políticas contables, control de acceso RBAC y parámetros de integración.
          </p>
        </div>

        {/* Selector Rápido de Rol de Demostración */}
        <div className="flex items-center gap-2 bg-gray-50 dark:bg-gray-800/60 p-1.5 rounded-2xl border border-gray-200 dark:border-gray-700">
          <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 pl-2">Vista Demo:</span>
          <button
            type="button"
            onClick={() => {
              onSelectDemoRole("admin");
              onAddToast("Cambiado a vista completa: Administrador / Socios", "info");
            }}
            className={`px-3 py-1.5 rounded-xl text-xs font-bold transition ${
              demoRole === "admin"
                ? "bg-indigo-600 text-white shadow"
                : "text-gray-600 dark:text-gray-400 hover:text-gray-900"
            }`}
          >
            👑 Administrador
          </button>
          <button
            type="button"
            onClick={() => {
              onSelectDemoRole("cajero");
              onAddToast("Cambiado a vista simplificada: Cajero Mostrador", "info");
            }}
            className={`px-3 py-1.5 rounded-xl text-xs font-bold transition ${
              demoRole === "cajero"
                ? "bg-emerald-600 text-white shadow"
                : "text-gray-600 dark:text-gray-400 hover:text-gray-900"
            }`}
          >
            🏪 Cajero
          </button>
        </div>
      </div>

      {/* Navegación por Pestañas */}
      <div className="flex flex-wrap gap-2 border-b border-gray-200 dark:border-gray-800 pb-2">
        <button
          type="button"
          onClick={() => setTabActiva("entorno")}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
            tabActiva === "entorno"
              ? "bg-indigo-600 text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🏢 Entorno & Bases
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("worker")}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
            tabActiva === "worker"
              ? "bg-indigo-600 text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          ⚡ Bridge Worker
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("usuarios")}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
            tabActiva === "usuarios"
              ? "bg-indigo-600 text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          👥 Control de Acceso (RBAC)
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("reglas")}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
            tabActiva === "reglas"
              ? "bg-indigo-600 text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          📜 9 Reglas Económicas
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("demo")}
          className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
            tabActiva === "demo"
              ? "bg-indigo-600 text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🧪 Ajustes de Demostración
        </button>
      </div>

      {/* TAB 1: Entorno & Bases */}
      {tabActiva === "entorno" && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-3">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>🛡️</span> Aislamiento y Política Operativa
              </h3>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                  <span className="text-gray-500">Modo de Operación:</span>
                  <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">STRICT_LOCAL_SQLITE</span>
                </div>
                <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                  <span className="text-gray-500">Conexión a Turso Cloud:</span>
                  <span className="font-mono font-bold text-gray-500">Desconectada (0 URLs)</span>
                </div>
                <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                  <span className="text-gray-500">Modo WAL (SQLite):</span>
                  <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">Activo (Concurrencia Segura)</span>
                </div>
                <div className="flex justify-between py-1 border-b border-gray-100 dark:border-gray-800">
                  <span className="text-gray-500">Integridad de Esquema:</span>
                  <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">PRAGMA integrity_check = ok</span>
                </div>
              </div>
            </div>

            <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-3">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>📁</span> Rutas de Archivos en Producción
              </h3>
              <div className="space-y-2 text-xs font-mono">
                <div>
                  <span className="text-gray-400 block font-sans text-[10px]">Base Comercial (GenaroPOS):</span>
                  <span className="text-gray-800 dark:text-gray-200 text-[11px]">/home/alanrod10/production/data/genaro-pos.db</span>
                </div>
                <div>
                  <span className="text-gray-400 block font-sans text-[10px]">Base Tesorería (Libro Mayor):</span>
                  <span className="text-gray-800 dark:text-gray-200 text-[11px]">/home/alanrod10/production/data/tesoreria.db</span>
                </div>
                <div>
                  <span className="text-gray-400 block font-sans text-[10px]">Directorio de Backups:</span>
                  <span className="text-gray-800 dark:text-gray-200 text-[11px]">/home/alanrod10/backups/</span>
                </div>
                <div>
                  <span className="text-gray-400 block font-sans text-[10px]">Hash SHA-256 Respaldo Pre-Go-Live:</span>
                  <span className="text-emerald-600 dark:text-emerald-400 text-[10px] break-all">
                    8a1df991b8ab9bee27770047d6e49a000dfd53b64f957618ec0c272f071c6f76
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: Bridge Worker */}
      {tabActiva === "worker" && (
        <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-4">
          <div className="flex justify-between items-center">
            <div>
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>⚡</span> Parámetros del Servicio Autónomo Commercial Bridge
              </h3>
              <p className="text-xs text-gray-500">
                Consumidor asíncrono que traslada eventos de la Outbox hacia el motor de Tesorería.
              </p>
            </div>
            <span className="px-3 py-1 bg-amber-100 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 text-xs font-bold rounded-lg border border-amber-200 dark:border-amber-800">
              Servicio Detenido (Listo para Día D)
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded-xl border border-gray-200 dark:border-gray-700">
              <span className="text-gray-400 block text-[10px] uppercase font-semibold">Intervalo de Sondeo</span>
              <span className="text-lg font-bold text-gray-900 dark:text-gray-100">5.0 segundos</span>
            </div>
            <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded-xl border border-gray-200 dark:border-gray-700">
              <span className="text-gray-400 block text-[10px] uppercase font-semibold">Tamaño Máximo Lote</span>
              <span className="text-lg font-bold text-gray-900 dark:text-gray-100">50 eventos/ciclo</span>
            </div>
            <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded-xl border border-gray-200 dark:border-gray-700">
              <span className="text-gray-400 block text-[10px] uppercase font-semibold">Timeout de Huérfanos</span>
              <span className="text-lg font-bold text-gray-900 dark:text-gray-100">30 segundos</span>
            </div>
            <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded-xl border border-gray-200 dark:border-gray-700">
              <span className="text-gray-400 block text-[10px] uppercase font-semibold">Concurrencia</span>
              <span className="text-lg font-bold text-gray-900 dark:text-gray-100">PID Lock Atómico</span>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: Usuarios y Roles (RBAC) */}
      {tabActiva === "usuarios" && (
        <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-4">
          <div>
            <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
              <span>👥</span> Matriz de Control de Acceso Basado en Roles (RBAC)
            </h3>
            <p className="text-xs text-gray-500">
              Separación de funciones entre socios administradores, cajeros de mostrador y cobradores externos.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            <div className="p-4 rounded-xl border border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/40 dark:bg-indigo-950/20 space-y-2">
              <div className="flex items-center gap-2 font-bold text-indigo-900 dark:text-indigo-200">
                <span>👑</span> Alan (Socio)
              </div>
              <p className="text-[11px] text-gray-600 dark:text-gray-400">
                Rol: <strong>Administrador Total</strong>. Acceso irrestricto a Mostrador, Tesorería, RADAR, Arqueos, Cierres y Conciliación.
              </p>
            </div>
            <div className="p-4 rounded-xl border border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/40 dark:bg-indigo-950/20 space-y-2">
              <div className="flex items-center gap-2 font-bold text-indigo-900 dark:text-indigo-200">
                <span>👑</span> Yilda (Socia)
              </div>
              <p className="text-[11px] text-gray-600 dark:text-gray-400">
                Rol: <strong>Administradora Total</strong>. Aprobación de arqueos, conciliación de libro mayor y supervisión financiera.
              </p>
            </div>
            <div className="p-4 rounded-xl border border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/40 dark:bg-emerald-950/20 space-y-2">
              <div className="flex items-center gap-2 font-bold text-emerald-900 dark:text-emerald-200">
                <span>🏪</span> Cajero Mostrador
              </div>
              <p className="text-[11px] text-gray-600 dark:text-gray-400">
                Rol: <strong>Operativo Mostrador</strong>. Punto de venta, recargas Prepay/Claro/SUBE, gastos autorizados y arqueo informativo de turno.
              </p>
            </div>
            <div className="p-4 rounded-xl border border-amber-200 dark:border-amber-900/60 bg-amber-50/40 dark:bg-amber-950/20 space-y-2">
              <div className="flex items-center gap-2 font-bold text-amber-900 dark:text-amber-200">
                <span>📱</span> Ezequiel (Cobranzas)
              </div>
              <p className="text-[11px] text-gray-600 dark:text-gray-400">
                Rol: <strong>Cuentas en Tránsito (1310)</strong>. Administración externa de cobranzas y transferencias pendientes de liquidación.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: Las 9 Reglas Económicas Innegociables */}
      {tabActiva === "reglas" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>1️⃣</span> Adicionales Físicos (1140)
            </span>
            <p className="text-[11px] text-gray-500">
              Solo se trasladan a 1140 mediante traslado físico documentado por los socios con comprobante y usuario responsable.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>2️⃣</span> Adicionales Digitales
            </span>
            <p className="text-[11px] text-gray-500">
              Cobros con QR o transferencia permanecen en 1210 Mercado Pago; no se extraen a 1140 por cajero ordinario.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>3️⃣</span> SUBE Mercado Pago
            </span>
            <p className="text-[11px] text-gray-500">
              Acredita directamente en 1210 Mercado Pago Propio. No alimenta automáticamente adicionales en efectivo.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-purple-200 dark:border-purple-900/60 shadow-sm space-y-1">
            <span className="font-bold text-purple-900 dark:text-purple-200 flex items-center gap-1.5">
              <span>4️⃣</span> Claro en Custodia (1150)
            </span>
            <p className="text-[11px] text-gray-500">
              Fondo en custodia de terceros. <strong>Completamente excluido</strong> de la disponibilidad propia para gastos del drugstore.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>5️⃣</span> Prohibición de Provisiones Ficticias
            </span>
            <p className="text-[11px] text-gray-500">
              Las provisiones o reservas jamás crean efectivo artificial ni alteran el balance líquido sin contrapartida.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>6️⃣</span> Respeto a Fondos Comprometidos
            </span>
            <p className="text-[11px] text-gray-500">
              Los montos reservados para pago a proveedores quedan bloqueados y no pueden usarse para caja operativa.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>7️⃣</span> Pedidos a Proveedores
            </span>
            <p className="text-[11px] text-gray-500">
              El pedido o recepción conforme no genera egresos automáticos de tesorería sin orden de pago firmada.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>8️⃣</span> Barrido Diario Formal
            </span>
            <p className="text-[11px] text-gray-500">
              Al cierre de jornada, el excedente de 1110 Drugstore Diario se traslada automáticamente a 1120 Acumulado.
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-1">
            <span className="font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <span>9️⃣</span> Inmutabilidad y Reversas
            </span>
            <p className="text-[11px] text-gray-500">
              Ningún registro de venta o movimiento se borra. Las anulaciones generan contra-asientos inmutables vinculados.
            </p>
          </div>
        </div>
      )}

      {/* TAB 5: Ajustes de Demostración */}
      {tabActiva === "demo" && (
        <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-4">
          <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <span>🧪</span> Controles de la Demostración Comercial
          </h3>
          <p className="text-xs text-gray-500">
            Ajustes para que Alan y Yilda puedan experimentar la interfaz desde distintas perspectivas.
          </p>

          <div className="flex flex-wrap gap-3 pt-2">
            <button
              type="button"
              onClick={() => {
                onAddToast("Datos de demostración reinicializados.", "info");
                if (onRefreshCatalog) onRefreshCatalog();
              }}
              className="px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-xl text-xs font-semibold transition"
            >
              🔄 Recargar Catálogo de Muestra
            </button>
            <button
              type="button"
              onClick={() => {
                setThemeMode(themeMode === "dark" ? "light" : "dark");
                onAddToast(`Tema cambiado a ${themeMode === "dark" ? "Modo Claro" : "Modo Oscuro"}`, "info");
              }}
              className="px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-xl text-xs font-semibold transition"
            >
              🌓 Alternar Tema (Claro / Oscuro)
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
