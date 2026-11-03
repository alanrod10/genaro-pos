import React, { useState, useEffect } from "react";
import { getApiClient, guardarSesionLocal } from "../services/api";

interface LoginModalProps {
  isOpen: boolean;
  onClose: () => void;
  onLoginSuccess: (user: { username: string; display_name: string }) => void;
  onCancelToDemo: () => void;
}

export const LoginModal: React.FC<LoginModalProps> = ({
  isOpen,
  onClose,
  onLoginSuccess,
  onCancelToDemo,
}) => {
  const [username, setUsername] = useState<string>("alan");
  const [password, setPassword] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [showPassword, setShowPassword] = useState<boolean>(false);

  // Limpiar credenciales y mensajes de error al abrir o cerrar el modal
  useEffect(() => {
    setPassword("");
    setError(null);
    setShowPassword(false);
  }, [isOpen]);

  const handleClose = () => {
    setPassword("");
    setError(null);
    setShowPassword(false);
    onClose();
  };

  const handleCancelToDemo = () => {
    setPassword("");
    setError(null);
    setShowPassword(false);
    onCancelToDemo();
  };

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) {
      setError("Por favor, ingresá la contraseña.");
      return;
    }

    setError(null);
    setIsLoading(true);

    try {
      const api = getApiClient();
      // No aplicar trim() a la contraseña: los espacios pueden ser parte legítima de la clave
      const res = await api.login(username, password);

      if (res.status === 200 && res.data) {
        guardarSesionLocal(res.data.access_token, res.data.user);
        setPassword("");
        setError(null);
        setShowPassword(false);
        onLoginSuccess(res.data.user);
      } else {
        setError(res.error || "No fue posible iniciar sesión. Verificá tu conexión o credenciales.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al conectar con el servidor.";
      setError(`Fallo de conexión: ${msg}`);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="modal-backdrop" data-testid="login-modal-backdrop">
      <div className="modal-card" data-testid="login-modal-card">
        <div className="modal-header">
          <div className="modal-title-row">
            <span className="modal-icon">🔐</span>
            <h3 className="modal-title">Acceso Operativo GenaroPOS</h3>
          </div>
          <button
            type="button"
            className="btn-modal-close"
            onClick={handleClose}
            title="Cerrar ventana de inicio de sesión"
            data-testid="btn-close-login"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="admin-demo-banner m-0">
              Acceso restringido a usuarios autorizados (<strong>Alan</strong> y <strong>Yilda</strong>).
              Se requiere sesión autenticada para interactuar con la API local.
            </p>

            {error && (
              <div
                className="alert-box-error error-banner"
                data-testid="login-error-banner"
              >
                ⚠️ {error}
              </div>
            )}

            <div className="form-group">
              <label htmlFor="login-username">Usuario Autorizado:</label>
              <select
                id="login-username"
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                data-testid="login-username-select"
                disabled={isLoading}
              >
                <option value="alan">Alan</option>
                <option value="yilda">Yilda</option>
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="login-password">Contraseña:</label>
              <div className="password-input-wrapper">
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  className="form-input input-password-field"
                  placeholder="Ingresá tu contraseña..."
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  data-testid="login-password-input"
                  disabled={isLoading}
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  autoComplete="current-password"
                  autoFocus
                />
                <button
                  type="button"
                  className="btn-toggle-password"
                  onClick={() => setShowPassword((prev) => !prev)}
                  disabled={isLoading}
                  aria-label={showPassword ? "Ocultar contraseña" : "Ver contraseña"}
                  title={showPassword ? "Ocultar contraseña" : "Ver contraseña"}
                  data-testid="btn-toggle-password"
                >
                  {showPassword ? "👁️‍🗨️" : "👁️"}
                </button>
              </div>
            </div>
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn-modal-cancel"
              onClick={handleCancelToDemo}
              disabled={isLoading}
              data-testid="btn-cancel-to-demo"
            >
              Volver al Modo Demo
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={isLoading}
              data-testid="btn-submit-login"
            >
              {isLoading ? "Verificando..." : "Iniciar Sesión"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
