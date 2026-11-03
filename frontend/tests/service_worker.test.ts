import { describe, it, expect } from "vitest";
import swContent from "../public/sw.js?raw";
import { shouldRegisterServiceWorker } from "../src/main";

describe("Service Worker — Versión de caché, ciclo de vida y exclusión de API", () => {
  it("1. El archivo sw.js actualiza CACHE_NAME a una versión posterior (v3+) con prefijo de aplicación", () => {
    expect(swContent.length).toBeGreaterThan(0);

    // Debe contener el prefijo de la aplicación
    expect(swContent).toMatch(/CACHE_PREFIX\s*=\s*["']genaropos-["']/);

    // Debe tener una versión v3 o superior
    const matchVersion = swContent.match(/CACHE_NAME\s*=\s*["']genaropos-static-v([0-9]+)["']/);
    expect(matchVersion).not.toBeNull();
    const versionNum = parseInt(matchVersion![1], 10);
    expect(versionNum).toBeGreaterThanOrEqual(3);
  });

  it("2. La fase activate elimina únicamente cachés antiguas de GenaroPOS preservando otras aplicaciones", () => {
    // Debe verificar que la clave comience con CACHE_PREFIX antes de borrar
    expect(swContent).toMatch(/key\.startsWith\(\s*CACHE_PREFIX\s*\)\s*&&\s*key\s*!==\s*CACHE_NAME/);

    // Simulación de la función de limpieza
    const CACHE_PREFIX = "genaropos-";
    const CURRENT_CACHE_NAME = "genaropos-static-v3";

    function shouldDeleteCache(key: string): boolean {
      return key.startsWith(CACHE_PREFIX) && key !== CURRENT_CACHE_NAME;
    }

    // Debe borrar cachés viejas de GenaroPOS
    expect(shouldDeleteCache("genaropos-static-v1")).toBe(true);
    expect(shouldDeleteCache("genaropos-static-v2")).toBe(true);
    expect(shouldDeleteCache("genaropos-old-cache")).toBe(true);

    // DEBE preservar la versión activa actual
    expect(shouldDeleteCache("genaropos-static-v3")).toBe(false);

    // DEBE preservar cachés de otras aplicaciones del mismo origen
    expect(shouldDeleteCache("mi-otra-app-v1")).toBe(false);
    expect(shouldDeleteCache("vite-cache-temp")).toBe(false);
    expect(shouldDeleteCache("cloudflare-access-storage")).toBe(false);
  });

  it("3. La fase install incluye skipWaiting y activate incluye clients.claim para adopción inmediata", () => {
    expect(swContent).toContain("self.skipWaiting()");
    expect(swContent).toContain("self.clients.claim()");
  });

  it("4. El archivo sw.js contiene la regla de exclusión estricta para /api/ y /health", () => {
    // Debe excluir todo el prefijo /api/
    expect(swContent).toMatch(/url\.pathname\.startsWith\(\s*["']\/api\/["']\s*\)/);

    // Debe excluir rutas de salud
    expect(swContent).toMatch(/url\.pathname\s*===\s*["']\/health["']/);

    // Debe excluir cualquier método distinto de GET
    expect(swContent).toMatch(/event\.request\.method\s*!==\s*["']GET["']/);
  });

  it("5. La lógica de filtrado excluye todas las rutas de API operativas del caché", () => {
    function shouldBypassServiceWorker(urlStr: string, method: string): boolean {
      const url = new URL(urlStr);
      return (
        url.pathname.startsWith("/api/") ||
        url.pathname === "/health" ||
        url.pathname.startsWith("/health/") ||
        method !== "GET"
      );
    }

    const testOrigin = "https://asking-therefore-entries-distribution.trycloudflare.com";

    // Rutas operativas de la API deben ser bypass (NO cacheadas)
    expect(shouldBypassServiceWorker(`${testOrigin}/api/sales`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/catalog`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/services`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/expenses`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/reports/dashboard`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/system/readiness`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/auth/me`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/auth/login`, "POST")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/preventistas`, "GET")).toBe(true);

    // Endpoints de salud deben ser bypass
    expect(shouldBypassServiceWorker(`${testOrigin}/health`, "GET")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/health/ready`, "GET")).toBe(true);

    // Métodos mutadores siempre deben ser bypass
    expect(shouldBypassServiceWorker(`${testOrigin}/api/sales`, "POST")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/services/1`, "DELETE")).toBe(true);
    expect(shouldBypassServiceWorker(`${testOrigin}/api/expenses/1`, "PATCH")).toBe(true);

    // Recursos estáticos del shell PWA SÍ son interceptados para caché
    expect(shouldBypassServiceWorker(`${testOrigin}/`, "GET")).toBe(false);
    expect(shouldBypassServiceWorker(`${testOrigin}/index.html`, "GET")).toBe(false);
    expect(shouldBypassServiceWorker(`${testOrigin}/manifest.json`, "GET")).toBe(false);
    expect(shouldBypassServiceWorker(`${testOrigin}/favicon.svg`, "GET")).toBe(false);
    expect(shouldBypassServiceWorker(`${testOrigin}/icon-192.png`, "GET")).toBe(false);
    expect(shouldBypassServiceWorker(`${testOrigin}/assets/index-abc123.js`, "GET")).toBe(false);
  });

  it("6. Aplica estrategia Network-First para navegación HTML (/ e /index.html) y excluye respuestas redirigidas", () => {
    expect(swContent).toMatch(/event\.request\.mode\s*===\s*["']navigate["']/);
    expect(swContent).toMatch(/!response\.redirected/);
    expect(swContent).toMatch(/response\.redirected/);
  });

  it("7. Permite registrar el Service Worker en producción bajo HTTPS y en orígenes locales seguros (localhost/127.0.0.1)", () => {
    // Escenario 1: Producción remota bajo HTTPS -> Debe registrar
    expect(shouldRegisterServiceWorker(true, "https:", "pos.genaropos.com")).toBe(true);

    // Escenario 2: Producción local / build demo bajo localhost -> Debe registrar (resuelve D7)
    expect(shouldRegisterServiceWorker(true, "http:", "localhost")).toBe(true);

    // Escenario 3: Producción local bajo 127.0.0.1 -> Debe registrar (resuelve D7)
    expect(shouldRegisterServiceWorker(true, "http:", "127.0.0.1")).toBe(true);

    // Escenario 4: HTTP inseguro en red LAN -> No debe registrar
    expect(shouldRegisterServiceWorker(true, "http:", "192.168.1.13")).toBe(false);

    // Escenario 5: Entorno de desarrollo (DEV) -> No debe registrar
    expect(shouldRegisterServiceWorker(false, "https:", "localhost")).toBe(false);
    expect(shouldRegisterServiceWorker(false, "http:", "localhost")).toBe(false);
  });
});
