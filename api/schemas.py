"""Modelos Pydantic para la API local de GenaroPOS."""
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from db.sales import ErrorVentasTurso, _fecha_utc


class HealthResponse(BaseModel):
    """Respuesta del estado de salud del proceso de la API."""

    status: str
    service: str
    version: str
    db_connectivity: str


class ProductoCatalogo(BaseModel):
    """Producto activo del catálogo con precio único canónico."""

    id_producto: int
    nombre: str
    categoria: str
    proveedor: str
    unidad: str
    costo: int
    margen_porcentaje: float
    precio_venta: int = Field(
        ...,
        description="Precio único de venta canónico (independiente de la hora)",
    )
    precio_dia: int = Field(
        ...,
        description="Campo de compatibilidad histórica (igual a precio_venta)",
    )
    precio_noche: int = Field(
        ...,
        description="Campo de compatibilidad histórica (igual a precio_venta)",
    )
    activo: bool = Field(default=True, description="Estado lógico de activación")
    fecha_act: str

    @classmethod
    def from_adapter_dict(cls, data: Dict[str, Any]) -> "ProductoCatalogo":
        # La fuente canónica única del precio de venta es PRECIO_DIA
        p_canonico = int(data.get("PRECIO_DIA", data.get("precio_dia", 0)))
        return cls(
            id_producto=int(data.get("ID_PRODUCTO", data.get("id_producto", 0))),
            nombre=str(data.get("NOMBRE", data.get("nombre", ""))).strip(),
            categoria=str(data.get("CATEGORIA", data.get("categoria", "Otro"))).strip(),
            proveedor=str(data.get("PROVEEDOR", data.get("proveedor", "General"))).strip(),
            unidad=str(data.get("UNIDAD", data.get("unidad", "Unidad"))).strip(),
            costo=int(data.get("COSTO", data.get("costo", 0))),
            margen_porcentaje=float(data.get("MARGEN_%", data.get("margen_porcentaje", 0.0))),
            precio_venta=p_canonico,
            precio_dia=p_canonico,
            precio_noche=p_canonico,
            activo=bool(data.get("ACTIVO", data.get("activo", True))),
            fecha_act=str(data.get("FECHA_ACT", data.get("fecha_act", ""))).strip(),
        )


class ProductoCreateIn(BaseModel):
    """Payload para dar de alta un nuevo producto en catálogo."""

    nombre: str = Field(..., min_length=1)
    categoria: str = Field(default="Otro")
    proveedor: str = Field(default="General")
    unidad: str = Field(default="Unidad")
    costo: int = Field(default=0, ge=0)
    precio_venta: int = Field(..., gt=0)
    activo: bool = Field(default=True)

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("El nombre del producto no puede estar vacío.")
        return s


