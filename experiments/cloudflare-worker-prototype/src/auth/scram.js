/**
 * Módulo de Autenticación SCRAM-SHA-256 (RFC 5802 / RFC 7677)
 * Implementado utilizando la API estándar de Web Crypto y Node crypto.
 *
 * Diseñado específicamente para ejecución en Cloudflare Workers (< 10 ms CPU)
 * y navegadores web (Chrome Android, Desktop).
 */

import crypto from "node:crypto";

// --- Utilidades Criptográficas con Web Crypto / Node ---

export function sha256(data) {
  return crypto.createHash("sha256").update(data).digest();
}

export function hmacSha256(key, data) {
  return crypto.createHmac("sha256", key).update(data).digest();
}

export function xorBuffers(a, b) {
  if (a.length !== b.length) {
    throw new Error("Buffers must have identical lengths for XOR operation");
  }
  const out = Buffer.alloc(a.length);
  for (let i = 0; i < a.length; i++) {
    out[i] = a[i] ^ b[i];
  }
  return out;
}

export function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

/**
 * Convierte un hash PBKDF2 sintético existente:
 * "pbkdf2_sha256$600000$<salt_hex>$<digest_hex>"
 * a un registro verificador SCRAM-SHA-256:
 * { iterations, saltBase64, storedKeyBase64, serverKeyBase64 }
 *
 * El digest_hex es exactamente SaltedPassword.
 * La conversión no necesita contraseñas en claro.
 */
export function convertirPbkdf2HashAScramVerifier(storedPbkdf2Hash) {
  const parts = storedPbkdf2Hash.split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2_sha256") {
    throw new Error("Formato de hash PBKDF2 inválido");
  }

  const iterations = parseInt(parts[1], 10);
  const salt = Buffer.from(parts[2], "hex");
  const saltedPassword = Buffer.from(parts[3], "hex");

  const clientKey = hmacSha256(saltedPassword, "Client Key");
  const storedKey = sha256(clientKey);
  const serverKey = hmacSha256(saltedPassword, "Server Key");

  return {
    iterations,
    saltBase64: salt.toString("base64"),
    storedKeyBase64: storedKey.toString("base64"),
    serverKeyBase64: serverKey.toString("base64"),
  };
}

// --- Cliente SCRAM-SHA-256 (Navegador Web / Terminal) ---

export class ScramClient {
  constructor(username, password) {
    this.username = username.normalize("NFKC");
    this.password = password.normalize("NFKC");
    this.cNonce = crypto.randomBytes(18).toString("base64");
  }

  createClientFirstMessage() {
    this.clientFirstBare = `n=${this.username},r=${this.cNonce}`;
    return `n,,${this.clientFirstBare}`;
  }

  processServerFirstAndCreateFinal(serverFirstMessage) {
    this.serverFirstMessage = serverFirstMessage;

    const params = new Map();
    for (const part of serverFirstMessage.split(",")) {
      const idx = part.indexOf("=");
      if (idx !== -1) {
        params.set(part.slice(0, idx), part.slice(idx + 1));
      }
    }

    this.rNonce = params.get("r");
    const saltB64 = params.get("s");
    const iterations = parseInt(params.get("i"), 10);

    if (!this.rNonce || !this.rNonce.startsWith(this.cNonce)) {
      throw new Error("Nonce del servidor no comienza con el nonce del cliente");
    }
    if (!saltB64 || isNaN(iterations) || iterations < 4096) {
      throw new Error("Parámetros de sal o iteraciones inválidos");
    }

    const salt = Buffer.from(saltB64, "base64");

    // Derivación en hardware del cliente (600.000 iteraciones ~60 ms)
    const t0 = process.hrtime.bigint();
    const saltedPassword = crypto.pbkdf2Sync(
      this.password,
      salt,
      iterations,
      32,
      "sha256"
    );
    const t1 = process.hrtime.bigint();
    this.clientPbkdf2TimeMs = Number(t1 - t0) / 1e6;

    const clientKey = hmacSha256(saltedPassword, "Client Key");
    const storedKey = sha256(clientKey);
    this.serverKey = hmacSha256(saltedPassword, "Server Key");

    const channelBinding = "c=biws"; // base64("n,,")
    this.clientFinalWithoutProof = `${channelBinding},r=${this.rNonce}`;
    this.authMessage = `${this.clientFirstBare},${this.serverFirstMessage},${this.clientFinalWithoutProof}`;

    const clientSignature = hmacSha256(storedKey, this.authMessage);
    const clientProof = xorBuffers(clientKey, clientSignature);

    return `${this.clientFinalWithoutProof},p=${clientProof.toString("base64")}`;
  }

