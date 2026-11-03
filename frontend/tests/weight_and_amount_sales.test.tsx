import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WeightSaleModal } from "../src/components/WeightSaleModal";
import { Cart } from "../src/components/Cart";
import { Producto } from "../src/types/catalog";
import { ItemCarrito } from "../src/types/sales";
import {
  getReferencePricePerKg,
  calcularVentaPorPeso,
  calcularVentaPorImporte,
} from "../src/utils/weightSales";

const PROD_MIGNON: Producto = {
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

describe("GenaroPOS — Venta Profesional por Peso e Importe (Reglas Obligatorias)", () => {
  // Caso 1: Mignon a $8.000/kg, Modalidad Por peso, 250 g -> Resultado: $2.000
  it("Caso 1: Calcula correctamente Modalidad POR PESO (250g a $8.000/kg = $2.000)", () => {
    const res = calcularVentaPorPeso(250, "g", 8000);
    expect(res.error).toBeUndefined();
    expect(res.data).toBeDefined();
    expect(res.data?.pesoKg).toBe(0.25);
    expect(res.data?.pesoGramos).toBe(250);
    expect(res.data?.subtotal).toBe(2000);
    expect(res.data?.formatoPesoDisplay).toBe("250 g");
  });

  // Caso 2: Mignon a $8.000/kg, Modalidad Por importe, $1.000 -> Peso equivalente 125 g, Total $1.000
  it("Caso 2: Calcula correctamente Modalidad POR IMPORTE ($1.000 a $8.000/kg = 125g, Total $1.000)", () => {
    const res = calcularVentaPorImporte(1000, 8000);
    expect(res.error).toBeUndefined();
    expect(res.data).toBeDefined();
    expect(res.data?.importe).toBe(1000);
    expect(res.data?.pesoKg).toBe(0.125);
    expect(res.data?.pesoGramos).toBe(125);
    expect(res.data?.subtotal).toBe(1000);
    expect(res.data?.formatoPesoDisplay).toBe("125 g");
  });

  // Caso 3: Precio $10.000/kg, Importe $2.500 -> Peso equivalente 250 g, Total $2.500
  it("Caso 3: Calcula correctamente $2.500 a $10.000/kg (Peso equivalente 250g, Total $2.500)", () => {
    const res = calcularVentaPorImporte(2500, 10000);
    expect(res.error).toBeUndefined();
    expect(res.data?.pesoKg).toBe(0.25);
    expect(res.data?.pesoGramos).toBe(250);
    expect(res.data?.subtotal).toBe(2500);
    expect(res.data?.formatoPesoDisplay).toBe("250 g");
  });

  // Caso 4: Producto por peso, Cantidad 1,5 kg, Precio $8.000/kg -> Total $12.000
  it("Caso 4: Calcula correctamente 1,5 kg a $8.000/kg = Total $12.000", () => {
    const res = calcularVentaPorPeso(1.5, "kg", 8000);
    expect(res.error).toBeUndefined();
    expect(res.data?.pesoKg).toBe(1.5);
    expect(res.data?.pesoGramos).toBe(1500);
    expect(res.data?.subtotal).toBe(12000);
  });

  // Caso 5: Producto con precio por gramo ($8 por gramo) -> normaliza a $8.000/kg y no produce multiplicación errónea
  it("Caso 5: Normaliza correctamente productos con precio por gramo ($8/g = $8.000/kg)", () => {
    const prodGramo: Producto = {
      id_producto: 777,
      nombre: "Hierba especial suelta",
      categoria: "Almacén",
      proveedor: "Herboristería",
      unidad: "g",
      costo: 4,
      margen_porcentaje: 100,
      precio_dia: 8,
      precio_noche: 8,
      precio_venta: 8, // $8 por gramo
      activo: true,
      fecha_act: "2026-10-30",
    };

    const ref = getReferencePricePerKg(prodGramo);
    expect(ref.precioPorKg).toBe(8000);
    expect(ref.precioPorGramo).toBe(8);

    // 250 gramos a $8/g = $2.000
    const venta = calcularVentaPorPeso(250, "g", ref.precioPorKg);
    expect(venta.data?.subtotal).toBe(2000);
  });

  // Caso 6: Producto por peso con precio inválido (<= 0 o no numérico) -> impide la operación con advertencia clara
  it("Caso 6: Rechaza productos con precio inválido y muestra advertencia clara", () => {
    const prodInvalido: Producto = {
      ...PROD_MIGNON,
      precio_venta: 0,
      precio_dia: 0,
    };

    const ref = getReferencePricePerKg(prodInvalido);
    expect(ref.error).toBe("El producto no tiene un precio de referencia válido para venta por peso.");

    const calcPeso = calcularVentaPorPeso(250, "g", 0);
    expect(calcPeso.error).toBe("Precio de referencia por kg inválido.");

    const calcImp = calcularVentaPorImporte(1000, 0);
    expect(calcImp.error).toBe("Precio de referencia por kg inválido.");
  });

  // Caso 7: Venta por importe con precio que produce cantidad periódica ($1.000 a $7.000/kg)
  // Conserva exactamente el importe cobrado de $1.000 según la regla monetaria
  it("Caso 7: Venta por importe periódica ($1.000 a $7.000/kg) conserva exactamente $1.000 de subtotal monetario", () => {
    const res = calcularVentaPorImporte(1000, 7000);
    expect(res.error).toBeUndefined();
    expect(res.data?.subtotal).toBe(1000); // Exactamente $1.000
    expect(res.data?.pesoKg).toBeCloseTo(0.142857, 4);
  });

  // Caso 8: Modal WeightSaleModal permite alternar entre POR PESO y POR IMPORTE y confirma el ítem
  it("Caso 8: WeightSaleModal permite alternar entre POR IMPORTE y POR PESO interactivamente", () => {
    const mockConfirm = vi.fn();
    const mockClose = vi.fn();

    render(
      <WeightSaleModal
        producto={PROD_MIGNON}
        onConfirm={mockConfirm}
        onClose={mockClose}
      />
    );

    // Modalidad por importe inicial
    expect(screen.getByTestId("weight-sale-modal")).toBeInTheDocument();
    expect(screen.getByTestId("btn-mode-importe")).toHaveClass("mode-active");

    const inputImporte = screen.getByTestId("input-requested-amount");
    fireEvent.change(inputImporte, { target: { value: "1000" } });

    // Peso equivalente esperado: 125 g
    expect(screen.getByTestId("text-equivalent-weight")).toHaveTextContent("125 g");
    expect(screen.getByTestId("line-total-amount")).toHaveTextContent("$1.000");

    // Cambiar a modalidad POR PESO
    const btnModePeso = screen.getByTestId("btn-mode-peso");
    fireEvent.click(btnModePeso);
    expect(btnModePeso).toHaveClass("mode-active");

    const inputPeso = screen.getByTestId("input-requested-weight");
    fireEvent.change(inputPeso, { target: { value: "500" } }); // 500g a $8000/kg = $4.000
    expect(screen.getByTestId("line-total-amount")).toHaveTextContent("$4.000");

    // Confirmar venta
    const btnConfirm = screen.getByTestId("btn-confirm-weight-sale");
    fireEvent.click(btnConfirm);

    expect(mockConfirm).toHaveBeenCalledTimes(1);
    const itemConfirmado: ItemCarrito = mockConfirm.mock.calls[0][0];
    expect(itemConfirmado.nombre).toBe("Mignon blandito");
    expect(itemConfirmado.cantidad).toBe(0.5);
    expect(itemConfirmado.subtotal).toBe(4000);
    expect(itemConfirmado.precio).toBe(8000);
  });

  // Caso 9: Ajustar precio puntual de esta venta desde Cart no altera el catálogo general
  it("Caso 9: Ajustar precio puntual de esta venta actualiza la línea del carrito sin alterar el catálogo", async () => {
    const mockUpdateLinePrice = vi.fn();
    const items: ItemCarrito[] = [
      {
        producto_id: 504,
        nombre: "Mignon blandito",
        unidad: "Gramos",
        precio: 8000,
        cantidad: 0.25,
        subtotal: 2000,
      },
    ];

    render(
      <Cart
        items={items}
        onUpdateQuantity={vi.fn()}
        onUpdateLinePrice={mockUpdateLinePrice}
        onRemoveItem={vi.fn()}
        onClearCart={vi.fn()}
        onCheckout={vi.fn()}
      />
    );

    // Botón de ajuste de precio puntual
    const btnAdjust = screen.getByTestId("btn-adjust-price-504");
    expect(btnAdjust).toHaveTextContent("Ajustar precio de esta venta");
    fireEvent.click(btnAdjust);

    // Modal de ajuste puntual abierto
    expect(screen.getByTestId("adjust-price-modal")).toBeInTheDocument();
    expect(screen.getByText(/No modifica el catálogo de productos/i)).toBeInTheDocument();

    const inputSubtotal = screen.getByTestId("input-adjust-subtotal");
    fireEvent.change(inputSubtotal, { target: { value: "1800" } }); // Descuento/ajuste puntual a $1.800

    const btnConfirmAdjust = screen.getByTestId("btn-confirm-adjust-price");
    fireEvent.click(btnConfirmAdjust);

    expect(mockUpdateLinePrice).toHaveBeenCalledWith(504, 7200, 1800);
  });

  // Caso 10: Carrito mixto con productos normales y productos por peso suma el total con exactitud
  it("Caso 10: Carrito mixto con producto normal ($1.200) y producto por peso ($2.000) suma exactamente $3.200", () => {
    const items: ItemCarrito[] = [
      {
        producto_id: 1,
        nombre: "Gaseosa Cola 500ml",
        unidad: "Unidad",
        precio: 1200,
        cantidad: 1,
        subtotal: 1200,
      },
      {
        producto_id: 504,
        nombre: "Mignon blandito",
        unidad: "Gramos",
        precio: 8000,
        cantidad: 0.25,
        subtotal: 2000,
      },
    ];

    render(
      <Cart
        items={items}
        onUpdateQuantity={vi.fn()}
        onRemoveItem={vi.fn()}
        onClearCart={vi.fn()}
        onCheckout={vi.fn()}
      />
    );

    expect(screen.getByTestId("cart-items-count")).toHaveTextContent("2 ítems");
    expect(screen.getByTestId("cart-total-amount")).toHaveTextContent("$3.200");
  });
});
