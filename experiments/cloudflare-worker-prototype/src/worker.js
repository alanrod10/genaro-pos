/**
 * Entrada principal del Cloudflare Worker para GenaroPOS (Staging).
 * Atiende las 42 rutas operativas, autenticación SCRAM-SHA-256, persistencia en Turso libSQL,
 * y sirve el Frontend React/PWA mediante Cloudflare Workers Static Assets.
 */

import { createClient } from "@libsql/client/web";
import { ScramServer, HybridChallengeStore } from "./auth/scram.js";
import { handleRequest, construirCabecerasCors, esOrigenCorsPermitido } from "./api/router.js";

// Instancias globales en isolate para optimizar reutilización de conexiones y caché segura
let _dbClient = null;
let _currentDbUrl = null;
let _currentDbAuthToken = null;

let _scramServer = null;
let _currentServerSecretHex = null;
let _currentVerifiersRaw = null;
let _currentDbClientRef = null;

/**
 * Función auxiliar para testing: reinicia el estado de singletons en memoria.
 */
export function resetWorkerSingletonsForTest() {
  _dbClient = null;
  _currentDbUrl = null;
  _currentDbAuthToken = null;
  _scramServer = null;
  _currentServerSecretHex = null;
  _currentVerifiersRaw = null;
  _currentDbClientRef = null;
}

/**
 * Valida y parsea la configuración de SCRAM bajo el principio fail-closed:
 * 1. SCRAM_SERVER_SECRET debe ser una cadena hexadecimal válida de al menos 32 bytes (64 caracteres hex).
 * 2. SCRAM_VERIFIERS_JSON debe ser un JSON válido con al menos un usuario verificado estructuralmente correcto.
 * Si falta o es inválido cualquier parámetro, falla arrojando CONFIG_ERROR sin degradar a valores por defecto.
 */
export function parseAndValidateScramConfig(env) {
  // 1. SCRAM_SERVER_SECRET (Obligatorio, hex estricto, mínimo 32 bytes / 64 caracteres)
  const rawSecret = env?.SCRAM_SERVER_SECRET;
  if (!rawSecret || typeof rawSecret !== "string" || rawSecret.trim() === "") {
    throw new Error("CONFIG_ERROR: SCRAM_SERVER_SECRET es obligatorio y no fue provisto en las variables de entorno.");
  }
  const trimmedSecret = rawSecret.trim();
  // Comprobación estricta de caracteres hexadecimales y longitud par
  if (!/^[0-9a-fA-F]+$/.test(trimmedSecret) || trimmedSecret.length % 2 !== 0) {
    throw new Error("CONFIG_ERROR: SCRAM_SERVER_SECRET tiene un formato hexadecimal inválido.");
  }
  if (trimmedSecret.length < 64) {
    throw new Error(
      `CONFIG_ERROR: SCRAM_SERVER_SECRET debe tener al menos 32 bytes (64 caracteres hexadecimales). Longitud recibida: ${trimmedSecret.length / 2} bytes.`
    );
  }
  const serverSecret = Buffer.from(trimmedSecret, "hex");
  if (serverSecret.length < 32) {
    throw new Error("CONFIG_ERROR: SCRAM_SERVER_SECRET decodificado tiene menos de 32 bytes.");
  }

  // 2. SCRAM_VERIFIERS_JSON (Obligatorio, JSON válido, objeto no vacío, verificadores válidos)
  const rawVerifiers = env?.SCRAM_VERIFIERS_JSON;
  if (!rawVerifiers) {
    throw new Error("CONFIG_ERROR: SCRAM_VERIFIERS_JSON es obligatorio y no fue provisto en las variables de entorno.");
  }

  let verifiersObj;
  if (typeof rawVerifiers === "string") {
    if (rawVerifiers.trim() === "") {
      throw new Error("CONFIG_ERROR: SCRAM_VERIFIERS_JSON no puede estar vacío.");
    }
    try {
      verifiersObj = JSON.parse(rawVerifiers);
    } catch (e) {
      throw new Error(`CONFIG_ERROR: SCRAM_VERIFIERS_JSON no es un JSON válido: ${e.message}`);
    }
  } else if (typeof rawVerifiers === "object" && rawVerifiers !== null) {
    verifiersObj = rawVerifiers;
  } else {
    throw new Error("CONFIG_ERROR: SCRAM_VERIFIERS_JSON debe ser una cadena JSON o un objeto.");
  }

  if (
    !verifiersObj ||
    typeof verifiersObj !== "object" ||
    Array.isArray(verifiersObj) ||
    Object.keys(verifiersObj).length === 0
  ) {
    throw new Error("CONFIG_ERROR: SCRAM_VERIFIERS_JSON debe contener al menos un usuario configurado.");
  }

  const verifiersMap = new Map();
  for (const [user, v] of Object.entries(verifiersObj)) {
    if (!user || typeof user !== "string" || user.trim() === "") {
      throw new Error("CONFIG_ERROR: SCRAM_VERIFIERS_JSON contiene un usuario con nombre vacío o inválido.");
    }
    if (!v || typeof v !== "object" || Array.isArray(v)) {
      throw new Error(`CONFIG_ERROR: Verificador inválido para el usuario '${user}': debe ser un objeto.`);
    }
    if (typeof v.storedKeyBase64 !== "string" || !v.storedKeyBase64.trim()) {
      throw new Error(`CONFIG_ERROR: Verificador inválido para '${user}': falta 'storedKeyBase64'.`);
    }
    if (typeof v.serverKeyBase64 !== "string" || !v.serverKeyBase64.trim()) {
      throw new Error(`CONFIG_ERROR: Verificador inválido para '${user}': falta 'serverKeyBase64'.`);
    }
    if (typeof v.saltBase64 !== "string" || !v.saltBase64.trim()) {
      throw new Error(`CONFIG_ERROR: Verificador inválido para '${user}': falta 'saltBase64'.`);
    }
    if (typeof v.iterations !== "number" || v.iterations < 4096) {
      throw new Error(
        `CONFIG_ERROR: Verificador inválido para '${user}': 'iterations' debe ser un número entero >= 4096.`
      );
    }

    // Validar longitudes criptográficas de claves decodificadas en Base64
    const storedBuf = Buffer.from(v.storedKeyBase64, "base64");
    const serverBuf = Buffer.from(v.serverKeyBase64, "base64");
    if (storedBuf.length !== 32 || serverBuf.length !== 32) {
      throw new Error(
        `CONFIG_ERROR: Verificador inválido para '${user}': 'storedKeyBase64' y 'serverKeyBase64' deben decodificar exactamente a 32 bytes (256 bits).`
      );
    }

    verifiersMap.set(user, v);
  }

  return { verifiersMap, serverSecret };
}

