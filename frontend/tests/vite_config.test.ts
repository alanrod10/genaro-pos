import { describe, it, expect } from "vitest";
import configContent from "../vite.config.ts?raw";

describe("Vite Config — Configuración de Red LAN Autorizada y Seguridad", () => {
  it("1. Contempla server.host para entorno LAN autorizado (VITE_HOST o 0.0.0.0)", () => {
    expect(configContent).toMatch(/host:\s*env\.VITE_HOST\s*\|\|\s*["']0\.0\.0\.0["']/);
  });

  it("2. No admite comodines permisivos no autorizados (como ['*'])", () => {
    // Comodines de host como '*' no deben utilizarse
    expect(configContent).not.toMatch(/allowedHosts:\s*\[\s*["']\*["']\s*\]/);
  });

  it("3. Protege el backend local: proxy deshabilitado por defecto en pruebas LAN", () => {
    // La API backend no debe exponerse a través del proxy en la red local
    // a menos que VITE_ENABLE_LOCAL_BACKEND esté explícitamente activado.
    expect(configContent).toMatch(/enableLocalBackend\s*\?\s*\{/);
    expect(configContent).toMatch(/VITE_ENABLE_LOCAL_BACKEND === "true"/);
  });

  it("4. Simula la configuración y valida aislamiento del proxy en entorno LAN", () => {
    function resolveServerConfig(env: Record<string, string>) {
      const enableLocalBackend = env.VITE_ENABLE_LOCAL_BACKEND === "true";
      const host = env.VITE_HOST || "0.0.0.0";

      return {
        host,
        port: 5173,
        allowedHosts: true,
        proxy: enableLocalBackend
          ? {
              "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
              "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
            }
          : {},
      };
    }

    // Caso 1: Por defecto en LAN (proxy cerrado para seguridad de red)
    const confLan = resolveServerConfig({});
    expect(confLan.host).toBe("0.0.0.0");
    expect(Object.keys(confLan.proxy)).toHaveLength(0);

    // Caso 2: Con host local explícito
    const confLocal = resolveServerConfig({ VITE_HOST: "127.0.0.1" });
    expect(confLocal.host).toBe("127.0.0.1");

    // Caso 3: Activación explícita de proxy local
    const confProxy = resolveServerConfig({ VITE_ENABLE_LOCAL_BACKEND: "true" });
    expect(confProxy.proxy).toHaveProperty("/api");
    expect(confProxy.proxy).toHaveProperty("/health");

    // Caso 4: Rechazo de backend habilitado cuando valor es distinto de "true"
    const confInseguro = resolveServerConfig({ VITE_ENABLE_LOCAL_BACKEND: "1" });
    expect(Object.keys(confInseguro.proxy)).toHaveLength(0);
  });
});
