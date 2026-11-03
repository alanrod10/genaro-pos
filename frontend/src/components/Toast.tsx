import React from "react";

export interface ToastMessage {
  id: string;
  tipo: "success" | "info" | "warning" | "error";
  mensaje: string;
}

interface ToastProps {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
}

export const ToastContainer: React.FC<ToastProps> = ({ toasts, onDismiss }) => {
  return (
    <div className="toast-container" data-testid="toast-container">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast-item toast-${t.tipo}`}
          onClick={() => onDismiss(t.id)}
          data-testid={`toast-${t.tipo}`}
        >
          <span className="toast-icon">
            {t.tipo === "success" && "✅"}
            {t.tipo === "info" && "ℹ️"}
            {t.tipo === "warning" && "⚠️"}
            {t.tipo === "error" && "❌"}
          </span>
          <span className="toast-text">{t.mensaje}</span>
        </div>
      ))}
    </div>
  );
};
