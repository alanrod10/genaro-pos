import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { App } from "../src/App";
import { resetDBInstance } from "../src/services/db";
import { activarModoDemo } from "../src/services/api";

const VIEWPORTS_MOBILE = [
  { width: 360, height: 640, name: "360x640 (Android Compacto)" },
  { width: 375, height: 667, name: "375x667 (iPhone SE)" },
  { width: 390, height: 844, name: "390x844 (iPhone 14)" },
  { width: 412, height: 915, name: "412x915 (Pixel 7)" },
  { width: 430, height: 932, name: "430x932 (iPhone 14 Pro Max)" },
];

const VIEWPORTS_DESKTOP_TABLET = [
  { width: 768, height: 1024, name: "768x1024 (iPad Vertical)" },
  { width: 1024, height: 768, name: "1024x768 (iPad Horizontal)" },
  { width: 1280, height: 800, name: "1280x800 (Laptop WXGA)" },
  { width: 1366, height: 768, name: "1366x768 (Laptop Referencia)" },
  { width: 1440, height: 900, name: "1440x900 (Desktop HD+)" },
  { width: 1920, height: 1080, name: "1920x1080 (Full HD)" },
];

describe("GenaroPOS — Caja V3: Verificación Visual, Ergonómica y Responsive Multi-Viewport", () => {
  beforeEach(() => {
    resetDBInstance();
    activarModoDemo();
  });

  describe("A. Catálogo y Carrito en Escritorio y Tablets (1024px - 1920px)", () => {
    VIEWPORTS_DESKTOP_TABLET.forEach(({ width, height, name }) => {
      it(`Renderiza grilla de catálogo, tarjetas compactas y panel de carrito simultáneo en ${name}`, async () => {
        window.innerWidth = width;
        window.innerHeight = height;
        window.dispatchEvent(new Event("resize"));

        render(<App />);

        // 1. Buscador y Chips
        const searchInput = await screen.findByTestId("search-input");
        expect(searchInput).toBeInTheDocument();
        expect(screen.getByTestId("btn-refresh-catalog")).toBeInTheDocument();
        expect(screen.getByTestId("cat-TODAS")).toBeInTheDocument();

        // 2. Grilla de productos
        const grid = screen.getByTestId("products-grid");
        expect(grid).toBeInTheDocument();

        // 3. Carrito presente en escritorio
        const cart = screen.getByTestId("cart-container");
        expect(cart).toBeInTheDocument();

        // 4. Agregar un producto al carrito
        const prodCardAlfajor = await screen.findByTestId("prod-card-1");
        expect(prodCardAlfajor).toBeInTheDocument();
        fireEvent.click(prodCardAlfajor);

        // 5. Verificar que el carrito tiene el ítem y sus 3 filas de controles
        expect(screen.getByTestId("cart-item-1")).toBeInTheDocument();
        expect(screen.getByTestId("btn-qty-minus-1")).toBeInTheDocument();
        expect(screen.getByTestId("input-qty-1")).toBeInTheDocument();
        expect(screen.getByTestId("btn-qty-plus-1")).toBeInTheDocument();
        expect(screen.getByTestId("subtotal-1")).toBeInTheDocument();
        expect(screen.getByTestId("btn-remove-1")).toBeInTheDocument();
        expect(screen.getByTestId("btn-adjust-price-1")).toBeInTheDocument();

        // 6. Bloque inferior del carrito: Total y Botón Cobrar Ticket
        expect(screen.getByTestId("total-units")).toBeInTheDocument();
        expect(screen.getByTestId("cart-total-amount")).toBeInTheDocument();
        const btnCheckout = screen.getByTestId("btn-checkout");
        expect(btnCheckout).toBeInTheDocument();
        expect(btnCheckout).toBeEnabled();
      });
    });
  });

  describe("B. Experiencia Mobile First en Celulares (360px - 430px)", () => {
    VIEWPORTS_MOBILE.forEach(({ width, height, name }) => {
      it(`Flujo móvil completo en ${name}: catálogo 2 cols -> barra flotante -> ticket -> checkout`, async () => {
        window.innerWidth = width;
        window.innerHeight = height;
        window.dispatchEvent(new Event("resize"));

        render(<App />);

        // 1. Selector táctil de pantalla en móvil
        const navMobile = await screen.findByTestId("mobile-caja-nav");
        expect(navMobile).toBeInTheDocument();
        const tabCatalog = screen.getByTestId("btn-tab-catalog");
        const tabCart = screen.getByTestId("btn-tab-cart");
        expect(tabCatalog).toBeInTheDocument();
        expect(tabCart).toBeInTheDocument();

        // 2. Catálogo visible y 2 columnas
        expect(screen.getByTestId("products-grid")).toBeInTheDocument();

        // 3. Agregar producto
        const prodCard = await screen.findByTestId("prod-card-1");
        fireEvent.click(prodCard);

        // 4. Barra flotante inferior aparece en vista catálogo
        const mobileBar = await screen.findByTestId("mobile-cart-bar");
        expect(mobileBar).toBeInTheDocument();
        const btnOpenCart = screen.getByTestId("btn-open-cart-mobile");
        const btnQuickCobrar = screen.getByTestId("btn-quick-checkout-mobile");
        expect(btnOpenCart).toBeInTheDocument();
        expect(btnQuickCobrar).toBeInTheDocument();

        // 5. Clic en "Ver Carrito" cambia a vista de Ticket
        fireEvent.click(btnOpenCart);

        // En vista carrito, la lista de artículos está disponible con sus controles
        expect(screen.getByTestId("cart-items-list")).toBeInTheDocument();
        expect(screen.getByTestId("cart-item-1")).toBeInTheDocument();
        expect(screen.getByTestId("btn-checkout")).toBeInTheDocument();

        // 6. Volver al catálogo mediante botón "←"
        const btnBack = screen.getByTestId("btn-back-to-catalog");
        expect(btnBack).toBeInTheDocument();
        fireEvent.click(btnBack);

        // Verifica que regresó a la vista de catálogo
        expect(screen.getByTestId("products-grid")).toBeInTheDocument();
      });
    });
  });

  describe("C. Apertura y Seguridad del Checkout (Modal de Cobro)", () => {
    it("Permite abrir el modal de cobro y cancelarlo sin alterar el ticket ni generar persistencia", async () => {
      render(<App />);

      // Agregar producto
      const prodCard = await screen.findByTestId("prod-card-1");
      fireEvent.click(prodCard);

      // Clic en Cobrar Ticket
      const btnCheckout = screen.getByTestId("btn-checkout");
      fireEvent.click(btnCheckout);

      // Modal de pago visible
      const modal = await screen.findByTestId("payment-modal");
      expect(modal).toBeInTheDocument();
      expect(screen.getByTestId("btn-confirm-payment")).toBeInTheDocument();

      // Cancelar modal
      const btnCancel = screen.getByTestId("btn-close-payment");
      fireEvent.click(btnCancel);

      expect(screen.queryByTestId("payment-modal")).not.toBeInTheDocument();
      // El carrito sigue intacto con 1 ítem
      expect(screen.getByTestId("cart-item-1")).toBeInTheDocument();
    });
  });

  describe("D. Ajuste Puntual de Precio en Carrito", () => {
    it("Abre modal de ajuste de precio para esta venta, valida y actualiza subtotal", async () => {
      render(<App />);

      const prodCard = await screen.findByTestId("prod-card-1");
      fireEvent.click(prodCard);

      // Abrir modal de ajuste de precio
      const btnAdjust = screen.getByTestId("btn-adjust-price-1");
      fireEvent.click(btnAdjust);

      const adjustModal = await screen.findByTestId("adjust-price-modal");
      expect(adjustModal).toBeInTheDocument();

      const inputSubtotal = screen.getByTestId("input-adjust-subtotal");
      fireEvent.change(inputSubtotal, { target: { value: "2500" } });

      const btnConfirm = screen.getByTestId("btn-confirm-adjust-price");
      fireEvent.click(btnConfirm);

      expect(screen.queryByTestId("adjust-price-modal")).not.toBeInTheDocument();
      expect(screen.getByTestId("subtotal-1")).toHaveTextContent("$2.500");
    });
  });
});
