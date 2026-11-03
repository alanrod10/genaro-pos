import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
// @ts-ignore
import crypto from "node:crypto";
import { RealApiClient, MockApiClient, getApiClient, activarModoDemo } from "../src/services/api";
import { LoginModal } from "../src/components/LoginModal";
import { Header } from "../src/components/Header";
import { App } from "../src/App";

function createMockScramFetch(
  user: { username: string; display_name: string; role?: string },
  password = "mi_password_secreto",
  token = "nuevo_token_emitido_alan"
) {
  const salt = crypto.randomBytes(16);
  const saltB64 = salt.toString("base64");
  const sNonce = crypto.randomBytes(12).toString("base64");
  const iterations = 4096;
  let clientFirstBare = "";
  let rNonce = "";
  let serverFirst = "";

  return vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.includes("/api/auth/scram-first")) {
      const body = JSON.parse(String(init?.body || "{}"));
      const clientFirst = body.clientFirstMessage || "";
      const match = clientFirst.match(/^n,,n=(.+),r=(.+)$/);
      clientFirstBare = clientFirst.replace(/^n,,/, "");
      const cNonce = match ? match[2] : "mock_c_nonce";
      rNonce = `${cNonce}${sNonce}`;
      serverFirst = `r=${rNonce},s=${saltB64},i=${iterations}`;
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          serverFirstMessage: serverFirst,
          stateToken: "mock_state_token",
        }),
      };
    }
    if (urlStr.includes("/api/auth/scram-final")) {
      const saltedPassword = crypto.pbkdf2Sync(
        password.normalize("NFKC"),
        salt,
        iterations,
        32,
        "sha256"
      );
      const serverKey = crypto
        .createHmac("sha256", saltedPassword)
        .update("Server Key")
        .digest();
      const authMessage = `${clientFirstBare},${serverFirst},c=biws,r=${rNonce}`;
      const serverSignature = crypto
        .createHmac("sha256", serverKey)
        .update(authMessage)
        .digest("base64");
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          serverFinalMessage: `v=${serverSignature}`,
          access_token: token,
          token_type: "bearer",
          user: {
            username: user.username,
            display_name: user.display_name,
            role: user.role || "operador",
          },
        }),
      };
    }
    if (urlStr.includes("/api/catalog")) {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({ total: 0, productos: [] }),
      };
    }
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ ok: true, status: "PREPARADO" }),
    };
  });
}

