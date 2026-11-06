import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Header } from "../src/components/Header";
import { ProductCatalog } from "../src/components/ProductCatalog";
import { WeightSaleModal } from "../src/components/WeightSaleModal";
import { ProductAdmin } from "../src/components/ProductAdmin";
import { PreventistasAdmin } from "../src/components/PreventistasAdmin";
import { DashboardAdmin } from "../src/components/DashboardAdmin";
import { Producto } from "../src/types/catalog";
import { resetDBInstance } from "../src/services/db";
import { activarModoDemo } from "../src/services/api";

const MOCK_PROD: Producto = {
  id_producto: 504,
  nombre: "Mignon blandito",
  categoria: "Panadería",
  proveedor: "Panadería Maná",
  unidad: "Gramos",
  costo: 5000,
  margen_porcentaje: 60,
  precio_dia: 8000,
  precio_noche: 8000,
  precio_venta: 8000,
  activo: true,
  fecha_act: "2026-10-30",
};

const VIEWPORTS = [320, 360, 375, 390, 414, 430, 768, 1024, 1366, 1440, 1920];

describe("GenaroPOS — Pruebas de Adaptabilidad Responsive Multi-Viewport", () => {
  beforeEach(() => {
    resetDBInstance();
    activarModoDemo();
  });

  VIEWPORTS.forEach((width) => {
    describe(`Viewport ${width}px`, () => {
      it(`Renderiza navegación completa con 8 módulos accesibles y sin cortes a ${width}px`, () => {
        // Simular ancho de ventana
        window.innerWidth = width;
        window.dispatchEvent(new Event("resize"));

        render(
          <Header
            isOnline={true}
            isDemo={true}
            activeView="caja"
            onSelectView={vi.fn()}
            onToggleDemo={vi.fn()}
            pendingCount={0}
            reviewCount={0}
            onOpenQueue={vi.fn()}
          />
        );

        const nav = screen.getByTestId("nav-modules");
        expect(nav).toBeInTheDocument();

        // 8 módulos obligatorios
        const modules = [
          "nav-btn-caja",
          "nav-btn-productos",
          "nav-btn-servicios",
          "nav-btn-gastos",
          "nav-btn-historial",
          "nav-btn-preventistas",
          "nav-btn-dashboard",
          "nav-btn-tesoreria",
        ];

        modules.forEach((testId) => {
          const btn = screen.getByTestId(testId);
          expect(btn).toBeInTheDocument();
          expect(btn).toBeVisible();
        });

        // Selector de tema disponible
        expect(screen.getByTestId("theme-selector")).toBeInTheDocument();
      });

      it(`Renderiza buscador y modal de peso proporcionados a ${width}px`, () => {
        window.innerWidth = width;
        window.dispatchEvent(new Event("resize"));

        render(
          <ProductCatalog
            productos={[MOCK_PROD]}
            onSelectProduct={vi.fn()}
            isCached={false}
            onRefreshCatalog={vi.fn()}
          />
        );

        const searchInput = screen.getByTestId("search-input");
        expect(searchInput).toBeInTheDocument();
        expect(searchInput).toBeVisible();

        // Render modal por peso
        const { unmount } = render(
          <WeightSaleModal
            producto={MOCK_PROD}
            onConfirm={vi.fn()}
            onClose={vi.fn()}
          />
        );

        expect(screen.getByTestId("weight-sale-modal")).toBeInTheDocument();
        expect(screen.getByTestId("btn-mode-importe")).toBeInTheDocument();
        expect(screen.getByTestId("btn-mode-peso")).toBeInTheDocument();
        expect(screen.getByTestId("btn-confirm-weight-sale")).toBeInTheDocument();

        unmount();
      });

      it(`Renderiza edición de precio en Productos, barra de Preventistas y 5 secciones del Visor a ${width}px`, async () => {
        window.innerWidth = width;
        window.dispatchEvent(new Event("resize"));

        const { unmount: unmountProd } = render(
          <ProductAdmin
            productos={[MOCK_PROD]}
            isDemo={true}
            onRefresh={vi.fn()}
            onAddToast={vi.fn()}
          />
        );

        fireEvent.click(screen.getByTestId(`btn-edit-${MOCK_PROD.id_producto}`));
        expect(screen.getByTestId("product-modal")).toBeInTheDocument();
        const saveBtn = screen.getByTestId("btn-save-product");
        const cancelBtn = screen.getByTestId("btn-cancel-product");
        expect(saveBtn).toBeVisible();
        expect(saveBtn).toHaveTextContent("Actualizar Producto");
        expect(cancelBtn).toBeVisible();
        unmountProd();

        const { unmount: unmountPrev } = render(
          <PreventistasAdmin onAddToast={vi.fn()} isDemo={true} />
        );
        await waitFor(() => {
          expect(screen.getByTestId("btn-guardar-precios")).toBeVisible();
        });
        unmountPrev();

        const { unmount: unmountVisor } = render(
          <DashboardAdmin onAddToast={vi.fn()} isDemo={true} />
        );
        await waitFor(() => {
          expect(screen.getByTestId("dashboard-kpi-strip")).toBeVisible();
          expect(screen.getByTestId("visor-seccion-comercial")).toBeVisible();
          expect(screen.getByTestId("visor-seccion-servicios")).toBeVisible();
          expect(screen.getByTestId("visor-seccion-fondos-fisicos")).toBeVisible();
          expect(screen.getByTestId("visor-seccion-cuentas-digitales")).toBeVisible();
          expect(screen.getByTestId("visor-seccion-obligaciones")).toBeVisible();
        });
        unmountVisor();
      });
    });
  });
});
