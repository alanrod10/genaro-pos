import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./index.css";

export function shouldRegisterServiceWorker(
  isProd: boolean,
  protocol: string,
  hostname: string
): boolean {
  return isProd && (protocol === "https:" || hostname === "localhost" || hostname === "127.0.0.1");
}

try {
  if (typeof window !== "undefined" && "serviceWorker" in navigator) {
    if (
      shouldRegisterServiceWorker(
        Boolean(import.meta.env.PROD),
        window.location.protocol,
        window.location.hostname
      )
    ) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js").catch(() => {});
      });
    } else if (import.meta.env.DEV) {
      // En desarrollo (p. ej. Vite detrás de túnel HTTPS), desregistrar cualquier SW previo
      navigator.serviceWorker
        .getRegistrations()
        .then((registrations) => {
          for (const registration of registrations) {
            registration.unregister().catch(() => {});
          }
        })
        .catch(() => {});
    }
  }
} catch {
  // Salvaguarda fail-safe: silenciar si navigator.serviceWorker está restringido por sandbox
}

const rootEl = document.getElementById("root");
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