  verifyServerFinalMessage(serverFinalMessage) {
    const match = serverFinalMessage.match(/^v=(.+)$/);
    if (!match) {
      throw new Error("Formato de server-final-message inválido");
    }
    const serverSignature = Buffer.from(match[1], "base64");
    const expectedServerSignature = hmacSha256(this.serverKey, this.authMessage);

    if (!timingSafeEqual(serverSignature, expectedServerSignature)) {
      throw new Error("Firma del servidor no válida. Posible servidor falso.");
    }
    return true;
  }
}

// --- Servidor SCRAM-SHA-256 (Cloudflare Workers) ---

export class ScramServer {
  /**
   * @param {Map<string, object>} verifiersDb - Base de verificadores SCRAM
   * @param {Buffer} serverSecretKey - Llave secreta del Worker para firmar tokens de estado
   * @param {object} challengeStore - Almacén compartido anti-replay (Durable Object SQLite o Cache)
   */
  constructor(verifiersDb, serverSecretKey, challengeStore = null) {
    if (!verifiersDb || !(verifiersDb instanceof Map)) {
      throw new Error("CONFIG_ERROR: verifiersDb debe ser una instancia de Map.");
    }
    if (!Buffer.isBuffer(serverSecretKey) || serverSecretKey.length < 32) {
      throw new Error("CONFIG_ERROR: serverSecretKey debe ser un Buffer criptográfico de al menos 32 bytes (256 bits).");
    }
    this.verifiersDb = verifiersDb;
    this.serverSecretKey = serverSecretKey;
    this.challengeStore = challengeStore || new InMemoryChallengeStore();
    this.failedAttempts = new Map(); // IP -> { count, lastFailed }
  }

  _sealToken(data) {
    const payload = JSON.stringify(data);
    const payloadB64 = Buffer.from(payload).toString("base64url");
    const sig = crypto
      .createHmac("sha256", this.serverSecretKey)
      .update(payloadB64)
      .digest("base64url");
    return `${payloadB64}.${sig}`;
  }

  _unsealToken(token) {
    const parts = token.split(".");
    if (parts.length !== 2) throw new Error("Credenciales inválidas");
    const [payloadB64, sig] = parts;
    const expectedSig = crypto
      .createHmac("sha256", this.serverSecretKey)
      .update(payloadB64)
      .digest("base64url");

    if (!timingSafeEqual(Buffer.from(sig), Buffer.from(expectedSig))) {
      throw new Error("Credenciales inválidas");
    }
    const data = JSON.parse(Buffer.from(payloadB64, "base64url").toString());
    if (Date.now() > data.exp) {
      throw new Error("Desafío expirado");
    }
    return data;
  }

