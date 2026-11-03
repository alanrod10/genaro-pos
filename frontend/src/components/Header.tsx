import React from "react";
import { ThemeMode, useTheme } from "../services/theme";

export type ActiveViewType =
  | "caja"
  | "productos"
  | "servicios"
  | "gastos"
  | "historial"
  | "preventistas"
  | "dashboard"
  | "tesoreria"
  | "radar"
  | "configuracion";

interface HeaderProps {
  isOnline: boolean;
  isDemo: boolean;
  isStaticDemoOnly?: boolean;
  isReadOnlySmoke?: boolean;
  currentUser?: { username: string; display_name: string } | null;
  activeView: ActiveViewType;
  onSelectView: (view: ActiveViewType) => void;
  onToggleDemo: () => void;
  onLogout?: () => void;
  onOpenLogin?: () => void;
  pendingCount: number;
  reviewCount: number;
  onOpenQueue: () => void;
  themeMode?: ThemeMode;
  onChangeTheme?: (mode: ThemeMode) => void;
  demoRole?: "admin" | "cajero";
  onSelectDemoRole?: (role: "admin" | "cajero") => void;
  syncStatus?: {
    sincronizadas: number;
    pendientes: number;
    requiere_atencion: boolean;
    mensaje_cajero: string;
  } | null;
}