describe("Seguridad e Integración de Autenticación en Cliente (Frontend)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    activarModoDemo();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
    activarModoDemo();
  });

  // 1. RealApiClient adjunta la cabecera Authorization: Bearer <token>
  it("1. RealApiClient adjunta la cabecera Authorization con el token de sesión en peticiones protegidas", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ total: 0, productos: [] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    client.setAuthToken("jwt_test_token_alan_12345");

    await client.obtenerCatalogo();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/catalog");
    expect(options.headers.get("Authorization")).toBe("Bearer jwt_test_token_alan_12345");
  });

  // 2. RealApiClient activa onUnauthorized al recibir un HTTP 401
  it("2. RealApiClient detecta HTTP 401 y dispara el callback de sesión vencida", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Token inválido o expirado" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const onUnauthorizedMock = vi.fn();
    const client = new RealApiClient("http://127.0.0.1:8000");
    client.setAuthToken("expired_token");
    client.setOnUnauthorized(onUnauthorizedMock);

    const res = await client.obtenerCatalogo();

    expect(res.status).toBe(401);
    expect(onUnauthorizedMock).toHaveBeenCalledTimes(1);
  });

  // 3. RealApiClient.login ejecuta autenticación SCRAM-SHA-256 en dos fases sin transmitir contraseñas en claro
  it("3. RealApiClient.login ejecuta autenticación SCRAM-SHA-256 en dos fases y almacena el token emitido sin enviar contraseñas en claro", async () => {
    const mockFetch = createMockScramFetch(
      { username: "alan", display_name: "Alan", role: "admin" },
      "mi_password_secreto",
      "nuevo_token_emitido_alan"
    );
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    const loginRes = await client.login("alan", "mi_password_secreto");

    expect(loginRes.status).toBe(200);
    expect(loginRes.data?.user.username).toBe("alan");
    expect(client.getAuthToken()).toBe("nuevo_token_emitido_alan");

    // Verificar que se llamaron scram-first y scram-final, y NUNCA el endpoint inseguro /api/auth/login
    const urlsCalled = mockFetch.mock.calls.map((c: any) => String(c[0]));
    expect(urlsCalled.some((u: string) => u.includes("/api/auth/scram-first"))).toBe(true);
    expect(urlsCalled.some((u: string) => u.includes("/api/auth/scram-final"))).toBe(true);
    expect(urlsCalled.some((u: string) => u.endsWith("/api/auth/login"))).toBe(false);

    // Verificar que ninguna petición transmitió la contraseña en texto claro
    for (const call of mockFetch.mock.calls) {
      const bodyStr = String(call[1]?.body || "");
      expect(bodyStr).not.toContain("mi_password_secreto");
    }
  });

  // 4. Componente LoginModal muestra formulario para Alan y Yilda y procesa login
  it("4. LoginModal permite seleccionar a Alan o Yilda, ingresar clave y dispara éxito", async () => {
    const onLoginSuccess = vi.fn();
    const onClose = vi.fn();
    const onCancelToDemo = vi.fn();

    // Simular modo real activo para que LoginModal use RealApiClient y fetch mockeado con SCRAM
    const client = new RealApiClient("http://127.0.0.1:8000");
    const mockFetch = createMockScramFetch(
      { username: "yilda", display_name: "Yilda", role: "operador" },
      "clave_prueba",
      "token_mock_modal"
    );
    vi.stubGlobal("fetch", mockFetch);

    const { setApiClient } = await import("../src/services/api");
    setApiClient(client);

    render(
      <LoginModal
        isOpen={true}
        onClose={onClose}
        onLoginSuccess={onLoginSuccess}
        onCancelToDemo={onCancelToDemo}
      />
    );

    expect(screen.getByTestId("login-modal-card")).toBeInTheDocument();

    // Seleccionar Yilda
    const selectUser = screen.getByTestId("login-username-select");
    fireEvent.change(selectUser, { target: { value: "yilda" } });

    // Ingresar contraseña
    const inputPass = screen.getByTestId("login-password-input");
    fireEvent.change(inputPass, { target: { value: "clave_prueba" } });

    // Enviar
    fireEvent.click(screen.getByTestId("btn-submit-login"));

    await waitFor(() => {
      expect(onLoginSuccess).toHaveBeenCalledWith(
        expect.objectContaining({
          username: "yilda",
          display_name: "Yilda",
        })
      );
    });

    activarModoDemo();
  });

  // 5. Header muestra usuario activo y botón para cerrar sesión en modo conectado
  it("5. Header muestra el nombre del operador autenticado y botón Salir", () => {
    const onLogout = vi.fn();
    render(
      <Header
        isOnline={true}
        isDemo={false}
        isStaticDemoOnly={false}
        currentUser={{ username: "alan", display_name: "Alan" }}
        activeView="caja"
        onSelectView={() => {}}
        onToggleDemo={() => {}}
        onLogout={onLogout}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={() => {}}
      />
    );

    expect(screen.getByTestId("user-badge")).toHaveTextContent("Alan");
    const btnLogout = screen.getByTestId("btn-logout");
    expect(btnLogout).toBeInTheDocument();
    fireEvent.click(btnLogout);
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  // 6. Header protege y aísla la demo pública ficticia
  it("6. En modo demo pública estática, Header muestra badge inmutable y bloquea switch a API real", () => {
    const onToggleDemo = vi.fn();
    render(
      <Header
        isOnline={true}
        isDemo={true}
        isStaticDemoOnly={true}
        activeView="caja"
        onSelectView={() => {}}
        onToggleDemo={onToggleDemo}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={() => {}}
      />
    );

    // Muestra badge estático
    expect(screen.getByTestId("static-demo-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("toggle-demo")).not.toBeInTheDocument();
    expect(screen.queryByTestId("auth-header-controls")).not.toBeInTheDocument();
  });

  // 7. MockApiClient opera con aislamiento total y datos simulados
  it("7. MockApiClient no realiza llamadas de red ni expone secretos de backend", async () => {
    const mockClient = new MockApiClient();
    expect(mockClient.isDemo()).toBe(true);

    const loginRes = await mockClient.login("alan", "demo");
    expect(loginRes.status).toBe(200);
    expect(loginRes.data?.user.username).toBe("alan");

    const meRes = await mockClient.obtenerUsuarioActual();
    expect(meRes.status).toBe(200);
    expect(meRes.data?.username).toBe("alan");
  });

  // 8. Limpieza de estado (error y contraseña) al cerrar y reabrir tras fallo 401
  it("8. Después de un fallo 401, cerrar y volver a abrir el modal elimina el mensaje de error anterior y deja el campo de contraseña vacío", async () => {
    const client = new RealApiClient("http://127.0.0.1:8000");
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Usuario o contraseña incorrectos." }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { setApiClient } = await import("../src/services/api");
    setApiClient(client);

    const onClose = vi.fn();
    const onLoginSuccess = vi.fn();
    const onCancelToDemo = vi.fn();

    const { rerender } = render(
      <LoginModal
        isOpen={true}
        onClose={onClose}
        onLoginSuccess={onLoginSuccess}
        onCancelToDemo={onCancelToDemo}
      />
    );

    // Ingresar contraseña errónea
    const inputPass = screen.getByTestId("login-password-input") as HTMLInputElement;
    fireEvent.change(inputPass, { target: { value: "clave_incorrecta" } });
    fireEvent.click(screen.getByTestId("btn-submit-login"));

    // Esperar mensaje de error
    await waitFor(() => {
      expect(screen.getByTestId("login-error-banner")).toHaveTextContent("Usuario o contraseña incorrectos.");
    });

    // Cerrar el modal (isOpen = false)
    rerender(
      <LoginModal
        isOpen={false}
        onClose={onClose}
        onLoginSuccess={onLoginSuccess}
        onCancelToDemo={onCancelToDemo}
      />
    );

    expect(screen.queryByTestId("login-modal-card")).not.toBeInTheDocument();

    // Reabrir el modal (isOpen = true)
    rerender(
      <LoginModal
        isOpen={true}
        onClose={onClose}
        onLoginSuccess={onLoginSuccess}
        onCancelToDemo={onCancelToDemo}
      />
    );

    // El banner de error DEBE haber desaparecido y la contraseña DEBE estar vacía
    expect(screen.queryByTestId("login-error-banner")).not.toBeInTheDocument();
    const inputReopened = screen.getByTestId("login-password-input") as HTMLInputElement;
    expect(inputReopened.value).toBe("");
  });

  // 9. Preservación exacta de contraseñas con espacios iniciales o finales
  it("9. Una contraseña que contiene espacios iniciales o finales se envía exactamente como fue introducida, sin alteraciones automáticas", async () => {
    const passConEspacios = "   clave con espacios iniciales y finales   ";
    const client = new RealApiClient("http://127.0.0.1:8000");
    const mockFetch = createMockScramFetch(
      { username: "alan", display_name: "Alan", role: "admin" },
      passConEspacios,
      "token_espacios"
    );
    vi.stubGlobal("fetch", mockFetch);

    const { setApiClient } = await import("../src/services/api");
    setApiClient(client);

    render(
      <LoginModal
        isOpen={true}
        onClose={() => {}}
        onLoginSuccess={() => {}}
        onCancelToDemo={() => {}}
      />
    );

    const inputPass = screen.getByTestId("login-password-input");
    fireEvent.change(inputPass, { target: { value: passConEspacios } });
    fireEvent.click(screen.getByTestId("btn-submit-login"));

    await waitFor(() => {
      expect(client.getAuthToken()).toBe("token_espacios");
    });
  });

  // 10. Atributos de accesibilidad, autocompletado y toggle de visualización móvil
  it("10. Los atributos de accesibilidad y autocompletado del input móvil están presentes y el toggle conmuta visibilidad", () => {
    render(
      <LoginModal
        isOpen={true}
        onClose={() => {}}
        onLoginSuccess={() => {}}
        onCancelToDemo={() => {}}
      />
    );

    const inputPass = screen.getByTestId("login-password-input") as HTMLInputElement;
    expect(inputPass.getAttribute("autocapitalize")).toBe("none");
    expect(inputPass.getAttribute("autocorrect")).toBe("off");
    expect(inputPass.getAttribute("spellcheck")).toBe("false");
    expect(inputPass.getAttribute("autocomplete")).toBe("current-password");
    expect(inputPass.type).toBe("password");

    const toggleBtn = screen.getByTestId("btn-toggle-password");
    expect(toggleBtn).toBeInTheDocument();
    expect(toggleBtn).toHaveAttribute("aria-label", "Ver contraseña");

    // Click toggle para ver
    fireEvent.click(toggleBtn);
    expect(inputPass.type).toBe("text");
    expect(toggleBtn).toHaveAttribute("aria-label", "Ocultar contraseña");

    // Click toggle para volver a ocultar
    fireEvent.click(toggleBtn);
    expect(inputPass.type).toBe("password");
    expect(toggleBtn).toHaveAttribute("aria-label", "Ver contraseña");
  });

  // 11. Cerrar o cancelar el acceso en App sin autenticarse devuelve a Demo y MockApiClient
  it("11. Cerrar o cancelar el acceso sin autenticarse devuelve la aplicación a Demo y a MockApiClient", async () => {
    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });

    // Inicialmente está en Modo Demo
    expect(getApiClient().isDemo()).toBe(true);
    expect(screen.getByTestId("toggle-demo")).toHaveTextContent(/Modo Demo/i);

    // Operador presiona el botón para cambiar de modo
    fireEvent.click(screen.getByTestId("toggle-demo"));

    // Modal de login debe aparecer y el cliente cambió transitoriamente a RealApiClient
    await waitFor(() => {
      expect(screen.getByTestId("login-modal-card")).toBeInTheDocument();
    });
    expect(getApiClient().isDemo()).toBe(false);

    // Operador cierra el modal pulsando el botón ✕
    fireEvent.click(screen.getByTestId("btn-close-login"));

    // La aplicación DEBE volver de inmediato y de forma segura a Modo Demo
    await waitFor(() => {
      expect(screen.queryByTestId("login-modal-card")).not.toBeInTheDocument();
    });
    expect(getApiClient().isDemo()).toBe(true);
    expect(screen.getByTestId("toggle-demo")).toHaveTextContent(/Modo Demo/i);

    // Probar también el botón "Volver al Modo Demo"
    fireEvent.click(screen.getByTestId("toggle-demo"));
    await waitFor(() => {
      expect(screen.getByTestId("login-modal-card")).toBeInTheDocument();
    });
    expect(getApiClient().isDemo()).toBe(false);

    fireEvent.click(screen.getByTestId("btn-cancel-to-demo"));
    await waitFor(() => {
      expect(screen.queryByTestId("login-modal-card")).not.toBeInTheDocument();
    });
    expect(getApiClient().isDemo()).toBe(true);

    unmount();
  });

  // 12. Un inicio de sesión correcto sigue seleccionando RealApiClient y mantiene la sesión válida
  it("12. Un inicio de sesión correcto sigue seleccionando RealApiClient y mantiene la sesión válida", async () => {
    const mockFetch = createMockScramFetch(
      { username: "alan", display_name: "Alan", role: "admin" },
      "password_valida",
      "jwt_token_exitoso_alan"
    );
    vi.stubGlobal("fetch", mockFetch);

    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });

    // Abrir modal de login cambiando de modo
    fireEvent.click(screen.getByTestId("toggle-demo"));

    await waitFor(() => {
      expect(screen.getByTestId("login-modal-card")).toBeInTheDocument();
    });

    // Ingresar contraseña y enviar
    const inputPass = screen.getByTestId("login-password-input");
    fireEvent.change(inputPass, { target: { value: "password_valida" } });
    fireEvent.click(screen.getByTestId("btn-submit-login"));

    // Modal debe cerrarse tras éxito y reflejar usuario conectado
    await waitFor(() => {
      expect(screen.queryByTestId("login-modal-card")).not.toBeInTheDocument();
      expect(screen.getByTestId("user-badge")).toHaveTextContent("Alan");
    });

    // El cliente API permanece en RealApiClient y no revierte a Demo
    expect(getApiClient().isDemo()).toBe(false);

    unmount();
  });

  // 13. Controles de solo lectura del frontend bloquean escrituras y mutaciones
  it("13. Los controles de solo lectura del frontend continúan bloqueando las escrituras y la sincronización", async () => {
    const client = new RealApiClient("http://127.0.0.1:8000", true);
    expect(client.isReadOnlySmoke()).toBe(true);

    // Intentar crear producto en modo solo lectura DEBE lanzar excepción bloqueante antes de fetch
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const res = await client.crearProducto({
      nombre: "Producto Bloqueado",
      categoria: "Prueba",
      proveedor: "Local",
      unidad: "u.",
      costo: 100,
      precio_venta: 150,
    });

    expect(res.status).toBe(0);
    expect(res.error).toMatch(/READ_ONLY_SMOKE_MODE/);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  // 14. RealApiClient.login con HTTP 401 retorna error de credenciales
  it("14. RealApiClient.login con HTTP 401 retorna error específico de credenciales", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Usuario o contraseña incorrectos." }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_invalida");

    expect(res.status).toBe(401);
    expect(res.error).toBe("Usuario o contraseña incorrectos.");
  });

  // 15. RealApiClient.login con HTTP 403 (Cloudflare o proxy no-JSON) distingue el error
  it("15. RealApiClient.login con HTTP 403 no-JSON devuelve mensaje de acceso denegado y no error de credenciales", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => {
        throw new Error("Unexpected token '<', '<!DOCTYPE '... is not valid JSON");
      },
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_cualquiera");

    expect(res.status).toBe(403);
    expect(res.error).toMatch(/Acceso denegado \(HTTP 403\)/);
    expect(res.error).not.toMatch(/Usuario o contraseña incorrectos/);
  });

  // 16. RealApiClient.login con HTTP 502 no-JSON devuelve error de gateway y no de contraseña
  it("16. RealApiClient.login con HTTP 502 no-JSON devuelve mensaje de puerta de enlace y no error de credenciales", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("SyntaxError: Unexpected token '<'");
      },
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_cualquiera");

    expect(res.status).toBe(502);
    expect(res.error).toMatch(/Error de puerta de enlace \(HTTP 502\)/);
    expect(res.error).not.toMatch(/Usuario o contraseña incorrectos/);
  });

  // 17. RealApiClient.login con HTTP 429 devuelve limitación de intentos y no de contraseña
  it("17. RealApiClient.login con HTTP 429 devuelve mensaje de limitación de tasa y no error de contraseña", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({
        detail: "Demasiados intentos fallidos. Por favor, aguardá 15 minutos.",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_cualquiera");

    expect(res.status).toBe(429);
    expect(res.error).toMatch(/Demasiados intentos/);
    expect(res.error).not.toMatch(/Usuario o contraseña incorrectos/);
  });

  // 18. RealApiClient.login con HTTP 423 devuelve mensaje de bloqueo de solo lectura
  it("18. RealApiClient.login con HTTP 423 devuelve mensaje de modo de solo lectura", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 423,
      json: async () => ({
        detail: "Operación bloqueada por modo de solo lectura en el servidor.",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_cualquiera");

    expect(res.status).toBe(423);
    expect(res.error).toMatch(/solo lectura/);
    expect(res.error).not.toMatch(/Usuario o contraseña incorrectos/);
  });

  // 19. Regresión P0-03: RealApiClient.login distingue respuesta redirigida o HTML de Cloudflare Access
  it("19. RealApiClient.login detecta respuestas redirigidas o text/html del túnel sin confundirlas con credenciales inválidas", async () => {
    const client = new RealApiClient("http://127.0.0.1:8000");

    // Caso A: Petición redirigida al portal de login del túnel
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        redirected: true,
        headers: new Headers({ "content-type": "text/html; charset=utf-8" }),
        json: async () => ({}),
      })
    );
    const resRedirect = await client.login("alan", "clave_valida");
    expect(resRedirect.error).toMatch(/redirigida por el túnel o proxy/i);
    expect(resRedirect.error).not.toMatch(/Usuario o contraseña incorrectos/);

    // Caso B: Respuesta text/html sin redirect explícito (ej. challenge / error page)
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        redirected: false,
        headers: new Headers({ "content-type": "text/html" }),
        json: async () => {
          throw new Error("SyntaxError");
        },
      })
    );
    const resHtml = await client.login("alan", "clave_valida");
    expect(resHtml.error).toMatch(/página HTML en lugar de una respuesta JSON/i);
    expect(resHtml.error).not.toMatch(/Usuario o contraseña incorrectos/);
  });

  // 20. Regresión P0-03: RealApiClient.login con HTTP 401 sin JSON de FastAPI distingue rechazo de proxy/túnel
  it("20. RealApiClient.login con HTTP 401 sin campo detail de la API informa bloqueo de proxy/túnel y no error de contraseña", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        redirected: false,
        headers: new Headers({ "content-type": "text/plain" }),
        json: async () => {
          throw new Error("Unexpected token U in JSON");
        },
      })
    );

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_valida");

    expect(res.status).toBe(401);
    expect(res.error).toMatch(/Respuesta 401 sin formato JSON de la API/i);
    expect(res.error).not.toBe("Usuario o contraseña incorrectos.");
  });

  // 21. Regresión P0-03: RealApiClient.login con HTTP 200 pero sin access_token válido devuelve error de formato
  it("21. RealApiClient.login con HTTP 200 sin access_token válido rechaza la respuesta y no guarda token indefinido", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        redirected: false,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({ status: "ok_pero_sin_token" }),
      })
    );

    const client = new RealApiClient("http://127.0.0.1:8000");
    const res = await client.login("alan", "clave_valida");

    expect(res.status).toBe(502);
    expect(res.error).toMatch(/Respuesta inválida del servidor/i);
    expect(client.getAuthToken()).toBeNull();
  });

  // 22. Regresión P0-03: Al cambiar de Modo Demo a Modo Real sin sesión guardada, App NO dispara llamadas autenticadas antes del login
  it("22. Al conmutar de Modo Demo a Modo Real sin sesión activa, App abre el modal sin disparar /api/catalog ni /api/system/readiness antes de iniciar sesión", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "No autenticado" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });

    mockFetch.mockClear();

    // Conmutar de Modo Demo a Modo Real sin sesión previa en sessionStorage
    fireEvent.click(screen.getByTestId("toggle-demo"));

    await waitFor(() => {
      expect(screen.getByTestId("login-modal-card")).toBeInTheDocument();
    });

    // Ninguna llamada a /api/catalog ni /api/system/readiness debe haberse ejecutado antes de que el usuario inicie sesión
    expect(mockFetch).not.toHaveBeenCalled();

    unmount();
  });
});
