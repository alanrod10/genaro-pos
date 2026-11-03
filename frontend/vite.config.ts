/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const enableLocalBackend = env.VITE_ENABLE_LOCAL_BACKEND === "true";
  const allowedHttpsHost = (env.VITE_ALLOWED_HTTPS_HOST || "").trim();

  return {
    plugins: [react()],
    server: {
      host: env.VITE_HOST || "0.0.0.0",
      port: 5173,
      allowedHosts: true,
      // Seguridad LAN: El proxy hacia la API real está DESHABILITADO por defecto.
      // Para pruebas por LAN, permanece deshabilitado evitando que dispositivos en la red
      // accedan a la API local FastAPI sin autenticación.
      // Solo se activa en localhost cuando VITE_ENABLE_LOCAL_BACKEND=true de forma explícita.
      proxy: enableLocalBackend
        ? {
            "/api": {
              target: "http://127.0.0.1:8000",
              changeOrigin: true,
            },
            "/health": {
              target: "http://127.0.0.1:8000",
              changeOrigin: true,
            },
          }
        : {},
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: "./tests/setup.ts",
    },
  };
});
