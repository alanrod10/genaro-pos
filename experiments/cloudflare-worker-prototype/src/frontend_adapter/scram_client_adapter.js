/**
 * Adaptador de Frontend para React/Vite: Cliente de Autenticación SCRAM-SHA-256.
 *
 * Se conecta a la API candidata de Cloudflare Workers preservando la compatibilidad
 * con la interfaz esperada por LoginModal.tsx y los componentes de GenaroPOS.
 */

import { ScramClient } from "../auth/scram.js";

export class ScramApiClient {
  constructor(baseUrl = "") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = null;
    this.inFlightLogin = false;
  }

  setToken(token) {
    this.token = token;
  }

  /**
   * Ejecuta el handshake SCRAM-SHA-256 completo en 2 pasos de red:
   * 1. Solicita desafío al Worker (scram-first).
   * 2. Calcula PBKDF2 (600k iters) en el navegador del cliente.
   * 3. Envía ClientProof y recibe ServerSignature + JWT (scram-final).
   * 4. Verifica la firma del servidor antes de considerar la sesión válida.
   */
  async login(username, password) {
    if (this.inFlightLogin) {
      return { status: 429, error: "Ya existe un inicio de sesión en curso. Por favor espere." };
    }

    this.inFlightLogin = true;
    try {
      const client = new ScramClient(username, password);

      // Paso 1: Enviar client-first
      const clientFirstMessage = client.createClientFirstMessage();
      const firstResp = await fetch(`${this.baseUrl}/api/auth/scram-first`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientFirstMessage }),
      });

      if (!firstResp.ok) {
        const errData = await firstResp.json().catch(() => ({}));
        return {
          status: firstResp.status,
          error: errData.detail || `Error en primer paso de autenticación (HTTP ${firstResp.status})`,
        };
      }

      const firstData = await firstResp.json();

      // Paso 2: Calcular PBKDF2 y generar client-final
      const clientFinalMessage = client.processServerFirstAndCreateFinal(firstData.serverFirstMessage);

      // Paso 3: Enviar client-final y verificar prueba en el Worker
      const finalResp = await fetch(`${this.baseUrl}/api/auth/scram-final`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clientFinalMessage,
          stateToken: firstData.stateToken,
        }),
      });

      if (!finalResp.ok) {
        const errData = await finalResp.json().catch(() => ({}));
        return {
          status: finalResp.status,
          error: errData.detail || "Credenciales inválidas",
        };
      }

      const finalData = await finalResp.json();

      // Paso 4: El cliente valida la firma del servidor (Autenticación Mutua)
      client.verifyServerFinalMessage(finalData.serverFinalMessage);

      // Guardar token JWT emitido
      this.token = finalData.access_token;

      return {
        status: 200,
        data: {
          access_token: finalData.access_token,
          token_type: finalData.token_type,
          user: finalData.user,
        },
      };
    } catch (err) {
      return {
        status: 0,
        error: `Error de conexión o autenticación: ${err.message}`,
      };
    } finally {
      this.inFlightLogin = false;
    }
  }

  async fetchWithAuth(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (this.token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    const resp = await fetch(`${this.baseUrl}${path}`, { ...options, headers });
    return resp;
  }
}
