"""Esquemas Pydantic para el módulo HISTÓRICO de GenaroPOS."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HistoricalSummaryOut(BaseModel):
    total_ventas_pesos: int
    total_tickets: int
    ticket_promedio: float
    total_ventas_efectivo: int
    total_ventas_transferencia: int
    fecha_inicio: str
    fecha_fin: str
    periodo_meses: int
    total_items_filas: int
    total_items_subtotal: int
    total_cargas_cobrado: int
    total_cargas_capital: int
    total_cargas_adicional: int
    total_operaciones_cargas: int
    total_cargas_efectivo: int
    total_cargas_transferencia: int
    advertencia_pagos: str
    advertencia_items: str


class HistoricalTicketResumenOut(BaseModel):
    id: str
    fecha_hora_local: str
    total_venta: int
    monto_efectivo: int
    monto_transferencia: int
    metodo_pago: str
    tiene_items: int
    balance_pago_diferencia: int


class HistoricalSeriePuntoOut(BaseModel):
    periodo: str
    tickets: int
    ventas_total: int
    monto_efectivo: int
    monto_transferencia: int


class HistoricalSalesOut(BaseModel):
    desde: Optional[str] = None
    hasta: Optional[str] = None
    metodo_pago: Optional[str] = None
    total_ventas: int
    cantidad_tickets: int
    ticket_promedio: float
    total_efectivo: int
    total_transferencia: int
    serie_temporal: List[HistoricalSeriePuntoOut]
    tickets: List[HistoricalTicketResumenOut]
    total_registros: int
    limit: int
    offset: int


class HistoricalProductItemOut(BaseModel):
    posicion: int
    producto_nombre: str
    operaciones: int
    unidades_cuantificables: float
    ventas_pesables_monto: int
    total_recaudado: int


class HistoricalProductsOut(BaseModel):
    desde: Optional[str] = None
    hasta: Optional[str] = None
    modo_depurado: bool
    total_productos_distintos: int
    productos: List[HistoricalProductItemOut]
    total_registros: int
    limit: int
    offset: int
    advertencia_pesables: str


class HistoricalServiceBreakdownOut(BaseModel):
    servicio: str
    total_operaciones: int
    capital_recargado: int
    adicional_ganancia: int
    total_cobrado: int
    pago_efectivo: int
    pago_transferencia: int


class HistoricalServiceSerieOut(BaseModel):
    periodo: str
    operaciones: int
    total_cobrado: int
    adicional_ganancia: int


class HistoricalServicesOut(BaseModel):
    desde: Optional[str] = None
    hasta: Optional[str] = None
    excluir_duplicados: bool
    total_operaciones: int
    capital_recargado: int
    adicional_ganancia: int
    total_cobrado: int
    pago_efectivo: int
    pago_transferencia: int
    servicios: List[HistoricalServiceBreakdownOut]
    serie_temporal: List[HistoricalServiceSerieOut]
    advertencia_duplicados: str


class HistoricalItemDetalleOut(BaseModel):
    id: int
    ticket_id: str
    producto_nombre: str
    cantidad: Optional[float] = None
    unidad: str
    precio_unitario: int
    subtotal: int
    metodo_pago: str
    calculo_subtotal_coincide: int
    es_duplicado_deriva: int
    ticket_id_correlacionado: Optional[str] = None


class HistoricalTicketDetalleOut(BaseModel):
    id: str
    fecha_hora_local: str
    fecha_hora_utc: str
    total_venta: int
    monto_efectivo: int
    monto_transferencia: int
    metodo_pago: str
    es_nocturno: int
    balance_pago_diferencia: int
    tiene_items: int
    origen_fila_excel: int
    items: List[HistoricalItemDetalleOut]
    alertas: List[str]


class HistoricalTicketsSearchOut(BaseModel):
    total_encontrados: int
    tickets: List[HistoricalTicketResumenOut]
    limit: int
    offset: int


class HistoricalPeriodoMetricasOut(BaseModel):
    desde: str
    hasta: str
    facturacion_ventas: int
    cantidad_tickets: int
    ticket_promedio: float
    cargas_cobrado: int
    cargas_adicional: int
    operaciones_cargas: int
    top_productos: List[Dict[str, Any]]


class HistoricalCompareOut(BaseModel):
    periodo_a: HistoricalPeriodoMetricasOut
    periodo_b: HistoricalPeriodoMetricasOut
    variaciones: Dict[str, float]
