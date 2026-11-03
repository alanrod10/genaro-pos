import { Producto } from "../types/catalog";

export interface CalculoVentaPeso {
  pesoKg: number;
  pesoGramos: number;
  subtotal: number;
  formatoPesoDisplay: string;
}

export interface CalculoVentaImporte {
  importe: number;
  pesoKg: number;
  pesoGramos: number;
  subtotal: number;
  formatoPesoDisplay: string;
}

/**
 * Determina con alta confiabilidad si un producto se comercializa fraccionado por peso.
 * REGLA DE AUDITORÍA: La información explícita de unidad de medida tiene prioridad absoluta
 * sobre cualquier heurística de nombre. Si un producto tiene unidad "Unidad", nunca se
 * clasifica erróneamente como pesable aunque su nombre contenga gramajes o descriptores.
 */
export function isWeighableProduct(
  producto: Pick<Producto, "nombre" | "unidad" | "categoria">
): boolean {
  const unidad = (producto.unidad || "").toLowerCase().trim();
  const nombre = (producto.nombre || "").toLowerCase().trim();

  // 1. Prioridad absoluta: Si la unidad es explícitamente "Unidad", NO es pesable
  if (unidad === "unidad" || unidad === "unidades" || unidad === "u") {
    return false;
  }

  // 2. Unidades explícitas de peso
  if (
    unidad === "gramos" ||
    unidad === "gramo" ||
    unidad === "gr" ||
    unidad === "g" ||
    unidad === "kg" ||
    unidad === "kilo" ||
    unidad === "kilos" ||
    unidad === "kilogramo" ||
    unidad === "kilogramos" ||
    unidad === "100g" ||
    unidad === "100gr" ||
    unidad === "250g" ||
    unidad === "500g"
  ) {
    return true;
  }

  // 3. Heurística segura: Solo si no hay unidad explícita (vacía o nula)
  // y el nombre indica explícitamente venta al peso / a granel
  if (!unidad) {
    if (
      nombre.includes("al peso") ||
      nombre.includes("fraccionado") ||
      nombre.includes("al granel") ||
      nombre.includes("suelto")
    ) {
      return true;
    }
  }

  return false;
}

/**
 * Obtiene el precio de referencia normalizado por kilogramo de un producto
 */
export function getReferencePricePerKg(
  producto: Pick<Producto, "nombre" | "unidad" | "precio_venta" | "precio_dia">
): { precioPorKg: number; precioPorGramo: number; unidadBase: "kg" | "100g" | "g"; error?: string } {
  const precio = producto.precio_venta ?? producto.precio_dia ?? 0;

  if (!Number.isFinite(precio) || precio <= 0) {
    return {
      precioPorKg: 0,
      precioPorGramo: 0,
      unidadBase: "kg",
      error: "El producto no tiene un precio de referencia válido para venta por peso.",
    };
  }

  const unidad = (producto.unidad || "").toLowerCase().trim();
  const nombre = (producto.nombre || "").toLowerCase().trim();

  // Caso: Unidad es por 100 gramos (o nombre indica x100gr)
  if (unidad === "100g" || unidad === "100gr" || nombre.includes("x100gr") || nombre.includes("x 100gr")) {
    const precioKg = Math.round(precio * 10);
    return {
      precioPorKg: precioKg,
      precioPorGramo: precio / 100,
      unidadBase: "100g",
    };
  }

  // Caso: Unidad es literalmente "g" o "gramo" con un precio unitario por gramo pequeño (<= 50)
  if ((unidad === "g" || unidad === "gramo" || unidad === "gr") && precio <= 50) {
    const precioKg = Math.round(precio * 1000);
    return {
      precioPorKg: precioKg,
      precioPorGramo: precio,
      unidadBase: "g",
    };
  }

  // Caso estándar (Kg, Kilogramo, o Gramos con precio minorista mayorista de referencia ya por kg como $2.500, $8.000)
  return {
    precioPorKg: Math.round(precio),
    precioPorGramo: precio / 1000,
    unidadBase: "kg",
  };
}

/**
 * Formatea el peso para presentación visual en gramos o kilogramos
 */
export function formatearPesoDisplay(pesoKg: number): string {
  if (!Number.isFinite(pesoKg) || pesoKg <= 0) return "0 g";

  const gramos = Math.round(pesoKg * 1000);
  if (gramos < 1000) {
    return `${gramos} g`;
  }

  // Mostrar en kg con coma decimal argentina y hasta 3 decimales sin ceros superfluos
  const valorKgStr = pesoKg.toLocaleString("es-AR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
  });

  return `${valorKgStr} kg`;
}

/**
 * Modalidad A: Venta por Peso
 * El usuario ingresa el peso (en gramos o en kg).
 * Calcula el importe exacto según precio por kg sin errores de coma flotante.
 */
export function calcularVentaPorPeso(
  pesoInput: number,
  unidadIngreso: "g" | "kg",
  precioPorKg: number
): { data?: CalculoVentaPeso; error?: string } {
  if (!Number.isFinite(precioPorKg) || precioPorKg <= 0) {
    return { error: "Precio de referencia por kg inválido." };
  }
  if (!Number.isFinite(pesoInput) || pesoInput <= 0) {
    return { error: "El peso debe ser un número mayor a cero." };
  }

  const pesoKg = unidadIngreso === "g" ? pesoInput / 1000 : pesoInput;
  const pesoGramos = Math.round(pesoKg * 1000);

  if (pesoGramos <= 0) {
    return { error: "El peso resultante es menor al mínimo permitido (1 gramo)." };
  }

  // Cálculo de subtotal con redondeo monetario estándar de GenaroPOS
  const subtotal = Math.round(pesoKg * precioPorKg);

  return {
    data: {
      pesoKg,
      pesoGramos,
      subtotal,
      formatoPesoDisplay: formatearPesoDisplay(pesoKg),
    },
  };
}

/**
 * Modalidad B: Venta por Importe
 * El usuario ingresa cuánto dinero cobrar.
 * peso_en_kg = importe_solicitado / precio_por_kg
 * Conserva el importe exacto solicitado como subtotal monetario de la línea.
 */
export function calcularVentaPorImporte(
  importeSolicitado: number,
  precioPorKg: number
): { data?: CalculoVentaImporte; error?: string } {
  if (!Number.isFinite(precioPorKg) || precioPorKg <= 0) {
    return { error: "Precio de referencia por kg inválido." };
  }
  if (!Number.isFinite(importeSolicitado) || importeSolicitado <= 0) {
    return { error: "El importe debe ser un número mayor a cero." };
  }

  // Redondear el importe monetario solicitado a número entero
  const importeRedondeado = Math.round(importeSolicitado);
  if (importeRedondeado <= 0) {
    return { error: "El importe a cobrar debe ser de al menos $1." };
  }

  const pesoKg = importeRedondeado / precioPorKg;
  const pesoGramos = Math.round(pesoKg * 1000);

  return {
    data: {
      importe: importeRedondeado,
      pesoKg,
      pesoGramos,
      subtotal: importeRedondeado,
      formatoPesoDisplay: formatearPesoDisplay(pesoKg),
    },
  };
}
