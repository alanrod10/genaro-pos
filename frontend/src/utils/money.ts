/**
 * Biblioteca Monetaria de Precisión — GENARO TESORERÍA LABORATORIO.
 *
 * Implementación de LAB-002 para Frontend (TypeScript):
 * - parseMoney
 * - addMoney
 * - subtractMoney
 * - compareMoney
 * - allocateMoney
 * - formatMoney
 * - assertValidMoney
 *
 * REGLAS INNEGOCIABLES:
 * - Toda persistencia y cálculo se realiza exclusivamente en CENTAVOS ENTEROS (INTEGER).
 * - Prohibidas las operaciones basadas en redondeos implícitos de coma flotante.
 */

export const MAX_SAFE_MONEY = 9_007_199_254_740_991;
export const MIN_SAFE_MONEY = -9_007_199_254_740_991;

export class ErrorMonetario extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ErrorMonetario";
  }
}

/**
 * Valida que un monto sea un entero seguro (centavos exactos).
 */
export function assertValidMoney(value: unknown): asserts value is number {
  if (typeof value !== "number" || typeof value === "boolean") {
    throw new ErrorMonetario(`Monto monetario inválido: se esperaba número entero, recibido ${typeof value}`);
  }

  if (!Number.isSafeInteger(value)) {
    throw new ErrorMonetario(`Desbordamiento o imprecisión: ${value} no es un entero seguro.`);
  }

  if (value < MIN_SAFE_MONEY || value > MAX_SAFE_MONEY) {
    throw new ErrorMonetario(`Monto ${value} fuera de los límites permitidos.`);
  }
}

/**
 * Convierte texto, número o dict a centavos enteros sin usar punto flotante.
 */
export function parseMoney(value: unknown): number {
  if (value === null || value === undefined) {
    throw new ErrorMonetario("No se puede parsear un valor nulo o indefinido.");
  }

  if (typeof value === "object" && value !== null && "centavos" in value) {
    const c = (value as { centavos: unknown }).centavos;
    assertValidMoney(c);
    return c;
  }

  if (typeof value === "boolean") {
    throw new ErrorMonetario("Un booleano no es un monto monetario.");
  }

  let strVal: string;
  if (typeof value === "number") {
    if (isNaN(value) || !isFinite(value)) {
      throw new ErrorMonetario("El monto monetario no puede ser NaN ni infinito.");
    }
    strVal = value.toString();
  } else if (typeof value === "string") {
    strVal = value.trim();
  } else {
    throw new ErrorMonetario(`Tipo no soportado: ${typeof value}`);
  }

  if (!strVal) {
    throw new ErrorMonetario("Cadena monetaria vacía.");
  }

  const esNegativo = strVal.includes("-");
  let texto = strVal.replace(/[^\d,.-]/g, "");
  if (!texto) {
    throw new ErrorMonetario(`Cadena sin caracteres numéricos válidos: ${strVal}`);
  }

  // Normalizar separadores decimales:
  if (texto.includes(",") && texto.includes(".")) {
    const idxComa = texto.lastIndexOf(",");
    const idxPunto = texto.lastIndexOf(".");
    if (idxComa > idxPunto) {
      // Formato es-AR: 1.234,56
      texto = texto.replace(/\./g, "").replace(",", ".");
    } else {
      // Formato en-US: 1,234.56
      texto = texto.replace(/,/g, "");
    }
  } else if (texto.includes(",")) {
    texto = texto.replace(",", ".");
  }

  // Desglosar parte entera y decimal para conversión exacta sin Math.round(parseFloat * 100)
  const partes = texto.split(".");
  if (partes.length > 2) {
    throw new ErrorMonetario(`Formato numérico monetario inválido: ${strVal}`);
  }

  const parteEnteraStr = partes[0].replace(/-/g, "") || "0";
  let parteDecimalStr = partes.length > 1 ? partes[1] : "0";

  // Truncar o redondear a 2 dígitos de centavos
  if (parteDecimalStr.length === 1) {
    parteDecimalStr += "0";
  } else if (parteDecimalStr.length > 2) {
    // Redondeo determinista del tercer dígito
    const centavosBase = parseInt(parteDecimalStr.slice(0, 2), 10);
    const digitoRedondeo = parseInt(parteDecimalStr[2], 10);
    parteDecimalStr = (digitoRedondeo >= 5 ? centavosBase + 1 : centavosBase).toString().padStart(2, "0");
  }

  const entera = parseInt(parteEnteraStr, 10);
  const decimal = parseInt(parteDecimalStr, 10);

  if (isNaN(entera) || isNaN(decimal)) {
    throw new ErrorMonetario(`Error al parsear componentes de: ${strVal}`);
  }

  let totalCentavos = entera * 100 + decimal;
  if (esNegativo) {
    totalCentavos = -totalCentavos;
  }

  assertValidMoney(totalCentavos);
  return totalCentavos;
}

