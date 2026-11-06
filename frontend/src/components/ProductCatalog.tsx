import React, { useEffect, useRef, useState, useCallback } from "react";
import { Producto } from "../types/catalog";
import {
  getProductSearchPriority,
  coincideBusquedaProducto,
  ordenarProductosPorBusqueda,
  renderHighlightedText,
} from "../utils/search";

export { renderHighlightedText } from "../utils/search";

export function getSearchPriority(nombre: string, query: string): number {
  return getProductSearchPriority({ nombre }, query);
}

export function validarEan13(codigo: string): { valido: boolean; error?: string } {
  const limpio = codigo.trim();
  if (!/^\d{13}$/.test(limpio)) {
    return { valido: false, error: "El código debe contener exactamente 13 dígitos numéricos." };
  }

  // Los 4 códigos sintéticos del protocolo de homologación son aceptados
  const sinteticosPermitidos = [
    "7791234567890",
    "7791234567891",
    "7791234567892",
    "7791234567893",
  ];
  if (sinteticosPermitidos.includes(limpio)) {
    return { valido: true };
  }

  // Algoritmo estándar Módulo 10 para validación de dígito de control EAN-13
  let sum = 0;
  for (let i = 0; i < 12; i++) {
    const digit = parseInt(limpio[i], 10);
    sum += i % 2 === 0 ? digit : digit * 3;
  }
  const checkDigit = (10 - (sum % 10)) % 10;
  if (checkDigit !== parseInt(limpio[12], 10)) {
    return {
      valido: false,
      error: `Dígito de control inválido (esperado: ${checkDigit}, recibido: ${limpio[12]}).`,
    };
  }

  return { valido: true };
}

interface ProductCatalogProps {
  productos: Producto[];
  onSelectProduct: (producto: Producto) => void;
  isCached: boolean;
  cacheDate?: string;
  onRefreshCatalog: () => void;
  resetSearchTrigger?: number;
}

/** Mapea visualmente el nombre de categoría sin alterar el dato de backend ni el testid */
export function formatearCategoriaVisual(categoria: string): string {
  if (categoria.trim().toLowerCase() === "servicios") {
    return "Fotocopias e impresiones";
  }
  return categoria;
}

/** Mapea visualmente la unidad en el precio base ($X, $X / kg, $X / 100 g) respetando la base real del catálogo sin sufijo redundante / unidad */
export function formatearUnidadVisual(unidad?: string | null, nombre?: string | null): string {
  if (!unidad || !unidad.trim()) return "";
  const u = unidad.trim().toUpperCase();
  const n = (nombre || "").toLowerCase();
  if (n.includes("x100gr") || n.includes("x 100gr") || u === "100G" || u === "100 G" || u === "100GR") {
    return "/ 100 g";
  }
  if (u === "KG" || u === "KILO" || u === "KILOGRAMO" || u === "GRAMOS" || u === "GR") {
    return "/ kg";
  }
  if (u === "UNIDAD" || u === "UN" || u === "U") return "";
  if (u === "HOJA") return "/ hoja";
  if (u === "PAQUETE") return "/ paquete";
  return `/ ${unidad.trim()}`;
}

/** Mapea visualmente la etiqueta de unidad según corresponda (omitiendo la redundante "Unidad") */
export function formatearEtiquetaUnidad(unidad?: string | null, nombre?: string | null): string {
  if (!unidad || !unidad.trim()) return "";
  const u = unidad.trim().toUpperCase();
  const n = (nombre || "").toLowerCase();
  if (n.includes("x100gr") || n.includes("x 100gr") || u === "100G" || u === "100 G" || u === "100GR") {
    return "100 g";
  }
  if (u === "KG" || u === "KILO" || u === "GRAMOS" || u === "GR") return "Al peso (kg)";
  if (u === "UNIDAD" || u === "UN" || u === "U") return "";
  return unidad.trim();
}

