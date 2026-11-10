import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ServicesAdmin, calcularRecargoAutomatico } from "../src/components/ServicesAdmin";
import { resetDBInstance, listarServiciosDemo } from "../src/services/db";
import { activarModoDemo } from "../src/services/api";

describe("GenaroPOS — Módulo Servicios: Corrección de Importe Libre y Accesos Rápidos", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // 1. Verificación matemática del recargo automático para importes libres
  it("1. Calcula el recargo automático para importes arbitrarios sin redondear el capital", () => {
    const testCases = [
      { monto: 7500, recargoEsperado: 400, totalEsperado: 7900 },
      { monto: 7600, recargoEsperado: 400, totalEsperado: 8000 },
      { monto: 7850, recargoEsperado: 400, totalEsperado: 8250 },
      { monto: 8000, recargoEsperado: 400, totalEsperado: 8400 },
      { monto: 12345, recargoEsperado: 700, totalEsperado: 13045 },
    ];

    testCases.forEach(({ monto, recargoEsperado, totalEsperado }) => {
      const recargo = calcularRecargoAutomatico(monto);
      expect(recargo).toBe(recargoEsperado);
      expect(monto + recargo).toBe(totalEsperado);
    });
  });

  // 2. Registro interactivo de los importes obligatorios: $7.600, $7.850, $12.345
  const freeAmounts = [7500, 7600, 7850, 8000, 12345];

  freeAmounts.forEach((monto) => {
    it(`2. Permite ingresar y registrar un importe libre de $${monto.toLocaleString("es-AR")} sin rechazo`, async () => {
      const mockToast = vi.fn();
      render(<ServicesAdmin onAddToast={mockToast} isDemo={true} />);

      // Esperar a que cargue la vista
      await waitFor(() => {
        expect(screen.getByTestId("services-form-card")).toBeInTheDocument();
      });

      // Seleccionar plataforma y empresa
      fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "PREPAY" } });
      fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "Personal" } });

      // Ingresar importe manual libre
      const inputMonto = screen.getByTestId("input-service-amount");
      fireEvent.change(inputMonto, { target: { value: monto.toString() } });

      // Verificar que el input conserva exactamente el valor ingresado sin redondeo
      expect(inputMonto).toHaveValue(monto);

      // Calcular recargo esperado
      const recargoEsperado = calcularRecargoAutomatico(monto);
      const totalEsperado = monto + recargoEsperado;

      // Banner muestra el total exacto
      expect(screen.getByTestId("charge-total-banner")).toHaveTextContent(
        `$${totalEsperado.toLocaleString("es-AR")}`
      );

      // Enviar formulario
      const btnSubmit = screen.getByTestId("btn-register-charge");
      fireEvent.click(btnSubmit);

      // Confirmar que no hay error de formulario
      expect(screen.queryByTestId("services-form-error")).not.toBeInTheDocument();

      // Verificar que se guardó en IndexedDB con el importe exacto
      await waitFor(async () => {
        const cargas = await listarServiciosDemo();
        const guardada = cargas.find(
          (c) => c.monto_carga === monto && c.servicio === "Personal"
        );
        expect(guardada).toBeDefined();
        expect(guardada?.monto_carga).toBe(monto);
        expect(guardada?.monto_adicional).toBe(recargoEsperado);
        expect(guardada?.total_cobrado).toBe(totalEsperado);
        expect(guardada?.pago_efvo).toBe(totalEsperado);
      });
    });
  });

  // 3. Los chips de importes sugeridos funcionan como atajos pero no bloquean el ingreso manual
  it("3. Los chips de importes sugeridos cargan el valor y permiten edición manual libre posterior", async () => {
    render(<ServicesAdmin onAddToast={vi.fn()} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("service-quick-chips")).toBeInTheDocument();
    });

    const inputMonto = screen.getByTestId("input-service-amount");

    // Click en chip de $5.000
    const chip5000 = screen.getByTestId("btn-chip-service-5000");
    fireEvent.click(chip5000);
    expect(inputMonto).toHaveValue(5000);
    expect(chip5000).toHaveClass("chip-selected");

    // Sobrescribir manualmente a $7.600
    fireEvent.change(inputMonto, { target: { value: "7600" } });
    expect(inputMonto).toHaveValue(7600);
    expect(chip5000).not.toHaveClass("chip-selected");
  });

  // 4. Edición de una carga puntual con importe libre ($9.350)
  it("4. Permite editar una carga existente fijando un importe libre ($9.350) sin errores de paso", async () => {
    render(<ServicesAdmin onAddToast={vi.fn()} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("services-form-card")).toBeInTheDocument();
    });

    // Registrar carga inicial de $2.000
    fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "CLARO COMERCIOS" } });
    fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "Claro" } });
    fireEvent.change(screen.getByTestId("input-service-amount"), { target: { value: "2000" } });
    fireEvent.click(screen.getByTestId("btn-register-charge"));

    await waitFor(async () => {
      expect(screen.getByTestId("select-plataforma-origen")).toHaveValue("");
      const cargas = await listarServiciosDemo();
      expect(cargas.some((c) => c.monto_carga === 2000)).toBe(true);
    });

    // Abrir modal de edición para la carga registrada
    const cargas = await listarServiciosDemo();
    const cargaReciente = cargas[0];
    const btnEdit = await screen.findByTestId(`btn-edit-charge-${cargaReciente.id}`);
    fireEvent.click(btnEdit);

    expect(screen.getByTestId("modal-edit-charge")).toBeInTheDocument();

    // Modificar a importe libre: $9.350
    const inputEditMonto = screen.getByTestId("input-edit-amount");
    fireEvent.change(inputEditMonto, { target: { value: "9350" } });
    expect(inputEditMonto).toHaveValue(9350);

    // Guardar cambios
    const btnSaveEdit = screen.getByTestId("btn-save-charge-edit");
    fireEvent.click(btnSaveEdit);

    await waitFor(async () => {
      expect(screen.queryByTestId("modal-edit-charge")).not.toBeInTheDocument();
      expect(screen.getByTestId("btn-refresh-services")).not.toBeDisabled();
      const actualizadas = await listarServiciosDemo();
      const editada = actualizadas.find((c) => c.id === cargaReciente.id);
      expect(editada?.monto_carga).toBe(9350);
    });
  });

  // 5. Validaciones permitidas: rechazar montos <= 0 o vacíos
  it("5. Rechaza montos <= 0 o vacíos con mensaje claro y sin bloquear importes libres positivos", async () => {
    render(<ServicesAdmin onAddToast={vi.fn()} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("services-form-card")).toBeInTheDocument();
    });

    // Empresa y plataforma seleccionada, monto 0
    fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "PREPAY" } });
    fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "SUBE" } });
    const btnSubmit = screen.getByTestId("btn-register-charge");
    expect(btnSubmit).toBeDisabled(); // Monto es 0 inicialmente

    // Ingresar monto negativo
    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "-500" } });
    expect(btnSubmit).toBeDisabled();

    // Ingresar monto positivo válido ($7.600) -> botón se habilita
    fireEvent.change(inputMonto, { target: { value: "7600" } });
    expect(btnSubmit).not.toBeDisabled();
  });
});
