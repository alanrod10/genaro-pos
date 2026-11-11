import React, { useCallback, useEffect, useRef, useState } from "react";
import { Header, ActiveViewType } from "./components/Header";
import { ProductCatalog } from "./components/ProductCatalog";
import { ProductAdmin } from "./components/ProductAdmin";
import { ServicesAdmin } from "./components/ServicesAdmin";
import { ExpensesCashAdmin } from "./components/ExpensesCashAdmin";
import { SalesHistoryAdmin } from "./components/SalesHistoryAdmin";
import { PreventistasAdmin } from "./components/PreventistasAdmin";
import { DashboardAdmin } from "./components/DashboardAdmin";
import { TesoreriaLab } from "./components/TesoreriaLab";
import { RadarMonitor } from "./components/RadarMonitor";
import { ConfigAdmin } from "./components/ConfigAdmin";
import { Cart } from "./components/Cart";
import { WeightSaleModal } from "./components/WeightSaleModal";
import { PaymentModal } from "./components/PaymentModal";
import { SyncQueueModal } from "./components/SyncQueueModal";
import { LoginModal } from "./components/LoginModal";
import { ToastContainer, ToastMessage } from "./components/Toast";
import { Producto } from "./types/catalog";
import { ItemCarrito, VentaLocal, VentaPayload } from "./types/sales";
import { SystemReadiness } from "./types/system";
import { isWeighableProduct } from "./utils/weightSales";
import {
  activarModoDemo,
  activarModoReal,
  AuthUser,
  cerrarSesion,
  debeIniciarEnModoConectado,
  getApiClient,
  guardarSesionLocal,
  isReadOnlySmokeActive,
  IS_STATIC_DEMO_ONLY,
  MockApiClient,
  obtenerSesionGuardada,
} from "./services/api";
import {
  GastoColaLocal,
  guardarCatalogoCache,
  guardarVentaLocal,
  guardarVentaDemo,
  inicializarVentasDemoSiVacio,
  listarGastosColaLocal,
  listarServiciosColaLocal,
  listarVentasLocales,
  listarVentasDemo,
  obtenerCatalogoCache,
  openDB,
  ServicioColaLocal,
} from "./services/db";
import { sincronizarCola, sincronizarVentaIndividual } from "./services/sync";
import { useTheme } from "./services/theme";
import { SimplifiedSyncStatus } from "./types/tesoreria";
import { TesoreriaService } from "./services/tesoreria";

