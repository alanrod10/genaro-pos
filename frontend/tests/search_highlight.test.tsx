import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  ProductCatalog,
  renderHighlightedText,
  getSearchPriority,
} from "../src/components/ProductCatalog";
import { Producto } from "../src/types/catalog";

const MOCK_PRODUCTOS: Producto[] = [
  {
    id_producto: 1,
    nombre: "Alfajor Triple Chocolate",
    categoria: "Golosinas",
    proveedor: "Fantoche",
    unidad: "Unidad",
    costo: 500,
    precio_dia: 1000,
    precio_noche: 1000,
    precio_venta: 1000,
    margen_porcentaje: 100,
    activo: true,
    fecha_act: "2026-10-29",
    codigo_barras: "7790001",
  },
  {
    id_producto: 2,
    nombre: "Gaseosa Cola 1.5L",
    categoria: "Bebidas",
    proveedor: "Secco",
    unidad: "Unidad",
    costo: 800,
    precio_dia: 1500,
    precio_noche: 1500,
    precio_venta: 1500,
    margen_porcentaje: 87.5,
    activo: true,
    fecha_act: "2026-10-29",
    codigo_barras: "7790002",
  },
  {
    id_producto: 3,
    nombre: "Galletitas Vainilla",
    categoria: "Almacén",
    proveedor: "Bagley",
    unidad: "Unidad",
    costo: 300,
    precio_dia: 600,
    precio_noche: 600,
    precio_venta: 600,
    margen_porcentaje: 100,
    activo: true,
    fecha_act: "2026-10-29",
    codigo_barras: "7790003",
  },
];