class ProductoUpdateIn(BaseModel):
    """Payload para modificación puntual de campos de un producto."""

    nombre: Optional[str] = None
    categoria: Optional[str] = None
    proveedor: Optional[str] = None
    unidad: Optional[str] = None
    costo: Optional[int] = Field(default=None, ge=0)
    precio_venta: Optional[int] = Field(default=None, gt=0)

    @field_validator("nombre")
    @classmethod
    def validar_nombre_opt(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("El nombre del producto no puede estar vacío.")
            return s
        return v


class ProductoStatusIn(BaseModel):
    """Payload para activar o desactivar un producto."""

    activo: bool


class ProductoDeleteOut(BaseModel):
    """Respuesta de eliminación inteligente (Smart Delete) de un producto."""

    id_producto: int
    nombre: str
    status: str
    accion: str
    eliminado_fisico: bool
    activo: bool = False
    tiene_ventas: bool
    mensaje: str


class CatalogoResponse(BaseModel):
    """Respuesta estructurada para el catálogo de productos."""

    total: int
    productos: List[ProductoCatalogo]


class ItemVentaInput(BaseModel):
    """Ítem individual dentro de un ticket de venta."""

    ticket_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ticket_id", "TICKET_ID"),
    )
    producto: str = Field(
        min_length=1,
        validation_alias=AliasChoices("producto", "PRODUCTO"),
    )
    cantidad: float = Field(
        gt=0,
        validation_alias=AliasChoices("cantidad", "CANTIDAD"),
    )
    unidad: str = Field(
        default="Unidad",
        validation_alias=AliasChoices("unidad", "UNIDAD"),
    )
    precio_unit: int = Field(
        ge=0,
        validation_alias=AliasChoices("precio_unit", "PRECIO_UNIT"),
    )
    subtotal: int = Field(
        ge=0,
        validation_alias=AliasChoices("subtotal", "SUBTOTAL"),
    )
    metodo_pago: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("metodo_pago", "METODO_PAGO"),
    )
    costo_unitario: Optional[int] = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("costo_unitario", "COSTO_UNITARIO"),
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("producto")
    @classmethod
    def validar_producto_no_vacio(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("El nombre del producto no puede estar vacío.")
        return s

    @model_validator(mode="after")
    def validar_subtotal(self) -> "ItemVentaInput":
        esperado = round(
            Decimal(str(self.cantidad)) * Decimal(str(self.precio_unit))
        )
        if self.subtotal != esperado:
            raise ValueError(
                f"El subtotal ({self.subtotal}) no coincide con cantidad ({self.cantidad}) "
                f"* precio_unit ({self.precio_unit}) = {esperado}."
            )
        return self


class TicketVentaInput(BaseModel):
    """Cabecera del ticket de venta."""

    ticket_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("ticket_id", "TICKET_ID"),
    )
    fecha: str = Field(
        min_length=1,
        validation_alias=AliasChoices("fecha", "FECHA"),
    )
    total_venta: int = Field(
        ge=0,
        validation_alias=AliasChoices("total_venta", "TOTAL_VENTA"),
    )
    monto_efectivo: int = Field(
        ge=0,
        validation_alias=AliasChoices("monto_efectivo", "MONTO_EFECTIVO"),
    )
    monto_transf: int = Field(
        ge=0,
        validation_alias=AliasChoices("monto_transf", "MONTO_TRANSF"),
    )
    metodo_pago: str = Field(
        validation_alias=AliasChoices("metodo_pago", "METODO_PAGO"),
    )
    es_nocturno: bool = Field(
        default=False,
        validation_alias=AliasChoices("es_nocturno", "ES_NOCTURNO"),
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("ticket_id")
    @classmethod
    def validar_ticket_id(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("TICKET_ID es obligatorio.")
        return s

    @field_validator("fecha")
    @classmethod
    def validar_fecha(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("La fecha es obligatoria.")
        try:
            _fecha_utc(s)
        except ErrorVentasTurso as e:
            raise ValueError(str(e)) from e
        return s

    @field_validator("metodo_pago")
    @classmethod
    def validar_metodo_pago(cls, v: str) -> str:
        m = v.strip().upper()
        if m not in {"EFECTIVO", "TRANSFERENCIA", "MIXTO"}:
            raise ValueError(
                f"Método de pago '{v}' no permitido. Debe ser EFECTIVO, TRANSFERENCIA o MIXTO."
            )
        return m

    @model_validator(mode="after")
    def validar_balance_pagos(self) -> "TicketVentaInput":
        if self.monto_efectivo + self.monto_transf != self.total_venta:
            raise ValueError(
                f"Efectivo ({self.monto_efectivo}) más transferencia ({self.monto_transf}) "
                f"debe coincidir exactamente con el total ({self.total_venta})."
            )
        if self.metodo_pago == "EFECTIVO" and (
            self.monto_efectivo != self.total_venta or self.monto_transf != 0
        ):
            raise ValueError(
                "En pago EFECTIVO, monto_efectivo debe ser igual al total y monto_transf debe ser 0."
            )
        if self.metodo_pago == "TRANSFERENCIA" and (
            self.monto_transf != self.total_venta or self.monto_efectivo != 0
        ):
            raise ValueError(
                "En pago TRANSFERENCIA, monto_transf debe ser igual al total y monto_efectivo debe ser 0."
            )
        return self


class VentaRequest(BaseModel):
    """Payload de entrada para el registro de ventas."""

    ticket: TicketVentaInput = Field(
        validation_alias=AliasChoices("ticket", "TICKET"),
    )
    items: List[ItemVentaInput] = Field(
        min_length=1,
        validation_alias=AliasChoices("items", "ITEMS"),
        description="Lista de ítems de la venta (mínimo 1 ítem).",
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validar_coherencia_global(self) -> "VentaRequest":
        # 1. Pertenencia de ítems al ticket
        for idx, item in enumerate(self.items):
            if item.ticket_id and item.ticket_id.strip() != self.ticket.ticket_id:
                raise ValueError(
                    f"El ítem #{idx + 1} posee ticket_id '{item.ticket_id}' que no coincide "
                    f"con el ticket principal '{self.ticket.ticket_id}'."
                )

        # 2. Coherencia entre suma de subtotales y total del ticket
        suma_items = sum(item.subtotal for item in self.items)
        if suma_items != self.ticket.total_venta:
            raise ValueError(
                f"La suma de subtotales de los ítems ({suma_items}) no coincide "
                f"con el total del ticket ({self.ticket.total_venta})."
            )

        return self


class VentaResponse(BaseModel):
    """Respuesta exitosa de venta registrada."""

    ticket_id: str
    status: str = "registrado"
    mensaje: str = "Venta registrada exitosamente."


class ItemVentaOut(BaseModel):
    """Detalle de un ítem perteneciente a un ticket registrado."""

    id: Optional[int] = None
    ticket_id: str
    producto_nombre: str
    cantidad: float
    unidad: str = "Unidad"
    precio_unitario: int
    subtotal: int
    metodo_pago: str
    costo_unitario: Optional[int] = None


class VentaAuditoriaOut(BaseModel):
    """Registro inmutable de trazabilidad de cambios en ventas."""

    id: int
    ticket_id: str
    item_id: Optional[int] = None
    accion: str
    motivo: Optional[str] = None
    usuario: str
    datos_previos_json: Optional[str] = None
    datos_nuevos_json: Optional[str] = None
    creado_en_utc: str


class TicketResumenOut(BaseModel):
    """Resumen de cabecera de ticket para listado de historial."""

    ticket_id: str
    fecha: str
    fecha_hora_utc: str
    total_venta: int
    monto_efectivo: int
    monto_transferencia: int
    metodo_pago: str
    cantidad_items: int
    anulado: bool
    motivo_anulacion: Optional[str] = None


class TicketDetalleOut(BaseModel):
    """Detalle exhaustivo de ticket con ítems y trazabilidad de auditoría."""

    ticket_id: str
    fecha: str
    fecha_hora_utc: str
    total_venta: int
    monto_efectivo: int
    monto_transferencia: int
    metodo_pago: str
    anulado: bool
    motivo_anulacion: Optional[str] = None
    items: List[ItemVentaOut] = []
    auditoria: List[VentaAuditoriaOut] = []


class VentasHistorialResponse(BaseModel):
    """Respuesta paginada o filtrada del historial de ventas."""

    total: int
    ventas: List[TicketResumenOut]


class AnularVentaIn(BaseModel):
    """Payload para anulación lógica de venta."""

    motivo: str = Field(..., min_length=1, max_length=250)
    usuario: str = Field(default="sistema", max_length=100)


class EditarItemVentaIn(BaseModel):
    """Payload para corrección de cantidad o subtotal de un ítem."""

    nueva_cantidad: Optional[float] = Field(default=None, gt=0)
    nuevo_subtotal: Optional[int] = Field(default=None, ge=0)
    usuario: str = Field(default="sistema", max_length=100)


# ==============================================================================
# MODELOS DE SERVICIOS Y CARGAS VIRTUALES
# ==============================================================================

MAPA_SERVICIOS_VALIDOS: Dict[str, List[str]] = {
    "PREPAY": ["SUBE", "Personal", "Movistar", "Claro"],
    "CLARO COMERCIOS": ["Claro"],
    "MERCADO PAGO": ["PREPAY", "SUBE", "Personal", "Movistar", "Claro"],
}


class CargaServicioCreateIn(BaseModel):
    """Payload para registrar una nueva carga de servicio."""

    servicio: Optional[str] = Field(default=None, description="Nombre de la empresa o servicio")
    plataforma_origen: Optional[str] = Field(default=None, description="Plataforma de origen (PREPAY, CLARO COMERCIOS, MERCADO PAGO)")
    servicio_destino: Optional[str] = Field(default=None, description="Servicio de destino dependiente de la plataforma")
    monto_carga: int = Field(..., gt=0, description="Capital o saldo a recargar")
    monto_adicional: int = Field(default=0, ge=0, description="Recargo o adicional cobrado")
    total_cobrado: Optional[int] = Field(default=None, ge=0, description="Total cobrado al cliente")
    pago_efvo: int = Field(default=0, ge=0, description="Monto cobrado en efectivo")
    pago_transf: int = Field(default=0, ge=0, description="Monto cobrado por transferencia")
    fecha: Optional[str] = Field(default=None, description="Fecha de la operación (opcional)")
    idempotency_key: Optional[str] = Field(default=None, max_length=255, description="Clave única de idempotencia del cliente")

    @model_validator(mode="after")
    def validar_plataforma_y_servicio(self) -> "CargaServicioCreateIn":
        plat = self.plataforma_origen
        dest = self.servicio_destino

        if plat is not None or dest is not None:
            if not plat or not str(plat).strip():
                raise ValueError("Debe seleccionar una plataforma de origen.")
            if not dest or not str(dest).strip():
                raise ValueError("Debe seleccionar un servicio de destino.")

            plat_clean = str(plat).strip().upper()
            matching_plat = next((k for k in MAPA_SERVICIOS_VALIDOS if k.upper() == plat_clean), None)
            if not matching_plat:
                raise ValueError(
                    f"Plataforma de origen '{plat}' inválida. Opciones válidas: {list(MAPA_SERVICIOS_VALIDOS.keys())}."
                )

            dest_clean = str(dest).strip()
            opciones_validas = MAPA_SERVICIOS_VALIDOS[matching_plat]
            matching_dest = next((d for d in opciones_validas if d.lower() == dest_clean.lower()), None)
            if not matching_dest:
                raise ValueError(
                    f"Combinación inválida: el servicio '{dest}' no está disponible para la plataforma '{matching_plat}'. "
                    f"Opciones válidas: {opciones_validas}."
                )

            object.__setattr__(self, "plataforma_origen", matching_plat)
            object.__setattr__(self, "servicio_destino", matching_dest)
            if not self.servicio or not str(self.servicio).strip():
                object.__setattr__(self, "servicio", matching_dest)
        else:
            # Compatibilidad con clientes legados (sin plataforma ni destino)
            if not self.servicio or not str(self.servicio).strip():
                raise ValueError("Debe seleccionar tanto la plataforma de origen como el servicio de destino.")
            object.__setattr__(self, "servicio", str(self.servicio).strip())
            object.__setattr__(self, "plataforma_origen", None)
            object.__setattr__(self, "servicio_destino", None)

        return self

    @model_validator(mode="after")
    def validar_consistencia_financiera(self) -> "CargaServicioCreateIn":
        esperado_total = self.monto_carga + self.monto_adicional
        if self.total_cobrado is not None and self.total_cobrado != esperado_total:
            raise ValueError(
                f"TOTAL_COBRADO ({self.total_cobrado}) debe coincidir con carga ({self.monto_carga}) "
                f"más adicional ({self.monto_adicional}) = {esperado_total}."
            )
        object.__setattr__(self, "total_cobrado", esperado_total)

        suma_pagos = self.pago_efvo + self.pago_transf
        if suma_pagos != esperado_total:
            raise ValueError(
                f"La suma de efectivo ({self.pago_efvo}) y transferencia ({self.pago_transf}) = {suma_pagos} "
                f"debe coincidir exactamente con el total cobrado ({esperado_total})."
            )

        return self


class CargaServicioUpdateIn(BaseModel):
    """Payload para editar una carga de servicio existente."""

    servicio: Optional[str] = None
    plataforma_origen: Optional[str] = None
    servicio_destino: Optional[str] = None
    monto_carga: Optional[int] = Field(default=None, gt=0)
    monto_adicional: Optional[int] = Field(default=None, ge=0)
    total_cobrado: Optional[int] = Field(default=None, ge=0)
    pago_efvo: Optional[int] = Field(default=None, ge=0)
    pago_transf: Optional[int] = Field(default=None, ge=0)
    fecha: Optional[str] = None

    @model_validator(mode="after")
    def validar_consistencia_plataforma(self) -> "CargaServicioUpdateIn":
        plat = self.plataforma_origen
        dest = self.servicio_destino

        if plat is not None and dest is not None:
            plat_clean = str(plat).strip().upper()
            matching_plat = next((k for k in MAPA_SERVICIOS_VALIDOS if k.upper() == plat_clean), None)
            if not matching_plat:
                raise ValueError(
                    f"Plataforma de origen '{plat}' inválida. Opciones válidas: {list(MAPA_SERVICIOS_VALIDOS.keys())}."
                )
            dest_clean = str(dest).strip()
            opciones_validas = MAPA_SERVICIOS_VALIDOS[matching_plat]
            matching_dest = next((d for d in opciones_validas if d.lower() == dest_clean.lower()), None)
            if not matching_dest:
                raise ValueError(
                    f"Combinación inválida: '{dest}' no está disponible para '{matching_plat}'."
                )
            object.__setattr__(self, "plataforma_origen", matching_plat)
            object.__setattr__(self, "servicio_destino", matching_dest)
            object.__setattr__(self, "servicio", matching_dest)
        elif plat is not None and dest is None:
            plat_clean = str(plat).strip().upper()
            if plat_clean not in MAPA_SERVICIOS_VALIDOS:
                raise ValueError(
                    f"Plataforma de origen '{plat}' inválida. Opciones válidas: {list(MAPA_SERVICIOS_VALIDOS.keys())}."
                )
            object.__setattr__(self, "plataforma_origen", plat_clean)
        elif dest is not None and plat is None:
            dest_clean = str(dest).strip()
            todos_destinos = {d.lower() for dests in MAPA_SERVICIOS_VALIDOS.values() for d in dests}
            if dest_clean.lower() not in todos_destinos:
                raise ValueError(f"Servicio de destino '{dest}' inválido.")
        return self


class CargaServicioOut(BaseModel):
    """Representación de una carga de servicio para la interfaz."""

    id: int
    fecha: str
    servicio: str
    plataforma_origen: Optional[str] = None
    servicio_destino: Optional[str] = None
    monto_carga: int
    monto_adicional: int
    total_cobrado: int
    pago_efvo: int
    pago_transf: int
    anulado: bool = False
    idempotency_key: Optional[str] = None


class CargasSummaryOut(BaseModel):
    """Resumen contable y de saldos de cargas para un período."""

    total_operaciones: int
    total_cobrado: int
    total_capital: int
    total_adicional: int
    total_efectivo: int
    total_transferencia: int
    capital_prepay: int
    capital_claro: int
    capital_sube_mp: int
    operaciones_anuladas: int


# ==============================================================================
# MODELOS DE GASTOS, ARQUEO Y CIERRES DIARIOS
# ==============================================================================


class GastoCreateIn(BaseModel):
    """Payload para registrar un gasto o retiro de caja."""

    detalle: str = Field(..., min_length=1, description="Concepto o descripción del gasto")
    monto_salida: int = Field(..., gt=0, description="Importe positivo del egreso")
    tipo: str = Field(default="General", description="Categoría del gasto (ej. Proveedores, Limpieza, etc.)")
    origen_fondos: str = Field(default="Caja diaria", description="Origen de los fondos")
    metodo_pago: str = Field(default="EFECTIVO", description="EFECTIVO o TRANSFERENCIA")
    fecha: Optional[str] = Field(default=None, description="Fecha de la operación (opcional)")
    idempotency_key: Optional[str] = Field(default=None, max_length=255, description="Clave única de idempotencia del cliente")

    @field_validator("detalle")
    @classmethod
    def validar_detalle(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("El concepto o detalle del gasto es obligatorio.")
        return s

    @field_validator("metodo_pago")
    @classmethod
    def validar_metodo_pago(cls, v: str) -> str:
        s = v.strip().upper()
        if s not in ("EFECTIVO", "TRANSFERENCIA"):
            raise ValueError("El método de pago debe ser 'EFECTIVO' o 'TRANSFERENCIA'.")
        return s


class GastoUpdateIn(BaseModel):
    """Payload para editar un gasto existente."""

    detalle: Optional[str] = None
    monto_salida: Optional[int] = Field(default=None, gt=0)
    tipo: Optional[str] = None
    origen_fondos: Optional[str] = None
    metodo_pago: Optional[str] = None
    fecha: Optional[str] = None


class GastoOut(BaseModel):
    """Representación de un gasto para la interfaz."""

    id: int
    fecha: str
    tipo: str
    detalle: str
    monto_salida: int
    origen_fondos: str
    metodo_pago: str
    anulado: bool = False
    idempotency_key: Optional[str] = None


class GastosSummaryOut(BaseModel):
    """Resumen de gastos por período."""

    total_operaciones: int
    gastos_efvo: int
    gastos_transf: int
    gastos_total: int
    operaciones_anuladas: int


class ArqueoDiarioOut(BaseModel):
    """Resumen de arqueo diario consolidando ventas, servicios y gastos."""

    fecha: str
    # Ventas
    ventas_efvo: int
    ventas_transf: int
    total_ventas: int
    ganancia_estimada_ventas: int
    # Servicios
    servicios_efvo: int
    servicios_transf: int
    total_servicios: int
    capital_prepay: int
    capital_claro: int
    capital_sube_mp: int
    adicionales_ganancia: int
    d_recargas: int
    # Gastos
    gastos_efvo: int
    gastos_transf: int
    gastos_total: int
    # Totales conciliados
    efectivo_disponible_drugstore: int
    efectivo_esperado: int
    total_banco: int


class CierreDiarioIn(BaseModel):
    """Payload para registrar el cierre diario de caja."""

    fecha: str = Field(..., min_length=8, description="Fecha de cierre (YYYY-MM-DD o DD/MM/YYYY)")
    efectivo_esperado: int
    efectivo_contado: int
    total_ventas_efvo: int = 0
    total_ventas_transf: int = 0
    total_servicios_efvo: int = 0
    total_servicios_transf: int = 0
    total_gastos_efvo: int = 0
    total_gastos_transf: int = 0
    total_banco: int = 0
    observaciones: Optional[str] = ""
    cerrado_por: str = "sistema"


class CierreDiarioOut(BaseModel):
    """Detalle de un cierre diario registrado."""

    id: int
    fecha: str
    efectivo_esperado: int
    efectivo_contado: int
    diferencia: int
    total_ventas_efvo: int
    total_ventas_transf: int
    total_servicios_efvo: int
    total_servicios_transf: int
    total_gastos_efvo: int
    total_gastos_transf: int
    total_banco: int
    observaciones: str
    cerrado_por: str
    cerrado_en_utc: Optional[str] = ""


# ==============================================================================
# MODELOS DE PREVENTISTAS Y PEDIDOS A PROVEEDORES
# ==============================================================================


class CambioPrecioPreventistaIn(BaseModel):
    """Ítem para actualización de costo y precio de venta."""

    id_producto: int = Field(..., gt=0, description="ID del producto en catálogo")
    costo: float = Field(..., ge=0, description="Costo de compra unitario")
    precio_venta: float = Field(..., gt=0, description="Precio de venta unitario")


class LotePreciosPreventistaIn(BaseModel):
    """Payload para guardar lote de modificaciones de precios por preventista."""

    proveedor: str = Field(..., min_length=1, description="Nombre del proveedor / preventista")
    cambios: List[CambioPrecioPreventistaIn] = Field(..., min_length=1)


class LotePreciosPreventistaOut(BaseModel):
    """Respuesta de actualización de lote de precios."""

    actualizados: int
    proveedor: str
    mensaje: str


class AltaRapidaProductoPreventistaIn(BaseModel):
    """Payload para alta rápida de producto ligada al preventista seleccionado."""

    proveedor: str = Field(..., min_length=1, description="Proveedor obligatorio")
    nombre: str = Field(..., min_length=1, description="Nombre del producto")
    categoria: str = Field(default="Otro", description="Categoría")
    unidad: str = Field(default="Unidad", description="Unidad de medida")
    costo: float = Field(default=0.0, ge=0, description="Costo")
    precio_venta: float = Field(..., gt=0, description="Precio de venta único")


class PedidoItemIn(BaseModel):
    """Ítem incluido en un pedido a proveedor."""

    producto_id: Optional[int] = None
    producto_nombre: str = Field(..., min_length=1)
    cantidad: float = Field(..., gt=0)
    costo_unitario: float = Field(default=0.0, ge=0)


class PedidoItemOut(BaseModel):
    """Representación de un ítem de pedido."""

    id: Optional[int] = None
    producto_id: Optional[int] = None
    producto_nombre: str
    cantidad: float
    costo_unitario: float
    subtotal: float


class PedidoProveedorCreateIn(BaseModel):
    """Payload para registrar un pedido de reposición a preventista."""

    id: Optional[str] = None
    fecha: Optional[str] = None
    proveedor: str = Field(..., min_length=1)
    observaciones: Optional[str] = None
    items: List[PedidoItemIn] = Field(..., min_length=1)


class PedidoProveedorOut(BaseModel):
    """Resumen de pedido para listados."""

    id: str
    fecha: str
    proveedor: str
    estado: str
    total: float
    observaciones: Optional[str] = ""
    creado_en_utc: Optional[str] = ""
    items_count: int = 0


class PedidoProveedorDetalleOut(BaseModel):
    """Detalle completo del pedido con sus ítems."""

    id: str
    fecha: str
    proveedor: str
    estado: str
    total: float
    observaciones: Optional[str] = ""
    creado_en_utc: Optional[str] = ""
    actualizado_en_utc: Optional[str] = ""
    items: List[PedidoItemOut]


class CambioEstadoPedidoIn(BaseModel):
    """Actualización de estado de un pedido."""

    estado: str = Field(..., description="PENDIENTE, RECIBIDO o CANCELADO")
    observaciones: Optional[str] = None


class CancelarPedidoIn(BaseModel):
    """Cancelación de un pedido con motivo."""

    motivo: str = Field(..., min_length=1, description="Motivo de la cancelación")


class LoginRequest(BaseModel):
    """Solicitud de inicio de sesión."""

    username: str = Field(..., min_length=1, max_length=50, description="Nombre de usuario (alan o yilda)")
    password: str = Field(..., min_length=1, description="Contraseña en texto claro para verificación")


class UserInfo(BaseModel):
    """Información pública mínima del usuario autenticado."""

    username: str
    display_name: str


class TokenResponse(BaseModel):
    """Respuesta exitosa de emisión de sesión."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


# ==============================================================================
# SCHEMAS PARA DASHBOARD Y REPORTES FINANCIEROS
# ==============================================================================


class PuntoSerieTemporalOut(BaseModel):
    fecha: str
    etiqueta: str
    ventas_total: int
    servicios_total: int
    gastos_total: int
    adicionales_ganancia: int
    banco_neto: int


class MetricasCajaAOut(BaseModel):
    total_ventas: int
    ventas_efvo: int
    ventas_transf: int
    efectivo_disponible_drugstore: int
    ganancia_estimada_ventas: int
    es_estimacion_heredada: bool
    cantidad_tickets: int
    ganancia_bruta_real: Optional[int] = None
    costo_historico_total: int
    ventas_con_costo_completo: int
    ventas_con_costo_incompleto: int
    items_con_costo_desconocido: int
    margen_bruto_porcentaje: Optional[float] = None
    tiene_costos_completos: bool


class MetricasCajaBOut(BaseModel):
    capital_total: int
    capital_efvo: int
    capital_transf: int
    cantidad_operaciones: int


class MetricasCajaCOut(BaseModel):
    ganancia_adicional_total: int
    adicional_efvo: int
    adicional_transf: int
    cantidad_operaciones: int


class MetricasCajaDOut(BaseModel):
    ventas_transf: int
    recargas_transf: int
    sube_mp_capital: int
    gastos_transf: int
    total_banco_neto: int


class MetricasCajaEOut(BaseModel):
    capital_total: int
    capital_efvo: int
    capital_transf: int
    cantidad_operaciones: int


class MetricasGastosOut(BaseModel):
    gastos_total: int
    gastos_efvo: int
    gastos_transf: int
    cantidad_gastos: int


class ConciliacionCajonOut(BaseModel):
    ventas_efvo: int
    servicios_efvo: int
    gastos_efvo: int
    d_recargas: int
    efectivo_esperado_cajon: int


class MetricasDashboardOut(BaseModel):
    periodo_tipo: str
    desde: str
    hasta: str
    caja_a: MetricasCajaAOut
    caja_b: MetricasCajaBOut
    caja_c: MetricasCajaCOut
    caja_d: MetricasCajaDOut
    caja_e: MetricasCajaEOut
    gastos: MetricasGastosOut
    conciliacion_cajon: ConciliacionCajonOut
    serie_temporal: List[PuntoSerieTemporalOut]
    cierres: List[CierreDiarioOut]

    # Metadatos de integridad de esquema en base de datos
    esquema_completo: bool = True
    tablas_faltantes: List[str] = Field(default_factory=list)
    advertencia_esquema: Optional[str] = None


class SystemReadinessOut(BaseModel):
    """Diagnóstico exhaustivo y seguro del estado del esquema de base de datos."""

    status: str = Field(
        ...,
        description="PREPARADO, MIGRACIONES_PENDIENTES, ESQUEMA_INCONSISTENTE o ERROR_CONEXION",
    )
    listo: bool = Field(
        ...,
        description="True si el esquema está 100% preparado para operaciones normales",
    )
    versiones_aplicadas: List[int] = Field(default_factory=list)
    migraciones_pendientes: List[int] = Field(default_factory=list)
    tablas_existentes: List[str] = Field(default_factory=list)
    tablas_faltantes: List[str] = Field(default_factory=list)
    columnas_faltantes: List[str] = Field(default_factory=list)
    indices_faltantes: List[str] = Field(default_factory=list)
    indices_incorrectos: List[str] = Field(default_factory=list)
    inconsistencias: List[str] = Field(default_factory=list)
    mensaje: str = Field(...)


# Importar esquemas del módulo HISTÓRICO
from api.schemas_historical import (
    HistoricalSummaryOut,
    HistoricalTicketResumenOut,
    HistoricalSeriePuntoOut,
    HistoricalSalesOut,
    HistoricalProductItemOut,
    HistoricalProductsOut,
    HistoricalServiceBreakdownOut,
    HistoricalServiceSerieOut,
    HistoricalServicesOut,
    HistoricalItemDetalleOut,
    HistoricalTicketDetalleOut,
    HistoricalTicketsSearchOut,
    HistoricalPeriodoMetricasOut,
    HistoricalCompareOut,
)




