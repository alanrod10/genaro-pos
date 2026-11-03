import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PaymentModal } from "../src/components/PaymentModal";
import { ItemCarrito } from "../src/types/sales";

describe("PaymentModal — Cobro en Efectivo y Cálculo de Vuelto (Caja Registradora)", () => {
  // Carrito base con total de $4.900 según especificación del requerimiento
  const itemsBase4900: ItemCarrito[] = [
    {
      producto_id: 101,
      nombre: "Aceite Girasol 900ml",
      cantidad: 2,
      precio: 2450,
      subtotal: 4900,
      unidad: "Unidad",
    },
  ];

  // Caso 1: Total $4.900, Exacto ($4.900) -> Vuelto $0, Cobro Habilitado
  it("1. Total $4.900, Exacto ($4.900): vuelto $0 y cobro habilitado", async () => {
    const handleConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={handleConfirm}
      />
    );

    // Por defecto al abrir o al pulsar Exacto, el efectivo recibido es igual al total
    const btnExacto = screen.getByTestId("btn-cash-exacto");
    expect(btnExacto).toHaveTextContent("Exacto");
    expect(btnExacto).toHaveAttribute("aria-pressed", "true");

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    expect(Number(inputEfvo.value)).toBe(4900);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$0");

    const confirmBtn = screen.getByTestId("btn-confirm-payment");
    expect(confirmBtn).not.toBeDisabled();

    // Confirmar venta debe enviar monto_efectivo = 4900 y monto_transf = 0
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(handleConfirm).toHaveBeenCalledTimes(1);
    });
    const payload = handleConfirm.mock.calls[0][0];
    expect(payload.ticket.total_venta).toBe(4900);
    expect(payload.ticket.monto_efectivo).toBe(4900);
    expect(payload.ticket.monto_transf).toBe(0);
    expect(payload.ticket.metodo_pago).toBe("EFECTIVO");
  });

  // Caso 2: Total $4.900, Efectivo $5.000 -> Vuelto $100, Cobro Habilitado
  it("2. Total $4.900, Efectivo $5.000: vuelto $100 y cobro habilitado", async () => {
    const handleConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={handleConfirm}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    fireEvent.change(inputEfvo, { target: { value: "5000" } });

    expect(Number(inputEfvo.value)).toBe(5000);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$100");

    const confirmBtn = screen.getByTestId("btn-confirm-payment");
    expect(confirmBtn).not.toBeDisabled();

    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(handleConfirm).toHaveBeenCalledTimes(1);
    });
    // La venta se registra por el total comercial ($4.900), no por el billete entregado
    const payload = handleConfirm.mock.calls[0][0];
    expect(payload.ticket.total_venta).toBe(4900);
    expect(payload.ticket.monto_efectivo).toBe(4900);
  });

  // Caso 3: Total $4.900, Efectivo $10.000 -> Vuelto $5.100, Cobro Habilitado
  it("3. Total $4.900, Efectivo $10.000: vuelto $5.100 y cobro habilitado", () => {
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    fireEvent.change(inputEfvo, { target: { value: "10000" } });

    expect(Number(inputEfvo.value)).toBe(10000);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$5.100");
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });

  // Caso 4: Total $4.900, Efectivo $20.000 -> Vuelto $15.100, Cobro Habilitado
  it("4. Total $4.900, Efectivo $20.000: vuelto $15.100 y cobro habilitado", () => {
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    fireEvent.change(inputEfvo, { target: { value: "20000" } });

    expect(Number(inputEfvo.value)).toBe(20000);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$15.100");
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });

  // Caso 5: Total $4.900, Efectivo $4.899 -> Faltan $1, Cobro Bloqueado
  it("5. Total $4.900, Efectivo $4.899: faltan $1, vuelto $0 y cobro bloqueado", () => {
    const handleConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={handleConfirm}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    fireEvent.change(inputEfvo, { target: { value: "4899" } });

    expect(screen.getByTestId("change-amount")).toHaveTextContent("$0");

    const shortageAlert = screen.getByTestId("cash-shortage-alert");
    expect(shortageAlert).toHaveTextContent(/faltan \$1/i);

    const confirmBtn = screen.getByTestId("btn-confirm-payment");
    expect(confirmBtn).toBeDisabled();

    fireEvent.click(confirmBtn);
    expect(handleConfirm).not.toHaveBeenCalled();
  });

  // Caso 6: Total $4.900, Campo vacío -> Sin vuelto engañoso, Cobro Bloqueado
  it("6. Total $4.900, Campo vacío: sin vuelto engañoso y cobro bloqueado", () => {
    const handleConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={handleConfirm}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    fireEvent.change(inputEfvo, { target: { value: "" } });

    // No debe mostrar un vuelto ficticio
    expect(screen.getByTestId("change-amount")).toHaveTextContent("—");

    const invalidAlert = screen.getByTestId("cash-invalid-alert");
    expect(invalidAlert).toHaveTextContent(/ingresá el monto de efectivo recibido/i);

    const confirmBtn = screen.getByTestId("btn-confirm-payment");
    expect(confirmBtn).toBeDisabled();

    fireEvent.click(confirmBtn);
    expect(handleConfirm).not.toHaveBeenCalled();
  });

  // Caso 7: Total $4.900, Importe negativo o texto inválido -> Error y Cobro Bloqueado
  it("7. Total $4.900, Importe negativo o inválido: muestra error y bloquea el cobro", () => {
    const handleConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={handleConfirm}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    fireEvent.change(inputEfvo, { target: { value: "-500" } });

    expect(screen.getByTestId("change-amount")).toHaveTextContent("—");

    const invalidAlert = screen.getByTestId("cash-invalid-alert");
    expect(invalidAlert).toHaveTextContent(/no puede ser negativo/i);

    const confirmBtn = screen.getByTestId("btn-confirm-payment");
    expect(confirmBtn).toBeDisabled();

    fireEvent.click(confirmBtn);
    expect(handleConfirm).not.toHaveBeenCalled();
  });

  // Caso 8: Botón Exacto restablece el valor tras haber tipeado otro importe
  it("8. Pulsar el botón «Exacto» restablece el efectivo al total de la venta y vuelto a $0", () => {
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    const btnExacto = screen.getByTestId("btn-cash-exacto");

    // Tipia $5.000
    fireEvent.change(inputEfvo, { target: { value: "5000" } });
    expect(Number(inputEfvo.value)).toBe(5000);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$100");
    expect(btnExacto).toHaveAttribute("aria-pressed", "false");

    // Clic en Exacto
    fireEvent.click(btnExacto);
    expect(Number(inputEfvo.value)).toBe(4900);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$0");
    expect(btnExacto).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });

  // Caso 9: Modificación del carrito o del total: el cálculo se actualiza de inmediato
  it("9. Modificación del total de la venta: los cálculos se recalculan de inmediato", () => {
    const { rerender } = render(
      <PaymentModal
        items={itemsBase4900} // Total $4.900
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    const inputEfvo = screen.getByTestId("input-efectivo-recibido") as HTMLInputElement;
    // Tipeamos $10.000 -> vuelto $5.100
    fireEvent.change(inputEfvo, { target: { value: "10000" } });
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$5.100");

    // Ahora el total del carrito se actualiza a $7.000
    const nuevosItems: ItemCarrito[] = [
      {
        producto_id: 102,
        nombre: "Producto Premium",
        cantidad: 1,
        precio: 7000,
        subtotal: 7000,
        unidad: "Unidad",
      },
    ];

    rerender(
      <PaymentModal
        items={nuevosItems}
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    expect(screen.getByTestId("payment-total-banner")).toHaveTextContent("$7.000");
    // Recibido sigue en $10.000 y el vuelto ahora es 10.000 - 7.000 = $3.000
    expect(Number(inputEfvo.value)).toBe(10000);
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$3.000");
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });

  // Caso 10: Prevención de pulsaciones repetidas
  it("10. Pulsaciones repetidas: no duplica confirmaciones ni llamadas concurrentes", async () => {
    let resolver: () => void = () => {};
    const promise = new Promise<void>((resolve) => {
      resolver = resolve;
    });
    const handleConfirm = vi.fn().mockReturnValue(promise);

    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={handleConfirm}
      />
    );

    const confirmBtn = screen.getByTestId("btn-confirm-payment");
    fireEvent.click(confirmBtn);
    fireEvent.click(confirmBtn);
    fireEvent.click(confirmBtn);

    expect(handleConfirm).toHaveBeenCalledTimes(1);

    resolver();
    await waitFor(() => {
      expect(confirmBtn).toBeDisabled();
    });
  });

  // Caso 11: Otros medios de pago: Transferencia funciona con importe exacto
  it("11. Medio de pago TRANSFERENCIA no se altera y preserva sus instrucciones", () => {
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    fireEvent.click(screen.getByTestId("tab-transferencia"));
    expect(screen.getByText(/verificá la acreditación/i)).toBeInTheDocument();
    expect(screen.getByText(/importe exacto:/i)).toHaveTextContent("$4.900");
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });

  // Caso 12: Otros medios de pago: MIXTO valida balance exacto
  it("12. Medio de pago MIXTO no se altera y valida el balance de sumas", () => {
    render(
      <PaymentModal
        items={itemsBase4900}
        onClose={vi.fn()}
        onConfirmSale={vi.fn().mockResolvedValue(undefined)}
      />
    );

    fireEvent.click(screen.getByTestId("tab-mixto"));
    const inputEfvo = screen.getByTestId("input-mixto-efvo");
    const inputTransf = screen.getByTestId("input-mixto-transf");

    // Desbalance: 2000 + 2000 = 4000 (!= 4900)
    fireEvent.change(inputEfvo, { target: { value: "2000" } });
    fireEvent.change(inputTransf, { target: { value: "2000" } });
    expect(screen.getByTestId("split-balance-error")).toBeInTheDocument();
    expect(screen.getByTestId("btn-confirm-payment")).toBeDisabled();

    // Balance correcto: 2900 + 2000 = 4900
    fireEvent.change(inputEfvo, { target: { value: "2900" } });
    expect(screen.queryByTestId("split-balance-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });
});