export const Header: React.FC<HeaderProps> = ({
  isOnline,
  isDemo,
  isStaticDemoOnly = false,
  isReadOnlySmoke = false,
  currentUser,
  activeView,
  onSelectView,
  onToggleDemo,
  onLogout,
  onOpenLogin,
  pendingCount,
  reviewCount,
  onOpenQueue,
  themeMode: propThemeMode,
  onChangeTheme: propOnChangeTheme,
  demoRole = "admin",
  onSelectDemoRole,
  syncStatus,
}) => {
  const internalTheme = useTheme();
  const activeThemeMode = propThemeMode ?? internalTheme.themeMode;
  const handleSelectTheme = propOnChangeTheme ?? internalTheme.setThemeMode;

  return (
    <header className="header">
      <div className="header-top-bar">
        <div className="brand">
          <span className="brand-logo" aria-hidden="true">🏪</span>
          <div className="brand-text-group">
            <span className="brand-title">GenaroPOS</span>
            <span className="brand-badge">
              {isStaticDemoOnly ? "Demo Pública" : "Caja Web"}
            </span>
          </div>
        </div>

        <div className="header-right">
          {/* Selector de Rol de Demostración: Administrador vs Cajero */}
          {onSelectDemoRole && (
            <div className="role-selector-header" data-testid="role-selector-header">
              <button
                type="button"
                className={`btn-role-badge ${demoRole === "admin" ? "role-admin-active" : "role-cajero-active"}`}
                onClick={() => onSelectDemoRole(demoRole === "admin" ? "cajero" : "admin")}
                title="Cambiar perspectiva entre Administrador y Cajero Mostrador"
                data-testid="btn-toggle-role"
              >
                <span>{demoRole === "admin" ? "👑 Admin" : "🏪 Cajero"}</span>
              </button>
            </div>
          )}

          {/* Selector de Tema (Claro / Oscuro / Sistema) */}
          <div
            className="theme-selector"
            role="group"
            aria-label="Selector de tema visual"
            data-testid="theme-selector"
          >
            <button
              type="button"
              className={`btn-theme-option ${activeThemeMode === "light" ? "theme-active" : ""}`}
              onClick={() => handleSelectTheme("light")}
              title="Modo Claro (Warm Ivory)"
              aria-label="Modo Claro"
              aria-pressed={activeThemeMode === "light"}
              data-testid="theme-btn-light"
            >
              <span className="theme-label">☀️ Claro</span>
            </button>
            <button
              type="button"
              className={`btn-theme-option ${activeThemeMode === "dark" ? "theme-active" : ""}`}
              onClick={() => handleSelectTheme("dark")}
              title="Modo Oscuro (Midnight Terracotta)"
              aria-label="Modo Oscuro"
              aria-pressed={activeThemeMode === "dark"}
              data-testid="theme-btn-dark"
            >
              <span className="theme-label">🌙 Oscuro</span>
            </button>
            <button
              type="button"
              className={`btn-theme-option ${activeThemeMode === "system" ? "theme-active" : ""}`}
              onClick={() => handleSelectTheme("system")}
              title="Tema según preferencia del dispositivo"
              aria-label="Tema del Sistema"
              aria-pressed={activeThemeMode === "system"}
              data-testid="theme-btn-system"
            >
              <span className="theme-label">💻 Auto</span>
            </button>
          </div>

          {/* Toggle Modo Demo / Real (o badge estático si es build demo) */}
          {isStaticDemoOnly ? (
            <div
              className="badge-demo demo-active static-demo-pill"
              title="Demostración pública con datos ficticios"
              data-testid="static-demo-badge"
            >
              🧪 Demo Simulada
            </div>
          ) : (
            <button
              type="button"
              onClick={onToggleDemo}
              className={`badge-demo ${isDemo ? "demo-active" : "real-active"} ${!isDemo && isReadOnlySmoke ? "smoke-readonly-active" : ""}`}
              title={
                isDemo
                  ? "Modo Demo Seguro con Datos Simulados"
                  : isReadOnlySmoke
                  ? "Modo Conectado de Solo Lectura: Escrituras y sincronización bloqueadas"
                  : "Alternar entre Modo Demo seguro y Conexión Backend"
              }
              data-testid={!isDemo && isReadOnlySmoke ? "badge-readonly-smoke" : "toggle-demo"}
            >
              {isDemo ? "🧪 Modo Demo (Simulado)" : isReadOnlySmoke ? "🔒 API DEV (Solo Lectura)" : "⚡ API Local"}
            </button>
          )}

          {/* Identidad de Usuario y Cierre de Sesión (en modo conectado) */}
          {!isStaticDemoOnly && !isDemo && (
            <div className="auth-header-controls" data-testid="auth-header-controls">
              {currentUser ? (
                <>
                  <span
                    className="status-pill user-session-pill"
                    title={`Sesión activa: ${currentUser.display_name}`}
                    data-testid="user-badge"
                  >
                    👤 {currentUser.display_name}
                  </span>
                  <button
                    type="button"
                    onClick={onLogout}
                    className="btn-modal-cancel btn-header-logout"
                    title="Cerrar sesión segura"
                    data-testid="btn-logout"
                  >
                    Salir
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={onOpenLogin}
                  className="btn-primary btn-header-login"
                  title="Iniciar sesión para acceder a la API"
                  data-testid="btn-open-login"
                >
                  🔐 Ingresar
                </button>
              )}
            </div>
          )}

          {/* Indicador de Conexión de Red */}
          <div
            className={`status-pill ${isOnline ? "pill-online" : "pill-offline"}`}
            title={isOnline ? "Conectividad de red activa" : "Sin conexión a internet (Modo Offline)"}
            data-testid="network-status"
          >
            <span className="status-dot"></span>
            <span className="network-label">{isOnline ? "En Línea" : "Desconectado"}</span>
          </div>

          {/* Indicador de Sincronización Simplificado para Cajero (Etapa 5) */}
          {syncStatus && (
            <div
              className={`status-pill ${
                syncStatus.requiere_atencion
                  ? "pill-offline sync-pill-attention"
                  : syncStatus.pendientes > 0
                  ? "pill-sync-pending"
                  : "pill-online sync-pill-ok"
              }`}
              title={syncStatus.mensaje_cajero}
              data-testid="cashier-sync-indicator"
            >
              <span className="status-dot"></span>
              <span className="sync-status-label">
                {syncStatus.requiere_atencion
                  ? "⚠️ Atención Sync"
                  : syncStatus.pendientes > 0
                  ? `⏳ Sync (${syncStatus.pendientes})`
                  : "✓ Tesorería al día"}
              </span>
            </div>
          )}

          {/* Botón de Cola de Sincronización */}
          <button
            type="button"
            onClick={onOpenQueue}
            className="btn-queue"
            title="Ver cola local de sincronización"
            data-testid="btn-open-queue"
          >
            <span className="queue-icon" aria-hidden="true">📤</span>
            <span className="queue-label">Cola</span>
            {pendingCount > 0 && (
              <span className="queue-badge-pending" data-testid="badge-pending">
                {pendingCount}
              </span>
            )}
            {reviewCount > 0 && (
              <span className="queue-badge-review" data-testid="badge-review">
                {reviewCount} ⚠️
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Navegación Principal de Módulos */}
      <div className="header-left">
        <nav className="nav-modules" aria-label="Módulos del sistema" data-testid="nav-modules">
          <button
            type="button"
            className={`btn-nav-module ${activeView === "caja" ? "nav-active" : ""}`}
            onClick={() => onSelectView("caja")}
            data-testid="nav-btn-caja"
          >
            🏪 Caja
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "productos" ? "nav-active" : ""}`}
            onClick={() => onSelectView("productos")}
            data-testid="nav-btn-productos"
          >
            📦 Productos
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "servicios" ? "nav-active" : ""}`}
            onClick={() => onSelectView("servicios")}
            data-testid="nav-btn-servicios"
          >
            ⚡ Servicios
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "gastos" ? "nav-active" : ""}`}
            onClick={() => onSelectView("gastos")}
            data-testid="nav-btn-gastos"
          >
            💰 Gastos y Cierre
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "historial" ? "nav-active" : ""}`}
            onClick={() => onSelectView("historial")}
            data-testid="nav-btn-historial"
          >
            📜 Historial
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "preventistas" ? "nav-active" : ""}`}
            onClick={() => onSelectView("preventistas")}
            data-testid="nav-btn-preventistas"
          >
            🚚 Preventistas
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "dashboard" ? "nav-active" : ""}`}
            onClick={() => onSelectView("dashboard")}
            data-testid="nav-btn-dashboard"
          >
            📊 Visor
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "tesoreria" ? "nav-active" : ""}`}
            onClick={() => onSelectView("tesoreria")}
            data-testid="nav-btn-tesoreria"
          >
            🏦 Tesorería Lab
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "radar" ? "nav-active" : ""}`}
            onClick={() => onSelectView("radar")}
            data-testid="nav-btn-radar"
          >
            🛰️ RADAR
          </button>
          <button
            type="button"
            className={`btn-nav-module ${activeView === "configuracion" ? "nav-active" : ""}`}
            onClick={() => onSelectView("configuracion")}
            data-testid="nav-btn-configuracion"
          >
            ⚙️ Configuración
          </button>
        </nav>
      </div>
    </header>
  );
};
