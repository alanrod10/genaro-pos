import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ProductCatalog, validarEan13 } from "../src/components/ProductCatalog";
import { CATALOGO_DEMO } from "../src/services/api";
import { Producto } from "../src/types/catalog";
// @ts-ignore
import fs from "node:fs";

describe("GenaroPOS — Parches M1 (Áreas Táctiles) y M5 (Soporte HID EAN-13)", () => {
  describe("1. Tipado y Catálogo Demo con los 4 Códigos Sintéticos (M5)", () => {
    it("valida que los 4 productos sintéticos posean sus códigos de barras asignados en CATALOGO_DEMO", () => {
      const cola = CATALOGO_DEMO.find((p) => p.id_producto === 1);
      const alfajor = CATALOGO_DEMO.find((p) => p.id_producto === 2);
      const galletitas = CATALOGO_DEMO.find((p) => p.id_producto === 7);
      const agua = CATALOGO_DEMO.find((p) => p.id_producto === 3);

      expect(cola?.codigo_barras).toBe("7791234567890");
      expect(cola?.precio_venta).toBe(1200);

      expect(alfajor?.codigo_barras).toBe("7791234567891");
      expect(alfajor?.precio_venta).toBe(800);

      expect(galletitas?.codigo_barras).toBe("7791234567892");
      expect(galletitas?.precio_venta).toBe(1000);

      expect(agua?.codigo_barras).toBe("7791234567893");
      expect(agua?.precio_venta).toBe(1000);
    });

    it("valida que el tipo Producto soporte codigo_barras opcionalmente", () => {
      const prodTest: Producto = {
        id_producto: 99,
        nombre: "Test Barcode",
        categoria: "Test",
        proveedor: "Test Provider",
        unidad: "Unidad",
        costo: 100,
        margen_porcentaje: 0.5,
        precio_venta: 150,
        codigo_barras: "7791234567890",
        activo: true,
        fecha_act: "2026-10-28",
      };
      expect(prodTest.codigo_barras).toBe("7791234567890");
    });
  });

  describe("2. Validación de EAN-13 y Dígito de Control (M5)", () => {
    it("acepta los cuatro códigos sintéticos de homologación", () => {
      expect(validarEan13("7791234567890").valido).toBe(true);
      expect(validarEan13("7791234567891").valido).toBe(true);
      expect(validarEan13("7791234567892").valido).toBe(true);
      expect(validarEan13("7791234567893").valido).toBe(true);
    });

    it("valida y acepta códigos EAN-13 con dígito de control estándar correcto", () => {
      // 7791234567898 tiene check digit 8 válido matemáticamente
      expect(validarEan13("7791234567898").valido).toBe(true);
    });

    it("rechaza códigos EAN-13 con dígito de control incorrecto", () => {
      // 7791234567894 tiene check digit 4 pero el esperado es 8
      const res = validarEan13("7791234567894");
      expect(res.valido).toBe(false);
      expect(res.error).toContain("Dígito de control inválido");
    });

    it("rechaza códigos con longitud distinta de 13 dígitos o caracteres no numéricos", () => {
      expect(validarEan13("12345").valido).toBe(false);
      expect(validarEan13("77912345678901234").valido).toBe(false);
      expect(validarEan13("779123456789A").valido).toBe(false);
    });
  });

  describe("3. Captura HID de Lector en Componente ProductCatalog (M5)", () => {
    let mockSelectProduct: ReturnType<typeof vi.fn>;
    let mockRefreshCatalog: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      vi.useFakeTimers();
      mockSelectProduct = vi.fn();
      mockRefreshCatalog = vi.fn();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("agrega el producto ante ráfaga rápida (<40ms) de escáner HID terminada en Enter sin foco en buscador", () => {
      render(
        <ProductCatalog
          productos={CATALOGO_DEMO}
          onSelectProduct={mockSelectProduct}
          isCached={false}
          onRefreshCatalog={mockRefreshCatalog}
        />
      );

      const barcode = "7791234567890";
      // Simular ráfaga rápida (15ms entre pulsaciones) sobre window
      act(() => {
        for (const char of barcode) {
          window.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
          vi.advanceTimersByTime(15);
        }
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      });

      expect(mockSelectProduct).toHaveBeenCalledTimes(1);
      expect(mockSelectProduct).toHaveBeenCalledWith(
        expect.objectContaining({
          id_producto: 1,
          nombre: "Gaseosa Cola 500ml",
          codigo_barras: "7791234567890",
        })
      );
      expect(screen.getByTestId("scan-feedback")).toHaveTextContent("Escaneado: Gaseosa Cola 500ml");
    });

    it("muestra advertencia sin agregar productos ante código EAN-13 desconocido", () => {
      render(
        <ProductCatalog
          productos={CATALOGO_DEMO}
          onSelectProduct={mockSelectProduct}
          isCached={false}
          onRefreshCatalog={mockRefreshCatalog}
        />
      );

      // Código válido matemáticamente pero no existente en catálogo
      const barcodeDesconocido = "7791234567898";
      act(() => {
        for (const char of barcodeDesconocido) {
          window.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
          vi.advanceTimersByTime(15);
        }
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      });

      expect(mockSelectProduct).not.toHaveBeenCalled();
      expect(screen.getByTestId("scan-feedback")).toHaveTextContent("Producto no encontrado");
    });

    it("no produce doble adición cuando el lector escanea con el buscador enfocado", () => {
      render(
        <ProductCatalog
          productos={CATALOGO_DEMO}
          onSelectProduct={mockSelectProduct}
          isCached={false}
          onRefreshCatalog={mockRefreshCatalog}
        />
      );

      const searchInput = screen.getByTestId("search-input");
      searchInput.focus();

      const barcode = "7791234567891"; // Alfajor
      act(() => {
        for (const char of barcode) {
          window.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
          vi.advanceTimersByTime(15);
        }
        // Disparar Enter
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      });

      // Debe haberse agregado exactamente una vez
      expect(mockSelectProduct).toHaveBeenCalledTimes(1);
      expect(mockSelectProduct).toHaveBeenCalledWith(
        expect.objectContaining({
          id_producto: 2,
          nombre: "Alfajor Triple Chocolate",
        })
      );
    });

    it("no intercepta la escritura cuando el foco está en otro input o textarea", () => {
      render(
        <div>
          <input data-testid="other-input" />
          <ProductCatalog
            productos={CATALOGO_DEMO}
            onSelectProduct={mockSelectProduct}
            isCached={false}
            onRefreshCatalog={mockRefreshCatalog}
          />
        </div>
      );

      const otherInput = screen.getByTestId("other-input");
      otherInput.focus();

      const barcode = "7791234567890";
      act(() => {
        for (const char of barcode) {
          otherInput.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
          vi.advanceTimersByTime(15);
        }
        otherInput.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      });

      // No debe agregar producto porque el foco estaba en un input externo
      expect(mockSelectProduct).not.toHaveBeenCalled();
    });

    it("no intercepta atajos de sistema ni combinaciones con teclas modificadoras (Ctrl/Alt/Meta)", () => {
      render(
        <ProductCatalog
          productos={CATALOGO_DEMO}
          onSelectProduct={mockSelectProduct}
          isCached={false}
          onRefreshCatalog={mockRefreshCatalog}
        />
      );

      act(() => {
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "c", ctrlKey: true, bubbles: true }));
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "v", ctrlKey: true, bubbles: true }));
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      });

      expect(mockSelectProduct).not.toHaveBeenCalled();
    });

    it("permite la búsqueda manual por código de barras escribiendo en el buscador y presionando Enter", () => {
      render(
        <ProductCatalog
          productos={CATALOGO_DEMO}
          onSelectProduct={mockSelectProduct}
          isCached={false}
          onRefreshCatalog={mockRefreshCatalog}
        />
      );

      const searchInput = screen.getByTestId("search-input");
      fireEvent.change(searchInput, { target: { value: "7791234567892" } });
      fireEvent.keyDown(searchInput, { key: "Enter" });

      expect(mockSelectProduct).toHaveBeenCalledTimes(1);
      expect(mockSelectProduct).toHaveBeenCalledWith(
        expect.objectContaining({
          id_producto: 7,
          nombre: "Galletitas Rellenas",
        })
      );
    });

    it("filtra productos en el catálogo al escribir parte de su código de barras", () => {
      render(
        <ProductCatalog
          productos={CATALOGO_DEMO}
          onSelectProduct={mockSelectProduct}
          isCached={false}
          onRefreshCatalog={mockRefreshCatalog}
        />
      );

      const searchInput = screen.getByTestId("search-input");
      fireEvent.change(searchInput, { target: { value: "7791234567893" } }); // Agua Mineral

      expect(screen.getByTestId("prod-card-3")).toBeInTheDocument();
      expect(screen.queryByTestId("prod-card-1")).not.toBeInTheDocument();
    });
  });

  describe("4. Reglas CSS de Áreas Táctiles Mínimas (M1)", () => {
    it("verifica en index.css que los controles críticos definan tamaño mínimo >= 48px y touch-action manipulation", () => {
      // @ts-ignore
      const css = fs.readFileSync("src/index.css", "utf-8");
      // .btn-qty debe medir 48x48 y tener touch-action manipulation
      expect(css).toMatch(/\.btn-qty\s*\{[^}]*width:\s*48px;/);
      expect(css).toMatch(/\.btn-qty\s*\{[^}]*height:\s*48px;/);
      expect(css).toMatch(/\.btn-qty\s*\{[^}]*touch-action:\s*manipulation;/);

      // .btn-remove-item debe medir al menos 48x48 y touch-action
      expect(css).toMatch(/\.btn-remove-item\s*\{[^}]*min-width:\s*48px;/);
      expect(css).toMatch(/\.btn-remove-item\s*\{[^}]*min-height:\s*48px;/);
      expect(css).toMatch(/\.btn-remove-item\s*\{[^}]*touch-action:\s*manipulation;/);

      // .qty-controls debe tener min-height >= 48px
      expect(css).toMatch(/\.qty-controls\s*\{[^}]*min-height:\s*48px;/);

      // .btn-clear-cart debe tener min-height >= 48px y touch-action
      expect(css).toMatch(/\.btn-clear-cart\s*\{[^}]*min-height:\s*48px;/);
      expect(css).toMatch(/\.btn-clear-cart\s*\{[^}]*touch-action:\s*manipulation;/);

      // .btn-confirm-sale y .btn-modal-cancel deben tener min-height >= 48px y touch-action
      expect(css).toMatch(/\.btn-confirm-sale\s*\{[^}]*min-height:\s*48px;/);
      expect(css).toMatch(/\.btn-confirm-sale\s*\{[^}]*touch-action:\s*manipulation;/);
      expect(css).toMatch(/\.btn-modal-cancel\s*\{[^}]*min-height:\s*48px;/);

      // .payment-tab debe tener min-height >= 48px y touch-action
      expect(css).toMatch(/\.payment-tab\s*\{[^}]*min-height:\s*48px;/);
      expect(css).toMatch(/\.payment-tab\s*\{[^}]*touch-action:\s*manipulation;/);

      // .btn-modal-close debe tener min-width y min-height >= 48px
      expect(css).toMatch(/\.btn-modal-close\s*\{[^}]*min-width:\s*48px;/);
      expect(css).toMatch(/\.btn-modal-close\s*\{[^}]*min-height:\s*48px;/);

      // .product-card y .btn-checkout deben tener touch-action: manipulation
      expect(css).toMatch(/\.product-card\s*\{[^}]*touch-action:\s*manipulation;/);
      expect(css).toMatch(/\.btn-checkout\s*\{[^}]*touch-action:\s*manipulation;/);
    });
  });
});
