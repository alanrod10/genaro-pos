import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import {
  openDB,
  resetDBInstance,
  listarGastosDemo,
  guardarGastoDemo,
  calcularArqueoDemo,
  guardarCierreDemo,
  listarCierresDemo,
  guardarVentaDemo,
  guardarCargaDemo,
  fechaArgentinaTexto,
} from "../src/services/db";
import { activarModoDemo, getApiClient } from "../src/services/api";

describe("GenaroPOS — Módulo de Gastos, Arqueo y Cierre Diario (Frontend / IndexedDB)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // 1. Navegación hacia Gastos y Cierre desde el Header
  it("1. Permite alternar la navegación hacia el módulo de Gastos y Cierre mediante el Header", async () => {
    render(<App />);

    // Inicia en la vista de Caja
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.queryByTestId("expenses-container")).not.toBeInTheDocument();

    // Clic en pestaña '💰 Gastos y Cierre'
    const btnNavGastos = screen.getByTestId("nav-btn-gastos");
    fireEvent.click(btnNavGastos);

    // Muestra la vista de Gastos y Cierre
    expect(screen.getByTestId("expenses-container")).toBeInTheDocument();
    expect(screen.getByTestId("subnav-tabs")).toBeInTheDocument();
    expect(screen.getByTestId("expense-form")).toBeInTheDocument();
    expect(screen.queryByTestId("search-input")).not.toBeInTheDocument();

    // Regresar a Caja
    fireEvent.click(screen.getByTestId("nav-btn-caja"));
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
  });

  // 2. Validación de monto inválido (<= 0 o texto vacío)
  it("2. Valida y rechaza el registro si el concepto está vacío o el monto es menor o igual a cero", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-gastos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-register-expense")).toBeInTheDocument();
    });

    const inputConcepto = screen.getByTestId("input-expense-detail");
    const inputMonto = screen.getByTestId("input-expense-amount");

    // Caso A: Concepto vacío
    await userEvent.clear(inputConcepto);
    fireEvent.change(inputMonto, { target: { value: "500" } });
    fireEvent.click(screen.getByTestId("btn-register-expense"));

    await waitFor(() => {
      expect(screen.getByTestId("expense-form-error")).toHaveTextContent(
        "El concepto o detalle del gasto es obligatorio."
      );
    });

    // Caso B: Monto menor o igual a 0
    await userEvent.type(inputConcepto, "Artículos de limpieza");
    fireEvent.change(inputMonto, { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("btn-register-expense"));

    await waitFor(() => {
      expect(screen.getByTestId("expense-form-error")).toHaveTextContent(
        "El importe del gasto debe ser mayor a $0."
      );
    });
  });

  // 3. Registro de gasto en efectivo e impacto en estado local
  it("3. Registra un gasto en efectivo y actualiza el listado y los totales", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-gastos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-register-expense")).toBeInTheDocument();
    });

    const inputConcepto = screen.getByTestId("input-expense-detail");
    await userEvent.clear(inputConcepto);
    await userEvent.type(inputConcepto, "Bolsas de consorcio");

    const inputMonto = screen.getByTestId("input-expense-amount");
    fireEvent.change(inputMonto, { target: { value: "1500" } });

    // Seleccionar categoría Limpieza
    const selectCat = screen.getByTestId("select-expense-category");
    fireEvent.change(selectCat, { target: { value: "Limpieza" } });

    // Registrar gasto
    fireEvent.click(screen.getByTestId("btn-register-expense"));

    // Verificar en IndexedDB
    await waitFor(async () => {
      const lista = await listarGastosDemo(false);
      const creado = lista.find((g) => g.detalle === "Bolsas de consorcio");
      expect(creado).toBeDefined();
      expect(creado?.monto_salida).toBe(1500);
      expect(creado?.tipo).toBe("Limpieza");
      expect(creado?.metodo_pago).toBe("EFECTIVO");
    });
  });

  // 4. Edición de gasto individual
  it("4. Permite editar el detalle, importe y medio de pago de un gasto puntual sin alterar otros", async () => {
    // Insertar gasto base
    const base = await guardarGastoDemo({
      tipo: "Proveedores",
      detalle: "Factura Gaseosas Original",
      monto_salida: 5000,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-gastos"));

    // Esperar a que la tabla cargue y hacer clic en Editar
    await waitFor(() => {
      expect(screen.getByTestId(`btn-edit-expense-${base.id}`)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId(`btn-edit-expense-${base.id}`));

    // Modal de edición
    expect(screen.getByTestId("modal-edit-expense")).toBeInTheDocument();
    const editDetalle = screen.getByTestId("input-edit-expense-detail");
    const editMonto = screen.getByTestId("input-edit-expense-amount");
    const editRadioTransf = screen.getByTestId("radio-edit-expense-transferencia");

    await userEvent.clear(editDetalle);
    await userEvent.type(editDetalle, "Factura Gaseosas Corregida");
    fireEvent.change(editMonto, { target: { value: "6200" } });
    fireEvent.click(editRadioTransf);

    fireEvent.click(screen.getByTestId("btn-save-expense-edit"));

    // Verificar actualización en IndexedDB
    await waitFor(async () => {
      const lista = await listarGastosDemo(false);
      const mod = lista.find((g) => g.id === base.id);
      expect(mod?.detalle).toBe("Factura Gaseosas Corregida");
      expect(mod?.monto_salida).toBe(6200);
      expect(mod?.metodo_pago).toBe("TRANSFERENCIA");
    });
  });

  // 5. Anulación lógica de gasto con preservación del registro
  it("5. Anula lógicamente un gasto con motivo, conservando el registro en la base", async () => {
    const base = await guardarGastoDemo({
      tipo: "Comida",
      detalle: "Almuerzo de prueba",
      monto_salida: 2500,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-gastos"));

    await waitFor(() => {
      expect(screen.getByTestId(`btn-cancel-expense-${base.id}`)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId(`btn-cancel-expense-${base.id}`));

    // Modal anulación
    expect(screen.getByTestId("modal-confirm-cancel-expense")).toBeInTheDocument();
    const inputMotivo = screen.getByTestId("input-cancel-expense-reason");
    await userEvent.type(inputMotivo, "Gasto duplicado por error");

    fireEvent.click(screen.getByTestId("btn-confirm-cancel-expense"));

    await waitFor(async () => {
      const activos = await listarGastosDemo(false);
      expect(activos.some((g) => g.id === base.id)).toBe(false);

      const todos = await listarGastosDemo(true);
      const anulado = todos.find((g) => g.id === base.id);
      expect(anulado).toBeDefined();
      expect(anulado?.anulado).toBe(true);
    });
  });

  // 6. Filtros por texto, categoría y mostrar anulados
  it("6. Filtra los gastos por texto de búsqueda, categoría y estado anulado", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-gastos"));

    await waitFor(() => {
      expect(screen.getByTestId("input-search-expenses")).toBeInTheDocument();
    });

    // Búsqueda por texto (Panadería existe en GASTOS_DEMO_INICIALES)
    const inputSearch = screen.getByTestId("input-search-expenses");
    await userEvent.type(inputSearch, "Panadería");

    await waitFor(() => {
      expect(screen.getAllByText("Panadería y medialunas").length).toBeGreaterThan(0);
      expect(screen.queryByText("Artículos de limpieza y bolsas")).not.toBeInTheDocument();
    });

    // Limpiar búsqueda
    await userEvent.clear(inputSearch);

    // Filtrar por categoría 'Limpieza'
    const selectCat = screen.getByTestId("filter-expense-category");
    fireEvent.change(selectCat, { target: { value: "Limpieza" } });

    await waitFor(() => {
      expect(screen.getAllByText("Artículos de limpieza y bolsas").length).toBeGreaterThan(0);
      expect(screen.queryByText("Panadería y medialunas")).not.toBeInTheDocument();
    });
  });

  // 7. Cálculo de arqueo según la fórmula de mostrar_visor()
  it("7. Calcula el efectivo esperado siguiendo rigurosamente la fórmula de mostrar_visor()", async () => {
    // Sembrar venta de drugstore en efectivo ($10.000)
    await guardarVentaDemo({
      ticket_id: "T-TEST-ARQUEO-1",
      creado_en: "2026-10-24 10:00:00",
      payload: {
        ticket: {
          ticket_id: "T-TEST-ARQUEO-1",
          fecha: "2026-10-24 10:00:00",
          total_venta: 10000,
          monto_efectivo: 10000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            producto: "Alfajor",
            cantidad: 10,
            unidad: "Unidad",
            precio_unit: 1000,
            subtotal: 10000,
          },
        ],
      },
      estado: "pendiente",
      intentos: 0,
    });

    // Sembrar servicio en efectivo: Carga de $2.000 + $100 recargo = $2.100 total cobrado en efectivo
    await guardarCargaDemo({
      servicio: "Personal",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    });

    // Sembrar gasto en efectivo: $1.500
    await guardarGastoDemo({
      tipo: "Limpieza",
      detalle: "Lavandina",
      monto_salida: 1500,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    // Calcular Arqueo
    const arqueo = await calcularArqueoDemo();

    // Comprobar fórmula rigurosa:
    // efectivo_esperado = ventas_efvo + servicios_efvo - gastos_efvo (sin restar transferencias bancarias)
    expect(arqueo.ventas_efvo).toBeGreaterThanOrEqual(10000);
    expect(arqueo.servicios_efvo).toBeGreaterThanOrEqual(2100);
    expect(arqueo.gastos_efvo).toBeGreaterThanOrEqual(1500);
    expect(arqueo.efectivo_esperado).toBe(
      arqueo.ventas_efvo + arqueo.servicios_efvo - arqueo.gastos_efvo
    );
  });

  // 8. Conciliación de efectivo contado vs esperado: cálculo de sobrante, faltante o exacto ($0)
  it("8. Muestra correctamente la diferencia de caja: cuadrada ($0), sobrante o faltante", async () => {
    const hoyTxt = fechaArgentinaTexto();
    // Sembrar venta para tener un monto positivo esperado en la fecha actual de arqueo
    await guardarVentaDemo({
      ticket_id: "T-TEST-ARQUEO-DIF",
      creado_en: hoyTxt,
      payload: {
        ticket: {
          ticket_id: "T-TEST-ARQUEO-DIF",
          fecha: hoyTxt,
          total_venta: 10000,
          monto_efectivo: 10000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [],
      },
      estado: "pendiente",
      intentos: 0,
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-gastos"));

    // Cambiar a pestaña Arqueo
    await waitFor(() => {
      expect(screen.getByTestId("tab-btn-arqueo")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("tab-btn-arqueo"));

    await waitFor(() => {
      expect(screen.getByTestId("input-cash-counted")).toBeInTheDocument();
    });

    const valEsperadoEl = screen.getByTestId("val-efectivo-esperado");
    expect(valEsperadoEl).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("btn-refresh-arqueo")).not.toBeDisabled();
    });

    // Leer el valor esperado directamente tras el arqueo calculado
    const valText = screen.getByTestId("val-efectivo-esperado").textContent || "";
    const parseado = Number(valText.replace(/[^0-9-]/g, "")) || 0;
    const esperado = parseado;

    const inputContado = screen.getByTestId("input-cash-counted");

    // Caso A: Cuadrada ($0)
    fireEvent.change(inputContado, { target: { value: String(esperado) } });
    await waitFor(() => {
      expect(screen.getByTestId("box-difference")).toHaveTextContent("Caja Cuadrada ($0)");
    });

    // Caso B: Sobrante (+1000)
    fireEvent.change(inputContado, { target: { value: String(esperado + 1000) } });
    await waitFor(() => {
      expect(screen.getByTestId("box-difference")).toHaveTextContent("Sobrante");
      expect(screen.getByTestId("box-difference")).toHaveTextContent("+$1.000");
    });

    // Caso C: Faltante (-1000)
    fireEvent.change(inputContado, { target: { value: String(Math.max(0, esperado - 1000)) } });
    await waitFor(() => {
      expect(screen.getByTestId("box-difference")).toHaveTextContent("Faltante");
    });
  });

  // 9. Segregación de medios de pago (Efectivo vs Transferencia) en banco
  it("9. Segrega correctamente las ventas y gastos por transferencia afectando Caja D (Banco)", async () => {
    // Venta con transferencia
    await guardarVentaDemo({
      ticket_id: "T-TEST-TRANSF-1",
      creado_en: "2026-10-24 11:00:00",
      payload: {
        ticket: {
          ticket_id: "T-TEST-TRANSF-1",
          fecha: "2026-10-24 11:00:00",
          total_venta: 8000,
          monto_efectivo: 0,
          monto_transf: 8000,
          metodo_pago: "TRANSFERENCIA",
        },
        items: [],
      },
      estado: "pendiente",
      intentos: 0,
    });

    // Gasto pagado con transferencia
    await guardarGastoDemo({
      tipo: "Proveedores",
      detalle: "Pago proveedor lácteos por MP",
      monto_salida: 3000,
      metodo_pago: "TRANSFERENCIA",
      origen_fondos: "Banco / MP",
    });

    const arqueo = await calcularArqueoDemo();

    // total_banco = ventas_transf + d_recargas - sube_mp_capital - gastos_transf
    expect(arqueo.ventas_transf).toBeGreaterThanOrEqual(8000);
    expect(arqueo.gastos_transf).toBeGreaterThanOrEqual(3000);
  });

  // 10. Preservación de cajas de servicios (B, C, D, E) en el arqueo diario
  it("10. Preserva la segregación contable de servicios (Prepay, Claro, SUBE MP) en el arqueo", async () => {
    // Carga de Claro ($1500 + $100 comision)
    await guardarCargaDemo({
      servicio: "Claro",
      monto_carga: 1500,
      monto_adicional: 100,
      total_cobrado: 1600,
      pago_efvo: 1600,
      pago_transf: 0,
    });

    // Carga de SUBE MP ($2000 + $100 comision con transferencia)
    await guardarCargaDemo({
      servicio: "SUBE (MP)",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 0,
      pago_transf: 2100,
    });

    const arqueo = await calcularArqueoDemo();
    expect(arqueo.capital_claro).toBeGreaterThanOrEqual(1500);
    expect(arqueo.capital_sube_mp).toBeGreaterThanOrEqual(2000);
    expect(arqueo.adicionales_ganancia).toBeGreaterThanOrEqual(200);
  });

  // 11. Registro de cierre diario y listado de cierres
  it("11. Permite registrar un cierre diario con responsable y observaciones y consultarlo", async () => {
    const api = getApiClient();
    const arqueo = await calcularArqueoDemo();

    const cierreRes = await api.registrarCierreDiario({
      fecha: "2026-10-24",
      efectivo_esperado: arqueo.efectivo_esperado,
      efectivo_contado: arqueo.efectivo_esperado,
      diferencia: 0,
      total_ventas_efvo: arqueo.ventas_efvo,
      total_ventas_transf: arqueo.ventas_transf,
      total_servicios_efvo: arqueo.servicios_efvo,
      total_servicios_transf: arqueo.servicios_transf,
      total_gastos_efvo: arqueo.gastos_efvo,
      total_gastos_transf: arqueo.gastos_transf,
      total_banco: arqueo.total_banco,
      observaciones: "Cierre de prueba Alan OK",
      cerrado_por: "Alan",
    });

    expect(cierreRes.status).toBe(201);
    expect(cierreRes.data?.fecha).toBe("2026-10-24");
    expect(cierreRes.data?.diferencia).toBe(0);
    expect(cierreRes.data?.cerrado_por).toBe("Alan");

    // Consultar cierres mediante api.listarCierres()
    const listaRes = await api.listarCierres();
    expect(listaRes.status).toBe(200);
    expect(listaRes.data?.some((c) => c.fecha === "2026-10-24")).toBe(true);
  });

  // 12. Actualización de cierre existente para la misma fecha (idempotente sin duplicar)
  it("12. Si se registra nuevamente el cierre para una fecha ya cerrada, actualiza el registro sin duplicar", async () => {
    await guardarCierreDemo({
      fecha: "2026-10-23",
      efectivo_esperado: 5000,
      efectivo_contado: 5000,
      diferencia: 0,
      total_ventas_efvo: 5000,
      total_ventas_transf: 0,
      total_servicios_efvo: 0,
      total_servicios_transf: 0,
      total_gastos_efvo: 0,
      total_gastos_transf: 0,
      total_banco: 0,
      observaciones: "Cierre inicial",
      cerrado_por: "Alan",
    });

    // Guardar actualización de cierre en la misma fecha
    await guardarCierreDemo({
      fecha: "2026-10-23",
      efectivo_esperado: 5000,
      efectivo_contado: 5200,
      diferencia: 200,
      total_ventas_efvo: 5000,
      total_ventas_transf: 0,
      total_servicios_efvo: 0,
      total_servicios_transf: 0,
      total_gastos_efvo: 0,
      total_gastos_transf: 0,
      total_banco: 0,
      observaciones: "Cierre corregido con sobrante",
      cerrado_por: "Yilda",
    });

    const lista = await listarCierresDemo();
    const cierresFecha = lista.filter((c) => c.fecha === "2026-10-23");
    expect(cierresFecha.length).toBe(1);
    expect(cierresFecha[0].cerrado_por).toBe("Yilda");
    expect(cierresFecha[0].diferencia).toBe(200);
  });

  // 13. Persistencia de datos en IndexedDB tras reiniciar sesión
  it("13. Los gastos y cierres persisten íntegramente en IndexedDB al reiniciar la instancia", async () => {
    const nuevoGasto = await guardarGastoDemo({
      tipo: "General",
      detalle: "Flete de mercadería persistente",
      monto_salida: 4500,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    // Resetear instancia simulando refresco de página
    resetDBInstance();

    const lista = await listarGastosDemo(false);
    expect(lista.some((g) => g.id === nuevoGasto.id && g.detalle === "Flete de mercadería persistente")).toBe(true);
  });

  // 14. Migración limpia a IndexedDB v4 sin pérdida de stores previas
  it("14. IndexedDB versión 4 contiene todas las tiendas operativas previas y agrega gastos_demo y cierres_demo", async () => {
    const db = await openDB();
    expect(db.version).toBeGreaterThanOrEqual(4);
    expect(db.objectStoreNames.contains("ventas_queue")).toBe(true);
    expect(db.objectStoreNames.contains("catalogo_cache")).toBe(true);
    expect(db.objectStoreNames.contains("catalogo_demo")).toBe(true);
    expect(db.objectStoreNames.contains("meta")).toBe(true);
    expect(db.objectStoreNames.contains("servicios_demo")).toBe(true);
    expect(db.objectStoreNames.contains("gastos_demo")).toBe(true);
    expect(db.objectStoreNames.contains("cierres_demo")).toBe(true);
  });

  // 15. Aislamiento en modo demo (0 llamadas fetch HTTP)
  it("15. Las operaciones de gastos y cierres en modo Demo son 100% locales y no realizan fetch de red", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await guardarGastoDemo({
      tipo: "Proveedores",
      detalle: "Gasto de prueba sin red",
      monto_salida: 1200,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    await guardarCierreDemo({
      fecha: "2026-10-22",
      efectivo_esperado: 1000,
      efectivo_contado: 1000,
      diferencia: 0,
      total_ventas_efvo: 1000,
      total_ventas_transf: 0,
      total_servicios_efvo: 0,
      total_servicios_transf: 0,
      total_gastos_efvo: 0,
      total_gastos_transf: 0,
      total_banco: 0,
      observaciones: "Prueba sin fetch",
      cerrado_por: "Alan",
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  // 16. Manejo seguro de errores ante entradas inválidas en la API
  it("16. Retorna error controlado y no lanza excepciones ante IDs inexistentes en edición o anulación", async () => {
    const api = getApiClient();

    // Editar gasto que no existe
    const editRes = await api.editarGasto(999999, {
      tipo: "General",
      detalle: "No existe",
      monto_salida: 100,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja",
    });
    expect(editRes.status).toBe(400);
    expect(editRes.error).toBeDefined();

    // Anular gasto que no existe
    const anularRes = await api.anularGasto(999999, "Error");
    expect(anularRes.status).toBe(400);
    expect(anularRes.error).toBeDefined();
  });

  // 17. Regresión contable: valores específicos Turso DEV en arqueo de caja
  it("17. Valida el arqueo con los valores exactos observados en Turso DEV y la no mezcla de medios de pago", async () => {
    const db = await openDB();
    const tx = db.transaction(["ventas_demo", "ventas_queue", "servicios_demo", "gastos_demo", "cierres_demo"], "readwrite");
    tx.objectStore("ventas_demo").clear();
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("servicios_demo").clear();
    tx.objectStore("gastos_demo").clear();
    tx.objectStore("cierres_demo").clear();
    await new Promise((resolve) => { tx.oncomplete = resolve; });
    const hoyTxt = fechaArgentinaTexto();

    // Venta: $1.000 efvo, $2.000 transf
    await guardarVentaDemo({
      ticket_id: "TICK-DEV-EXP",
      estado: "sincronizada",
      intentos: 0,
      creado_en: new Date().toISOString(),
      payload: {
        ticket: {
          ticket_id: "TICK-DEV-EXP",
          fecha: hoyTxt,
          total_venta: 3000,
          monto_efectivo: 1000,
          monto_transf: 2000,
          metodo_pago: "MIXTO",
          anulado: false,
        },
        items: [
          {
            producto: "Alfajor DEV",
            unidad: "Unidad",
            cantidad: 1,
            precio_unit: 3000,
            subtotal: 3000,
          },
        ],
      },
    });

    // Cargas de servicios:
    // - Prepay efvo: $116.100
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "PREPAY",
      monto_carga: 116100,
      monto_adicional: 0,
      total_cobrado: 116100,
      pago_efvo: 116100,
      pago_transf: 0,
    });
    // - Prepay transf: $10.000 capital + $500 adicional = $10.500
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "PREPAY",
      monto_carga: 10000,
      monto_adicional: 500,
      total_cobrado: 10500,
      pago_efvo: 0,
      pago_transf: 10500,
    });
    // - Claro efvo: $1.000
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "CLARO",
      monto_carga: 1000,
      monto_adicional: 0,
      total_cobrado: 1000,
      pago_efvo: 1000,
      pago_transf: 0,
    });
    // - Recargos efvo: $6.500
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "MOVISTAR",
      monto_carga: 1,
      monto_adicional: 6500,
      total_cobrado: 6501,
      pago_efvo: 6501,
      pago_transf: 0,
    });

    const a = await calcularArqueoDemo();
    expect(a.ventas_efvo).toBe(1000);
    expect(a.ventas_transf).toBe(2000);
    expect(a.efectivo_disponible_drugstore).toBe(1000);
    expect(a.total_banco).toBe(12500); // 2000 + 10500
    expect(a.efectivo_esperado).toBe(124601); // 1000 + 123601 - 0

    // Agregar gasto efvo $4.600
    await guardarGastoDemo({
      fecha: hoyTxt,
      detalle: "Bolsas",
      tipo: "Limpieza",
      monto_salida: 4600,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });
    const aEfvo = await calcularArqueoDemo();
    expect(aEfvo.gastos_efvo).toBe(4600);
    expect(aEfvo.ventas_efvo).toBe(1000);
    expect(aEfvo.efectivo_disponible_drugstore).toBe(1000);
    expect(aEfvo.efectivo_esperado).toBe(120001);
    expect(aEfvo.total_banco).toBe(12500);

    // Agregar gasto transf $2.500
    await guardarGastoDemo({
      fecha: hoyTxt,
      detalle: "Luz",
      tipo: "Servicios",
      monto_salida: 2500,
      metodo_pago: "TRANSFERENCIA",
      origen_fondos: "Banco / MP",
    });
    const aTransf = await calcularArqueoDemo();
    expect(aTransf.gastos_transf).toBe(2500);
    expect(aTransf.total_banco).toBe(10000);
    expect(aTransf.efectivo_esperado).toBe(120001);
  });

  // 18. Regresión P2-04: calcularArqueoDemo filtra ventas por fechaFiltro y excluye ventas de otros días
  it("18. calcularArqueoDemo filtra las ventas por fechaFiltro y no acumula ventas históricas de otros días", async () => {
    const db = await openDB();
    const tx = db.transaction(["ventas_demo", "ventas_queue", "servicios_demo", "gastos_demo"], "readwrite");
    tx.objectStore("ventas_demo").clear();
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("servicios_demo").clear();
    tx.objectStore("gastos_demo").clear();
    await new Promise((resolve) => { tx.oncomplete = resolve; });

    // Venta del 24/10/2026
    await guardarVentaDemo({
      ticket_id: "TICK-DIA-24",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-24T12:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-DIA-24",
          fecha: "24/10/2026 12:00:00",
          total_venta: 5000,
          monto_efectivo: 5000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [],
      },
    });

    // Venta del 25/10/2026
    await guardarVentaDemo({
      ticket_id: "TICK-DIA-25",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-25T12:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-DIA-25",
          fecha: "25/10/2026 12:00:00",
          total_venta: 9000,
          monto_efectivo: 9000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [],
      },
    });

    const arqueo24 = await calcularArqueoDemo("2026-10-24");
    expect(arqueo24.ventas_efvo).toBe(5000);
    expect(arqueo24.efectivo_esperado).toBe(5000);

    const arqueo25 = await calcularArqueoDemo("2026-10-25");
    expect(arqueo25.ventas_efvo).toBe(9000);
    expect(arqueo25.efectivo_esperado).toBe(9000);
  });

  // 19. Cola offline para servicios y gastos con sincronización idempotente
  it("19. Encola servicios y gastos localmente ante caída de red y los sincroniza idempotentemente al reconectar", async () => {
    const {
      guardarServicioColaLocal,
      listarServiciosColaLocal,
      guardarGastoColaLocal,
      listarGastosColaLocal,
    } = await import("../src/services/db");
    const { sincronizarColaServicios, sincronizarColaGastos } = await import("../src/services/sync");
    const { RealApiClient } = await import("../src/services/api");

    await guardarServicioColaLocal({
      servicio: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
      idempotency_key: "srv-offline-test-1",
    });

    await guardarGastoColaLocal({
      detalle: "Flete offline",
      monto_salida: 3500,
      tipo: "Proveedores",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
      idempotency_key: "gst-offline-test-1",
    });

    const mockFetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers || {});
      if (url.endsWith("/api/services")) {
        expect(headers.get("Idempotency-Key")).toBe("srv-offline-test-1");
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            id: 101,
            fecha: "25/10/2026 10:00:00",
            servicio: "Claro",
            monto_carga: 2000,
            monto_adicional: 100,
            total_cobrado: 2100,
            pago_efvo: 2100,
            pago_transf: 0,
            anulado: false,
          }),
        });
      }
      if (url.endsWith("/api/expenses")) {
        expect(headers.get("Idempotency-Key")).toBe("gst-offline-test-1");
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({
            id: 202,
            fecha: "25/10/2026 10:01:00",
            detalle: "Flete offline",
            monto_salida: 3500,
            tipo: "Proveedores",
            origen_fondos: "Caja diaria",
            metodo_pago: "EFECTIVO",
            anulado: false,
          }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", false);
    client.setAuthToken("jwt_test");

    const resSrv = await sincronizarColaServicios(client);
    expect(resSrv.procesados).toBe(1);
    expect(resSrv.exitosos).toBe(1);

    const resGst = await sincronizarColaGastos(client);
    expect(resGst.procesados).toBe(1);
    expect(resGst.exitosos).toBe(1);

    const srvList = await listarServiciosColaLocal();
    expect(srvList.find((s) => s.idempotency_key === "srv-offline-test-1")?.estado).toBe("sincronizada");

    const gstList = await listarGastosColaLocal();
    expect(gstList.find((g) => g.idempotency_key === "gst-offline-test-1")?.estado).toBe("sincronizada");

    vi.unstubAllGlobals();
  });

  // 20. Invariante Caja A ($4.600) vs Efectivo Esperado del Cajón ($7.400)
  it("20. Mantiene ventas_efvo y efectivo_disponible_drugstore en $4.600 (sin restar gastos_efvo de $1.500) y calcula efectivo_esperado en $7.400", async () => {
    const db = await openDB();
    const tx = db.transaction(["ventas_demo", "ventas_queue", "servicios_demo", "gastos_demo"], "readwrite");
    tx.objectStore("ventas_demo").clear();
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("servicios_demo").clear();
    tx.objectStore("gastos_demo").clear();
    await new Promise((resolve) => { tx.oncomplete = resolve; });

    // Ventas activas en efectivo = $4.600 ($2.600 efvo + $2.000 parte efvo de mixto)
    await guardarVentaDemo({
      ticket_id: "TICK-4600-A",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-25T12:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-4600-A",
          fecha: "25/10/2026 09:00:00",
          total_venta: 2600,
          monto_efectivo: 2600,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [],
      },
    });
    await guardarVentaDemo({
      ticket_id: "TICK-4600-B",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-25T15:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-4600-B",
          fecha: "25/10/2026 12:00:00",
          total_venta: 3400,
          monto_efectivo: 2000,
          monto_transf: 1400,
          metodo_pago: "MIXTO",
          anulado: false,
        },
        items: [],
      },
    });
    // Venta anulada en efectivo ($5.000) -> excluida
    await guardarVentaDemo({
      ticket_id: "TICK-4600-ANUL",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-25T16:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-4600-ANUL",
          fecha: "25/10/2026 13:00:00",
          total_venta: 5000,
          monto_efectivo: 5000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: true,
        },
        items: [],
      },
    });

    // Servicios cobrados en efectivo = $4.300 ($2.100 Claro + $2.200 Personal)
    await guardarCargaDemo({
      fecha: "25/10/2026 10:00:00",
      servicio: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    });
    await guardarCargaDemo({
      fecha: "25/10/2026 11:00:00",
      servicio: "Personal",
      monto_carga: 4000,
      monto_adicional: 200,
      total_cobrado: 4200,
      pago_efvo: 2200,
      pago_transf: 2000,
    });

    // Gasto en efectivo = $1.500
    await guardarGastoDemo({
      fecha: "25/10/2026 14:00:00",
      detalle: "Bolsas consorcio",
      tipo: "Limpieza",
      monto_salida: 1500,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    const arqueo = await calcularArqueoDemo("2026-10-25");
    expect(arqueo.ventas_efvo).toBe(4600);
    expect(arqueo.efectivo_disponible_drugstore).toBe(4600); // NUNCA $3.100
    expect(arqueo.servicios_efvo).toBe(4300);
    expect(arqueo.gastos_efvo).toBe(1500);
    expect(arqueo.efectivo_esperado).toBe(7400); // 4600 + 4300 - 1500
  });
});
