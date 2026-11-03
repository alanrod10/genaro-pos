import React from "react";
import { Producto } from "../types/catalog";

/**
 * Normaliza un texto eliminando tildes y diacríticos y convirtiendo a minúsculas
 */
export function normalizarTexto(texto: string): string {
  if (!texto) return "";
  return texto
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

/**
 * Calcula la prioridad de búsqueda para un producto según las reglas de GenaroPOS:
 * 1. El nombre del producto comienza con el texto buscado (Prioridad 1)
 * 2. Alguna palabra del nombre comienza con el texto buscado (Prioridad 2)
 * 3. La coincidencia aparece en cualquier otra parte del nombre (Prioridad 3)
 * 4. La coincidencia aparece en código de barras, ID o proveedor (Prioridad 4)
 * Si no coincide con ninguno: 99
 */
export function getProductSearchPriority(
  producto: { nombre: string; id_producto?: number; proveedor?: string; codigo_barras?: string },
  query: string
): number {
  const qNorm = normalizarTexto(query);
  if (!qNorm) return 0;

  const nombreNorm = normalizarTexto(producto.nombre || "");

  // 1. Nombre comienza con el texto
  if (nombreNorm.startsWith(qNorm)) return 1;

  // 2. Alguna palabra del nombre comienza con el texto
  if (nombreNorm.includes(" " + qNorm)) return 2;

  // 3. Coincidencia en cualquier otra parte del nombre
  if (nombreNorm.includes(qNorm)) return 3;

  // 4. Coincidencias que no están en el nombre (código, ID, proveedor u otro)
  return 4;
}

/**
 * Verifica si un producto coincide con la búsqueda
 */
export function coincideBusquedaProducto(
  producto: { nombre: string; id_producto?: number; proveedor?: string; codigo_barras?: string },
  query: string
): boolean {
  const qNorm = normalizarTexto(query);
  if (!qNorm) return true;

  const nombreNorm = normalizarTexto(producto.nombre || "");
  const idStr = producto.id_producto !== undefined ? producto.id_producto.toString() : "";
  const codNorm = normalizarTexto(producto.codigo_barras || "");
  const provNorm = normalizarTexto(producto.proveedor || "");

  return (
    nombreNorm.includes(qNorm) ||
    idStr === qNorm ||
    (codNorm !== "" && codNorm.includes(qNorm)) ||
    (provNorm !== "" && provNorm.includes(qNorm))
  );
}

/**
 * Ordena una lista de productos aplicando la prioridad de búsqueda unificada
 */
export function ordenarProductosPorBusqueda<T extends Pick<Producto, "nombre" | "id_producto" | "proveedor" | "codigo_barras">>(
  productos: T[],
  query: string
): T[] {
  const q = query.trim();
  if (!q) return productos;

  return [...productos].sort((a, b) => {
    const prioA = getProductSearchPriority(a, q);
    const prioB = getProductSearchPriority(b, q);

    if (prioA !== prioB) {
      return prioA - prioB;
    }

    return (a.nombre || "").localeCompare(b.nombre || "", "es", { sensitivity: "base" });
  });
}

/**
 * Resalta de forma segura y exclusivamente el fragmento coincidente en el nombre del producto
 */
export function renderHighlightedText(text: string, query: string): React.ReactNode {
  const trimmed = query.trim();
  if (!trimmed || !text) return text;

  // Escapar caracteres especiales para la expresión regular
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`(${escaped})`, "gi");
  const parts = text.split(regex);

  if (parts.length <= 1) return text;

  return parts.map((part, index) =>
    part.toLowerCase() === trimmed.toLowerCase() ? (
      <mark key={index} className="search-highlight">
        {part}
      </mark>
    ) : (
      part
    )
  );
}
