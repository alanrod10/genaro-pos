import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DashboardAdmin } from "../src/components/DashboardAdmin";
import { Header } from "../src/components/Header";
import {
  openDB,
  resetDBInstance,
  calcularMetricasDashboardDemo,
  guardarCargaDemo,
  guardarGastoDemo,
  guardarVentaDemo,
  guardarCierreDemo,
  fechaArgentinaTexto,
} from "../src/services/db";
import { activarModoDemo } from "../src/services/api";

describe("GenaroPOS — Módulo de Dashboard Financiero y Visor (Frontend / IndexedDB)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  it("1. Renderiza el visor financiero con todas las tarjetas de cajas y selector de período", async () => {
    const handleToast = vi.fn();
    render(<DashboardAdmin onAddToast={handleToast} isDemo={true} />);

    expect(screen.getByText(/Visor Financiero y Reportes/i)).toBeInTheDocument();
    expect(screen.getByTestId("periodo-selector")).toBeInTheDocument();
    expect(screen.getByTestId("btn-periodo-hoy")).toBeInTheDocument();
    expect(screen.getByTestId("btn-periodo-7dias")).toBeInTheDocument();
    expect(screen.getByTestId("btn-periodo-30dias")).toBeInTheDocument();
    expect(screen.getByTestId("btn-periodo-personalizado")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("card-caja-a")).toBeInTheDocument();
      expect(screen.getByTestId("card-caja-b")).toBeInTheDocument();
      expect(screen.getByTestId("card-caja-c")).toBeInTheDocument();
      expect(screen.getByTestId("card-caja-d")).toBeInTheDocument();
      expect(screen.getByTestId("card-caja-e")).toBeInTheDocument();
      expect(screen.getByTestId("card-gastos")).toBeInTheDocument();
      expect(screen.getByTestId("card-conciliacion")).toBeInTheDocument();
    });
  });

  it("2. Muestra claramente el cartel de estimación heredada (10%) en Caja A", async () => {
    const handleToast = vi.fn();
    render(<DashboardAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      const aviso = screen.getByTestId("aviso-estimacion-10");
      expect(aviso).toBeInTheDocument();
      expect(aviso).toHaveTextContent(/Estimación heredada, no ganancia real/i);
      expect(aviso).toHaveTextContent(/El catálogo histórico no guardaba costo unitario/i);
    });
  });

  it("3. Permite alternar períodos (Hoy, 7 días, 30 días, Personalizado)", async () => {
    const handleToast = vi.fn();
    render(<DashboardAdmin onAddToast={handleToast} isDemo={true} />);

    // Click 7 días
    const btn7 = screen.getByTestId("btn-periodo-7dias");
    fireEvent.click(btn7);
    expect(btn7).toHaveClass("active");

    // Click 30 días
    const btn30 = screen.getByTestId("btn-periodo-30dias");
    fireEvent.click(btn30);
    expect(btn30).toHaveClass("active");

    // Click Personalizado abre campos de fecha
    const btnPers = screen.getByTestId("btn-periodo-personalizado");
    fireEvent.click(btnPers);
    expect(btnPers).toHaveClass("active");
    expect(screen.getByLabelText(/Desde:/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Hasta:/i)).toBeInTheDocument();
  });

  it("4. Calcula con precisión contable la segregación de Cajas A, B, C, D, E y conciliación física", async () => {
    const hoyTxt = fechaArgentinaTexto(); // "DD/MM/YYYY HH:MM:SS"

    // Guardar una venta de Drugstore: $5.000 ($3.000 efvo, $2.000 transf)
    await guardarVentaDemo({
      ticket_id: "TICK-TEST-DASH",
      payload: {
        ticket: {
          ticket_id: "TICK-TEST-DASH",
          fecha: hoyTxt,
          total_venta: 5000,
          monto_efectivo: 3000,
          monto_transf: 2000,
          metodo_pago: "MIXTO",
          anulado: false,
        },
        items: [
          {
            producto: "Producto Test",
            unidad: "Unidad",
            cantidad: 2,
            precio_unit: 2500,
            subtotal: 5000,
          },
        ],
      },
      creado_en: new Date().toISOString(),
      estado: "pendiente",
      intentos: 0,
    });

    // Guardar una carga Claro: $1.000 carga + $200 recargo = $1.200 (todo transf)
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "Claro",
      monto_carga: 1000,
      monto_adicional: 200,
      total_cobrado: 1200,
      pago_efvo: 0,
      pago_transf: 1200,
    });

    // Guardar una carga SUBE (Caja B): $2.000 carga + $300 recargo = $2.300 (todo efvo)
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "SUBE",
      monto_carga: 2000,
      monto_adicional: 300,
      total_cobrado: 2300,
      pago_efvo: 2300,
      pago_transf: 0,
    });

    // Guardar un gasto: $1.500 en efectivo
    await guardarGastoDemo({
      fecha: hoyTxt,
      tipo: "Limpieza",
      detalle: "Bolsas",
      monto_salida: 1500,
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
    });

    // Guardar un cierre diario
    await guardarCierreDemo({
      fecha: "2026-10-24",
      efectivo_esperado: 2600,
      efectivo_contado: 2600,
      diferencia: 0,
      total_ventas_efvo: 3000,
      total_ventas_transf: 2000,
      total_servicios_efvo: 2300,
      total_servicios_transf: 1200,
      total_gastos_efvo: 1500,
      total_gastos_transf: 0,
      total_banco: 3200,
      cerrado_por: "Alan",
      observaciones: "Cierre exacto",
    });

    // Ejecutar cálculo
    const res = await calcularMetricasDashboardDemo(undefined, undefined, "hoy");

    // Validar Caja A (Ventas)
    expect(res.caja_a.total_ventas).toBeGreaterThanOrEqual(5000);
    expect(res.caja_a.ganancia_estimada_ventas).toBe(Math.round(res.caja_a.total_ventas * 0.1));
    expect(res.caja_a.es_estimacion_heredada).toBe(true);

    // Validar Caja B (SUBE / Prepay): Capital recaudado NO es ganancia
    expect(res.caja_b.capital_total).toBeGreaterThanOrEqual(2000);

    // Validar Caja C (Adicionales / Ganancia servicios): Sube al menos los $500
    expect(res.caja_c.ganancia_adicional_total).toBeGreaterThanOrEqual(500);

    // Validar Caja E (Claro): Capital claro
    expect(res.caja_e.capital_total).toBeGreaterThanOrEqual(1000);

    // Validar Gastos
    expect(res.gastos.gastos_efvo).toBeGreaterThanOrEqual(1500);

    // Validar Conciliación física de cajón:
    // Efectivo esperado = Ventas efvo + Servicios efvo - Gastos efvo (sin restar transferencias)
    const expEsperado =
      res.conciliacion_cajon.ventas_efvo +
      res.conciliacion_cajon.servicios_efvo -
      res.conciliacion_cajon.gastos_efvo;
    expect(res.conciliacion_cajon.efectivo_esperado_cajon).toBe(expEsperado);

    // Validar Banco Neto (Caja D):
    const expBanco =
      res.caja_d.ventas_transf +
      res.caja_d.recargas_transf -
      res.caja_d.sube_mp_capital -
      res.caja_d.gastos_transf;
    expect(res.caja_d.total_banco_neto).toBe(expBanco);
  });

  it("5. Muestra aviso amigable de período sin datos sin fallar ni mostrar NaN", async () => {
    const handleToast = vi.fn();
    render(<DashboardAdmin onAddToast={handleToast} isDemo={true} />);

    // Cambiar a rango personalizado lejano (año 2099)
    const btnPers = screen.getByTestId("btn-periodo-personalizado");
    fireEvent.click(btnPers);

    const inputDesde = screen.getByLabelText(/Desde:/i);
    const inputHasta = screen.getByLabelText(/Hasta:/i);
    const form = screen.getByTestId("form-rango-personalizado");

    fireEvent.change(inputDesde, { target: { value: "2099-01-01" } });
    fireEvent.change(inputHasta, { target: { value: "2099-01-02" } });
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByTestId("empty-dashboard-notice")).toBeInTheDocument();
      expect(screen.getByText(/Sin operaciones registradas en el período seleccionado/i)).toBeInTheDocument();
    });

    // Los importes deben mostrar $0 sin NaN
    expect(screen.getByTestId("total-efectivo-esperado")).toHaveTextContent("$0");
  });

  it("6. Header integra el botón de navegación a Visor", () => {
    const handleSelectView = vi.fn();
    const handleToggleDemo = vi.fn();
    const handleOpenQueue = vi.fn();

    render(
      <Header
        isOnline={true}
        isDemo={true}
        activeView="dashboard"
        onSelectView={handleSelectView}
        onToggleDemo={handleToggleDemo}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={handleOpenQueue}
      />
    );

    const btnVisor = screen.getByTestId("nav-btn-dashboard");
    expect(btnVisor).toBeInTheDocument();
    expect(btnVisor).toHaveClass("nav-active");

    fireEvent.click(btnVisor);
    expect(handleSelectView).toHaveBeenCalledWith("dashboard");
  });

  it("7. Distingue entre ventas con costo congelado y ventas históricas incompletas", async () => {
    const hoyTxt = fechaArgentinaTexto();

    // Guardar una venta con costo unitario congelado explícito ($1.000 costo, $1.500 venta -> $500 ganancia)
    await guardarVentaDemo({
      ticket_id: "TICK-CON-COSTO",
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
      payload: {
        ticket: {
          ticket_id: "TICK-CON-COSTO",
          fecha: hoyTxt,
          total_venta: 1500,
          monto_efectivo: 1500,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [
          {
            producto: "Producto Con Costo",
            unidad: "Unidad",
            cantidad: 1,
            precio_unit: 1500,
            subtotal: 1500,
            costo_unitario: 1000,
          },
        ],
      },
    });

    const metricas = await calcularMetricasDashboardDemo(undefined, undefined, "hoy");

    // Debe haber ventas con costo y ventas históricas sin costo
    expect(metricas.caja_a.ganancia_bruta_real).toBeGreaterThanOrEqual(500);
    expect(metricas.caja_a.costo_historico_total).toBeGreaterThanOrEqual(1000);
    expect(metricas.caja_a.margen_bruto_porcentaje).toBeGreaterThan(0);

    const handleToast = vi.fn();
    render(<DashboardAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("box-ganancia-bruta-real")).toBeInTheDocument();
      expect(screen.getByTestId("monto-ganancia-bruta-real")).toBeInTheDocument();
      expect(screen.getByTestId("aviso-datos-incompletos")).toBeInTheDocument();
      expect(screen.getByText(/datos históricos incompletos/i)).toBeInTheDocument();
    });
  });

  it("8. Reglas críticas: capital de recargas no es ganancia, transferencias no son efectivo y SUBE MP descuenta banco", async () => {
    const hoyTxt = fechaArgentinaTexto();

    // Carga SUBE (MP): $2.000 capital + $200 adicional cobrado en efectivo ($2.200 total)
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "SUBE (MP)",
      monto_carga: 2000,
      monto_adicional: 200,
      total_cobrado: 2200,
      pago_efvo: 2200,
      pago_transf: 0,
    });

    const metricas = await calcularMetricasDashboardDemo(undefined, undefined, "hoy");

    // SUBE (MP) no debe figurar en el capital de Caja B (Prepay) ni Caja E (Claro)
    expect(metricas.caja_d.sube_mp_capital).toBeGreaterThanOrEqual(2000);
    // El adicional sí es ganancia en Caja C
    expect(metricas.caja_c.ganancia_adicional_total).toBeGreaterThanOrEqual(200);

    // Banco neto debe descontar sube_mp_capital
    const expBanco =
      metricas.caja_d.ventas_transf +
      metricas.caja_d.recargas_transf -
      metricas.caja_d.sube_mp_capital -
      metricas.caja_d.gastos_transf;
    expect(metricas.caja_d.total_banco_neto).toBe(expBanco);
  });

  it("9. Regresión contable: valores específicos Turso DEV y segregación estricta de efectivo vs banco", async () => {
    const db = await openDB();
    const tx = db.transaction(["ventas_demo", "ventas_queue", "servicios_demo", "gastos_demo", "cierres_demo"], "readwrite");
    tx.objectStore("ventas_demo").clear();
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("servicios_demo").clear();
    tx.objectStore("gastos_demo").clear();
    tx.objectStore("cierres_demo").clear();
    await new Promise((resolve) => { tx.oncomplete = resolve; });

    const hoyTxt = fechaArgentinaTexto();

    // 1. Venta: $1.000 efvo, $2.000 transf
    await guardarVentaDemo({
      ticket_id: "TICK-DEV-VALS",
      estado: "sincronizada",
      intentos: 0,
      creado_en: new Date().toISOString(),
      payload: {
        ticket: {
          ticket_id: "TICK-DEV-VALS",
          fecha: hoyTxt,
          total_venta: 3000,
          monto_efectivo: 1000,
          monto_transf: 2000,
          metodo_pago: "MIXTO",
          anulado: false,
        },
        items: [
          {
            producto: "Golosinas Kiosco",
            unidad: "Unidad",
            cantidad: 1,
            precio_unit: 3000,
            subtotal: 3000,
          },
        ],
      },
    });

    // 2. Servicios:
    // - Prepay efvo: $116.100 capital
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "PREPAY",
      monto_carga: 116100,
      monto_adicional: 0,
      total_cobrado: 116100,
      pago_efvo: 116100,
      pago_transf: 0,
    });
    // - Prepay transf: $10.000 capital + $500 recargo = $10.500 transferido
    await guardarCargaDemo({
      fecha: hoyTxt,
      servicio: "PREPAY",
      monto_carga: 10000,
      monto_adicional: 500,
      total_cobrado: 10500,
      pago_efvo: 0,
      pago_transf: 10500,
    });
    // - Claro efvo: $1.000 capital
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
      servicio: "PERSONAL",
      monto_carga: 1,
      monto_adicional: 6500,
      total_cobrado: 6501,
      pago_efvo: 6501,
      pago_transf: 0,
    });

    const m = await calcularMetricasDashboardDemo(undefined, undefined, "hoy");

    // Caja A: ventas efvo $1.000, disponible drugstore $1.000, transf $2.000
    expect(m.caja_a.ventas_efvo).toBe(1000);
    expect(m.caja_a.ventas_transf).toBe(2000);
    expect(m.caja_a.efectivo_disponible_drugstore).toBe(1000);

    // Caja D: Banco total = $12.500 ($2.000 ventas transf + $10.500 recargas transf)
    expect(m.caja_d.ventas_transf).toBe(2000);
    expect(m.caja_d.recargas_transf).toBe(10500);
    expect(m.caja_d.total_banco_neto).toBe(12500);

    // Conciliación de cajón físico esperado:
    // ventas_efvo (1.000) + servicios_efvo (123.601) - gastos_efvo (0) = 124.601
    expect(m.conciliacion_cajon.ventas_efvo).toBe(1000);
    expect(m.conciliacion_cajon.servicios_efvo).toBe(123601);
    expect(m.conciliacion_cajon.gastos_efvo).toBe(0);
    expect(m.conciliacion_cajon.efectivo_esperado_cajon).toBe(124601);

    // Agregar gasto en EFECTIVO ($4.600):
    // Debe reducir efectivo esperado a 120.001 y mantener banco en 12.500
    await guardarGastoDemo({
      fecha: hoyTxt,
      detalle: "Limpieza",
      tipo: "Limpieza",
      monto_salida: 4600,
      metodo_pago: "EFECTIVO",
      origen_fondos: "Caja diaria",
    });

    const mGastoEfvo = await calcularMetricasDashboardDemo(undefined, undefined, "hoy");
    expect(mGastoEfvo.conciliacion_cajon.gastos_efvo).toBe(4600);
    expect(mGastoEfvo.caja_a.ventas_efvo).toBe(1000);
    expect(mGastoEfvo.caja_a.efectivo_disponible_drugstore).toBe(1000);
    expect(mGastoEfvo.conciliacion_cajon.efectivo_esperado_cajon).toBe(120001);
    expect(mGastoEfvo.caja_d.total_banco_neto).toBe(12500);

    // Agregar gasto por TRANSFERENCIA ($2.500):
    // Debe reducir banco a 10.000 y mantener efectivo esperado en 120.001
    await guardarGastoDemo({
      fecha: hoyTxt,
      detalle: "Abono",
      tipo: "Servicios",
      monto_salida: 2500,
      metodo_pago: "TRANSFERENCIA",
      origen_fondos: "Banco / MP",
    });

    const mGastoTransf = await calcularMetricasDashboardDemo(undefined, undefined, "hoy");
    expect(mGastoTransf.caja_d.gastos_transf).toBe(2500);
    expect(mGastoTransf.caja_d.total_banco_neto).toBe(10000);
    expect(mGastoTransf.conciliacion_cajon.efectivo_esperado_cajon).toBe(120001);
  });

  // 10. Invariante Caja A ($4.600) vs Efectivo Esperado del Cajón ($7.400) en Dashboard
  it("10. Mantiene caja_a.ventas_efvo y caja_a.efectivo_disponible_drugstore en $4.600 ante gastos_efvo de $1.500 y calcula efectivo_esperado_cajon en $7.400", async () => {
    const db = await openDB();
    const tx = db.transaction(["ventas_demo", "ventas_queue", "servicios_demo", "gastos_demo", "cierres_demo"], "readwrite");
    tx.objectStore("ventas_demo").clear();
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("servicios_demo").clear();
    tx.objectStore("gastos_demo").clear();
    tx.objectStore("cierres_demo").clear();
    await new Promise((resolve) => { tx.oncomplete = resolve; });

    // Ventas activas en efectivo = $4.600 ($2.600 efvo + $2.000 parte efvo de mixto)
    await guardarVentaDemo({
      ticket_id: "TICK-DASH-4600-A",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-25T12:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-DASH-4600-A",
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
      ticket_id: "TICK-DASH-4600-B",
      estado: "sincronizada",
      intentos: 0,
      creado_en: "2026-10-25T15:00:00Z",
      payload: {
        ticket: {
          ticket_id: "TICK-DASH-4600-B",
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

    const m = await calcularMetricasDashboardDemo("2026-10-25", "2026-10-25", "personalizado");
    expect(m.caja_a.ventas_efvo).toBe(4600);
    expect(m.caja_a.efectivo_disponible_drugstore).toBe(4600); // NUNCA $3.100
    expect(m.conciliacion_cajon.ventas_efvo).toBe(4600);
    expect(m.conciliacion_cajon.servicios_efvo).toBe(4300);
    expect(m.conciliacion_cajon.gastos_efvo).toBe(1500);
    expect(m.conciliacion_cajon.efectivo_esperado_cajon).toBe(7400); // 4600 + 4300 - 1500
  });

  it("11. Estructura el Visor Financiero en 5 secciones conceptuales separando actividad del período y saldos de Tesorería", async () => {
    const handleToast = vi.fn();
    render(<DashboardAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-kpi-strip")).toBeInTheDocument();
      expect(screen.getByTestId("visor-seccion-comercial")).toBeInTheDocument();
      expect(screen.getByTestId("visor-seccion-servicios")).toBeInTheDocument();
      expect(screen.getByTestId("visor-seccion-fondos-fisicos")).toBeInTheDocument();
      expect(screen.getByTestId("visor-seccion-cuentas-digitales")).toBeInTheDocument();
      expect(screen.getByTestId("visor-seccion-obligaciones")).toBeInTheDocument();
    });
  });
});
