import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { App } from "../src/App";
import { ServicesAdmin } from "../src/components/ServicesAdmin";
import { resetDBInstance, guardarCargaDemo, listarServiciosDemo } from "../src/services/db";
import { activarModoDemo } from "../src/services/api";

describe("Corrección Definitiva: Servicios (2 Desplegables Dependientes) y Caja (Buscador Limpio)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // --- 1. Desplegables dependientes en el módulo de Servicios ---
  describe("1. Dos Desplegables Dependientes en Servicios", () => {
    it("1.1. Inicia con ambos selectores vacíos y el selector de destino deshabilitado", () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const selectPlat = screen.getByTestId("select-plataforma-origen") as HTMLSelectElement;
      const selectDest = screen.getByTestId("select-servicio-destino") as HTMLSelectElement;

      expect(selectPlat.value).toBe("");
      expect(selectDest.value).toBe("");
      expect(selectDest.disabled).toBe(true);
    });

    it("1.2. Al seleccionar 'PREPAY', habilita el segundo desplegable con: SUBE, Personal, Movistar, Claro", () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const selectPlat = screen.getByTestId("select-plataforma-origen");
      fireEvent.change(selectPlat, { target: { value: "PREPAY" } });

      const selectDest = screen.getByTestId("select-servicio-destino") as HTMLSelectElement;
      expect(selectDest.disabled).toBe(false);

      const options = Array.from(selectDest.options).map((o) => o.value).filter(Boolean);
      expect(options).toEqual(["SUBE", "Personal", "Movistar", "Claro"]);
    });

    it("1.3. Al seleccionar 'CLARO COMERCIOS', habilita el segundo desplegable únicamente con: Claro", () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const selectPlat = screen.getByTestId("select-plataforma-origen");
      fireEvent.change(selectPlat, { target: { value: "CLARO COMERCIOS" } });

      const selectDest = screen.getByTestId("select-servicio-destino") as HTMLSelectElement;
      expect(selectDest.disabled).toBe(false);

      const options = Array.from(selectDest.options).map((o) => o.value).filter(Boolean);
      expect(options).toEqual(["Claro"]);
    });

    it("1.4. Al seleccionar 'MERCADO PAGO', habilita el segundo desplegable con: PREPAY, SUBE, Personal, Movistar, Claro", () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const selectPlat = screen.getByTestId("select-plataforma-origen");
      fireEvent.change(selectPlat, { target: { value: "MERCADO PAGO" } });

      const selectDest = screen.getByTestId("select-servicio-destino") as HTMLSelectElement;
      expect(selectDest.disabled).toBe(false);

      const options = Array.from(selectDest.options).map((o) => o.value).filter(Boolean);
      expect(options).toEqual(["PREPAY", "SUBE", "Personal", "Movistar", "Claro"]);
    });

    it("1.5. Limpia la selección anterior si deja de ser válida al cambiar de plataforma", () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const selectPlat = screen.getByTestId("select-plataforma-origen");
      const selectDest = screen.getByTestId("select-servicio-destino") as HTMLSelectElement;

      // 1. Selecciona PREPAY -> SUBE (válido)
      fireEvent.change(selectPlat, { target: { value: "PREPAY" } });
      fireEvent.change(selectDest, { target: { value: "SUBE" } });
      expect(selectDest.value).toBe("SUBE");

      // 2. Cambia a CLARO COMERCIOS -> SUBE deja de ser válido y debe limpiarse a ""
      fireEvent.change(selectPlat, { target: { value: "CLARO COMERCIOS" } });
      expect(selectDest.value).toBe("");

      // 3. Selecciona Claro (válido para Claro Comercios)
      fireEvent.change(selectDest, { target: { value: "Claro" } });
      expect(selectDest.value).toBe("Claro");

      // 4. Cambia a MERCADO PAGO -> Claro sigue siendo válido en Mercado Pago
      fireEvent.change(selectPlat, { target: { value: "MERCADO PAGO" } });
      expect(selectDest.value).toBe("Claro");
    });

    it("1.6. Bloquea el botón de registro si falta seleccionar plataforma o destino", () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const btnSubmit = screen.getByTestId("btn-register-charge") as HTMLButtonElement;
      expect(btnSubmit.disabled).toBe(true);

      const selectPlat = screen.getByTestId("select-plataforma-origen");
      fireEvent.change(selectPlat, { target: { value: "PREPAY" } });
      expect(btnSubmit.disabled).toBe(true);

      const selectDest = screen.getByTestId("select-servicio-destino");
      fireEvent.change(selectDest, { target: { value: "SUBE" } });

      // Sigue deshabilitado porque monto es 0
      expect(btnSubmit.disabled).toBe(true);

      const inputMonto = screen.getByTestId("input-service-amount");
      fireEvent.change(inputMonto, { target: { value: "2000" } });

      // Ahora que ambos desplegables y monto están seleccionados, se habilita
      expect(btnSubmit.disabled).toBe(false);
    });

    it("1.7. Registra y persiste plataforma_origen y servicio_destino de forma independiente", async () => {
      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      const selectPlat = screen.getByTestId("select-plataforma-origen");
      fireEvent.change(selectPlat, { target: { value: "MERCADO PAGO" } });

      const selectDest = screen.getByTestId("select-servicio-destino");
      fireEvent.change(selectDest, { target: { value: "SUBE" } });

      const inputMonto = screen.getByTestId("input-service-amount");
      fireEvent.change(inputMonto, { target: { value: "3000" } });

      const form = screen.getByTestId("services-form");
      fireEvent.submit(form);

      await waitFor(async () => {
        const cargas = await listarServiciosDemo(false);
        const subeMp = cargas.find((c) => c.plataforma_origen === "MERCADO PAGO" && c.servicio_destino === "SUBE");
        expect(subeMp).toBeDefined();
        expect(subeMp?.monto_carga).toBe(3000);
        expect(subeMp?.total_cobrado).toBe(3200);
      });
    });

    it("1.8. Registros históricos sin plataforma se muestran claramente como 'Histórico (sin origen especificado)'", async () => {
      // Guardar carga histórica sin plataforma_origen
      await guardarCargaDemo({
        servicio: "Claro",
        monto_carga: 1000,
        monto_adicional: 100,
        total_cobrado: 1100,
        pago_efvo: 1100,
        pago_transf: 0,
        plataforma_origen: null,
        servicio_destino: "Claro",
      });

      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      await waitFor(() => {
        expect(screen.getAllByText("Histórico (sin origen especificado)").length).toBeGreaterThan(0);
      });
    });

    it("1.9. Permite filtrar el historial por plataforma y destino", async () => {
      await guardarCargaDemo({
        servicio: "SUBE",
        plataforma_origen: "PREPAY",
        servicio_destino: "SUBE",
        monto_carga: 2000,
        monto_adicional: 100,
        total_cobrado: 2100,
        pago_efvo: 2100,
        pago_transf: 0,
      });
      const cargaClaro = await guardarCargaDemo({
        servicio: "Claro",
        plataforma_origen: "CLARO COMERCIOS",
        servicio_destino: "Claro",
        monto_carga: 4000,
        monto_adicional: 200,
        total_cobrado: 4200,
        pago_efvo: 4200,
        pago_transf: 0,
      });

      render(<ServicesAdmin onAddToast={() => {}} isDemo={true} />);

      await waitFor(() => {
        expect(screen.getByTestId(`history-platform-${cargaClaro.id}`)).toHaveTextContent("CLARO COMERCIOS");
      });

      // Filtrar por plataforma CLARO COMERCIOS
      const filterPlat = screen.getByTestId("filter-platform");
      fireEvent.change(filterPlat, { target: { value: "CLARO COMERCIOS" } });

      await waitFor(() => {
        const platformBadges = screen.getAllByTestId(/history-platform-/);
        for (const badge of platformBadges) {
          expect(badge).toHaveTextContent("CLARO COMERCIOS");
        }
      });
    });
  });

  // --- 2. Caja: Buscador limpio al seleccionar y tras cobrar ---
  describe("2. Caja — Limpieza del Buscador", () => {
    it("2.1. Al seleccionar un producto haciendo clic en su tarjeta, limpia el buscador", async () => {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId("search-input")).toBeInTheDocument();
        expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
      });

      const searchInput = screen.getByTestId("search-input") as HTMLInputElement;

      // Escribir en el buscador un término que coincida con prod-card-1 (Gaseosa Cola)
      fireEvent.change(searchInput, { target: { value: "Cola" } });
      expect(searchInput.value).toBe("Cola");

      // Clic en tarjeta de producto
      const card = screen.getByTestId("prod-card-1");
      fireEvent.click(card);

      // El buscador debe limpiarse inmediatamente
      expect(searchInput.value).toBe("");

      // El producto debe estar agregado en el carrito
      await waitFor(() => {
        expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
      });
    });

    it("2.2. Después de confirmar exitosamente una venta, el buscador permanece vacío y el carrito se vacía", async () => {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
      });

      const searchInput = screen.getByTestId("search-input") as HTMLInputElement;

      // Agregar producto
      fireEvent.click(screen.getByTestId("prod-card-1"));
      await waitFor(() => {
        expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
      });

      // Cobrar venta
      fireEvent.click(screen.getByTestId("btn-checkout"));
      fireEvent.click(screen.getByTestId("btn-confirm-payment"));

      // El carrito debe estar vacío
      await waitFor(() => {
        expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
      });

      // El buscador debe permanecer vacío
      expect(searchInput.value).toBe("");

      // El catálogo permanece intacto
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
      expect(screen.getByTestId("prod-card-2")).toBeInTheDocument();
    });

    it("2.3. Si se cancela el cobro (modal cerrado), el carrito se conserva y el catálogo no cambia", async () => {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("prod-card-1"));
      await waitFor(() => {
        expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
      });

      // Abrir modal de cobro y cancelar con el botón cerrar
      fireEvent.click(screen.getByTestId("btn-checkout"));
      fireEvent.click(screen.getByTestId("btn-close-payment"));

      // Carrito sigue con 1 ítem
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");

      // Catálogo intacto
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
  });
});