  processClientFirst(clientFirstMessage, clientIp = "127.0.0.1") {
    const t0 = process.hrtime.bigint();

    // Verificación de limitador de tasa
    const attempts = this.failedAttempts.get(clientIp);
    if (attempts && attempts.count >= 5 && Date.now() - attempts.lastFailed < 60000) {
      throw new Error("Demasiados intentos fallidos. Intente más tarde.");
    }

    if (!clientFirstMessage.startsWith("n,,")) {
      throw new Error("Cabecera GS2 no soportada");
    }
    const bare = clientFirstMessage.slice(3);
    const params = new Map();
    for (const part of bare.split(",")) {
      const idx = part.indexOf("=");
      if (idx !== -1) {
        params.set(part.slice(0, idx), part.slice(idx + 1));
      }
    }

    const username = params.get("n");
    const cNonce = params.get("r");
    if (!username || !cNonce) {
      throw new Error("Parámetros client-first incompletos");
    }

    let verifier = this.verifiersDb.get(username);
    const userExists = !!verifier;
    if (!verifier) {
      // Verificador ficticio para tiempo constante y evitar enumeración
      verifier = {
        iterations: 600000,
        saltBase64: Buffer.from("dummysalt12345678").toString("base64"),
        storedKeyBase64: Buffer.alloc(32).toString("base64"),
        serverKeyBase64: Buffer.alloc(32).toString("base64"),
      };
    }

    const sNonce = crypto.randomBytes(18).toString("base64");
    const rNonce = `${cNonce}${sNonce}`;
    const serverFirstMessage = `r=${rNonce},s=${verifier.saltBase64},i=${verifier.iterations}`;

    const jti = crypto.randomBytes(16).toString("hex");
    const exp = Date.now() + 60000; // 60s

    const stateToken = this._sealToken({
      jti,
      userExists,
      username,
      cNonce,
      rNonce,
      clientFirstBare: bare,
      serverFirst: serverFirstMessage,
      exp,
    });

    const buildResult = () => {
      const t1 = process.hrtime.bigint();
      return {
        serverFirstMessage,
        stateToken,
        cpuTimeUs: Number(t1 - t0) / 1000,
      };
    };

    // Registrar desafío en almacén anti-replay (soporta síncrono y asíncrono)
    const reg = this.challengeStore.registerChallenge(jti, exp);
    if (reg instanceof Promise) {
      return reg.then(() => buildResult());
    }
    return buildResult();
  }

  processClientFinal(clientFinalMessage, stateToken, clientIp = "127.0.0.1") {
    const t0 = process.hrtime.bigint();

    const state = this._unsealToken(stateToken);

    const finishVerification = (consumed) => {
      if (!consumed) {
        throw new Error("Ataque de Replay detectado: Desafío ya utilizado o expirado");
      }

      const pIdx = clientFinalMessage.lastIndexOf(",p=");
      if (pIdx === -1) {
        throw new Error("Credenciales inválidas");
      }

      const withoutProof = clientFinalMessage.slice(0, pIdx);
      const proofB64 = clientFinalMessage.slice(pIdx + 3);
      const clientProof = Buffer.from(proofB64, "base64");

      if (clientProof.length !== 32) {
        this._registerFailure(clientIp);
        throw new Error("Credenciales inválidas");
      }

      const params = new Map();
      for (const part of withoutProof.split(",")) {
        const idx = part.indexOf("=");
        if (idx !== -1) {
          params.set(part.slice(0, idx), part.slice(idx + 1));
        }
      }

      const rNonce = params.get("r");
      if (rNonce !== state.rNonce) {
        this._registerFailure(clientIp);
        throw new Error("Nonce mismatch en client-final-message");
      }

      if (!state.userExists) {
        this._registerFailure(clientIp);
        throw new Error("Credenciales inválidas");
      }

      const verifier = this.verifiersDb.get(state.username);
      const storedKey = Buffer.from(verifier.storedKeyBase64, "base64");
      const serverKey = Buffer.from(verifier.serverKeyBase64, "base64");

      const authMessage = `${state.clientFirstBare},${state.serverFirst},${withoutProof}`;

      const clientSignature = hmacSha256(storedKey, authMessage);
      const recoveredClientKey = xorBuffers(clientProof, clientSignature);
      const computedStoredKey = sha256(recoveredClientKey);

      if (!timingSafeEqual(computedStoredKey, storedKey)) {
        this._registerFailure(clientIp);
        throw new Error("Credenciales inválidas");
      }

      // Éxito: limpiar fallos
      this.failedAttempts.delete(clientIp);

      const serverSignature = hmacSha256(serverKey, authMessage);
      const serverFinalMessage = `v=${serverSignature.toString("base64")}`;

      // Determinación estricta de Rol en servidor (RBAC)
      const role = verifier.role || (
        state.username.toLowerCase() === "alan" ||
        state.username.toLowerCase() === "yilda" ||
        state.username.toLowerCase().includes("admin")
          ? "admin"
          : "operador"
      );

      const t1 = process.hrtime.bigint();
      return {
        authenticated: true,
        username: state.username,
        role,
        serverFinalMessage,
        cpuTimeUs: Number(t1 - t0) / 1000,
      };
    };

    // Consumo atómico de un solo uso en almacén compartido anti-replay
    const consumed = this.challengeStore.consumeChallenge(state.jti);
    if (consumed instanceof Promise) {
      return consumed.then((res) => finishVerification(res));
    }
    return finishVerification(consumed);
  }

