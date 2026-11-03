export interface SystemReadiness {
  status: "PREPARADO" | "MIGRACIONES_PENDIENTES" | "ESQUEMA_INCONSISTENTE" | "ERROR_CONEXION";
  listo: boolean;
  versiones_aplicadas: number[];
  migraciones_pendientes: number[];
  tablas_existentes: string[];
  tablas_faltantes: string[];
  columnas_faltantes: string[];
  indices_faltantes?: string[];
  indices_incorrectos?: string[];
  inconsistencias: string[];
  mensaje: string;
}