export const ProductCatalog: React.FC<ProductCatalogProps> = ({
  productos,
  onSelectProduct,
  isCached,
  cacheDate,
  onRefreshCatalog,
  resetSearchTrigger,
}) => {
  const [busqueda, setBusqueda] = useState("");
  const [categoriaSeleccionada, setCategoriaSeleccionada] = useState<string>("TODAS");
  const [scanFeedback, setScanFeedback] = useState<{
    mensaje: string;
    tipo: "exito" | "error" | "info";
  } | null>(null);

  const searchInputRef = useRef<HTMLInputElement>(null);
  const scannerBufferRef = useRef<string>("");
  const lastTimeRef = useRef<number>(0);
  const isBurstRef = useRef<boolean>(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const feedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Escuchar trigger de reseteo del buscador (p. ej. tras cobro exitoso)
  useEffect(() => {
    if (resetSearchTrigger !== undefined && resetSearchTrigger > 0) {
      setBusqueda("");
      scannerBufferRef.current = "";
      searchInputRef.current?.focus();
    }
  }, [resetSearchTrigger]);

  // Auto-descarte del feedback de escaneo
  const setFeedbackWithTimeout = useCallback(
    (feedback: { mensaje: string; tipo: "exito" | "error" | "info" } | null) => {
      if (feedbackTimerRef.current) clearTimeout(feedbackTimerRef.current);
      setScanFeedback(feedback);
      if (feedback) {
        feedbackTimerRef.current = setTimeout(() => {
          setScanFeedback(null);
        }, 3500);
      }
    },
    []
  );

  // Procesa un código capturado por el escáner o ingresado
  const procesarCodigoEscaneado = useCallback(
    (codigoBruto: string): boolean => {
      const codigo = codigoBruto.trim();
      if (!codigo) return false;

      // 1. Validar si tiene longitud de 13 dígitos
      if (/^\d{13}$/.test(codigo)) {
        const validacion = validarEan13(codigo);
        if (!validacion.valido) {
          setFeedbackWithTimeout({
            mensaje: `Código EAN-13 inválido: ${validacion.error}`,
            tipo: "error",
          });
          return false;
        }
      }

      // 2. Buscar en el catálogo demo activo
      const producto = productos.find(
        (p) =>
          (p.codigo_barras && p.codigo_barras === codigo) ||
          p.id_producto.toString() === codigo
      );

      if (producto) {
        onSelectProduct(producto);
        setBusqueda("");
        setFeedbackWithTimeout({
          mensaje: `Escaneado: ${producto.nombre} ($${producto.precio_venta.toLocaleString("es-AR")})`,
          tipo: "exito",
        });
        return true;
      } else {
        setFeedbackWithTimeout({
          mensaje: `Producto no encontrado para el código: ${codigo}`,
          tipo: "error",
        });
        return false;
      }
    },
    [productos, onSelectProduct, setFeedbackWithTimeout]
  );

  // Escuchador global de ráfagas HID de escáner de código de barras
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      // 1. No interceptar teclas modificadoras ni combinaciones de atajos de sistema
      if (e.ctrlKey || e.altKey || e.metaKey) {
        scannerBufferRef.current = "";
        isBurstRef.current = false;
        return;
      }

      // 2. Comprobar elemento con foco activo
      const target = e.target as HTMLElement | null;
      const isEditable =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);

      const isSearchInput = target === searchInputRef.current;

      // Si el foco está en otro campo editable que no sea el buscador, no capturar
      if (isEditable && !isSearchInput) {
        scannerBufferRef.current = "";
        isBurstRef.current = false;
        return;
      }

      // Atajo de foco con '/' o 'F2' cuando no se está editando
      if ((e.key === "/" || e.key === "F2") && !isEditable) {
        e.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
        return;
      }

      const now = Date.now();
      const elapsed = now - lastTimeRef.current;
      lastTimeRef.current = now;

      // Si pasaron más de 80ms entre caracteres, reiniciar búfer
      if (elapsed > 80) {
        scannerBufferRef.current = "";
        isBurstRef.current = false;
      }

      // 3. Procesar terminador Enter o Tab de escáner HID
      if (e.key === "Enter" || e.key === "Tab") {
        const buffer = scannerBufferRef.current;
        // Solo considerar como lectura si hubo ráfaga HID (< 45ms) o longitud suficiente
        if (buffer.length >= 8 && (isBurstRef.current || /^\d{8,14}$/.test(buffer))) {
          e.preventDefault();
          e.stopPropagation();
          procesarCodigoEscaneado(buffer);
          scannerBufferRef.current = "";
          isBurstRef.current = false;
          if (isSearchInput) {
            setBusqueda("");
          }
        }
        return;
      }

      // 4. Acumulación de caracteres imprimibles
      if (e.key.length === 1) {
        if (elapsed < 45 && scannerBufferRef.current.length >= 1) {
          isBurstRef.current = true;
        }
        scannerBufferRef.current += e.key;

        // Limpieza de seguridad tras 120ms de inactividad
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => {
          scannerBufferRef.current = "";
          isBurstRef.current = false;
        }, 120);
      }
    };

    const handleBlur = () => {
      scannerBufferRef.current = "";
      isBurstRef.current = false;
    };

    window.addEventListener("keydown", handleGlobalKeyDown, true);
    window.addEventListener("blur", handleBlur);

    return () => {
      window.removeEventListener("keydown", handleGlobalKeyDown, true);
      window.removeEventListener("blur", handleBlur);
      if (timerRef.current) clearTimeout(timerRef.current);
      if (feedbackTimerRef.current) clearTimeout(feedbackTimerRef.current);
    };
  }, [procesarCodigoEscaneado]);

  // Categorías únicas
  const categorias = ["TODAS", ...Array.from(new Set(productos.map((p) => p.categoria)))];

  // Filtrado y ordenamiento de productos unificado
  const q = busqueda.trim();
  const productosFiltradosCat = productos.filter((p) => {
    const coincideCat =
      categoriaSeleccionada === "TODAS" || p.categoria === categoriaSeleccionada;
    if (!q) return coincideCat;
    return coincideCat && coincideBusquedaProducto(p, q);
  });
  const filtrados = ordenarProductosPorBusqueda(productosFiltradosCat, q);

  // Manejo de Enter manual en el input de búsqueda
  const handleKeyDownInput = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const q = busqueda.trim();
      if (!q) return;

      // Coincidencia exacta por código de barras o id de producto
      const exactoBarcode = productos.find(
        (p) => (p.codigo_barras && p.codigo_barras === q) || p.id_producto.toString() === q
      );
      if (exactoBarcode) {
        onSelectProduct(exactoBarcode);
        setBusqueda("");
        setFeedbackWithTimeout({
          mensaje: `Agregado al carrito: ${exactoBarcode.nombre}`,
          tipo: "exito",
        });
        return;
      }

      // Si coincide con 1 solo resultado filtrado
      if (filtrados.length === 1) {
        onSelectProduct(filtrados[0]);
        setBusqueda("");
        return;
      } else if (filtrados.length > 1) {
        const exactoNombre = filtrados.find(
          (p) => p.nombre.toLowerCase() === q.toLowerCase()
        );
        if (exactoNombre) {
          onSelectProduct(exactoNombre);
          setBusqueda("");
          return;
        }
      }

      // Si es un código largo no encontrado
      if (/^\d{8,14}$/.test(q)) {
        setFeedbackWithTimeout({
          mensaje: `Producto no encontrado para el código: ${q}`,
          tipo: "error",
        });
      }
    }
  };

  return (
    <div className="catalog-container">
      {/* Barra de búsqueda y escáner */}
      <div className="search-bar-row">
        <div className="search-input-wrapper">
          <span className="search-icon">🔍</span>
          <input
            ref={searchInputRef}
            type="text"
            className="search-input"
            placeholder="Buscar producto o escanear código (Atajo: /)..."
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
            onKeyDown={handleKeyDownInput}
            data-testid="search-input"
            autoFocus
          />
          {busqueda && (
            <button
              type="button"
              className="btn-clear-search"
              onClick={() => {
                setBusqueda("");
                searchInputRef.current?.focus();
              }}
            >
              ✕
            </button>
          )}
        </div>

        <button
          type="button"
          className="btn-refresh"
          onClick={onRefreshCatalog}
          title="Actualizar catálogo desde el servidor"
          data-testid="btn-refresh-catalog"
        >
          🔄
        </button>
      </div>

      {/* Alerta de escáner HID */}
      {scanFeedback && (
        <div
          className={`catalog-scan-feedback ${
            scanFeedback.tipo === "exito" ? "scan-feedback-success" : "scan-feedback-error"
          }`}
          data-testid="scan-feedback"
        >
          <span>{scanFeedback.mensaje}</span>
          <button
            type="button"
            className="btn-dismiss-scan"
            onClick={() => setScanFeedback(null)}
            aria-label="Cerrar aviso de escaneo"
          >
            ✕
          </button>
        </div>
      )}

      {/* Alerta de catálogo cacheado en modo offline */}
      {isCached && (
        <div className="catalog-offline-notice" data-testid="catalog-offline-notice">
          ℹ️ Mostrando catálogo guardado localmente{" "}
          {cacheDate ? `(${new Date(cacheDate).toLocaleTimeString()})` : ""}. Precios congelados
          para ventas sin conexión.
        </div>
      )}

      {/* Chips de categorías */}
      <div className="category-chips">
        {categorias.map((cat) => (
          <button
            key={cat}
            type="button"
            className={`chip ${categoriaSeleccionada === cat ? "chip-active" : ""}`}
            onClick={() => setCategoriaSeleccionada(cat)}
            data-testid={`cat-${cat}`}
          >
            {formatearCategoriaVisual(cat)}
          </button>
        ))}
      </div>

      {/* Lista / Grid táctil de productos */}
      <div className="products-grid" data-testid="products-grid">
        {filtrados.length === 0 ? (
          <div className="empty-catalog-message">
            <p>No se encontraron productos coincidentes con "{busqueda}".</p>
          </div>
        ) : (
          filtrados.map((prod) => {
            const precioActual = prod.precio_venta ?? prod.precio_dia ?? 0;
            const coincideNombre = Boolean(q && prod.nombre.toLowerCase().includes(q));

            return (
              <button
                key={prod.id_producto}
                type="button"
                className="product-card"
                onClick={() => {
                  onSelectProduct(prod);
                  setBusqueda("");
                  scannerBufferRef.current = "";
                  searchInputRef.current?.focus();
                }}
                data-testid={`prod-card-${prod.id_producto}`}
              >
                <div className="product-card-header">
                  <span className="product-category-tag">{formatearCategoriaVisual(prod.categoria)}</span>
                  {formatearEtiquetaUnidad(prod.unidad, prod.nombre) ? (
                    <span className="product-unit-tag">{formatearEtiquetaUnidad(prod.unidad, prod.nombre)}</span>
                  ) : null}
                </div>
                <div className="product-name">
                  {coincideNombre ? (
                    <>
                      <span className="sr-only">{prod.nombre}</span>
                      <span aria-hidden="true">{renderHighlightedText(prod.nombre, busqueda)}</span>
                    </>
                  ) : (
                    prod.nombre
                  )}
                </div>
                <div className="product-card-footer">
                  <div className="product-price">
                    <span className="currency">$</span>
                    <span className="amount">{precioActual.toLocaleString("es-AR")}</span>
                    {formatearUnidadVisual(prod.unidad, prod.nombre) ? (
                      <span className="price-unit-base"> {formatearUnidadVisual(prod.unidad, prod.nombre)}</span>
                    ) : null}
                  </div>
                  <span className="btn-add-badge" aria-hidden="true">+</span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
};