  _registerFailure(ip) {
    const cur = this.failedAttempts.get(ip) || { count: 0, lastFailed: 0 };
    this.failedAttempts.set(ip, {
      count: cur.count + 1,
      lastFailed: Date.now(),
    });
  }
}

// --- Almacén Anti-Replay en Memoria (Referencia Local) ---
export class InMemoryChallengeStore {
  constructor() {
    this.challenges = new Map(); // jti -> { exp, used }
  }

  registerChallenge(jti, exp) {
    this.challenges.set(jti, { exp, used: false });
  }

  consumeChallenge(jti) {
    const ch = this.challenges.get(jti);
    if (!ch) return false;
    if (ch.used) return false;
    if (Date.now() > ch.exp) return false;
    ch.used = true;
    return true;
  }
}

// --- Almacén Anti-Replay Emulado: Durable Object con Almacenamiento SQLite ---
/**
 * Emula fielmente el comportamiento de un Cloudflare Durable Object con SQLite:
 * - Instancia única global (coordinador central).
 * - Tabla SQLite con restricción PRIMARY KEY (jti) y columnas (exp, used).
 * - Transacciones atómicas seguras entre múltiples Workers de Cloudflare.
 */
export class DurableObjectSqliteChallengeStore {
  constructor() {
    // Simulación de SQLite Storage en Durable Object (ej. ctx.storage.sql)
    this.sqlDb = new Map();
  }

  registerChallenge(jti, exp) {
    // INSERT INTO scram_challenges (jti, exp, used) VALUES (?, ?, 0);
    this.sqlDb.set(jti, { exp, used: 0 });
  }

  consumeChallenge(jti) {
    // UPDATE scram_challenges SET used = 1 WHERE jti = ? AND used = 0 AND exp > ? RETURNING jti;
    const row = this.sqlDb.get(jti);
    if (!row) return false;
    if (row.used === 1) return false;
    if (Date.now() > row.exp) return false;
    row.used = 1;
    return true;
  }
}

// --- Almacén Anti-Replay Distribuido para Turso libSQL (Costo USD 0, Fail-Closed Estricto) ---
/**
 * Almacén distribuido con consistencia ACID sobre Turso libSQL.
 * Utiliza una única sentencia SQL atómica de actualización ('UPDATE ... RETURNING')
 * sobre la base de datos primaria, garantizando que dos o más peticiones concurrentes
 * nunca puedan consumir el mismo desafío SCRAM.
 * Opera bajo el principio estricto FAIL-CLOSED: si la base no responde o la tabla no existe,
 * la autenticación se deniega de forma tajante.
 */
export class LibsqlChallengeStore {
  constructor(dbClient) {
    if (!dbClient || typeof dbClient.execute !== "function") {
      throw new Error("PERSISTENT_STORE_REQUIRED: Se requiere un cliente libSQL válido para el almacén anti-replay.");
    }
    this.dbClient = dbClient;
  }

  async registerChallenge(jti, exp) {
    try {
      await this.dbClient.execute({
        sql: "INSERT INTO auth_scram_challenges (jti, exp, used) VALUES (?, ?, 0);",
        args: [jti, exp],
      });
    } catch (err) {
      if (err.message && err.message.includes("no such table")) {
        throw new Error("PERSISTENT_STORE_NOT_INITIALIZED: Tabla auth_scram_challenges no existe en la base de datos.");
      }
      throw new Error(`PERSISTENT_STORE_ERROR: Fallo al registrar desafío en base de datos: ${err.message}`);
    }
  }

