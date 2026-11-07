import React, { useEffect, useRef, useState } from "react";
import { ItemCarrito, MetodoPago, VentaPayload } from "../types/sales";
import { IS_STATIC_DEMO_ONLY } from "../services/api";

export interface FastCashOption {
  id: string;
  label: string;
  additional: number;
}

export const FAST_CASH_OPTIONS: readonly FastCashOption[] = [
  { id: "exacto", label: "Exacto", additional: 0 },
] as const;

interface PaymentModalProps {
  items: ItemCarrito[];
  onClose: () => void;
  onConfirmSale: (payload: VentaPayload) => Promise<void>;
}

export const PaymentModal: React.FC<PaymentModalProps> = ({
  items,
  onClose,
  onConfirmSale,
}) => {
  const totalVenta = items.reduce((acc, it) => acc + it.subtotal, 0);

  const [metodoPago, setMetodoPago] = useState<MetodoPago>("EFECTIVO");
  const [efectivoRecibido, setEfectivoRecibido] = useState<number | "">(totalVenta);
  const [isExactMode, setIsExactMode] = useState<boolean>(true);
  const [montoMixtoEfvo, setMontoMixtoEfvo] = useState<number>(Math.floor(totalVenta / 2));
  const [montoMixtoTransf, setMontoMixtoTransf] = useState<number>(
    totalVenta - Math.floor(totalVenta / 2)
  );
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const submittingRef = useRef<boolean>(false);

  // Sincronizar importes cuando el total del carrito cambia
  const prevTotalRef = useRef<number>(totalVenta);
  useEffect(() => {
    if (prevTotalRef.current !== totalVenta) {
      prevTotalRef.current = totalVenta;
      if (isExactMode) {
        setEfectivoRecibido(totalVenta);
      }
      setMontoMixtoEfvo(Math.floor(totalVenta / 2));
      setMontoMixtoTransf(totalVenta - Math.floor(totalVenta / 2));
    }
  }, [totalVenta, isExactMode]);

  // Cerrar modal con tecla Escape si no se está enviando
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submittingRef.current && !isSubmitting) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, isSubmitting]);

  // Validación y cálculo de efectivo recibido
  const hasCashInput = efectivoRecibido !== "";
  const numEfectivo =
    typeof efectivoRecibido === "number" && Number.isFinite(efectivoRecibido)
      ? efectivoRecibido
      : null;

  const isCashNegative = numEfectivo !== null && numEfectivo < 0;
  const isCashShortage =
    numEfectivo !== null && !isCashNegative && numEfectivo < totalVenta;
  const faltaEfectivo = isCashShortage ? totalVenta - (numEfectivo ?? 0) : 0;
  const isExacto = numEfectivo === totalVenta;

  let vueltoDisplay = "—";
  let cashErrorMessage: string | null = null;

  if (metodoPago === "EFECTIVO") {
    if (!hasCashInput || numEfectivo === null) {
      vueltoDisplay = "—";
      cashErrorMessage = "⚠️ Ingresá el monto de efectivo recibido.";
    } else if (isCashNegative) {
      vueltoDisplay = "—";
      cashErrorMessage = "⚠️ El importe recibido no puede ser negativo.";
    } else if (isCashShortage) {
      vueltoDisplay = "$0";
      cashErrorMessage = `⚠️ El efectivo recibido es menor que el total (faltan $${faltaEfectivo.toLocaleString("es-AR")}).`;
    } else {
      const vuelto = numEfectivo - totalVenta;
      vueltoDisplay = `$${vuelto.toLocaleString("es-AR")}`;
      cashErrorMessage = null;
    }
  }

  const isCashValid =
    numEfectivo !== null && !isCashNegative && !isCashShortage;

  const balanceMixto =
    Number.isFinite(montoMixtoEfvo) &&
    Number.isFinite(montoMixtoTransf) &&
    montoMixtoEfvo >= 0 &&
    montoMixtoTransf >= 0 &&
    Math.round(montoMixtoEfvo) + Math.round(montoMixtoTransf) === Math.round(totalVenta);

  const isConfirmDisabled =
    isSubmitting ||
    (metodoPago === "EFECTIVO" && !isCashValid) ||
    (metodoPago === "MIXTO" && !balanceMixto);

  const handleCashInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setIsExactMode(false);
    const rawVal = e.target.value;
    if (rawVal === "") {
      setEfectivoRecibido("");
      return;
    }
    const val = parseFloat(rawVal);
    if (!Number.isFinite(val)) {
      setEfectivoRecibido("");
      return;
    }
    setEfectivoRecibido(val);
  };

  const handleConfirmar = async () => {
    if (submittingRef.current || isSubmitting) return;

    if (metodoPago === "EFECTIVO" && !isCashValid) {
      return;
    }

    if (metodoPago === "MIXTO" && !balanceMixto) {
      return;
    }

    submittingRef.current = true;
    setIsSubmitting(true);

    try {
      // 1. Generación de ticket_id estable antes de persistir y enviar
      const timestamp = Date.now();
      const sufijo = Math.random().toString(36).substring(2, 10);
      const ticketId = `T-${timestamp}-${sufijo}`;

      // 2. Fecha en formato DD/MM/YYYY HH:MM:SS
      const ahora = new Date();
      const pad = (n: number) => n.toString().padStart(2, "0");
      const fechaStr = `${pad(ahora.getDate())}/${pad(ahora.getMonth() + 1)}/${ahora.getFullYear()} ${pad(ahora.getHours())}:${pad(ahora.getMinutes())}:${pad(ahora.getSeconds())}`;

      let montoEfectivo = 0;
      let montoTransferencia = 0;

      if (metodoPago === "EFECTIVO") {
        montoEfectivo = totalVenta;
        montoTransferencia = 0;
      } else if (metodoPago === "TRANSFERENCIA") {
        montoEfectivo = 0;
        montoTransferencia = totalVenta;
      } else {
        // MIXTO
        montoEfectivo = montoMixtoEfvo;
        montoTransferencia = montoMixtoTransf;
      }

      const payload: VentaPayload = {
        ticket: {
          ticket_id: ticketId,
          fecha: fechaStr,
          total_venta: Math.round(totalVenta),
          monto_efectivo: Math.round(montoEfectivo),
          monto_transf: Math.round(montoTransferencia),
          metodo_pago: metodoPago,
          es_nocturno: false,
        },
        items: items.map((it) => ({
          ticket_id: ticketId,
          producto: it.nombre.trim(),
          cantidad: parseFloat(it.cantidad.toFixed(6)),
          unidad: it.unidad.trim() || "Unidad",
          precio_unit: Math.round(it.precio),
          subtotal: Math.round(it.subtotal),
          metodo_pago: metodoPago,
          costo_unitario: it.costo !== undefined ? it.costo : null,
        })),
      };

      await onConfirmSale(payload);
      onClose();
    } catch (error) {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" data-testid="payment-modal">
      <div className="modal-card">
        <div className="modal-header">
          <h3 className="modal-title">Finalizar Venta</h3>
          <button
            type="button"
            className="btn-modal-close"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Cerrar modal de pago"
            data-testid="btn-close-payment"
          >
            ✕
          </button>
        </div>

        <div className="modal-body">
          {IS_STATIC_DEMO_ONLY && (
            <div className="demo-modal-notice" data-testid="demo-modal-notice">
              🧪 <strong>SIMULACIÓN:</strong> Cobro ficticio de prueba. No genera transacciones ni escrituras reales.
            </div>
          )}

          {/* Monto Total */}
          <div className="checkout-total-banner">
            <span className="checkout-label">Total a Cobrar</span>
            <span className="checkout-value" data-testid="payment-total-banner">
              ${totalVenta.toLocaleString("es-AR")}
            </span>
          </div>

          {/* Selector de Métodos de Pago */}
          <div className="payment-tabs">
            <button
              type="button"
              className={`payment-tab ${metodoPago === "EFECTIVO" ? "tab-active" : ""}`}
              onClick={() => setMetodoPago("EFECTIVO")}
              data-testid="tab-efectivo"
            >
              💵 Efectivo
            </button>
            <button
              type="button"
              className={`payment-tab ${metodoPago === "TRANSFERENCIA" ? "tab-active" : ""}`}
              onClick={() => setMetodoPago("TRANSFERENCIA")}
              data-testid="tab-transferencia"
            >
              📱 Transferencia
            </button>
            <button
              type="button"
              className={`payment-tab ${metodoPago === "MIXTO" ? "tab-active" : ""}`}
              onClick={() => setMetodoPago("MIXTO")}
              data-testid="tab-mixto"
            >
              🔄 Mixto
            </button>
          </div>

          {/* Contenido según medio de pago */}
          {metodoPago === "EFECTIVO" && (
            <div className="payment-detail-box">
              <div className="cash-tender-controls">
                <div className="cash-input-header">
                  <label htmlFor="input-cash-tender" className="input-label">
                    Efectivo recibido:
                  </label>
                  <button
                    type="button"
                    className={`btn-fast-cash btn-exacto ${isExacto ? "selected" : ""}`}
                    onClick={() => {
                      setIsExactMode(true);
                      setEfectivoRecibido(totalVenta);
                    }}
                    data-testid="btn-cash-exacto"
                    aria-pressed={isExacto}
                    title="Cobrar importe exacto sin vuelto"
                  >
                    Exacto
                  </button>
                </div>
                <input
                  id="input-cash-tender"
                  type="number"
                  step="1"
                  min="0"
                  className="input-number-large"
                  value={efectivoRecibido}
                  onChange={handleCashInputChange}
                  placeholder="0"
                  data-testid="input-efectivo-recibido"
                />
              </div>

              {/* Resumen de Vuelto */}
              <div className="change-summary-row">
                <span className="change-label">Vuelto a entregar:</span>
                <span className="change-value" data-testid="change-amount">
                  {vueltoDisplay}
                </span>
              </div>

              {cashErrorMessage && (
                <div
                  className="cash-shortage-alert"
                  data-testid={isCashShortage ? "cash-shortage-alert" : "cash-invalid-alert"}
                >
                  {cashErrorMessage}
                </div>
              )}
            </div>
          )}

          {metodoPago === "TRANSFERENCIA" && (
            <div className="payment-detail-box">
              <div className="transfer-instructions">
                <p>Verificá la acreditación en la app bancaria o billetera digital.</p>
                <div className="transfer-amount-check">
                  Importe exacto: <strong>${totalVenta.toLocaleString("es-AR")}</strong>
                </div>
              </div>
            </div>
          )}

          {metodoPago === "MIXTO" && (
            <div className="payment-detail-box">
              <div className="split-payment-row">
                <label htmlFor="input-split-cash" className="input-label">Monto en Efectivo ($):</label>
                <input
                  id="input-split-cash"
                  type="number"
                  min="0"
                  className="input-number"
                  value={montoMixtoEfvo}
                  onChange={(e) => {
                    const parsed = parseFloat(e.target.value);
                    const efvo = Number.isFinite(parsed) ? Math.round(parsed) : 0;
                    setMontoMixtoEfvo(efvo);
                    if (efvo >= 0) {
                      setMontoMixtoTransf(Math.max(0, totalVenta - efvo));
                    }
                  }}
                  data-testid="input-mixto-efvo"
                />
              </div>

              <div className="split-payment-row">
                <label htmlFor="input-split-transf" className="input-label">Monto en Transferencia ($):</label>
                <input
                  id="input-split-transf"
                  type="number"
                  min="0"
                  className="input-number"
                  value={montoMixtoTransf}
                  onChange={(e) => {
                    const parsed = parseFloat(e.target.value);
                    const transf = Number.isFinite(parsed) ? Math.round(parsed) : 0;
                    setMontoMixtoTransf(transf);
                  }}
                  data-testid="input-mixto-transf"
                />
              </div>

              <div className="split-balance-status">
                {balanceMixto ? (
                  <span className="text-balance-ok">
                    ✅ Efectivo (${montoMixtoEfvo}) + Transferencia (${montoMixtoTransf}) = $
                    {totalVenta}
                  </span>
                ) : (
                  <span className="text-balance-error" data-testid="split-balance-error">
                    ❌ La suma (${montoMixtoEfvo + montoMixtoTransf}) debe ser exactamente $
                    {totalVenta}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-modal-cancel"
            onClick={onClose}
            disabled={isSubmitting}
          >
            Cancelar
          </button>

          <button
            type="button"
            className="btn-confirm-sale"
            onClick={handleConfirmar}
            disabled={isConfirmDisabled}
            data-testid="btn-confirm-payment"
          >
            {isSubmitting ? "Guardando..." : "Confirmar e Imprimir"}
          </button>
        </div>
      </div>
    </div>
  );
};
