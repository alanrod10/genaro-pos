import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { App } from "../src/App";
import { resetDBInstance } from "../src/services/db";
import { MockApiClient, activarModoDemo, setApiClient } from "../src/services/api";

describe("Regla de Negocio: Vaciar el Carrito al Cobrar — Casos de Prueba Exhaustivos", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  it("1. Vacía el carrito automáticamente cuando la venta es confirmada por el backend", async () => {
    render(<App />);

    // 1. Agregar producto al carrito
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    // Verificar que el carrito tiene 1 ítem
    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    });

    // 2. Cobrar exitosamente (MockApiClient retorna 201 por defecto)
    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // 3. El carrito debe quedar completamente vacío y la caja lista para la próxima venta
    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });

    // 4. El catálogo permanece intacto
    expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    expect(screen.getByTestId("prod-card-2")).toBeInTheDocument();
  });

  it("2. NO vacía el carrito si el cobro es cancelado por el usuario (modal cerrado)", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    });

    // Abrir modal de cobro y cancelar con el botón ✕
    fireEvent.click(screen.getByTestId("btn-checkout"));
    expect(screen.getByTestId("payment-modal")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("btn-close-payment"));

    // El modal se cierra pero el carrito permanece intacto con su ítem
    await waitFor(() => {
      expect(screen.queryByTestId("payment-modal")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    expect(screen.queryByTestId("cart-empty")).not.toBeInTheDocument();
  });

  it("3. NO vacía el carrito si el cobro falla (error 500 / error de red en el backend)", async () => {
    // Configurar cliente que simula error de servidor
    const mockClient = new MockApiClient();
    mockClient.setSimulationMode("timeout"); // Lanza Error simulado de red / Timeout
    setApiClient(mockClient);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    });

    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // El carrito NO debe ser vaciado porque el servidor no confirmó la venta
    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    });
    expect(screen.queryByTestId("cart-empty")).not.toBeInTheDocument();
  });

  it("4. NO vacía el carrito si el backend rechaza con conflicto 409 o requiere revisión", async () => {
    const mockClient = new MockApiClient();
    mockClient.setSimulationMode("conflicto_409");
    setApiClient(mockClient);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // El carrito permanece intacto para permitir revisión al cajero
    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    });
    expect(screen.queryByTestId("cart-empty")).not.toBeInTheDocument();
  });

  it("5. Vacía el carrito en un reintento idempotente exitoso (200 OK existente)", async () => {
    const mockClient = new MockApiClient();
    mockClient.setSimulationMode("reintento_200"); // Simula ticket ya registrado que responde 200 OK
    setApiClient(mockClient);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // En reintento idempotente exitoso, el carrito sí se vacía
    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });
  });

  it("6. Permite una segunda venta inmediatamente después con nuevo carrito limpio", async () => {
    render(<App />);

    // Primera venta
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));
    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });

    // Segunda venta inmediata con producto distinto
    fireEvent.click(screen.getByTestId("prod-card-2"));
    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
      expect(screen.getByTestId("cart-total-amount")).toHaveTextContent("$800");
    });

    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });
  });
});