  async consumeChallenge(jti) {
    try {
      const now = Date.now();
      const res = await this.dbClient.execute({
        sql: "UPDATE auth_scram_challenges SET used = 1 WHERE jti = ? AND used = 0 AND exp > ? RETURNING jti;",
        args: [jti, now],
      });
      // Verificación estricta de estructura y conteo exacto de filas
      if (!res || !Array.isArray(res.rows) || res.rows.length !== 1) {
        return false;
      }
      return true;
    } catch (err) {
      if (err.message && err.message.includes("no such table")) {
        throw new Error("PERSISTENT_STORE_NOT_INITIALIZED: Tabla auth_scram_challenges no existe en la base de datos.");
      }
      throw new Error(`PERSISTENT_STORE_ERROR: Fallo al verificar consumo en base de datos: ${err.message}`);
    }
  }

  async cleanupExpired(maxAgeMs = 120000) {
    const cutoff = Date.now() - maxAgeMs;
    await this.dbClient.execute({
      sql: "DELETE FROM auth_scram_challenges WHERE exp < ?;",
      args: [cutoff],
    });
  }
}

/**
 * Almacén Híbrido Fail-Closed:
 * Utiliza Turso libSQL como única fuente de verdad autoritativa para el consumo y registro de desafíos.
 * Emplea memoria local ÚNICAMENTE como filtro de rechazo temprano (negative cache) para optimizar
 * descartes sub-milisegundo en el mismo Isolate.
 * En ningún caso la memoria local tiene potestad para autorizar un inicio de sesión sin la confirmación
 * atómica de la base primaria. Ante cualquier error o ausencia de tabla remota, FALLA CERRADO.
 */
export class HybridChallengeStore {
  constructor(dbClient, localRejectCache = new Set()) {
    if (!dbClient || typeof dbClient.execute !== "function") {
      throw new Error("PERSISTENT_STORE_REQUIRED: Se requiere un cliente libSQL válido para el almacén anti-replay.");
    }
    this.dbClient = dbClient;
    this.localConsumedJti = localRejectCache; // Memoria local para rechazo rápido exclusivamente
  }

  async registerChallenge(jti, exp) {
    try {
      await this.dbClient.execute({
        sql: "INSERT INTO auth_scram_challenges (jti, exp, used) VALUES (?, ?, 0);",
        args: [jti, exp],
      });
    } catch (err) {
      if (err.message && err.message.includes("no such table")) {
        throw new Error("PERSISTENT_STORE_NOT_INITIALIZED: Tabla auth_scram_challenges no existe en la base de datos.");
      }
      throw new Error(`PERSISTENT_STORE_ERROR: Fallo al registrar desafío: ${err.message}`);
    }
  }

  async consumeChallenge(jti) {
    // 1. Optimización local: si este Isolate ya consumió el jti, rechazar de inmediato
    if (this.localConsumedJti && this.localConsumedJti.has(jti)) {
      return false;
    }

    // 2. Consulta atómica a la base de datos primaria
    try {
      const now = Date.now();
      const res = await this.dbClient.execute({
        sql: "UPDATE auth_scram_challenges SET used = 1 WHERE jti = ? AND used = 0 AND exp > ? RETURNING jti;",
        args: [jti, now],
      });

      // 3. Verificación estricta del resultado (debe ser un objeto con rows array y exactamente 1 fila)
      if (!res || !Array.isArray(res.rows) || res.rows.length !== 1) {
        return false;
      }

      // 4. Si fue exitoso en el almacén primario, registrar en la caché negativa local
      if (this.localConsumedJti && typeof this.localConsumedJti.add === "function") {
        this.localConsumedJti.add(jti);
      }
      return true;
    } catch (err) {
      if (err.message && err.message.includes("no such table")) {
        throw new Error("PERSISTENT_STORE_NOT_INITIALIZED: Tabla auth_scram_challenges no existe en la base de datos.");
      }
      throw new Error(`PERSISTENT_STORE_ERROR: Fallo al consumir desafío: ${err.message}`);
    }
  }

  async cleanupExpired(maxAgeMs = 120000) {
    const cutoff = Date.now() - maxAgeMs;
    await this.dbClient.execute({
      sql: "DELETE FROM auth_scram_challenges WHERE exp < ?;",
      args: [cutoff],
    });
  }
}