export const App: React.FC = () => {
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const { themeMode, setThemeMode } = useTheme();
  // Estado de configuración y red
  const [isOnline, setIsOnline] = useState<boolean>(navigator.onLine);
  const [isDemo, setIsDemo] = useState<boolean>(() => {
    if (IS_STATIC_DEMO_ONLY) {
      return true;
    }
    const forceConnected =
      import.meta.env.VITE_DEMO_MODE === "false" ||
      import.meta.env.VITE_CONNECTED_MODE === "true";
    if (forceConnected || debeIniciarEnModoConectado()) {
      if (getApiClient().isDemo()) {
        activarModoReal();
      }
      return false;
    }
    if (!getApiClient().isDemo()) {
      return false;
    }
    return true;
  });
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(() => {
    const sesion = obtenerSesionGuardada();
    return sesion ? sesion.user : null;
  });
  const [isLoginOpen, setIsLoginOpen] = useState<boolean>(false);

  // Estado de preparación de base de datos / mantenimiento
  const [schemaReadiness, setSchemaReadiness] = useState<SystemReadiness | null>(null);

  // Catálogo
  const [productos, setProductos] = useState<Producto[]>([]);
  const [isCatalogCached, setIsCatalogCached] = useState<boolean>(false);
  const [catalogCacheDate, setCatalogCacheDate] = useState<string | undefined>();

  // Carrito de ventas
  const [carrito, setCarrito] = useState<ItemCarrito[]>([]);
  const [catalogResetTrigger, setCatalogResetTrigger] = useState<number>(0);

  // Cola local en IndexedDB (Ventas, Servicios y Gastos)
  const [ventasLocales, setVentasLocales] = useState<VentaLocal[]>([]);
  const [serviciosCola, setServiciosCola] = useState<ServicioColaLocal[]>([]);
  const [gastosCola, setGastosCola] = useState<GastoColaLocal[]>([]);

  // Modales, vista y rol de demostración
  const [activeView, setActiveView] = useState<ActiveViewType>("caja");
  const [demoRole, setDemoRole] = useState<"admin" | "cajero">("admin");
  const [mobileTab, setMobileTab] = useState<"catalog" | "cart">("catalog");
  const [isPaymentOpen, setIsPaymentOpen] = useState<boolean>(false);
  const [isQueueOpen, setIsQueueOpen] = useState<boolean>(false);
  const [isWeightModalOpen, setIsWeightModalOpen] = useState<boolean>(false);
  const [weightModalProduct, setWeightModalProduct] = useState<Producto | null>(null);
  const [weightModalInitialItem, setWeightModalInitialItem] = useState<ItemCarrito | undefined>(undefined);

  // Notificaciones Toast
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [syncStatus, setSyncStatus] = useState<SimplifiedSyncStatus | null>(null);

  // Sincronización simplificada para Cajero (Etapa 5)
  useEffect(() => {
    let cancel = false;
    const fetchSync = async () => {
      if (isDemo || !isOnline || !currentUser) {
        setSyncStatus(null);
        return;
      }
      try {
        const s = await TesoreriaService.getCommercialBridgeSyncStatus();
        if (!cancel) setSyncStatus(s);
      } catch {
        // En error de red o timeout, no interrumpir el flujo principal
      }
    };
    fetchSync();
    const interval = setInterval(fetchSync, 10000);
    return () => {
      cancel = true;
      clearInterval(interval);
    };
  }, [isDemo, isOnline, currentUser]);

  const addToast = useCallback(
    (mensaje: string, tipo: ToastMessage["tipo"] = "info") => {
      const id = `${Date.now()}-${Math.random()}`;
      setToasts((prev) => [...prev, { id, mensaje, tipo }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 4000);
    },
    []
  );

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Carga de la cola desde IndexedDB
  const refrescarCola = useCallback(async () => {
    try {
      const api = getApiClient();
      if (api.isDemo()) {
        await inicializarVentasDemoSiVacio();
        const lista = await listarVentasDemo();
        setVentasLocales(lista);
        setServiciosCola([]);
        setGastosCola([]);
      } else {
        const [lista, srvCola, gstCola] = await Promise.all([
          listarVentasLocales(),
          listarServiciosColaLocal(),
          listarGastosColaLocal(),
        ]);
        setVentasLocales(lista);
        setServiciosCola(srvCola);
        setGastosCola(gstCola);
      }
    } catch (err) {
      console.error("Error al leer cola IndexedDB:", err);
    }
  }, []);

  // Carga del catálogo desde API con fallback a IndexedDB
  const cargarCatalogo = useCallback(async () => {
    const api = getApiClient();
    const tieneToken =
      api.isDemo() || (typeof api.getAuthToken === "function" && Boolean(api.getAuthToken()));

    if (tieneToken) {
      try {
        // Solicitar todos los productos (activos e inactivos) para administración
        const res = await api.obtenerCatalogo(true);
        if (res.status === 200 && res.data) {
          setProductos(res.data.productos);
          setIsCatalogCached(false);
          // Guardar en caché local para uso offline
          await guardarCatalogoCache(res.data.productos);
          const meta = await obtenerCatalogoCache();
          setCatalogCacheDate(meta.actualizado_en);
          return;
        }
      } catch {
        // Ignorar fallo de red y recurrir al caché
      }
    }

    // Fallback: leer de caché local IndexedDB
    try {
      const cache = await obtenerCatalogoCache();
      if (cache.productos.length > 0) {
        setProductos(cache.productos);
        setIsCatalogCached(true);
        setCatalogCacheDate(cache.actualizado_en);
        if (tieneToken) {
          addToast("Catálogo cargado desde la memoria local (offline)", "info");
        }
      }
    } catch (err) {
      console.error("Error al leer caché de catálogo:", err);
    }
  }, [addToast]);

  // Sincronizar cola pendiente
  const ejecutarSincronizacion = useCallback(async () => {
    const api = getApiClient();
    const res = await sincronizarCola(api);
    await refrescarCola();
    if (res.exitosos > 0) {
      addToast(
        `Se sincronizaron ${res.exitosos} venta(s) correctamente con el servidor.`,
        "success"
      );
    }
    if (res.fallidos > 0) {
      addToast(
        `${res.fallidos} venta(s) no pudieron sincronizarse. Verificá la cola.`,
        "warning"
      );
    }
  }, [addToast, refrescarCola]);

  // Verificación de preparación de esquema
  const comprobarPreparacion = useCallback(async () => {
    const api = getApiClient();
    if (!isMountedRef.current) return;
    if (api.isDemo()) {
      if (isMountedRef.current) setSchemaReadiness(null);
      return;
    }
    if (typeof api.getAuthToken === "function" && !api.getAuthToken()) {
      return;
    }
    try {
      const res = await api.verificarPreparacionSistema();
      if (!isMountedRef.current) return;
      if (res.status === 200 && res.data) {
        if (!res.data.listo || res.data.status !== "PREPARADO") {
          setSchemaReadiness(res.data);
        } else {
          setSchemaReadiness(null);
        }
      } else if (res.status === 503) {
        setSchemaReadiness({
          status: "MIGRACIONES_PENDIENTES",
          listo: false,
          versiones_aplicadas: [],
          migraciones_pendientes: [4, 5, 6, 7, 8, 9, 10],
          tablas_existentes: [],
          tablas_faltantes: [],
          columnas_faltantes: [],
          inconsistencias: [],
          mensaje: res.error || "Base de datos en mantenimiento: migraciones requeridas.",
        });
      }
    } catch {
      // Ignorar fallos de red
    }
  }, []);

  // Validación de sesión activa contra /api/auth/me al iniciar o recargar (F5) en modo conectado
  const validarSesionAlIniciar = useCallback(async () => {
    const api = getApiClient();
    if (api.isDemo() || IS_STATIC_DEMO_ONLY) {
      return;
    }
    const sesion = obtenerSesionGuardada();
    if (!sesion?.token) {
      setCurrentUser(null);
      setIsLoginOpen(true);
      return;
    }
    if (api.setAuthToken && (!api.getAuthToken || !api.getAuthToken())) {
      api.setAuthToken(sesion.token);
    }
    try {
      const res = await api.obtenerUsuarioActual();
      if (res.status === 200 && res.data) {
        const userActualizado: AuthUser = {
          username: res.data.username,
          display_name: res.data.display_name || sesion.user.display_name || res.data.username,
          role: res.data.role || sesion.user.role || "operador",
        };
        guardarSesionLocal(sesion.token, userActualizado);
        setCurrentUser(userActualizado);
      } else if (res.status === 401 || res.status === 403) {
        cerrarSesion();
        setCurrentUser(null);
        setIsLoginOpen(true);
      } else if (res.status === 0 || res.status >= 500) {
        addToast(
          "Sin conexión con el servidor. Operando en modo conectado con cola offline local.",
          "warning"
        );
      }
    } catch {
      addToast(
        "Sin conexión con el servidor. Operando en modo conectado con cola offline local.",
        "warning"
      );
    }
  }, [addToast]);

  // Inicialización
  useEffect(() => {
    openDB()
      .then(() => {
        validarSesionAlIniciar();
        refrescarCola();
        cargarCatalogo();
        comprobarPreparacion();
      })
      .catch((err) => console.error("Error al abrir IndexedDB:", err));

    const handleOnline = () => {
      setIsOnline(true);
      if (isReadOnlySmokeActive(getApiClient())) {
        addToast("Conexión detectada (Modo Solo Lectura: Sincronización bloqueada)", "info");
        return;
      }
      addToast("Conexión restablecida. Sincronizando ventas pendientes...", "info");
      ejecutarSincronizacion();
    };
    const handleOffline = () => {
      setIsOnline(false);
      addToast("Sin conexión a internet. La caja operará en modo local.", "warning");
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [cargarCatalogo, ejecutarSincronizacion, refrescarCola, addToast]);

  // Control de sesión expirada
  const handleSessionExpired = useCallback(() => {
    cerrarSesion();
    setCurrentUser(null);
    if (!isDemo && !IS_STATIC_DEMO_ONLY) {
      setIsLoginOpen(true);
      addToast(
        "Tu sesión ha expirado o no es válida. Por favor, iniciá sesión nuevamente.",
        "warning"
      );
    }
  }, [isDemo, addToast]);

  useEffect(() => {
    const api = getApiClient();
    if (api.setOnUnauthorized) {
      api.setOnUnauthorized(handleSessionExpired);
    }
  }, [handleSessionExpired, isDemo]);

  // Alternar Modo Demo / Real
  const handleToggleDemo = () => {
    if (IS_STATIC_DEMO_ONLY) {
      addToast("Modo Demo Ficticio obligatorio en esta versión pública", "info");
      return;
    }
    if (isDemo) {
      activarModoReal();
      setIsDemo(false);
      const isSmoke = isReadOnlySmokeActive(getApiClient());
      const sesion = obtenerSesionGuardada();
      if (!sesion) {
        setIsLoginOpen(true);
        addToast(
          isSmoke
            ? "Modo API DEV (Solo Lectura) activado. Por favor iniciá sesión para acceder."
            : "Modo API Local activado. Por favor iniciá sesión para acceder.",
          "info"
        );
        refrescarCola();
        return;
      } else {
        setCurrentUser(sesion.user);
        addToast(
          isSmoke
            ? `Conectado a API DEV (Solo Lectura) como ${sesion.user.display_name}`
            : `Conectado a API Local como ${sesion.user.display_name}`,
          "info"
        );
      }
    } else {
      activarModoDemo();
      setIsDemo(true);
      addToast("Activado Modo Demo Seguro (USD 0, sin escrituras remotas)", "info");
    }
    refrescarCola();
    cargarCatalogo();
    comprobarPreparacion();
  };

  const handleLogout = useCallback(() => {
    cerrarSesion();
    setCurrentUser(null);
    addToast("Sesión cerrada correctamente.", "info");
    if (!isDemo && !IS_STATIC_DEMO_ONLY) {
      setIsLoginOpen(true);
    }
  }, [isDemo, addToast]);

  const handleLoginSuccess = useCallback(
    (user: AuthUser) => {
      setCurrentUser(user);
      setIsLoginOpen(false);
      addToast(`Sesión iniciada correctamente como ${user.display_name}.`, "success");
      refrescarCola();
      cargarCatalogo();
      comprobarPreparacion();
    },
    [addToast, refrescarCola, cargarCatalogo, comprobarPreparacion]
  );

  const handleCancelLoginToDemo = useCallback(() => {
    setIsLoginOpen(false);
    activarModoDemo();
    setIsDemo(true);
    addToast("Regresando al Modo Demo Seguro.", "info");
    cargarCatalogo();
    comprobarPreparacion();
  }, [addToast, cargarCatalogo, comprobarPreparacion]);

  const handleCloseLogin = useCallback(() => {
    const sesion = obtenerSesionGuardada();
    if (!currentUser && !sesion) {
      handleCancelLoginToDemo();
    } else {
      setIsLoginOpen(false);
    }
  }, [currentUser, handleCancelLoginToDemo]);

  // Agregar producto al carrito (maneja modalidad por peso / importe para fraccionables)
  const handleSelectProduct = (producto: Producto) => {
    // Si el producto se comercializa fraccionado por peso, abrir el selector dedicado
    if (isWeighableProduct(producto)) {
      setWeightModalProduct(producto);
      const existing = carrito.find((i) => i.producto_id === producto.id_producto);
      setWeightModalInitialItem(existing);
      setIsWeightModalOpen(true);
      return;
    }

    const precio = producto.precio_venta ?? producto.precio_dia ?? 0;

    setCarrito((prev) => {
      const idx = prev.findIndex((i) => i.producto_id === producto.id_producto);
      if (idx !== -1) {
        const actual = prev[idx];
        const nuevaCant = actual.cantidad + 1;
        const copia = [...prev];
        copia[idx] = {
          ...actual,
          cantidad: nuevaCant,
          subtotal: Math.round(nuevaCant * actual.precio),
        };
        return copia;
      } else {
        return [
          ...prev,
          {
            producto_id: producto.id_producto,
            nombre: producto.nombre,
            unidad: producto.unidad,
            precio,
            cantidad: 1,
            subtotal: precio,
            costo: producto.costo !== undefined ? producto.costo : undefined,
          },
        ];
      }
    });
  };

  // Confirmar alta o edición de ítem por peso / importe
  const handleConfirmWeightSale = (confirmedItem: ItemCarrito) => {
    setCarrito((prev) => {
      const idx = prev.findIndex((i) => i.producto_id === confirmedItem.producto_id);
      if (idx !== -1) {
        const copia = [...prev];
        copia[idx] = confirmedItem;
        return copia;
      }
      return [...prev, confirmedItem];
    });
    setIsWeightModalOpen(false);
    setWeightModalProduct(null);
    setWeightModalInitialItem(undefined);
  };

  // Abrir modal de peso desde una línea existente del carrito
  const handleEditWeightItem = (item: ItemCarrito) => {
    const prod = productos.find((p) => p.id_producto === item.producto_id) || {
      id_producto: item.producto_id,
      nombre: item.nombre,
      categoria: "Al peso",
      proveedor: "",
      unidad: item.unidad,
      costo: item.costo || 0,
      margen_porcentaje: 0,
      precio_venta: item.precio,
      activo: true,
      fecha_act: "",
    };
    setWeightModalProduct(prod);
    setWeightModalInitialItem(item);
    setIsWeightModalOpen(true);
  };

  // Ajustar precio excepcional de una venta puntual (no altera el catálogo)
  const handleUpdateLinePrice = (
    productoId: number,
    nuevoPrecioUnitario: number,
    nuevoSubtotal: number
  ) => {
    setCarrito((prev) =>
      prev.map((it) =>
        it.producto_id === productoId
          ? {
              ...it,
              precio: nuevoPrecioUnitario,
              subtotal: nuevoSubtotal,
            }
          : it
      )
    );
  };

  // Actualizar cantidad de un ítem del carrito
  const handleUpdateQuantity = (productoId: number, nuevaCantidad: number) => {
    if (!Number.isFinite(nuevaCantidad) || nuevaCantidad <= 0) {
      handleRemoveItem(productoId);
      return;
    }
    setCarrito((prev) =>
      prev.map((item) =>
        item.producto_id === productoId
          ? {
              ...item,
              cantidad: nuevaCantidad,
              subtotal: Math.round(nuevaCantidad * item.precio),
            }
          : item
      )
    );
  };

  // Eliminar ítem
  const handleRemoveItem = (productoId: number) => {
    setCarrito((prev) => prev.filter((i) => i.producto_id !== productoId));
  };

  // Vaciar carrito
  const handleClearCart = () => {
    setCarrito([]);
  };

  // Confirmar y persistir venta en IndexedDB
  const handleConfirmSale = async (payload: VentaPayload) => {
    // Protección estricta: si el modo conectado de solo lectura está activo, abortar antes de persistir
    if (!isDemo && isReadOnlySmokeActive(getApiClient())) {
      addToast(
        "Acción bloqueada: La aplicación se encuentra en modo de prueba conectada de solo lectura (READ_ONLY_SMOKE_MODE).",
        "warning"
      );
      return;
    }

    const nuevaVentaLocal: VentaLocal = {
      ticket_id: payload.ticket.ticket_id,
      payload,
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    };

    // 1. Guardar en IndexedDB ANTES de cualquier intento de envío
    // En modo conectado (isDemo === false), jamás escribir en ventas_demo incluso si la sesión está validándose o expirada
    const api = getApiClient();
    const usarAlmacenDemo = IS_STATIC_DEMO_ONLY || (isDemo && api.isDemo());
    if (usarAlmacenDemo) {
      await guardarVentaDemo(nuevaVentaLocal);
    } else {
      await guardarVentaLocal(nuevaVentaLocal);
    }
    await refrescarCola();

    addToast(
      IS_STATIC_DEMO_ONLY
        ? `Ticket ficticio guardado localmente (${payload.ticket.ticket_id}). Simulación completada.`
        : `Venta confirmada localmente (${payload.ticket.ticket_id}). Encolada para sincronización.`,
      "info"
    );

    // 2. Disparar sincronización con el backend antes de vaciar el carrito
    const res = await sincronizarVentaIndividual(payload.ticket.ticket_id, api);
    await refrescarCola();

    if (res.estado === "sincronizada") {
      // 3. Confirmación exitosa del backend:
      // - Vaciar el carrito automáticamente
      // - Reiniciar el estado temporal del ticket
      // - Mantener intacto el catálogo
      // - Dejar Caja preparada para la próxima venta
      setCarrito([]);
      setCatalogResetTrigger((prev) => prev + 1);
      addToast(`Venta ${payload.ticket.ticket_id} confirmada por el servidor.`, "success");
    } else if (res.estado === "requiere_revision") {
      // Rechazado o requiere revisión: NO vaciar carrito
      addToast(
        `Venta ${payload.ticket.ticket_id} requiere revisión: ${res.mensaje}. El carrito no fue vaciado.`,
        "error"
      );
      throw new Error(res.mensaje || "Venta requiere revisión en el servidor");
    } else {
      // Estado 'pendiente' o fallo de confirmación/sincronización: NO vaciar carrito
      addToast(
        `Venta ${payload.ticket.ticket_id} encolada pero no confirmada aún por el servidor (${res.mensaje || "error de sincronización"}). El carrito no fue vaciado.`,
        "warning"
      );
      throw new Error(res.mensaje || "El servidor no confirmó la venta");
    }
  };

  const handleSyncOne = async (ticketId: string) => {
    const api = getApiClient();
    const res = await sincronizarVentaIndividual(ticketId, api);
    await refrescarCola();
    if (res.estado === "sincronizada") {
      addToast(`Ticket ${ticketId} sincronizado exitosamente.`, "success");
    } else if (res.estado === "requiere_revision") {
      addToast(`Ticket ${ticketId} requiere revisión: ${res.mensaje}`, "error");
    } else {
      addToast(`Ticket ${ticketId} permanece pendiente (${res.mensaje}).`, "warning");
    }
  };

  const pendingCount =
    ventasLocales.filter((v) => v.estado === "pendiente").length +
    serviciosCola.filter((s) => s.estado === "pendiente").length +
    gastosCola.filter((g) => g.estado === "pendiente").length;
  const reviewCount =
    ventasLocales.filter((v) => v.estado === "requiere_revision").length +
    serviciosCola.filter((s) => s.estado === "requiere_revision").length +
    gastosCola.filter((g) => g.estado === "requiere_revision").length;
  const mockClientInstance = isDemo ? (getApiClient() as MockApiClient) : undefined;

  const isReadOnlySmoke = !isDemo && isReadOnlySmokeActive(getApiClient());

  return (
    <div className="app-layout">
      {/* Barra Superior */}
      <Header
        isOnline={isOnline}
        isDemo={isDemo}
        isStaticDemoOnly={IS_STATIC_DEMO_ONLY}
        isReadOnlySmoke={isReadOnlySmoke}
        currentUser={currentUser}
        activeView={activeView}
        onSelectView={setActiveView}
        onToggleDemo={handleToggleDemo}
        onLogout={handleLogout}
        onOpenLogin={() => setIsLoginOpen(true)}
        pendingCount={pendingCount}
        reviewCount={reviewCount}
        onOpenQueue={() => setIsQueueOpen(true)}
        themeMode={themeMode}
        onChangeTheme={setThemeMode}
        demoRole={demoRole}
        onSelectDemoRole={setDemoRole}
        syncStatus={syncStatus}
      />

      {/* Barra Persistente de Identificación de Entorno (Etapa 8.3) */}
      <div
        className={`environment-identity-bar ${
          isDemo || IS_STATIC_DEMO_ONLY ? "env-bar-demo" : "env-bar-prod"
        }`}
        data-testid="environment-identity-bar"
      >
        <div className="env-bar-content">
          <span className="env-indicator-dot"></span>
          <span className="env-title">
            {isDemo || IS_STATIC_DEMO_ONLY ? "DEMO — DATOS SIMULADOS" : "PRODUCCIÓN"}
          </span>
          <span className="env-subtext">
            {isDemo || IS_STATIC_DEMO_ONLY
              ? "— Entorno aislado con datos ficticios. Operaciones locales sin impacto en bases comerciales reales."
              : "— Sistema operativo conectado a base de datos comercial."}
          </span>
        </div>
      </div>

      {/* Banner de Modo Cajero Mostrador (Demostración de Experiencia Cajero) */}
      {demoRole === "cajero" && (
        <div className="cashier-mode-banner bg-emerald-600 text-white px-4 py-2 text-xs font-semibold flex justify-between items-center" data-testid="cashier-mode-banner">
          <span>🏪 <strong>VISTA CAJERO MOSTRADOR:</strong> Interfaz optimizada para atención y cobro rápido. Tesorería sincronizada automáticamente en background.</span>
          <button
            type="button"
            onClick={() => setDemoRole("admin")}
            className="text-[11px] underline bg-white/20 hover:bg-white/30 px-2.5 py-0.5 rounded transition"
          >
            Cambiar a Vista Socios/Admin →
          </button>
        </div>
      )}

      {/* Banner de Aviso Modo Demo Público */}
      {IS_STATIC_DEMO_ONLY && (
        <div className="demo-notice-bar" data-testid="demo-notice-bar">
          ℹ️ <strong>MODO DEMO PÚBLICA:</strong> Datos y tickets 100% ficticios. No registra ventas reales ni accede a bases operativas.
        </div>
      )}

      {/* Banner Persistente de Modo Solo Lectura Conectado (Smoke DEV) */}
      {!isDemo && !IS_STATIC_DEMO_ONLY && isReadOnlySmoke && (
        <div
          className="readonly-smoke-notice-bar"
          data-testid="banner-readonly-smoke"
        >
          <div className="readonly-smoke-content">
            <span>🔒</span>
            <span>API DEV — SOLO LECTURA — SIN SINCRONIZACIÓN</span>
            <span className="readonly-smoke-subtext">
              (Inspección de Turso DEV. Escrituras comerciales y sincronización bloqueadas.)
            </span>
          </div>
          <span className="badge-smoke-pill">
            SMOKE DEV
          </span>
        </div>
      )}

      {/* Banner de Mantenimiento / Esquema Incompleto en Modo Conectado */}
      {!isDemo && schemaReadiness && !schemaReadiness.listo && (
        <div
          className="maintenance-notice-bar"
          data-testid="banner-mantenimiento-esquema"
        >
          <div className="maintenance-notice-header">
            <span>⚠️</span>
            <span>Base de datos en mantenimiento (Migración pendiente):</span>
          </div>
          <p className="maintenance-notice-body">
            {schemaReadiness.mensaje ||
              "La base de datos remota requiere aplicar migraciones pendientes antes de aceptar operaciones. Las ventas se retienen de forma segura en la cola local de este dispositivo."}
          </p>
        </div>
      )}

      {/* Cuerpo Principal: Caja vs Productos vs Servicios vs Gastos vs Historial */}
      {activeView === "productos" ? (
        <main className="main-content admin-view">
          <ProductAdmin
            productos={productos}
            onRefresh={cargarCatalogo}
            onAddToast={addToast}
            isDemo={isDemo}
          />
        </main>
      ) : activeView === "servicios" ? (
        <main className="main-content services-view">
          <ServicesAdmin
            onAddToast={addToast}
            isDemo={isDemo}
            onQueueChange={refrescarCola}
          />
        </main>
      ) : activeView === "gastos" ? (
        <main className="main-content expenses-view">
          <ExpensesCashAdmin
            onAddToast={addToast}
            isDemo={isDemo}
            onQueueChange={refrescarCola}
          />
        </main>
      ) : activeView === "historial" ? (
        <main className="main-content sales-history-view">
          <SalesHistoryAdmin
            onAddToast={addToast}
            isDemo={isDemo}
          />
        </main>
      ) : activeView === "preventistas" ? (
        <main className="main-content preventistas-view">
          <PreventistasAdmin
            onAddToast={addToast}
            isDemo={isDemo}
            onRefreshCatalog={cargarCatalogo}
          />
        </main>
      ) : activeView === "dashboard" ? (
        <main className="main-content dashboard-view">
          <DashboardAdmin
            onAddToast={addToast}
            isDemo={isDemo}
          />
        </main>
      ) : activeView === "tesoreria" ? (
        <main className="main-content tesoreria-view">
          <TesoreriaLab currentUser={currentUser ?? undefined} />
        </main>
      ) : activeView === "radar" ? (
        <main className="main-content radar-view">
          <RadarMonitor
            onAddToast={addToast}
            isDemo={isDemo}
            onNavigateToTesoreria={() => setActiveView("tesoreria")}
          />
        </main>
      ) : activeView === "configuracion" ? (
        <main className="main-content configuracion-view">
          <ConfigAdmin
            onAddToast={addToast}
            isDemo={isDemo}
            demoRole={demoRole}
            onSelectDemoRole={setDemoRole}
            onRefreshCatalog={cargarCatalogo}
          />
        </main>
      ) : (
        <main className="main-content">
          {/* Selector táctil de pantalla para móvil en Caja */}
          <div className="mobile-caja-nav" data-testid="mobile-caja-nav">
            <button
              type="button"
              className={`btn-mobile-caja-tab ${mobileTab === "catalog" ? "active" : ""}`}
              onClick={() => setMobileTab("catalog")}
              data-testid="btn-tab-catalog"
            >
              📦 Catálogo
            </button>
            <button
              type="button"
              className={`btn-mobile-caja-tab ${mobileTab === "cart" ? "active" : ""}`}
              onClick={() => setMobileTab("cart")}
              data-testid="btn-tab-cart"
            >
              🧾 Ticket Actual {carrito.length > 0 ? `(${carrito.reduce((a, b) => a + b.cantidad, 0)})` : "(Vacío)"}
            </button>
          </div>

          <section className={`catalog-pane ${mobileTab === "cart" ? "mobile-hide" : ""}`}>
            <ProductCatalog
              productos={productos.filter((p) => p.activo !== false)}
              onSelectProduct={handleSelectProduct}
              isCached={isCatalogCached}
              cacheDate={catalogCacheDate}
              onRefreshCatalog={cargarCatalogo}
              resetSearchTrigger={catalogResetTrigger}
            />
          </section>

          <section className={`cart-pane ${mobileTab === "catalog" ? "mobile-hide" : ""}`}>
            <Cart
              items={carrito}
              onUpdateQuantity={handleUpdateQuantity}
              onUpdateLinePrice={handleUpdateLinePrice}
              onEditWeightItem={handleEditWeightItem}
              onRemoveItem={handleRemoveItem}
              onClearCart={handleClearCart}
              onCheckout={() => setIsPaymentOpen(true)}
              onBackToCatalog={() => setMobileTab("catalog")}
            />
          </section>
        </main>
      )}

      {/* Barra flotante inferior para celulares (cuando estamos en Caja y hay ítems en carrito) */}
      {activeView === "caja" && mobileTab === "catalog" && carrito.length > 0 && (
        <div className="mobile-cart-bar" data-testid="mobile-cart-bar">
          <button
            type="button"
            className="btn-open-cart-mobile"
            onClick={() => setMobileTab("cart")}
            data-testid="btn-open-cart-mobile"
          >
            <span>🛒 Ver Carrito ({carrito.reduce((acc, it) => acc + it.cantidad, 0)})</span>
            <span className="mobile-bar-total">
              ${carrito.reduce((acc, it) => acc + it.subtotal, 0).toLocaleString("es-AR")}
            </span>
          </button>
          <button
            type="button"
            className="btn-quick-checkout-mobile"
            onClick={() => setIsPaymentOpen(true)}
            data-testid="btn-quick-checkout-mobile"
          >
            Cobrar
          </button>
        </div>
      )}

      {/* Modal de Cobro */}
      {isPaymentOpen && (
        <PaymentModal
          items={carrito}
          onClose={() => setIsPaymentOpen(false)}
          onConfirmSale={handleConfirmSale}
        />
      )}

      {/* Modal de Venta por Peso / Importe */}
      {isWeightModalOpen && weightModalProduct && (
        <WeightSaleModal
          producto={weightModalProduct}
          initialItem={weightModalInitialItem}
          onConfirm={handleConfirmWeightSale}
          onClose={() => {
            setIsWeightModalOpen(false);
            setWeightModalProduct(null);
            setWeightModalInitialItem(undefined);
          }}
        />
      )}

      {/* Modal de Cola de Sincronización */}
      {isQueueOpen && (
        <SyncQueueModal
          ventas={ventasLocales}
          serviciosCola={serviciosCola}
          gastosCola={gastosCola}
          isDemo={isDemo}
          isReadOnlySmoke={isReadOnlySmoke}
          mockClient={mockClientInstance}
          onClose={() => setIsQueueOpen(false)}
          onSyncAll={ejecutarSincronizacion}
          onSyncOne={handleSyncOne}
          onDiscard={async (ticketId) => {
            await refrescarCola();
            addToast(`Ticket ${ticketId} descartado de la cola`, "info");
          }}
        />
      )}

      {/* Modal de Inicio de Sesión (en modo conectado) */}
      <LoginModal
        isOpen={isLoginOpen}
        onClose={handleCloseLogin}
        onLoginSuccess={handleLoginSuccess}
        onCancelToDemo={handleCancelLoginToDemo}
      />

      {/* Notificaciones flotantes */}
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  );
};