export function getDbClient(env) {
  if (env?.DB_CLIENT) {
    return env.DB_CLIENT;
  }
  const url = env?.TURSO_DATABASE_URL;
  if (!url || typeof url !== "string" || !url.trim()) {
    throw new Error("CONFIG_ERROR: TURSO_DATABASE_URL es obligatoria y no está configurada.");
  }
  const authToken = env?.TURSO_AUTH_TOKEN || "";

  if (!_dbClient || _currentDbUrl !== url || _currentDbAuthToken !== authToken) {
    _dbClient = createClient({ url, authToken });
    _currentDbUrl = url;
    _currentDbAuthToken = authToken;
  }
  return _dbClient;
}

export function getScramServer(env, dbClient = null) {
  if (!dbClient) {
    throw new Error("PERSISTENT_STORE_REQUIRED: dbClient es obligatorio para inicializar el servidor SCRAM en producción.");
  }

  const secretHex = env?.SCRAM_SERVER_SECRET;
  const verifiersRaw = typeof env?.SCRAM_VERIFIERS_JSON === "string"
    ? env.SCRAM_VERIFIERS_JSON
    : JSON.stringify(env?.SCRAM_VERIFIERS_JSON);

  // Invalida el singleton si cambió la configuración o el cliente de base de datos
  if (
    !_scramServer ||
    _currentServerSecretHex !== secretHex ||
    _currentVerifiersRaw !== verifiersRaw ||
    _currentDbClientRef !== dbClient
  ) {
    const { verifiersMap, serverSecret } = parseAndValidateScramConfig(env);
    const challengeStore = new HybridChallengeStore(dbClient);
    _scramServer = new ScramServer(verifiersMap, serverSecret, challengeStore);

    _currentServerSecretHex = secretHex;
    _currentVerifiersRaw = verifiersRaw;
    _currentDbClientRef = dbClient;
  }

  return _scramServer;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const originHeader = request.headers.get("origin");
    const corsHeaders = construirCabecerasCors(request, env);

    if (request.method.toUpperCase() === "OPTIONS") {
      if (originHeader && !esOrigenCorsPermitido(originHeader, request.url, env)) {
        return new Response(JSON.stringify({ detail: "Origen CORS no autorizado." }), {
          status: 403,
          headers: { "Content-Type": "application/json", ...corsHeaders },
        });
      }
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    // 1. Enrutamiento de API y Health check
    if (path.startsWith("/api/") || path === "/health") {
      try {
        const dbClient = getDbClient(env);
        const scramServer = getScramServer(env, dbClient);
        return await handleRequest(request, env, dbClient, scramServer);
      } catch (err) {
        if (err.message && (err.message.includes("CONFIG_ERROR") || err.message.includes("PERSISTENT_STORE_"))) {
          const status = err.message.includes("PERSISTENT_STORE_") ? 503 : 500;
          const msg = err.message.includes("PERSISTENT_STORE_")
            ? "Servicio de autenticación no disponible temporalmente. Intente nuevamente más tarde."
            : (err.message.includes("TURSO_DATABASE_URL")
                ? "Error de configuración del servicio de base de datos."
                : "Error de configuración del servicio de autenticación.");
          return new Response(JSON.stringify({ detail: msg }), {
            status,
            headers: { "Content-Type": "application/json", ...corsHeaders },
          });
        }
        throw err;
      }
    }

    // 2. Servir Assets Estáticos (Frontend React/PWA)
    if (env.ASSETS) {
      const response = await env.ASSETS.fetch(request);
      if (response.status === 404 && !path.includes(".")) {
        // SPA Fallback para React Router / History API
        return env.ASSETS.fetch(new Request(new URL("/", request.url), request));
      }
      return response;
    }

    // 3. Fallback raíz si no hay binding de assets
    try {
      const dbClient = getDbClient(env);
      const scramServer = getScramServer(env, dbClient);
      return await handleRequest(request, env, dbClient, scramServer);
    } catch (err) {
      if (err.message && (err.message.includes("CONFIG_ERROR") || err.message.includes("PERSISTENT_STORE_"))) {
        const status = err.message.includes("PERSISTENT_STORE_") ? 503 : 500;
        const msg = err.message.includes("PERSISTENT_STORE_")
          ? "Servicio de autenticación no disponible temporalmente. Intente nuevamente más tarde."
          : (err.message.includes("TURSO_DATABASE_URL")
              ? "Error de configuración del servicio de base de datos."
              : "Error de configuración del servicio de autenticación.");
        return new Response(JSON.stringify({ detail: msg }), {
          status,
          headers: { "Content-Type": "application/json", ...corsHeaders },
        });
      }
      throw err;
    }
  },
};
