import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TesoreriaLab } from "../src/components/TesoreriaLab";
import { TesoreriaService } from "../src/services/tesoreria";

describe("TesoreriaLab — Pestaña de Saldos Iniciales / Apertura", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(TesoreriaService, "getSaldosIniciales").mockResolvedValue({
      fondos_fisicos: [
        { codigo: "1110", nombre: "Drugstore Diario", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1120", nombre: "Drugstore Acumulado", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1130", nombre: "Prepay Efectivo", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1140", nombre: "Adicionales Efectivo", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1150", nombre: "Claro Efectivo", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1160", nombre: "Prepay Diario Efectivo", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
      ],
      cuentas_digitales: [
        { codigo: "1210", nombre: "Mercado Pago", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1220", nombre: "Prepay Digital", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1230", nombre: "Claro Comercios", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "1310", nombre: "Tránsito Preventistas", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
      ],
      obligaciones: [
        { codigo: "DAVID", nombre: "Deuda pendiente con David", categoria: "OBLIGACION", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "CLARO", nombre: "Obligaciones circuito Claro / Ezequiel", categoria: "OBLIGACION", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
        { codigo: "PREVENTISTAS", nombre: "Obligaciones de reposición Preventistas (histórico)", categoria: "OBLIGACION", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: null, estado_configuracion: "INICIAL_CERO", editable: true },
      ],
      total_fondos_fisicos_cents: 0,
      total_cuentas_digitales_cents: 0,
      total_obligaciones_cents: 0,
    });

    vi.spyOn(TesoreriaService, "getHistorialAperturas").mockResolvedValue([
      {
        id: 1,
        item_codigo: "1110",
        item_nombre: "Drugstore Diario",
        categoria: "FONDO_FISICO",
        saldo_anterior_cents: 0,
        saldo_nuevo_cents: 0,
        fecha_hora: "2026-11-05T12:00:00Z",
        usuario_responsable: "SISTEMA_INICIAL",
        motivo: "Inicialización oficial de saldos en $0 según directiva MEGA SYSTEM PROMPT",
        referencia_autorizacion: null,
        es_apertura_inicial: true,
      },
    ]);
  });

  it("1. Renderiza la pestaña de aperturas y muestra todos los saldos en $0", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", role: "socio" }} />);

    // Click en la pestaña de aperturas
    const tabAperturas = screen.getByTestId("tab-aperturas");
    expect(tabAperturas).toBeInTheDocument();
    fireEvent.click(tabAperturas);

    await waitFor(() => {
      expect(screen.getByTestId("seccion-aperturas-tesoreria")).toBeInTheDocument();
    });

    // Validar tarjetas de totales en 0
    expect(screen.getByTestId("total-apertura-fisicos")).toHaveTextContent("$0,00");
    expect(screen.getByTestId("total-apertura-digitales")).toHaveTextContent("$0,00");
    expect(screen.getByTestId("total-apertura-obligaciones")).toHaveTextContent("$0,00");

    // Validar presencia de las 3 tablas
    expect(screen.getByTestId("tabla-apertura-fisicos")).toBeInTheDocument();
    expect(screen.getByTestId("tabla-apertura-digitales")).toBeInTheDocument();
    expect(screen.getByTestId("tabla-apertura-obligaciones")).toBeInTheDocument();
    expect(screen.getByTestId("tabla-historial-aperturas")).toBeInTheDocument();
  });

  it("2. Permite abrir modal de configuración para 1110 y completar motivo y valor nuevo", async () => {
    const modificarSpy = vi.spyOn(TesoreriaService, "modificarSaldoInicial").mockResolvedValue({
      status: "SUCCESS",
      item_codigo: "1110",
      saldo_nuevo_cents: 5000000,
    });

    render(<TesoreriaLab currentUser={{ username: "alan", role: "socio" }} />);
    fireEvent.click(screen.getByTestId("tab-aperturas"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-configurar-apertura-1110")).toBeInTheDocument();
    });

    // Click en configurar fondo 1110
    fireEvent.click(screen.getByTestId("btn-configurar-apertura-1110"));

    expect(screen.getByTestId("modal-configurar-apertura")).toBeInTheDocument();

    // Completar nuevo saldo y motivo
    const inputSaldo = screen.getByTestId("input-nuevo-saldo-apertura");
    const inputMotivo = screen.getByTestId("input-motivo-apertura");
    fireEvent.change(inputSaldo, { target: { value: "50000" } });
    fireEvent.change(inputMotivo, { target: { value: "Fondo de cambio para inicio operativo" } });

    // Click en Revisar y Confirmar
    fireEvent.click(screen.getByTestId("btn-guardar-apertura-previa"));

    // Click en Confirmar y Guardar
    const btnConfirmar = screen.getByTestId("btn-confirmar-guardar-apertura");
    expect(btnConfirmar).toBeInTheDocument();
    fireEvent.click(btnConfirmar);

    await waitFor(() => {
      expect(modificarSpy).toHaveBeenCalledWith({
        codigo: "1110",
        saldo_nuevo_cents: 5000000,
        motivo: "Fondo de cambio para inicio operativo",
        referencia_autorizacion: undefined,
      });
      expect(screen.queryByTestId("modal-configurar-apertura")).not.toBeInTheDocument();
    });
  });
});
