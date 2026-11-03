import React, { useState, useId } from "react";
import { Producto } from "../types/catalog";
import { ItemCarrito } from "../types/sales";
import {
  getReferencePricePerKg,
  calcularVentaPorPeso,
  calcularVentaPorImporte,
} from "../utils/weightSales";

interface WeightSaleModalProps {
  producto: Producto;
  initialItem?: ItemCarrito;
  onConfirm: (item: ItemCarrito) => void;
  onClose: () => void;
}

export const WeightSaleModal: React.FC<WeightSaleModalProps> = ({
  producto,
  initialItem,
  onConfirm,
  onClose,
}) => {
  const pesoInputId = useId();
  const importeInputId = useId();
  const precioAjustadoInputId = useId();
  const refData = getReferencePricePerKg(producto);
  const [precioPorKg, setPrecioPorKg] = useState<number>(refData.precioPorKg);
  const [ajustarPrecioManual, setAjustarPrecioManual] = useState<boolean>(false);
  const [precioAjustadoInput, setPrecioAjustadoInput] = useState<string>(
    refData.precioPorKg.toString()
  );

  // Modo: "peso" o "importe"
  const [modalidad, setModalidad] = useState<"peso" | "importe">(() => {
    if (initialItem && initialItem.cantidad && initialItem.cantidad < 1) {
      return "peso";
    }
    return "importe"; // Importe es la modalidad más frecuente en mostrador ($1000 de mignon, etc.)
  });

  // Modalidad A: Por Peso
  const [unidadPeso, setUnidadPeso] = useState<"g" | "kg">("g");
  const [pesoInput, setPesoInput] = useState<string>(() => {
    if (initialItem) {
      return Math.round(initialItem.cantidad * 1000).toString();
    }
    return "250"; // Sugerencia inicial estándar (250 gramos)
  });

  // Modalidad B: Por Importe
  const [importeInput, setImporteInput] = useState<string>(() => {
    if (initialItem) {
      return initialItem.subtotal.toString();
    }
    return "1000"; // Sugerencia inicial estándar ($1.000)
  });

  const [errorMsg, setErrorMsg] = useState<string | null>(refData.error || null);

  // Manejo de cambio en ajuste de precio puntual
  const handlePrecioAjustadoChange = (valStr: string) => {
    setPrecioAjustadoInput(valStr);
    const parsed = parseFloat(valStr);
    if (!isNaN(parsed) && parsed > 0) {
      setPrecioPorKg(parsed);
      setErrorMsg(null);
    } else {
      setErrorMsg("El precio de referencia ajustado debe ser un número mayor a cero.");
    }
  };

  // Cálculo en tiempo real según modalidad
  let calculoSubtotal = 0;
  let calculoPesoKg = 0;
  let displayPesoCalculado = "";
  let calculoValido = false;

  if (precioPorKg > 0) {
    if (modalidad === "peso") {
      const pesoNum = parseFloat(pesoInput);
      if (!isNaN(pesoNum) && pesoNum > 0) {
        const res = calcularVentaPorPeso(pesoNum, unidadPeso, precioPorKg);
        if (res.data) {
          calculoSubtotal = res.data.subtotal;
          calculoPesoKg = res.data.pesoKg;
          displayPesoCalculado = res.data.formatoPesoDisplay;
          calculoValido = true;
        }
      }
    } else {
      const impNum = parseFloat(importeInput);
      if (!isNaN(impNum) && impNum > 0) {
        const res = calcularVentaPorImporte(impNum, precioPorKg);
        if (res.data) {
          calculoSubtotal = res.data.subtotal;
          calculoPesoKg = res.data.pesoKg;
          displayPesoCalculado = res.data.formatoPesoDisplay;
          calculoValido = true;
        }
      }
    }
  }

  const handleConfirm = () => {
    if (!calculoValido || calculoSubtotal <= 0 || calculoPesoKg <= 0) {
      setErrorMsg("Verificá los valores ingresados antes de agregar al ticket.");
      return;
    }

    const item: ItemCarrito = {
      producto_id: producto.id_producto,
      nombre: producto.nombre,
      unidad: producto.unidad || "Gramos",
      precio: precioPorKg,
      cantidad: parseFloat(calculoPesoKg.toFixed(6)),
      subtotal: calculoSubtotal,
      costo: producto.costo !== undefined ? producto.costo : undefined,
    };

    onConfirm(item);
  };

  return (
    <div className="modal-backdrop" data-testid="weight-sale-modal">
      <div className="modal-card weight-modal-card">
        {/* Cabecera del modal */}
        <div className="modal-header">
          <div className="weight-modal-title-group">
            <span className="weight-modal-badge">⚖️ Venta por Peso / Importe</span>
            <h3 className="modal-title weight-product-name">{producto.nombre}</h3>
            <span className="weight-ref-price" data-testid="ref-price-display">
              Precio de referencia: <strong>${precioPorKg.toLocaleString("es-AR")}/kg</strong>
            </span>
          </div>
          <button
            type="button"
            className="btn-modal-close"
            onClick={onClose}
            aria-label="Cerrar modal"
            data-testid="btn-close-weight-modal"
          >
            ✕
          </button>
        </div>

        {/* Selector de Modalidad: POR IMPORTE | POR PESO */}
        <div className="weight-mode-selector" role="group" aria-label="Modalidad de venta">
          <button
            type="button"
            className={`btn-weight-mode ${modalidad === "importe" ? "mode-active" : ""}`}
            onClick={() => {
              setModalidad("importe");
              setErrorMsg(null);
            }}
            data-testid="btn-mode-importe"
          >
            💵 POR IMPORTE
          </button>
          <button
            type="button"
            className={`btn-weight-mode ${modalidad === "peso" ? "mode-active" : ""}`}
            onClick={() => {
              setModalidad("peso");
              setErrorMsg(null);
            }}
            data-testid="btn-mode-peso"
          >
            ⚖️ POR PESO
          </button>
        </div>

        {/* Cuerpo del formulario según la modalidad */}
        <div className="weight-modal-body">
          {modalidad === "importe" ? (
            <div className="weight-input-section" data-testid="section-mode-importe">
              <label htmlFor={importeInputId} className="weight-input-label">
                Importe solicitado a cobrar ($):
              </label>
              <div className="input-money-wrapper">
                <span className="money-prefix">$</span>
                <input
                  id={importeInputId}
                  type="number"
                  inputMode="numeric"
                  min="1"
                  step="1"
                  className="weight-number-input"
                  placeholder="Ej: 1000"
                  value={importeInput}
                  onChange={(e) => {
                    setImporteInput(e.target.value);
                    setErrorMsg(null);
                  }}
                  autoFocus
                  data-testid="input-requested-amount"
                />
              </div>

              {/* Botones de acceso rápido para importes comunes */}
              <div className="weight-quick-chips">
                {[500, 1000, 1500, 2000, 2500, 3000].map((imp) => (
                  <button
                    key={imp}
                    type="button"
                    className={`btn-quick-chip ${importeInput === imp.toString() ? "chip-selected" : ""}`}
                    onClick={() => {
                      setImporteInput(imp.toString());
                      setErrorMsg(null);
                    }}
                  >
                    ${imp.toLocaleString("es-AR")}
                  </button>
                ))}
              </div>

              {/* Peso equivalente calculado */}
              <div className="weight-calculated-box" data-testid="box-equivalent-weight">
                <span className="calc-label">Peso equivalente:</span>
                <span className="calc-value" data-testid="text-equivalent-weight">
                  {calculoValido ? displayPesoCalculado : "—"}
                </span>
              </div>
            </div>
          ) : (
            <div className="weight-input-section" data-testid="section-mode-peso">
              <div className="weight-unit-header">
                <label htmlFor={pesoInputId} className="weight-input-label">
                  Cantidad o peso solicitado:
                </label>
                <div className="unit-toggle-group">
                  <button
                    type="button"
                    className={`btn-unit-toggle ${unidadPeso === "g" ? "toggle-active" : ""}`}
                    onClick={() => {
                      if (unidadPeso === "kg") {
                        const val = parseFloat(pesoInput);
                        if (!isNaN(val)) setPesoInput(Math.round(val * 1000).toString());
                      }
                      setUnidadPeso("g");
                    }}
                    data-testid="btn-unit-g"
                  >
                    Gramos (g)
                  </button>
                  <button
                    type="button"
                    className={`btn-unit-toggle ${unidadPeso === "kg" ? "toggle-active" : ""}`}
                    onClick={() => {
                      if (unidadPeso === "g") {
                        const val = parseFloat(pesoInput);
                        if (!isNaN(val)) setPesoInput((val / 1000).toString());
                      }
                      setUnidadPeso("kg");
                    }}
                    data-testid="btn-unit-kg"
                  >
                    Kilos (kg)
                  </button>
                </div>
              </div>

              <div className="input-weight-wrapper">
                <input
                  id={pesoInputId}
                  type="number"
                  inputMode="decimal"
                  min="0.001"
                  step="any"
                  className="weight-number-input"
                  placeholder={unidadPeso === "g" ? "Ej: 250" : "Ej: 0.25"}
                  value={pesoInput}
                  onChange={(e) => {
                    setPesoInput(e.target.value);
                    setErrorMsg(null);
                  }}
                  autoFocus
                  data-testid="input-requested-weight"
                />
                <span className="weight-suffix">{unidadPeso}</span>
              </div>

              {/* Atajos de peso frecuentes */}
              <div className="weight-quick-chips">
                {unidadPeso === "g" ? (
                  <>
                    {[100, 200, 250, 500, 750, 1000].map((gr) => (
                      <button
                        key={gr}
                        type="button"
                        className={`btn-quick-chip ${pesoInput === gr.toString() ? "chip-selected" : ""}`}
                        onClick={() => {
                          setPesoInput(gr.toString());
                          setErrorMsg(null);
                        }}
                      >
                        {gr >= 1000 ? "1 kg" : `${gr}g`}
                      </button>
                    ))}
                  </>
                ) : (
                  <>
                    {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 2].map((k) => (
                      <button
                        key={k}
                        type="button"
                        className={`btn-quick-chip ${pesoInput === k.toString() ? "chip-selected" : ""}`}
                        onClick={() => {
                          setPesoInput(k.toString());
                          setErrorMsg(null);
                        }}
                      >
                        {k} kg
                      </button>
                    ))}
                  </>
                )}
              </div>

              <div className="weight-calculated-box">
                <span className="calc-label">Peso normalizado:</span>
                <span className="calc-value">{calculoValido ? displayPesoCalculado : "—"}</span>
              </div>
            </div>
          )}

          {/* Ajustar precio de esta venta puntual (Sin alterar el catálogo) */}
          <div className="exceptional-price-box">
            <button
              type="button"
              className="btn-toggle-exceptional"
              onClick={() => setAjustarPrecioManual(!ajustarPrecioManual)}
              data-testid="btn-toggle-adjust-price"
            >
              <span>{ajustarPrecioManual ? "▼" : "▶"}</span>
              <strong>Ajustar precio de esta venta</strong>
              <small className="notice-no-catalog">(No modifica el catálogo general)</small>
            </button>

            {ajustarPrecioManual && (
              <div className="exceptional-input-row" data-testid="exceptional-price-inputs">
                <label htmlFor={precioAjustadoInputId} className="exceptional-label">
                  Precio unitario por kg para este ticket:
                </label>
                <div className="input-money-wrapper">
                  <span className="money-prefix">$</span>
                  <input
                    id={precioAjustadoInputId}
                    type="number"
                    inputMode="numeric"
                    min="1"
                    className="weight-number-input"
                    value={precioAjustadoInput}
                    onChange={(e) => handlePrecioAjustadoChange(e.target.value)}
                    data-testid="input-exceptional-price"
                  />
                  <span className="weight-suffix">/ kg</span>
                </div>
                {precioPorKg !== refData.precioPorKg && (
                  <button
                    type="button"
                    className="btn-restore-ref-price"
                    onClick={() => {
                      setPrecioPorKg(refData.precioPorKg);
                      setPrecioAjustadoInput(refData.precioPorKg.toString());
                      setErrorMsg(null);
                    }}
                  >
                    Restablecer precio original (${refData.precioPorKg.toLocaleString("es-AR")})
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Mensajes de error o advertencia */}
          {errorMsg && (
            <div className="weight-modal-error" data-testid="weight-modal-error">
              ⚠️ {errorMsg}
            </div>
          )}

          {/* Total destacado de la línea */}
          <div className="weight-modal-total-row" data-testid="weight-modal-total-row">
            <span className="total-label">Total de la línea:</span>
            <span className="total-amount-highlight" data-testid="line-total-amount">
              ${calculoSubtotal.toLocaleString("es-AR")}
            </span>
          </div>
        </div>

        {/* Acciones: Confirmar y Cancelar */}
        <div className="modal-footer">
          <button
            type="button"
            className="btn-modal-cancel"
            onClick={onClose}
            data-testid="btn-cancel-weight"
          >
            Cancelar
          </button>
          <button
            type="button"
            className="btn-primary btn-confirm-weight"
            onClick={handleConfirm}
            disabled={!calculoValido || calculoSubtotal <= 0}
            data-testid="btn-confirm-weight-sale"
          >
            {initialItem ? "Actualizar Ítem" : "Agregar al Ticket"} ($
            {calculoSubtotal.toLocaleString("es-AR")})
          </button>
        </div>
      </div>
    </div>
  );
};
