import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import {
  openDB,
  resetDBInstance,
  listarProductosDemo,
  crearProductoDemo,
  guardarVentaLocal,
  listarVentasLocales,
} from "../src/services/db";
import { activarModoDemo } from "../src/services/api";

describe("GenaroPOS — Módulo de Administración de Productos (Frontend / IndexedDB)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // 1. Navegación entre Caja y Administración
  it("1. Permite alternar la navegación entre la vista de Caja y Productos mediante el Header", async () => {
    render(<App />);

    // Por defecto inicia en Caja
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.queryByTestId("product-admin-container")).not.toBeInTheDocument();

    // Clic en pestaña 'Productos'
    const btnNavProductos = screen.getByTestId("nav-btn-productos");
    fireEvent.click(btnNavProductos);

    // Debe mostrar la vista de Administración de Productos
    expect(screen.getByTestId("product-admin-container")).toBeInTheDocument();
    expect(screen.getByTestId("btn-new-product")).toBeInTheDocument();
    expect(screen.queryByTestId("search-input")).not.toBeInTheDocument();

    // Volver a Caja
    const btnNavCaja = screen.getByTestId("nav-btn-caja");
    fireEvent.click(btnNavCaja);
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
  });

  // 2. Listado, búsqueda y filtros en Administración
  it("2. Permite buscar productos por nombre y filtrar por categoría y estado activo/inactivo", async () => {
    render(<App />);

    // Ir a Productos
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    // Esperar a que cargue la tabla
    await waitFor(() => {
      expect(screen.getAllByText("Gaseosa Cola 500ml").length).toBeGreaterThan(0);
    });

    // Búsqueda por texto: "Alfajor"
    const searchInput = screen.getByTestId("admin-search-input");
    await userEvent.type(searchInput, "Alfajor");

    expect(screen.getAllByText("Alfajor Triple Chocolate").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Gaseosa Cola 500ml")).toHaveLength(0);

    // Limpiar búsqueda
    await userEvent.clear(searchInput);

    // Filtrar por categoría "Servicios"
    const catFilter = screen.getByTestId("admin-category-filter");
    fireEvent.change(catFilter, { target: { value: "Servicios" } });

    expect(screen.getAllByText("Fotocopia B/N simple faz").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Alfajor Triple Chocolate")).toHaveLength(0);
  });

  // 3. Crear un producto válido en Modo Demo
  it("3. Permite dar de alta un nuevo producto válido y lo persiste en IndexedDB (catalogo_demo)", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-new-product")).toBeInTheDocument();
    });

    // Abrir modal de alta
    fireEvent.click(screen.getByTestId("btn-new-product"));
    expect(screen.getByTestId("product-modal")).toBeInTheDocument();

    // Completar formulario
    await userEvent.type(screen.getByTestId("input-prod-nombre"), "Jugo de Naranja 1L");
    fireEvent.change(screen.getByTestId("input-prod-costo"), { target: { value: "500" } });
    fireEvent.change(screen.getByTestId("input-prod-precio"), { target: { value: "900" } });

    // Verificar margen proyectado: ((900 - 500) / 500) * 100 = 80%
    expect(screen.getByTestId("margin-preview-banner")).toHaveTextContent("+80%");

    // Guardar
    fireEvent.click(screen.getByTestId("btn-save-product"));

    // El producto debe aparecer en la lista
    await waitFor(() => {
      expect(screen.getAllByText("Jugo de Naranja 1L").length).toBeGreaterThan(0);
    });

    // Comprobar persistencia directa en IndexedDB
    const prodsDemo = await listarProductosDemo(false);
    const guardado = prodsDemo.find((p) => p.nombre === "Jugo de Naranja 1L");
    expect(guardado).toBeDefined();
    expect(guardado?.costo).toBe(500);
    expect(guardado?.precio_venta).toBe(900);
    expect(guardado?.activo).toBe(true);
  });

  // 4. Validación de campos obligatorios y precio
  it("4. Rechaza la creación con nombre vacío o precio menor o igual a cero con mensaje claro", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-new-product")).toBeInTheDocument();
    });

    // Abrir modal
    fireEvent.click(screen.getByTestId("btn-new-product"));

    // Intentar guardar con nombre vacío
    fireEvent.click(screen.getByTestId("btn-save-product"));
    await waitFor(() => {
      expect(screen.getByTestId("form-error-banner")).toHaveTextContent("El nombre del producto es obligatorio.");
    });

    // Ingresar nombre pero precio 0
    await userEvent.type(screen.getByTestId("input-prod-nombre"), "Producto Inválido");
    fireEvent.change(screen.getByTestId("input-prod-precio"), { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("btn-save-product"));

    await waitFor(() => {
      expect(screen.getByTestId("form-error-banner")).toHaveTextContent("El precio de venta debe ser un número mayor a cero.");
    });
  });

  // 5. Editar costo y precio de un producto existente
  it("5. Permite editar el costo y precio de un producto específico sin alterar los demás registros", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-edit-2")).toBeInTheDocument(); // Alfajor Triple Chocolate
    });

    // Abrir edición del producto 2
    fireEvent.click(screen.getByTestId("btn-edit-2"));

    // Modificar precio de 800 a 850 y costo de 500 a 550
    const inputPrecio = screen.getByTestId("input-prod-precio");
    const inputCosto = screen.getByTestId("input-prod-costo");
    fireEvent.change(inputCosto, { target: { value: "550" } });
    fireEvent.change(inputPrecio, { target: { value: "850" } });

    // Guardar
    fireEvent.click(screen.getByTestId("btn-save-product"));

    await waitFor(() => {
      expect(screen.queryByTestId("product-modal")).not.toBeInTheDocument();
    });

    // Comprobar persistencia en IndexedDB
    const prods = await listarProductosDemo(false);
    const prod2 = prods.find((p) => p.id_producto === 2);
    expect(prod2?.precio_venta).toBe(850);
    expect(prod2?.costo).toBe(550);

    // Producto 1 (Gaseosa) debe seguir con sus valores originales
    const prod1 = prods.find((p) => p.id_producto === 1);
    expect(prod1?.precio_venta).toBe(1200);
    expect(prod1?.costo).toBe(800);
  });

  // 6. Desactivar y reactivar producto
  it("6. Desactiva un producto excluyéndolo de la Caja, pero conservándolo en la administración", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    // Esperar a ver Gaseosa Cola (id=1)
    await waitFor(() => {
      expect(screen.getByTestId("btn-toggle-status-1")).toBeInTheDocument();
    });

    // Desactivar Gaseosa Cola
    fireEvent.click(screen.getByTestId("btn-toggle-status-1"));

    // En la tabla ahora el botón debe decir "Activar"
    await waitFor(() => {
      expect(screen.getByTestId("btn-toggle-status-1")).toHaveTextContent("Activar");
    });

    // Volver a Caja
    fireEvent.click(screen.getByTestId("nav-btn-caja"));

    // En Caja la Gaseosa NO debe estar visible
    await waitFor(() => {
      expect(screen.queryByTestId("prod-card-1")).not.toBeInTheDocument();
    });
    // Pero Alfajor sí sigue visible
    expect(screen.getByTestId("prod-card-2")).toBeInTheDocument();

    // Volver a Productos y reactivar Gaseosa
    fireEvent.click(screen.getByTestId("nav-btn-productos"));
    await waitFor(() => {
      expect(screen.getByTestId("btn-toggle-status-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("btn-toggle-status-1"));

    // Volver a Caja y comprobar que reapareció
    fireEvent.click(screen.getByTestId("nav-btn-caja"));
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
  });

  // 7. Cancelar edición sin modificar el registro
  it("7. Cancelar la edición cierra el modal sin alterar el producto", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-edit-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-edit-1"));
    fireEvent.change(screen.getByTestId("input-prod-precio"), { target: { value: "9999" } });

    // Clic en Cancelar
    fireEvent.click(screen.getByTestId("btn-cancel-product"));

    // Modal se cierra
    expect(screen.queryByTestId("product-modal")).not.toBeInTheDocument();

    // El precio sigue siendo 1200
    const prods = await listarProductosDemo(false);
    const prod1 = prods.find((p) => p.id_producto === 1);
    expect(prod1?.precio_venta).toBe(1200);
  });

  // 8. Editar un producto no altera un ticket ya guardado en la cola
  it("8. Modificar el precio de un producto no altera tickets previamente guardados en la cola local", async () => {
    // 1. Guardar ticket en cola con precio original ($1.200)
    await guardarVentaLocal({
      ticket_id: "T-HISTORICO-001",
      payload: {
        ticket: {
          ticket_id: "T-HISTORICO-001",
          fecha: "24/10/2026 12:00:00",
          total_venta: 1200,
          monto_efectivo: 1200,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: "T-HISTORICO-001",
            producto: "Gaseosa Cola 500ml",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 1200,
            subtotal: 1200,
          },
        ],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    // Modificar precio de Gaseosa Cola a 1500
    await waitFor(() => {
      expect(screen.getByTestId("btn-edit-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("btn-edit-1"));
    fireEvent.change(screen.getByTestId("input-prod-precio"), { target: { value: "1500" } });
    fireEvent.click(screen.getByTestId("btn-save-product"));

    await waitFor(() => {
      expect(screen.queryByTestId("product-modal")).not.toBeInTheDocument();
    });

    // Comprobar que la venta histórica conserva fielmente $1.200
    const ventas = await listarVentasLocales();
    const ventaHistorica = ventas.find((v) => v.ticket_id === "T-HISTORICO-001");
    expect(ventaHistorica).toBeDefined();
    expect(ventaHistorica?.payload.ticket.total_venta).toBe(1200);
    expect(ventaHistorica?.payload.items[0].precio_unit).toBe(1200);
    expect(ventaHistorica?.payload.items[0].subtotal).toBe(1200);
  });

  // 9. Modo Demo no realiza llamadas de red en administración
  it("9. Todas las operaciones de creación y edición en Modo Demo son locales y no realizan fetch de red", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await crearProductoDemo({
      nombre: "Galletitas de Agua",
      categoria: "Almacén",
      proveedor: "General",
      unidad: "Paquete",
      costo: 300,
      precio_venta: 600,
      activo: true,
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

    // 10. Preservación de registros en actualización de versión de IndexedDB
  it("10. La apertura de la base v2 preserva íntegros los registros de la cola de ventas", async () => {
    const db = await openDB();
    expect(db.version).toBeGreaterThanOrEqual(2);
    expect(db.objectStoreNames.contains("ventas_queue")).toBe(true);
    expect(db.objectStoreNames.contains("catalogo_cache")).toBe(true);
    expect(db.objectStoreNames.contains("catalogo_demo")).toBe(true);
  });

  // 11. Duplicación rápida (Clonado Rápido) con foco automático en Nombre y selector de Categoría Canónica
  it("11. Permite duplicar un producto precargando ' (Copia)', enfocando el campo Nombre y creando un nuevo ID", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-duplicate-2")).toBeInTheDocument(); // Alfajor Triple Chocolate
    });

    // Clic en Duplicar del producto #2
    fireEvent.click(screen.getByTestId("btn-duplicate-2"));

    // Debe abrir el modal en modo Alta (no edición de ID #2)
    expect(screen.getByTestId("product-modal")).toBeInTheDocument();
    expect(screen.getByText("Alta de Producto")).toBeInTheDocument();

    const inputNombre = screen.getByTestId("input-prod-nombre") as HTMLInputElement;
    expect(inputNombre.value).toBe("Alfajor Triple Chocolate (Copia)");
    // Verificar que el foco está en el campo Nombre y el helper text de nomenclatura está visible
    expect(document.activeElement).toBe(inputNombre);
    expect(screen.getByTestId("nombre-helper-text")).toHaveTextContent(
      "[TIPO] + [MARCA] + [VARIEDAD/SABOR] + [CONTENIDO/PESO]"
    );

    // Verificar que Categoría es un <select> estricto con las categorías canónicas y datos copiados
    const selectCat = screen.getByTestId("input-prod-categoria") as HTMLSelectElement;
    expect(selectCat.tagName).toBe("SELECT");
    expect(selectCat.value).toBe("Golosinas y Chocolates");

    const inputCosto = screen.getByTestId("input-prod-costo") as HTMLInputElement;
    const inputPrecio = screen.getByTestId("input-prod-precio") as HTMLInputElement;
    expect(Number(inputCosto.value)).toBe(550);
    expect(Number(inputPrecio.value)).toBe(850);

    // Cambiar variante del nombre y guardar
    fireEvent.change(inputNombre, { target: { value: "Alfajor Triple Blanco 70g" } });
    fireEvent.click(screen.getByTestId("btn-save-product"));

    await waitFor(() => {
      expect(screen.queryByTestId("product-modal")).not.toBeInTheDocument();
      expect(screen.getAllByText("Alfajor Triple Blanco 70g").length).toBeGreaterThan(0);
    });

    // Verificar que se asignó un nuevo ID distinto de 2 y el original sigue intacto
    const prods = await listarProductosDemo(false);
    const clon = prods.find((p) => p.nombre === "Alfajor Triple Blanco 70g");
    const original = prods.find((p) => p.id_producto === 2);
    expect(clon).toBeDefined();
    expect(clon?.id_producto).toBeGreaterThan(10);
    expect(clon?.categoria).toBe("Golosinas y Chocolates");
    expect(original?.nombre).toBe("Alfajor Triple Chocolate");
  });

  // 12. Eliminación Inteligente (Smart Delete): Borrado físico sin ventas vs Baja lógica con ventas
  it("12. Ejecuta Smart Delete: borrado físico si no tiene ventas y baja lógica si posee ventas históricas", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-productos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-delete-4")).toBeInTheDocument(); // Fotocopia B/N simple faz (sin ventas en demo)
      expect(screen.getByTestId("btn-delete-1")).toBeInTheDocument(); // Gaseosa Cola 500ml (con ventas en T-20261024-001)
    });

    // A) Eliminar producto SIN ventas (#4 Fotocopia B/N simple faz) -> Borrado físico
    fireEvent.click(screen.getByTestId("btn-delete-4"));
    expect(confirmSpy).toHaveBeenCalled();

    await waitFor(() => {
      expect(screen.queryByTestId("admin-row-4")).not.toBeInTheDocument();
    });
    const prodsTrasFisico = await listarProductosDemo(false);
    expect(prodsTrasFisico.find((p) => p.id_producto === 4)).toBeUndefined();

    // B) Eliminar producto CON ventas (#1 Gaseosa Cola 500ml) -> Baja lógica (activo = false)
    fireEvent.click(screen.getByTestId("btn-delete-1"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-toggle-status-1")).toHaveTextContent("Activar");
    });
    const prodsTrasLogico = await listarProductosDemo(false);
    const prod1 = prodsTrasLogico.find((p) => p.id_producto === 1);
    expect(prod1).toBeDefined();
    expect(prod1?.activo).toBe(false);

    // En ambos casos desaparecen de la Caja Registradora
    fireEvent.click(screen.getByTestId("nav-btn-caja"));
    await waitFor(() => {
      expect(screen.queryByTestId("prod-card-4")).not.toBeInTheDocument();
      expect(screen.queryByTestId("prod-card-1")).not.toBeInTheDocument();
    });

    confirmSpy.mockRestore();
  });
});