describe("Ajuste Final del Buscador de Productos (Prioridad y Resaltado en Nombre)", () => {
  const mockSelect = vi.fn();
  const mockRefresh = vi.fn();

  it("calcula correctamente la prioridad de búsqueda: nombre que comienza > palabra > parcial > no nombre", () => {
    expect(getSearchPriority("Secco 1,5lts", "secco")).toBe(1);
    expect(getSearchPriority("Soda Secco 1,5lts", "secco")).toBe(2);
    expect(getSearchPriority("Gaseosa antisecco", "secco")).toBe(3);
    expect(getSearchPriority("Agua Vitalissima 500cc", "secco")).toBe(4);
    expect(getSearchPriority("Cualquier producto", "")).toBe(0);
  });

  it("renderHighlightedText divide y resalta de forma insensible a mayúsculas exclusivamente el fragmento coincidente", () => {
    const res = render(<div>{renderHighlightedText("Alfajor Triple", "alfajor")}</div>);
    const mark = res.container.querySelector("mark.search-highlight");
    expect(mark).not.toBeNull();
    expect(mark?.textContent).toBe("Alfajor");
    expect(res.container.textContent).toBe("Alfajor Triple");
  });

  it("renderHighlightedText no resalta si la búsqueda está vacía", () => {
    const res = render(<div>{renderHighlightedText("Alfajor Triple", "")}</div>);
    const mark = res.container.querySelector("mark.search-highlight");
    expect(mark).toBeNull();
    expect(res.container.textContent).toBe("Alfajor Triple");
  });

  it("resalta directamente el fragmento coincidente en el nombre del producto", () => {
    render(
      <ProductCatalog
        productos={MOCK_PRODUCTOS}
        onSelectProduct={mockSelect}
        isCached={false}
        onRefreshCatalog={mockRefresh}
      />
    );

    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "chocolate" } });

    // La tarjeta 1 debe mostrar "Chocolate" resaltado
    const card1 = screen.getByTestId("prod-card-1");
    expect(card1).toBeInTheDocument();
    const mark = card1.querySelector("mark.search-highlight");
    expect(mark).not.toBeNull();
    expect(mark?.textContent).toBe("Chocolate");
    expect(mark).toHaveClass("search-highlight");

    // No debe haber ninguna etiqueta secundaria de proveedor
    expect(card1.querySelector("[data-testid='match-proveedor']")).toBeNull();
  });

  it("no resalta el nombre ni muestra etiquetas secundarias si la coincidencia fue por proveedor", () => {
    render(
      <ProductCatalog
        productos={MOCK_PRODUCTOS}
        onSelectProduct={mockSelect}
        isCached={false}
        onRefreshCatalog={mockRefresh}
      />
    );

    const input = screen.getByTestId("search-input");
    // Buscamos "Secco" (proveedor del producto 2 "Gaseosa Cola 1.5L")
    fireEvent.change(input, { target: { value: "secco" } });

    const card2 = screen.getByTestId("prod-card-2");
    expect(card2).toBeInTheDocument();

    // El nombre del producto NO debe contener <mark> porque "secco" no está en el nombre
    const nameEl = card2.querySelector(".product-name");
    expect(nameEl?.querySelector("mark")).toBeNull();
    expect(nameEl?.textContent).toBe("Gaseosa Cola 1.5L");

    // No deben existir etiquetas del tipo Proveedor: Secco
    expect(card2.querySelector("[data-testid='match-proveedor']")).toBeNull();
    expect(document.querySelector("mark.search-highlight")).toBeNull();
  });

  it("quita todos los resaltados cuando se borra el texto de búsqueda", () => {
    render(
      <ProductCatalog
        productos={MOCK_PRODUCTOS}
        onSelectProduct={mockSelect}
        isCached={false}
        onRefreshCatalog={mockRefresh}
      />
    );

    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "choco" } });

    // Previo a borrar: existe mark
    expect(document.querySelector("mark.search-highlight")).not.toBeNull();

    // Borrar texto de búsqueda
    fireEvent.change(input, { target: { value: "" } });

    // Todos los marks deben desaparecer
    expect(document.querySelector("mark.search-highlight")).toBeNull();
    expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    expect(screen.getByTestId("prod-card-2")).toBeInTheDocument();
    expect(screen.getByTestId("prod-card-3")).toBeInTheDocument();
  });

  it("al hacer click en una tarjeta con texto resaltado, se ejecuta onSelectProduct correctamente", () => {
    render(
      <ProductCatalog
        productos={MOCK_PRODUCTOS}
        onSelectProduct={mockSelect}
        isCached={false}
        onRefreshCatalog={mockRefresh}
      />
    );

    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "Vainilla" } });

    const card3 = screen.getByTestId("prod-card-3");
    fireEvent.click(card3);

    expect(mockSelect).toHaveBeenCalledWith(
      expect.objectContaining({
        id_producto: 3,
        nombre: "Galletitas Vainilla",
      })
    );
  });

  it("verifica el ejemplo obligatorio de 'secco': prioriza coincidencias en nombre sobre proveedor, sin etiquetas secundarias", () => {
    const catalogoEjemplo: Producto[] = [
      {
        id_producto: 27,
        nombre: "Agua Vitalissima 500cc",
        categoria: "Bebidas",
        proveedor: "Secco",
        unidad: "Unidad",
        costo: 300,
        precio_dia: 600,
        precio_noche: 600,
        precio_venta: 600,
        margen_porcentaje: 100,
        activo: true,
        fecha_act: "2026-10-29",
        codigo_barras: "779027",
      },
      {
        id_producto: 705,
        nombre: "Soda Secco 1,5lts",
        categoria: "Bebidas",
        proveedor: "Secco",
        unidad: "Unidad",
        costo: 500,
        precio_dia: 1000,
        precio_noche: 1000,
        precio_venta: 1000,
        margen_porcentaje: 100,
        activo: true,
        fecha_act: "2026-10-29",
        codigo_barras: "779705",
      },
      {
        id_producto: 685,
        nombre: "Secco 1,5lts",
        categoria: "Bebidas",
        proveedor: "Secco",
        unidad: "Unidad",
        costo: 500,
        precio_dia: 1000,
        precio_noche: 1000,
        precio_venta: 1000,
        margen_porcentaje: 100,
        activo: true,
        fecha_act: "2026-10-29",
        codigo_barras: "779685",
      },
      {
        id_producto: 686,
        nombre: "Secco 3lts",
        categoria: "Bebidas",
        proveedor: "Secco",
        unidad: "Unidad",
        costo: 800,
        precio_dia: 1600,
        precio_noche: 1600,
        precio_venta: 1600,
        margen_porcentaje: 100,
        activo: true,
        fecha_act: "2026-10-29",
        codigo_barras: "779686",
      },
      {
        id_producto: 999,
        nombre: "Yerba Mate 500g",
        categoria: "Almacén",
        proveedor: "Playadito",
        unidad: "Unidad",
        costo: 800,
        precio_dia: 1600,
        precio_noche: 1600,
        precio_venta: 1600,
        margen_porcentaje: 100,
        activo: true,
        fecha_act: "2026-10-29",
        codigo_barras: "779999",
      },
    ];

    const onSelect = vi.fn();
    render(
      <ProductCatalog
        productos={catalogoEjemplo}
        onSelectProduct={onSelect}
        isCached={false}
        onRefreshCatalog={vi.fn()}
      />
    );

    const input = screen.getByTestId("search-input");

    // 1. Búsqueda 'secco'
    fireEvent.change(input, { target: { value: "secco" } });

    // Los productos devueltos deben ser 4 (excluyendo Yerba Mate)
    expect(screen.queryByTestId("prod-card-999")).toBeNull();

    // Obtener orden de tarjetas en el DOM
    const cards = screen.getAllByRole("button").filter((b) => b.getAttribute("data-testid")?.startsWith("prod-card-"));
    const cardIds = cards.map((c) => c.getAttribute("data-testid"));

    // Orden esperado:
    // 1 y 2: prod-card-685 ("Secco 1,5lts") y prod-card-686 ("Secco 3lts") (nombre empieza con Secco)
    // 3: prod-card-705 ("Soda Secco 1,5lts") (nombre contiene Secco)
    // 4: prod-card-27 ("Agua Vitalissima 500cc") (sólo proveedor Secco, va al final)
    expect(cardIds[0]).toBe("prod-card-685");
    expect(cardIds[1]).toBe("prod-card-686");
    expect(cardIds[2]).toBe("prod-card-705");
    expect(cardIds[3]).toBe("prod-card-27");

    // Resaltado exclusivo en el nombre:
    // - En "Secco 1,5lts": "Secco" resaltado
    const mark685 = cards[0].querySelector(".product-name mark.search-highlight");
    expect(mark685).not.toBeNull();
    expect(mark685?.textContent).toBe("Secco");

    // - En "Soda Secco 1,5lts": "Secco" resaltado
    const mark705 = cards[2].querySelector(".product-name mark.search-highlight");
    expect(mark705).not.toBeNull();
    expect(mark705?.textContent).toBe("Secco");

    // - En "Agua Vitalissima 500cc": NO resaltado, y NO etiqueta secundaria
    const name27 = cards[3].querySelector(".product-name");
    expect(name27?.querySelector("mark")).toBeNull();
    expect(cards[3].querySelector("[data-testid='match-proveedor']")).toBeNull();
  });

  it("muestra el mensaje de catálogo vacío ante una búsqueda sin resultados", () => {
    render(
      <ProductCatalog
        productos={MOCK_PRODUCTOS}
        onSelectProduct={mockSelect}
        isCached={false}
        onRefreshCatalog={mockRefresh}
      />
    );

    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "inexistente999" } });

    expect(screen.getByText('No se encontraron productos coincidentes con "inexistente999".')).toBeInTheDocument();
  });
});
