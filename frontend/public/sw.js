// Service Worker para GenaroPOS PWA
const CACHE_PREFIX = "genaropos-";
const CACHE_NAME = "genaropos-static-v3";
const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/manifest.json",
  "/favicon.svg",
  "/icon-192.png",
  "/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          // Eliminar únicamente cachés obsoletas que pertenezcan a GenaroPOS
          if (key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Exclusión estricta de la API y salud:
  // Nunca interceptar ni cachear rutas bajo /api/* ni endpoints de salud (/health).
  // Todas las consultas de catálogo, servicios, gastos, historial, preventistas, reportes
  // y autenticación (/api/auth/scram-*, /api/auth/me) deben llegar directamente al backend
  // y no servirse desde una caché antigua.
  // Asimismo, se excluyen todos los métodos que no sean GET (POST, PUT, PATCH, DELETE).
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname === "/health" ||
    url.pathname.startsWith("/health/") ||
    event.request.method !== "GET"
  ) {
    return;
  }

  // Navegación HTML (/ e /index.html): Estrategia Network-First con respaldo en caché offline.
  // Evita servir un index.html obsoleto o respuestas redirigidas de Cloudflare Access.
  const isNavigation =
    event.request.mode === "navigate" ||
    url.pathname === "/" ||
    url.pathname === "/index.html";

  if (isNavigation) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (
            response &&
            response.status === 200 &&
            response.type === "basic" &&
            !response.redirected
          ) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() =>
          caches.match(event.request).then((cached) => cached || caches.match("/index.html"))
        )
    );
    return;
  }

  // Para recursos estáticos del shell: Cache first, fallback to network
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        // En segundo plano actualiza el caché (stale-while-revalidate)
        fetch(event.request)
          .then((response) => {
            if (
              response &&
              response.status === 200 &&
              response.type === "basic" &&
              !response.redirected
            ) {
              const copy = response.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
            }
          })
          .catch(() => {});
        return cached;
      }

      return fetch(event.request).then((response) => {
        if (
          !response ||
          response.status !== 200 ||
          response.type !== "basic" ||
          response.redirected
        ) {
          return response;
        }
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      });
    })
  );
});
