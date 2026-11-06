import React, { useState, useId } from "react";
import { ItemCarrito } from "../types/sales";
import { isWeighableProduct, formatearPesoDisplay } from "../utils/weightSales";

interface CartProps {
  items: ItemCarrito[];
  onUpdateQuantity: (productoId: number, nuevaCantidad: number) => void;
  onUpdateLinePrice?: (
    productoId: number,
    nuevoPrecioUnitario: number,
    nuevoSubtotal: number
  ) => void;
  onEditWeightItem?: (item: ItemCarrito) => void;
  onRemoveItem: (productoId: number) => void;
  onClearCart: () => void;
  onCheckout: () => void;
  onBackToCatalog?: () => void;
}

export const Cart: React.FC<CartProps> = ({
  items,
  onUpdateQuantity,
  onUpdateLinePrice,
  onEditWeightItem,
  onRemoveItem,
  onClearCart,
  onCheckout,
  onBackToCatalog,
}) => {
  const customSubtotalInputId = useId();
  const customUnitInputId = useId();
  // Estado para el modal de "Ajustar precio de esta venta"
  const [adjustingItem, setAdjustingItem] = useState<ItemCarrito | null>(null);
  const [customPriceUnit, setCustomPriceUnit] = useState<string>("");
  const [customSubtotal, setCustomSubtotal] = useState<string>("");
  const [adjustError, setAdjustError] = useState<string | null>(null);

  const totalArticulos = items.reduce((acc, it) => {
    // Si la cantidad es fraccionaria (peso), mostrar 1 ítem fraccionado o sumar unidades enteras
    return acc + it.cantidad;
  }, 0);

  const totalVenta = items.reduce((acc, it) => acc + it.subtotal, 0);

  const handleOpenAdjustPrice = (item: ItemCarrito) => {
    setAdjustingItem(item);
    setCustomPriceUnit(item.precio.toString());
    setCustomSubtotal(item.subtotal.toString());
    setAdjustError(null);
  };

  const handleApplyPriceAdjustment = () => {
    if (!adjustingItem || !onUpdateLinePrice) return;

    const sub = parseFloat(customSubtotal);
    const unit = parseFloat(customPriceUnit);

    if (isNaN(sub) || sub <= 0) {
      setAdjustError("El importe de la línea debe ser un número mayor a cero.");
      return;
    }
    if (isNaN(unit) || unit <= 0) {
      setAdjustError("El precio unitario debe ser un número mayor a cero.");
      return;
    }

    onUpdateLinePrice(adjustingItem.producto_id, Math.round(unit), Math.round(sub));
    setAdjustingItem(null);
  };

  return (
    <div className="cart-container" data-testid="cart-container">
      <div className="cart-header">
        <div className="cart-title-row">
          {onBackToCatalog && (
            <button
              type="button"
              className="btn-mobile-back"
              onClick={onBackToCatalog}
              title="Volver al catálogo"
              data-testid="btn-back-to-catalog"
            >
              ←
            </button>
          )}
          <span className="cart-icon">🛒</span>
          <h2 className="cart-title">Ticket Actual</h2>
          {items.length > 0 && (
            <span className="items-count-badge" data-testid="cart-items-count">
              {items.length} {items.length === 1 ? "ítem" : "ítems"}
            </span>
          )}
        </div>
        {items.length > 0 && (
          <button
            type="button"
            className="btn-clear-cart"
            onClick={onClearCart}
            title="Vaciar carrito"
            data-testid="btn-clear-cart"
          >
            Vaciar
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <div className="cart-empty-state" data-testid="cart-empty">
          <div className="cart-empty-icon">🧾</div>
          <p className="cart-empty-title">El carrito está vacío</p>
          <p className="cart-empty-desc">
            Seleccioná productos del catálogo o escaneá un código de barras.
          </p>
        </div>
      ) : (
        <div className="cart-items-list" data-testid="cart-items-list">
          {items.map((item) => {
            const isWeight = isWeighableProduct({
              nombre: item.nombre,
              unidad: item.unidad,
              categoria: "",
            });

            return (
              <div
                key={item.producto_id}
                className="cart-item-row"
                data-testid={`cart-item-${item.producto_id}`}
              >
                <div className="cart-item-info">
                  <div className="cart-item-name-group">
                    <span className="cart-item-name">{item.nombre}</span>
                    {isWeight && (
                      <span className="badge-weight-item" title="Producto comercializado por peso">
                        ⚖️ Al peso
                      </span>
                    )}
                  </div>
                  <div className="cart-item-pricing-meta">
                    <div className="cart-item-unit-price">
                      {isWeight ? (
                        <>
                          <span>${item.precio.toLocaleString("es-AR")}/kg</span>
                          <span className="weight-qty-display">
                            {" "}
                            • Peso: <strong>{formatearPesoDisplay(item.cantidad)}</strong>
                          </span>
                        </>
                      ) : (
                        <span>
                          ${item.precio.toLocaleString("es-AR")} c/u{item.unidad && !["unidad", "unidades", "u", "un"].includes(item.unidad.toLowerCase().trim()) ? ` (${item.unidad})` : ""}
                        </span>
                      )}
                    </div>

                    {/* Acciones de línea: Ajustar peso o Ajustar precio de esta venta */}
                    <div className="cart-item-extra-actions">
                      {isWeight && onEditWeightItem && (
                        <button
                          type="button"
                          className="btn-line-action btn-adjust-weight"
                          onClick={() => onEditWeightItem(item)}
                          title="Modificar peso o importe de este producto"
                          data-testid={`btn-edit-weight-${item.producto_id}`}
                        >
                          ⚖️ Ajustar peso / importe
                        </button>
                      )}
                      {onUpdateLinePrice && (
                        <button
                          type="button"
                          className="btn-line-action btn-adjust-sale-price"
                          onClick={() => handleOpenAdjustPrice(item)}
                          title="Ajustar precio solo para este ticket"
                          data-testid={`btn-adjust-price-${item.producto_id}`}
                        >
                          ✏️ Ajustar precio de esta venta
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                <div className="cart-item-actions">
                  <div className="qty-controls">
                    <button
                      type="button"
                      className="btn-qty"
                      onClick={() =>
                        onUpdateQuantity(
                          item.producto_id,
                          isWeight ? Math.max(0, item.cantidad - 0.25) : item.cantidad - 1
                        )
                      }
                      disabled={item.cantidad <= 0.001}
                      data-testid={`btn-qty-minus-${item.producto_id}`}
                    >
                      -
                    </button>
                    <input
                      type="number"
                      step={isWeight ? "any" : "1"}
                      min="0.001"
                      className="qty-input"
                      value={item.cantidad}
                      onChange={(e) => {
                        const val = parseFloat(e.target.value);
                        if (!isNaN(val) && val > 0) {
                          onUpdateQuantity(item.producto_id, val);
                        }
                      }}
                      data-testid={`input-qty-${item.producto_id}`}
                    />
                    <button
                      type="button"
                      className="btn-qty"
                      onClick={() =>
                        onUpdateQuantity(
                          item.producto_id,
                          isWeight ? item.cantidad + 0.25 : item.cantidad + 1
                        )
                      }
                      data-testid={`btn-qty-plus-${item.producto_id}`}
                    >
                      +
                    </button>
                  </div>

                  <div className="cart-item-subtotal" data-testid={`subtotal-${item.producto_id}`}>
                    ${item.subtotal.toLocaleString("es-AR")}
                  </div>

                  <button
                    type="button"
                    className="btn-remove-item"
                    onClick={() => onRemoveItem(item.producto_id)}
                    title="Eliminar del carrito"
                    data-testid={`btn-remove-${item.producto_id}`}
                  >
                    🗑️
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pie del Carrito: Totales y Botón de Cobro */}
      <div className="cart-footer">
        <div className="cart-summary-box">
          <div className="summary-row">
            <span>Unidades totales:</span>
            <span data-testid="total-units">
              {Number.isInteger(totalArticulos)
                ? totalArticulos
                : totalArticulos.toLocaleString("es-AR", {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 3,
                  })}
            </span>
          </div>
          <div className="summary-total-row">
            <span className="total-label">TOTAL:</span>
            <span className="total-amount" data-testid="cart-total-amount">
              ${totalVenta.toLocaleString("es-AR")}
            </span>
          </div>
        </div>

        <button
          type="button"
          className="btn-checkout"
          onClick={onCheckout}
          disabled={items.length === 0}
          data-testid="btn-checkout"
        >
          <span>Cobrar Ticket</span>
          <span className="checkout-badge">${totalVenta.toLocaleString("es-AR")}</span>
        </button>
      </div>

      {/* Modal: Ajustar precio de esta venta */}
      {adjustingItem && (
        <div className="modal-backdrop" data-testid="adjust-price-modal">
          <div className="modal-card adjust-price-modal-card">
            <div className="modal-header">
              <h3 className="modal-title">Ajustar precio de esta venta</h3>
              <button
                type="button"
                className="btn-modal-close"
                onClick={() => setAdjustingItem(null)}
                aria-label="Cerrar modal de ajuste"
                data-testid="btn-close-adjust-modal"
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <p className="adjust-product-title">
                Producto: <strong>{adjustingItem.nombre}</strong>
              </p>
              <div className="adjust-price-notice">
                ℹ️ Esta modificación es puntual para este ticket.{" "}
                <strong>No modifica el catálogo de productos</strong> ni altera registros generales.
              </div>

              <div className="form-group">
                <label htmlFor={customUnitInputId}>Precio unitario para esta venta ($):</label>
                <input
                  id={customUnitInputId}
                  type="number"
                  inputMode="numeric"
                  min="1"
                  className="form-input"
                  value={customPriceUnit}
                  onChange={(e) => {
                    const u = e.target.value;
                    setCustomPriceUnit(u);
                    const parsed = parseFloat(u);
                    if (!isNaN(parsed) && parsed > 0) {
                      setCustomSubtotal(Math.round(parsed * adjustingItem.cantidad).toString());
                    }
                    setAdjustError(null);
                  }}
                  data-testid="input-adjust-unit-price"
                />
              </div>

              <div className="form-group">
                <label htmlFor={customSubtotalInputId}>O subtotal directo a cobrar en este ticket ($):</label>
                <input
                  id={customSubtotalInputId}
                  type="number"
                  inputMode="numeric"
                  min="1"
                  className="form-input"
                  value={customSubtotal}
                  onChange={(e) => {
                    const s = e.target.value;
                    setCustomSubtotal(s);
                    const parsedSub = parseFloat(s);
                    if (!isNaN(parsedSub) && parsedSub > 0 && adjustingItem.cantidad > 0) {
                      setCustomPriceUnit(
                        Math.round(parsedSub / adjustingItem.cantidad).toString()
                      );
                    }
                    setAdjustError(null);
                  }}
                  data-testid="input-adjust-subtotal"
                />
              </div>

              {adjustError && (
                <div className="alert-box-error" data-testid="adjust-price-error">
                  ⚠️ {adjustError}
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-modal-cancel"
                onClick={() => setAdjustingItem(null)}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={handleApplyPriceAdjustment}
                data-testid="btn-confirm-adjust-price"
              >
                Aplicar a este ticket
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