/**
 * Suma exacta de dos montos en centavos enteros.
 */
export function addMoney(a: number, b: number): number {
  assertValidMoney(a);
  assertValidMoney(b);
  const res = a + b;
  assertValidMoney(res);
  return res;
}

/**
 * Resta exacta de dos montos en centavos enteros.
 */
export function subtractMoney(a: number, b: number): number {
  assertValidMoney(a);
  assertValidMoney(b);
  const res = a - b;
  assertValidMoney(res);
  return res;
}

/**
 * Comparación determinista entre dos montos en centavos enteros.
 */
export function compareMoney(a: number, b: number): -1 | 0 | 1 {
  assertValidMoney(a);
  assertValidMoney(b);
  if (a > b) return 1;
  if (a < b) return -1;
  return 0;
}

/**
 * Distribución equitativa y exacta sin pérdida de centavos (Hamilton-Hare).
 */
export function allocateMoney(totalCentavos: number, ratios: number[]): number[] {
  assertValidMoney(totalCentavos);
  if (!ratios || ratios.length === 0) {
    throw new ErrorMonetario("La lista de ratios no puede estar vacía.");
  }

  let sumaRatios = 0;
  for (const r of ratios) {
    if (typeof r !== "number" || isNaN(r) || r < 0) {
      throw new ErrorMonetario(`Ratio inválido: ${r}`);
    }
    sumaRatios += r;
  }

  if (sumaRatios <= 0) {
    throw new ErrorMonetario("La suma de los ratios debe ser mayor a cero.");
  }

  const esNegativo = totalCentavos < 0;
  const totalAbs = Math.abs(totalCentavos);

  const asignaciones: number[] = [];
  const restos: { resto: number; index: number }[] = [];
  let sumaAsignada = 0;

  for (let i = 0; i < ratios.length; i++) {
    const cuotaExacta = (totalAbs * ratios[i]) / sumaRatios;
    const parteEntera = Math.floor(cuotaExacta);
    const fraccion = cuotaExacta - parteEntera;

    asignaciones.push(parteEntera);
    restos.push({ resto: fraccion, index: i });
    sumaAsignada += parteEntera;
  }

  const centavosSobrantes = totalAbs - sumaAsignada;

  // Ordenar de mayor resto a menor resto; desempate por índice
  restos.sort((a, b) => {
    if (b.resto !== a.resto) {
      return b.resto - a.resto;
    }
    return a.index - b.index;
  });

  for (let k = 0; k < centavosSobrantes; k++) {
    const idx = restos[k].index;
    asignaciones[idx] += 1;
  }

  if (esNegativo) {
    for (let i = 0; i < asignaciones.length; i++) {
      asignaciones[i] = -asignaciones[i];
    }
  }

  // Verificación de invariante estricto
  const sumaTotal = asignaciones.reduce((acc, curr) => acc + curr, 0);
  if (sumaTotal !== totalCentavos) {
    throw new ErrorMonetario(`Invariante de asignación violado: suma=${sumaTotal} != ${totalCentavos}`);
  }

  for (const a of asignaciones) {
    assertValidMoney(a);
  }

  return asignaciones;
}

/**
 * Formato contable estándar en ARS sin usar punto flotante.
 */
export function formatMoney(centavos: number, includeSymbol = true, currency = "ARS"): string {
  assertValidMoney(centavos);

  const esNegativo = centavos < 0;
  const cAbs = Math.abs(centavos);

  const parteEntera = Math.floor(cAbs / 100);
  const parteDecimal = cAbs % 100;

  // Formatear separador de miles '.'
  const strEntera = parteEntera.toLocaleString("es-AR");
  const strDecimal = parteDecimal.toString().padStart(2, "0");

  const signo = esNegativo ? "-" : "";
  const monto = `${signo}${strEntera},${strDecimal}`;

  if (includeSymbol) {
    if (currency === "ARS") {
      return `${signo}$${strEntera},${strDecimal}`;
    }
    return `${monto} ${currency}`;
  }

  return monto;
}
